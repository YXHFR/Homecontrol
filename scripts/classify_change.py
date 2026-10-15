#!/usr/bin/env python3
"""Avgör om en ändring är "större" och därför ska granskas av alla fyra agenterna.

En ändring är större om något av följande gäller:

* mer än cirka 200 ändrade rader (låsfiler räknas inte),
* minst 5 ändrade filer (låsfiler räknas inte),
* en ny modul (ny Python-fil i kärnan, ny TypeScript-fil i webben eller ny toppkatalog),
* ett nytt beroende (Python, npm, Docker-avbild),
* ett känsligt område (hemligheter, nätverk, CI, behörigheter, lagrad data ...).

Användning:
    python3 scripts/classify_change.py              # jämför med origin/main
    python3 scripts/classify_change.py --base main  # jämför med en annan gren
    python3 scripts/classify_change.py --json

Skriptet jämför arbetskatalogen (inklusive ocommittade och nya filer) med
den punkt där grenen lämnade basgrenen. Det använder bara standardbiblioteket.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath

MAX_LINES = 200
MIN_FILES = 5

LOCKFILES = {"uv.lock", "package-lock.json"}

# Sökvägar (glob, matchas mot hela sökvägen) som alltid räknas som känsliga.
SENSITIVE_PATTERNS = [
    ".env*",
    "*/.env*",
    ".gitignore",
    ".dockerignore",
    "compose*.yaml",
    "docker-compose*.yml",
    "*Dockerfile*",
    "mosquitto/*",
    ".github/workflows/*",
    ".github/dependabot.yml",
    ".gitleaks*",
    ".claude/agents/*",
    "scripts/classify_change.py",
    "*migrations/*",
    "LICENSE",
]

# Ord i filnamn som tyder på säkerhet eller integritet.
SENSITIVE_WORDS = [
    "auth",
    "secret",
    "token",
    "password",
    "passwd",
    "crypt",
    "security",
    "permission",
    "privacy",
    "floorplan",
    "planritning",
]

PYTHON_REQUIREMENT = re.compile(
    r'^\s*"([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*([<>=~!;].*)?",?\s*$'
)
NPM_DEPENDENCY = re.compile(r'^\s*"(@?[A-Za-z0-9][\w.\-/]*)"\s*:\s*"[\^~<>=*\d]')
DOCKER_FROM = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*([^\s:@]+)", re.IGNORECASE)
DOCKER_COPY_FROM = re.compile(r"^\s*COPY\s+--from=([^\s:@]+)[:@]", re.IGNORECASE)
COMPOSE_IMAGE = re.compile(r"^\s*image:\s*['\"]?([^\s:@'\"]+)")


@dataclass
class FileChange:
    path: str
    added: int
    deleted: int
    status: str  # A = ny, M = ändrad, D = borttagen (flyttar räknas som D + A)
    added_lines: list[str] = field(default_factory=list)
    deleted_lines: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    larger: bool
    reasons: list[str]
    lines: int
    files: int


def is_lockfile(path: str) -> bool:
    return PurePosixPath(path).name in LOCKFILES


def is_sensitive(path: str) -> bool:
    if any(fnmatch.fnmatch(path, pattern) for pattern in SENSITIVE_PATTERNS):
        return True
    lowered = path.lower()
    return any(word in lowered for word in SENSITIVE_WORDS)


def is_new_module(change: FileChange, existing_top_dirs: set[str]) -> bool:
    if change.status != "A":
        return False
    path = PurePosixPath(change.path)
    if len(path.parts) > 1 and path.parts[0] not in existing_top_dirs:
        return True
    if path.parts[:2] == ("core", "src") and path.suffix == ".py":
        return True
    is_web_source = path.parts[:2] == ("web", "src") and path.suffix in {".ts", ".tsx"}
    return is_web_source and ".test." not in path.name


def dependency_names(path: str, lines: list[str]) -> set[str]:
    name = PurePosixPath(path).name
    patterns: list[re.Pattern[str]] = []
    if name == "pyproject.toml":
        patterns = [PYTHON_REQUIREMENT]
    elif name == "package.json":
        patterns = [NPM_DEPENDENCY]
    elif "Dockerfile" in name:
        patterns = [DOCKER_FROM, DOCKER_COPY_FROM]
    elif name.startswith(("compose", "docker-compose")) and name.endswith((".yaml", ".yml")):
        patterns = [COMPOSE_IMAGE]
    found = set()
    for line in lines:
        for pattern in patterns:
            match = pattern.match(line)
            if match:
                found.add(match.group(1).lower())
    return found


def new_dependencies(change: FileChange) -> set[str]:
    added = dependency_names(change.path, change.added_lines)
    removed = dependency_names(change.path, change.deleted_lines)
    return added - removed


def classify(changes: list[FileChange], existing_top_dirs: set[str]) -> Verdict:
    counted = [c for c in changes if not is_lockfile(c.path)]
    lines = sum(c.added + c.deleted for c in counted)
    reasons: list[str] = []

    if lines > MAX_LINES:
        reasons.append(f"{lines} ändrade rader (gräns: {MAX_LINES})")
    if len(counted) >= MIN_FILES:
        reasons.append(f"{len(counted)} ändrade filer (gräns: {MIN_FILES})")

    new_modules = [c.path for c in changes if is_new_module(c, existing_top_dirs)]
    if new_modules:
        reasons.append("ny modul: " + ", ".join(sorted(new_modules)))

    # "FROM ... AS web" namnger ett eget byggsteg; senare "FROM web" är inget beroende.
    stage_names = {
        m.group(1).lower()
        for c in changes
        for line in c.added_lines
        if (m := re.match(r"^\s*FROM\s+\S+\s+AS\s+(\S+)", line, re.IGNORECASE))
    }
    deps = sorted(
        d
        for d in {d for c in changes for d in new_dependencies(c)} - stage_names
        # Avbilder som projektet bygger självt är inga externa beroenden.
        if not d.startswith("homecontrol")
    )
    if deps:
        reasons.append("nytt beroende: " + ", ".join(deps))

    sensitive = sorted(c.path for c in changes if is_sensitive(c.path))
    if sensitive:
        reasons.append("känsligt område: " + ", ".join(sensitive))

    return Verdict(larger=bool(reasons), reasons=reasons, lines=lines, files=len(counted))


# --- Läsa ändringar från git ---


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return result.stdout


def parse_patch(patch: str) -> dict[str, tuple[list[str], list[str]]]:
    """Plockar ut tillagda och borttagna rader per fil ur en unified diff."""
    lines_by_file: dict[str, tuple[list[str], list[str]]] = {}
    current: tuple[list[str], list[str]] | None = None
    in_header = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            in_header, current = True, None
        elif in_header and line.startswith(("--- ", "+++ ")):
            name = line[4:]
            if name != "/dev/null":
                path = name[2:] if name[:2] in ("a/", "b/") else name
                current = lines_by_file.setdefault(path, ([], []))
        elif line.startswith("@@"):
            in_header = False
        elif not in_header and current is not None and line.startswith("+"):
            current[0].append(line[1:])
        elif not in_header and current is not None and line.startswith("-"):
            current[1].append(line[1:])
    return lines_by_file


def collect_changes(base: str) -> tuple[list[FileChange], set[str]]:
    merge_base = git("merge-base", base, "HEAD").strip()
    existing_top_dirs = {
        PurePosixPath(p).parts[0]
        for p in git("ls-tree", "-r", "--name-only", merge_base).splitlines()
        if "/" in p
    }

    statuses: dict[str, str] = {}
    for line in git("diff", "--name-status", "--no-renames", merge_base).splitlines():
        parts = line.split("\t")
        statuses[parts[-1]] = parts[0][0]

    changes: dict[str, FileChange] = {}
    for line in git("diff", "--numstat", "--no-renames", merge_base).splitlines():
        added, deleted, path = line.split("\t", 2)
        changes[path] = FileChange(
            path=path,
            added=int(added) if added != "-" else 0,
            deleted=int(deleted) if deleted != "-" else 0,
            status=statuses.get(path, "M"),
        )

    for path, (plus, minus) in parse_patch(git("diff", "-U0", "--no-renames", merge_base)).items():
        if path in changes:
            changes[path].added_lines = plus
            changes[path].deleted_lines = minus

    for path in git("ls-files", "--others", "--exclude-standard").splitlines():
        try:
            with open(path, encoding="utf-8") as handle:
                content = handle.read().splitlines()
        except (OSError, UnicodeDecodeError):
            content = []
        changes[path] = FileChange(path, len(content), 0, "A", added_lines=content)

    return list(changes.values()), existing_top_dirs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--base", default="origin/main", help="gren att jämföra med")
    parser.add_argument("--json", action="store_true", help="skriv resultatet som JSON")
    args = parser.parse_args(argv)

    try:
        changes, top_dirs = collect_changes(args.base)
    except subprocess.CalledProcessError as error:
        print(f"git misslyckades: {error.stderr.strip()}", file=sys.stderr)
        return 2

    verdict = classify(changes, top_dirs)
    if args.json:
        print(json.dumps(verdict.__dict__, ensure_ascii=False, indent=2))
    elif verdict.larger:
        print("STÖRRE ändring – kör alla fyra granskningsagenterna.")
        for reason in verdict.reasons:
            print(f"  - {reason}")
    else:
        print(f"Mindre ändring ({verdict.lines} rader, {verdict.files} filer).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
import tomllib
from dataclasses import dataclass
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

REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
DOCKER_STAGE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*\S+\s+AS\s+(\S+)", re.IGNORECASE)
DOCKER_IMAGE = re.compile(r"^\s*(?:FROM\s+(?:--\S+\s+)*|COPY\s+--from=)([^\s:@]+)", re.IGNORECASE)
COMPOSE_IMAGE = re.compile(r"^\s*image:\s*['\"]?([^\s:@'\"]+)")
NPM_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


@dataclass
class FileChange:
    path: str
    added: int
    deleted: int
    status: str  # A = ny, M = ändrad, D = borttagen (flyttar räknas som D + A)
    before: str = ""  # filens innehåll före ändringen
    after: str = ""  # filens innehåll efter ändringen


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


def python_dependencies(text: str) -> set[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return set()
    project = data.get("project", {})
    requirements = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        requirements += group
    for group in data.get("dependency-groups", {}).values():
        requirements += [r for r in group if isinstance(r, str)]
    names = (REQUIREMENT_NAME.match(r) for r in requirements)
    return {m.group(1).lower().replace("_", "-") for m in names if m}


def npm_dependencies(text: str) -> set[str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return set()
    return {name for section in NPM_SECTIONS for name in data.get(section, {})}


def docker_images(text: str) -> set[str]:
    lines = text.splitlines()
    # "FROM node AS web" namnger ett eget byggsteg; "FROM web" senare är inget beroende.
    stages = {m.group(1).lower() for line in lines if (m := DOCKER_STAGE.match(line))}
    images = {m.group(1).lower() for line in lines if (m := DOCKER_IMAGE.match(line))}
    return images - stages


def compose_images(text: str) -> set[str]:
    return {m.group(1).lower() for line in text.splitlines() if (m := COMPOSE_IMAGE.match(line))}


def dependencies(path: str, text: str) -> set[str]:
    name = PurePosixPath(path).name
    if name == "pyproject.toml":
        found = python_dependencies(text)
    elif name == "package.json":
        found = npm_dependencies(text)
    elif "Dockerfile" in name:
        found = docker_images(text)
    elif name.startswith(("compose", "docker-compose")) and name.endswith((".yaml", ".yml")):
        found = compose_images(text)
    else:
        return set()
    # Avbilder som projektet bygger självt är inga externa beroenden.
    return {d for d in found if not d.startswith("homecontrol")}


def new_dependencies(change: FileChange) -> set[str]:
    return dependencies(change.path, change.after) - dependencies(change.path, change.before)


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

    deps = sorted({d for c in changes for d in new_dependencies(c)})
    if deps:
        reasons.append("nytt beroende: " + ", ".join(deps))

    sensitive = sorted(c.path for c in changes if is_sensitive(c.path))
    if sensitive:
        reasons.append("känsligt område: " + ", ".join(sensitive))

    return Verdict(larger=bool(reasons), reasons=reasons, lines=lines, files=len(counted))


# --- Läsa ändringar från git ---


def git(*args: str) -> str:
    # quotepath=off gör att sökvägar med å, ä och ö skrivs ut som de är.
    result = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def read_at(revision: str, path: str) -> str:
    try:
        return git("show", f"{revision}:{path}")
    except (subprocess.CalledProcessError, UnicodeDecodeError):
        return ""


def collect_changes(base: str) -> tuple[list[FileChange], set[str]]:
    merge_base = git("merge-base", base, "HEAD").strip()
    existing_top_dirs = {
        PurePosixPath(p).parts[0]
        for p in git("ls-tree", "-r", "--name-only", merge_base).splitlines()
        if "/" in p
    }

    statuses: dict[str, str] = {}
    for line in git("diff", "--name-status", "--no-renames", merge_base).splitlines():
        status, path = line.split("\t", 1)
        statuses[path] = status[0]

    changes: list[FileChange] = []
    for line in git("diff", "--numstat", "--no-renames", merge_base).splitlines():
        added, deleted, path = line.split("\t", 2)
        status = statuses.get(path, "M")
        changes.append(
            FileChange(
                path=path,
                added=int(added) if added != "-" else 0,
                deleted=int(deleted) if deleted != "-" else 0,
                status=status,
                before="" if status == "A" else read_at(merge_base, path),
                after="" if status == "D" else read_text(path),
            )
        )

    for path in git("ls-files", "--others", "--exclude-standard").splitlines():
        content = read_text(path)
        changes.append(FileChange(path, len(content.splitlines()), 0, "A", after=content))

    return changes, existing_top_dirs


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

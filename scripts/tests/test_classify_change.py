import subprocess
from pathlib import Path

import pytest

from classify_change import FileChange, classify, collect_changes

TOP_DIRS = {"core", "web", "scripts"}


def change(
    path: str,
    added: int = 1,
    deleted: int = 0,
    status: str = "M",
    before: str = "",
    after: str = "",
) -> FileChange:
    return FileChange(path, added, deleted, status, before=before, after=after)


def test_small_change_is_not_larger() -> None:
    verdict = classify([change("core/src/homecontrol/app.py", 10, 2)], TOP_DIRS)

    assert not verdict.larger
    assert verdict.lines == 12
    assert verdict.files == 1


def test_more_than_200_lines_is_larger() -> None:
    verdict = classify([change("core/src/homecontrol/app.py", 150, 51)], TOP_DIRS)

    assert verdict.larger
    assert "201 ändrade rader" in verdict.reasons[0]


def test_exactly_200_lines_is_not_larger() -> None:
    assert not classify([change("core/src/homecontrol/app.py", 100, 100)], TOP_DIRS).larger


def test_five_files_is_larger() -> None:
    changes = [change(f"core/tests/test_{i}.py") for i in range(5)]

    verdict = classify(changes, TOP_DIRS)

    assert verdict.larger
    assert "5 ändrade filer" in verdict.reasons[0]


def test_lockfiles_do_not_count() -> None:
    changes = [change("core/uv.lock", 5000), change("web/package-lock.json", 3000)]

    verdict = classify(changes, TOP_DIRS)

    assert not verdict.larger
    assert verdict.lines == 0


@pytest.mark.parametrize(
    "path",
    ["core/src/homecontrol/radar.py", "web/src/floor.ts", "firmware/main.yaml"],
)
def test_new_module_is_larger(path: str) -> None:
    verdict = classify([change(path, status="A")], TOP_DIRS)

    assert verdict.reasons == [f"ny modul: {path}"]


def test_new_test_file_is_not_a_module() -> None:
    assert not classify([change("web/src/floor.test.ts", status="A")], TOP_DIRS).larger
    assert not classify([change("core/tests/test_x.py", status="A")], TOP_DIRS).larger


PYPROJECT = """
[project]
name = "homecontrol"
version = "0.1.0"
dependencies = [{deps}]

[dependency-groups]
dev = ["pytest>=9"]
"""


def test_new_python_dependency_is_larger() -> None:
    pyproject = change(
        "core/pyproject.toml",
        before=PYPROJECT.format(deps='"fastapi>=0.1"'),
        after=PYPROJECT.format(deps='"fastapi>=0.1", "asyncpg>=0.30"'),
    )

    assert classify([pyproject], TOP_DIRS).reasons == ["nytt beroende: asyncpg"]


def test_python_version_bump_is_not_a_new_dependency() -> None:
    pyproject = change(
        "core/pyproject.toml",
        before=PYPROJECT.format(deps='"fastapi>=0.1"'),
        after=PYPROJECT.format(deps='"fastapi>=0.2"'),
    )

    assert not classify([pyproject], TOP_DIRS).larger


def test_npm_only_counts_dependency_sections() -> None:
    before = '{"name": "web", "version": "0.1.0", "devDependencies": {"vite": "^8.0.0"}}'
    after = (
        '{"name": "web", "version": "0.2.0", "engines": {"node": ">=22"},'
        ' "dependencies": {"konva": "^10.0.0"}, "devDependencies": {"vite": "^9.0.0"}}'
    )

    verdict = classify([change("web/package.json", before=before, after=after)], TOP_DIRS)

    assert verdict.reasons == ["nytt beroende: konva"]


def test_new_docker_image_is_larger_but_build_stage_is_not() -> None:
    dockerfile = change(
        "core/Dockerfile",
        before="FROM python:3.13-slim\n",
        after=(
            "FROM --platform=$BUILDPLATFORM rust:1 AS build\n"
            "FROM python:3.13-slim\nCOPY --from=build /x /y\n"
        ),
    )

    reasons = classify([dockerfile], TOP_DIRS).reasons

    assert "nytt beroende: rust" in reasons
    assert "build" not in reasons[0]


def test_own_image_is_not_a_dependency() -> None:
    compose = change(
        "compose.yaml", after="services:\n  core:\n    image: homecontrol-core:local\n"
    )

    assert not any("beroende" in r for r in classify([compose], TOP_DIRS).reasons)


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        "compose.yaml",
        "mosquitto/mosquitto.conf",
        ".github/workflows/ci.yml",
        ".claude/agents/sakerhet-integritet.md",
        "core/src/homecontrol/auth.py",
        "core/migrations/001_init.sql",
    ],
)
def test_sensitive_area_is_larger(path: str) -> None:
    verdict = classify([change(path)], TOP_DIRS)

    assert any(reason.startswith("känsligt område") for reason in verdict.reasons)


def run(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def test_collect_changes_from_real_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(tmp_path, "git", "init", "-q", "-b", "main")
    run(tmp_path, "git", "config", "user.email", "test@example.com")
    run(tmp_path, "git", "config", "user.name", "Test")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text("a = 1\n")
    run(tmp_path, "git", "add", ".")
    run(tmp_path, "git", "commit", "-q", "-m", "start")
    run(tmp_path, "git", "checkout", "-q", "-b", "feature")
    (tmp_path / "core" / "a.py").write_text("a = 2\nb = 3\n")
    (tmp_path / "ny").mkdir()
    (tmp_path / "ny" / "b.py").write_text("x = 1\n")
    (tmp_path / "core" / "väg.txt").write_text("å\n")
    monkeypatch.chdir(tmp_path)

    changes, top_dirs = collect_changes("main")

    by_path = {c.path: c for c in changes}
    assert top_dirs == {"core"}
    assert (by_path["core/a.py"].added, by_path["core/a.py"].deleted) == (2, 1)
    assert by_path["core/a.py"].before == "a = 1\n"
    assert by_path["core/a.py"].after == "a = 2\nb = 3\n"
    assert by_path["ny/b.py"].status == "A"
    assert by_path["core/väg.txt"].status == "A"
    assert classify(changes, top_dirs).reasons == ["ny modul: ny/b.py"]

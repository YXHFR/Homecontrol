import subprocess
from pathlib import Path

import pytest

from classify_change import FileChange, classify, collect_changes, parse_patch

TOP_DIRS = {"core", "web", "scripts"}


def change(
    path: str, added: int = 1, deleted: int = 0, status: str = "M", **kw: list[str]
) -> FileChange:
    return FileChange(path, added, deleted, status, **kw)


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


def test_new_python_dependency_is_larger() -> None:
    pyproject = change("core/pyproject.toml", added_lines=['    "asyncpg>=0.30",'])

    verdict = classify([pyproject], TOP_DIRS)

    assert "nytt beroende: asyncpg" in verdict.reasons


def test_version_bump_is_not_a_new_dependency() -> None:
    pyproject = change(
        "core/pyproject.toml",
        added_lines=['    "fastapi>=0.200",'],
        deleted_lines=['    "fastapi>=0.143.2",'],
    )
    package = change(
        "web/package.json",
        added_lines=['    "vite": "^9.0.0",'],
        deleted_lines=['    "vite": "^8.3.4",'],
    )

    assert not classify([pyproject, package], TOP_DIRS).larger


def test_new_npm_dependency_is_larger() -> None:
    package = change("web/package.json", added_lines=['    "konva": "^10.0.0",'])

    assert "nytt beroende: konva" in classify([package], TOP_DIRS).reasons


def test_new_docker_image_is_larger_but_build_stage_is_not() -> None:
    dockerfile = change(
        "core/Dockerfile",
        added_lines=["FROM rust:1 AS build", "FROM build", "COPY --from=build /x /y"],
    )

    verdict = classify([dockerfile], TOP_DIRS)

    assert "nytt beroende: rust" in verdict.reasons


def test_own_image_is_not_a_dependency() -> None:
    compose = change("compose.yaml", added_lines=["    image: homecontrol-core:local"])

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


def test_parse_patch_ignores_content_that_looks_like_headers() -> None:
    patch = "\n".join(
        [
            "diff --git a/x.txt b/x.txt",
            "--- a/x.txt",
            "+++ b/x.txt",
            "@@ -1 +1 @@",
            "--- gammal rad",
            "+++ ny rad",
        ]
    )

    assert parse_patch(patch) == {"x.txt": (["++ ny rad"], ["-- gammal rad"])}


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
    monkeypatch.chdir(tmp_path)

    changes, top_dirs = collect_changes("main")

    by_path = {c.path: c for c in changes}
    assert top_dirs == {"core"}
    assert (by_path["core/a.py"].added, by_path["core/a.py"].deleted) == (2, 1)
    assert by_path["core/a.py"].added_lines == ["a = 2", "b = 3"]
    assert by_path["ny/b.py"].status == "A"
    assert classify(changes, top_dirs).reasons == ["ny modul: ny/b.py"]

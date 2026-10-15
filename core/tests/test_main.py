"""Kommandoraden: start, export och import."""

import io
from pathlib import Path

import pytest

from homecontrol import store
from homecontrol.__main__ import load_or_seed_home, main
from homecontrol.config import Settings
from homecontrol.homefile import dumps, example_home, loads
from homes import demo_home


def test_export_needs_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOMECONTROL_DATABASE", raising=False)

    with pytest.raises(SystemExit, match="HOMECONTROL_DATABASE"):
        main(["exportera"])


def test_import_needs_a_file_or_the_example(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["importera"])

    assert "--exempelhem" in capsys.readouterr().err


def test_first_start_saves_the_example_home(database: str) -> None:
    settings = Settings(host="127.0.0.1", port=8000, web_dir=Path("web"), database=database)

    assert load_or_seed_home(settings) == example_home()
    with store.connect(database, attempts=1) as conn:
        assert store.load_home(conn) == example_home()


def test_start_uses_the_saved_home(database: str) -> None:
    with store.connect(database, attempts=1) as conn:
        store.migrate(conn)
        store.save_home(conn, demo_home())
    settings = Settings(host="127.0.0.1", port=8000, web_dir=Path("web"), database=database)

    assert load_or_seed_home(settings) == demo_home()


def test_import_from_stdin_and_export_to_stdout(
    database: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOMECONTROL_DATABASE", database)
    monkeypatch.setattr("sys.stdin", io.StringIO(dumps(demo_home())))

    main(["importera", "-"])
    main(["exportera"])

    assert loads(capsys.readouterr().out) == demo_home()

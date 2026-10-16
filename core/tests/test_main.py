"""Kommandoraden: start, export och import."""

import io
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from homecontrol import store
from homecontrol.__main__ import load_or_seed_home, main
from homecontrol.config import Settings
from homecontrol.homefile import dumps, example_home, loads, to_document
from homes import one_room_home


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


def test_start_stops_with_an_explanation_if_the_saved_home_is_invalid(database: str) -> None:
    document = to_document(one_room_home())
    # En zon som sticker ut genom väggen, som en äldre version kunde spara.
    document["home"]["floors"][0]["rooms"][0]["zones"] = [
        {"id": "z", "name": "Z", "kind": "vanlig", "polygon": [[4, 1], [6, 1], [6, 2], [4, 2]]}
    ]
    with store.connect(database, attempts=1) as conn:
        store.migrate(conn)
        conn.execute("INSERT INTO home (document) VALUES (%s)", (Jsonb(document),))
    settings = Settings(host="127.0.0.1", port=8000, web_dir=Path("web"), database=database)

    with pytest.raises(SystemExit, match=r"(?s)går utanför rummet.*homecontrol importera"):
        load_or_seed_home(settings)


def test_start_uses_the_saved_home(database: str) -> None:
    with store.connect(database, attempts=1) as conn:
        store.migrate(conn)
        store.save_home(conn, one_room_home())
    settings = Settings(host="127.0.0.1", port=8000, web_dir=Path("web"), database=database)

    assert load_or_seed_home(settings) == one_room_home()


def test_import_from_stdin_and_export_to_stdout(
    database: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOMECONTROL_DATABASE", database)
    monkeypatch.setattr("sys.stdin", io.StringIO(dumps(one_room_home())))

    main(["importera", "-"])
    main(["exportera"])

    assert loads(capsys.readouterr().out) == one_room_home()

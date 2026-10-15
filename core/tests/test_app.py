from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from homecontrol import __version__
from homecontrol.app import create_app


def test_health_reports_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_serves_web_ui_from_web_dir(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>Homecontrol</title>")
    client = TestClient(create_app(tmp_path))

    response = client.get("/")

    assert response.status_code == 200
    assert "<title>Homecontrol</title>" in response.text


def test_health_wins_over_web_ui(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path / "health").write_text("fel")
    client = TestClient(create_app(tmp_path))

    assert client.get("/health").json()["status"] == "ok"


def test_missing_web_dir_warns_and_root_is_not_found(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = TestClient(create_app(tmp_path / "saknas"))

    assert client.get("/").status_code == 404
    assert "Hittar inte webbgränssnittet" in caplog.text

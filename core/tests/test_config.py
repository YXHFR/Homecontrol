from pathlib import Path

import pytest

from homecontrol.config import Settings


def test_defaults_listen_on_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HOMECONTROL_HOST",
        "HOMECONTROL_PORT",
        "HOMECONTROL_WEB_DIR",
        "HOMECONTROL_ALLOWED_HOSTS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings == Settings(host="127.0.0.1", port=8000, web_dir=Path("web"))


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMECONTROL_HOST", "0.0.0.0")
    monkeypatch.setenv("HOMECONTROL_PORT", "9000")
    monkeypatch.setenv("HOMECONTROL_WEB_DIR", "/srv/web")

    settings = Settings.from_env()

    assert settings == Settings(host="0.0.0.0", port=9000, web_dir=Path("/srv/web"))


def test_reads_allowed_hosts_as_lowercase_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMECONTROL_ALLOWED_HOSTS", " Hemma.local, ,homecontrol.lan ")

    settings = Settings.from_env()

    assert settings.allowed_hosts == {"hemma.local", "homecontrol.lan"}

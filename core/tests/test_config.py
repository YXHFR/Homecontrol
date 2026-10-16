from pathlib import Path

import pytest

from homecontrol.config import Settings
from homecontrol.zigbee import MqttSettings


def test_defaults_listen_on_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HOMECONTROL_HOST",
        "HOMECONTROL_PORT",
        "HOMECONTROL_WEB_DIR",
        "HOMECONTROL_ALLOWED_HOSTS",
        "HOMECONTROL_DATABASE",
        "HOMECONTROL_MQTT_HOST",
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


def test_reads_database_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMECONTROL_DATABASE", " host=db dbname=homecontrol ")

    assert Settings.from_env().database == "host=db dbname=homecontrol"


def test_empty_database_setting_means_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMECONTROL_DATABASE", "")

    assert Settings.from_env().database is None


def test_reads_the_mqtt_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMECONTROL_MQTT_HOST", " mosquitto ")
    monkeypatch.setenv("HOMECONTROL_MQTT_PORT", "1884")
    monkeypatch.setenv("HOMECONTROL_MQTT_USER", "homecontrol")
    monkeypatch.setenv("HOMECONTROL_MQTT_PASSWORD", "hemligt")

    assert Settings.from_env().mqtt == MqttSettings("mosquitto", 1884, "homecontrol", "hemligt")


def test_without_mqtt_host_no_real_lamps_are_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMECONTROL_MQTT_HOST", "")
    monkeypatch.setenv("HOMECONTROL_MQTT_PASSWORD", "hemligt")

    assert Settings.from_env().mqtt is None

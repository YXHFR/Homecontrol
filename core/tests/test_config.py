from datetime import timedelta
from pathlib import Path

import pytest

from homecontrol.config import MqttSettings, Settings


def test_defaults_listen_on_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HOMECONTROL_HOST",
        "HOMECONTROL_PORT",
        "HOMECONTROL_WEB_DIR",
        "HOMECONTROL_ALLOWED_HOSTS",
        "HOMECONTROL_DATABASE",
        "HOMECONTROL_HISTORY_FULL_HOURS",
        "HOMECONTROL_HISTORY_KEEP_DAYS",
        "HOMECONTROL_ESPHOME_SECRET",
        "HOMECONTROL_MQTT_HOST",
        "HOMECONTROL_HOME_COPY",
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


def test_reads_the_path_of_the_home_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMECONTROL_HOME_COPY", " /var/lib/homecontrol/hem.json ")

    assert Settings.from_env().home_copy == Path("/var/lib/homecontrol/hem.json")


def test_without_a_home_copy_path_there_is_no_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMECONTROL_HOME_COPY", "")

    assert Settings.from_env().home_copy is None


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


def test_core_prepares_the_database_itself_unless_told_not_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HOMECONTROL_PREPARE_DATABASE", raising=False)
    assert Settings.from_env().prepare_database is True

    monkeypatch.setenv("HOMECONTROL_PREPARE_DATABASE", "false")
    assert Settings.from_env().prepare_database is False

    monkeypatch.setenv("HOMECONTROL_PREPARE_DATABASE", "kanske")
    with pytest.raises(ValueError, match="HOMECONTROL_PREPARE_DATABASE"):
        Settings.from_env()


def test_full_resolution_history_lasts_one_day_unless_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HOMECONTROL_HISTORY_FULL_HOURS", raising=False)
    assert Settings.from_env().history_full_resolution == timedelta(days=1)

    monkeypatch.setenv("HOMECONTROL_HISTORY_FULL_HOURS", "48")

    assert Settings.from_env().history_full_resolution == timedelta(days=2)


def test_history_is_kept_without_time_limit_unless_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HOMECONTROL_HISTORY_KEEP_DAYS", raising=False)
    assert Settings.from_env().history_keep is None

    monkeypatch.setenv("HOMECONTROL_HISTORY_KEEP_DAYS", "90")

    assert Settings.from_env().history_keep == timedelta(days=90)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (
            "HOMECONTROL_HISTORY_FULL_HOURS",
            "ett dygn",
            "HISTORY_FULL_HOURS i .env ska vara ett tal",
        ),
        ("HOMECONTROL_HISTORY_FULL_HOURS", "nan", "HISTORY_FULL_HOURS i .env ska vara ett tal"),
        ("HOMECONTROL_HISTORY_FULL_HOURS", "inf", "HISTORY_FULL_HOURS i .env ska vara ett tal"),
        ("HOMECONTROL_HISTORY_FULL_HOURS", "0.5", "HISTORY_FULL_HOURS i .env ska vara minst 1"),
        ("HOMECONTROL_HISTORY_KEEP_DAYS", "0", "HISTORY_KEEP_DAYS i .env ska vara minst 1"),
        (
            "HOMECONTROL_HISTORY_KEEP_DAYS",
            "1",
            r"HISTORY_KEEP_DAYS i \.env \(1 dagar\) ska vara längre",
        ),
    ],
)
def test_bad_history_settings_say_which_setting_is_wrong(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str
) -> None:
    monkeypatch.delenv("HOMECONTROL_HISTORY_FULL_HOURS", raising=False)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        Settings.from_env()


def test_reads_esphome_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMECONTROL_ESPHOME_SECRET", " inte-en-riktig-nyckel ")

    assert Settings.from_env().esphome_secret == "inte-en-riktig-nyckel"  # noqa: S105


def test_empty_esphome_secret_means_no_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMECONTROL_ESPHOME_SECRET", "")

    assert Settings.from_env().esphome_secret is None


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

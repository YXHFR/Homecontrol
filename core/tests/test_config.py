import base64
from datetime import timedelta
from pathlib import Path

import pytest

from homecontrol.config import DoorbellSettings, MqttSettings, Settings


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
        "HOMECONTROL_DOORBELL_NTFY_TOPIC",
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


# --- Dörrklockan ---

DOORBELL_KEY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
"""32 påhittade byte i base64, som en nyckel från openssl rand -base64 32."""


def clear_doorbell(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "NTFY_TOPIC",
        "NTFY_URL",
        "NTFY_TOKEN",
        "CAMERA_HOST",
        "CAMERA_KEY",
        "SPEAKER_HOST",
        "SPEAKER_VOLUME",
        "MEDIA_HOST",
        "SOUND_FILE",
        "LAMP",
    ):
        monkeypatch.delenv(f"HOMECONTROL_DOORBELL_{name}", raising=False)


def test_the_doorbell_is_off_until_it_has_a_ntfy_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_doorbell(monkeypatch)
    # Andra inställningar för dörrklockan räcker inte för att slå på den.
    monkeypatch.setenv("HOMECONTROL_DOORBELL_SPEAKER_HOST", "192.0.2.20")

    assert Settings.from_env().doorbell is None


def test_reads_the_doorbell(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_doorbell(monkeypatch)
    monkeypatch.setenv("HOMECONTROL_DOORBELL_NTFY_TOPIC", "dorr_abc-123")
    monkeypatch.setenv("HOMECONTROL_DOORBELL_NTFY_URL", "https://ntfy.example/")
    monkeypatch.setenv("HOMECONTROL_DOORBELL_NTFY_TOKEN", "tk_hemlig")
    monkeypatch.setenv("HOMECONTROL_DOORBELL_CAMERA_HOST", "192.0.2.30")
    monkeypatch.setenv("HOMECONTROL_DOORBELL_CAMERA_KEY", DOORBELL_KEY)
    monkeypatch.setenv("HOMECONTROL_DOORBELL_SPEAKER_HOST", "192.0.2.20")
    monkeypatch.setenv("HOMECONTROL_DOORBELL_SPEAKER_VOLUME", "35")
    monkeypatch.setenv("HOMECONTROL_DOORBELL_LAMP", "KAJPLATS hall")

    doorbell = Settings.from_env().doorbell

    assert doorbell == DoorbellSettings(
        ntfy_topic="dorr_abc-123",
        ntfy_url="https://ntfy.example",
        ntfy_token="tk_hemlig",  # noqa: S106 (påhittad)
        camera_host="192.0.2.30",
        camera_key=b"0123456789abcdef0123456789abcdef",
        speaker_host="192.0.2.20",
        speaker_volume=35,
        lamp="KAJPLATS hall",
    )


def test_the_doorbell_settings_are_never_printed() -> None:
    doorbell = DoorbellSettings(
        ntfy_topic="hemligt_amne",
        ntfy_token="tk_hemlig",  # noqa: S106 (påhittad)
        camera_host="192.0.2.30",
        camera_key=b"k" * 32,
        lamp="KAJPLATS hall",
    )

    shown = repr(doorbell)

    for secret in ("hemligt_amne", "tk_hemlig", "192.0.2.30", "kkkk", "KAJPLATS"):
        assert secret not in shown


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("NTFY_TOPIC", "mitt ämne", "DOORBELL_NTFY_TOPIC"),
        ("NTFY_URL", "ftp://ntfy.example", "DOORBELL_NTFY_URL"),
        ("CAMERA_KEY", "inte-base64", "DOORBELL_CAMERA_KEY"),
        ("CAMERA_KEY", "c2hvcnQ=", "DOORBELL_CAMERA_KEY"),
        ("CAMERA_HOST", "dorrklockan.local", "tillsammans"),
        ("SPEAKER_HOST", "google-home", "DOORBELL_SPEAKER_HOST"),
        ("SPEAKER_VOLUME", "högt", "DOORBELL_SPEAKER_VOLUME"),
        ("SPEAKER_VOLUME", "150", "DOORBELL_SPEAKER_VOLUME"),
        ("LAMP", "lampor/#", "DOORBELL_LAMP"),
    ],
)
def test_a_wrong_doorbell_setting_says_which(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str
) -> None:
    clear_doorbell(monkeypatch)
    monkeypatch.setenv("HOMECONTROL_DOORBELL_NTFY_TOPIC", "dorr")
    monkeypatch.setenv(f"HOMECONTROL_DOORBELL_{name}", value)

    with pytest.raises(ValueError, match=message):
        Settings.from_env()


def test_the_camera_needs_both_address_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_doorbell(monkeypatch)
    monkeypatch.setenv("HOMECONTROL_DOORBELL_NTFY_TOPIC", "dorr")
    monkeypatch.setenv("HOMECONTROL_DOORBELL_CAMERA_KEY", DOORBELL_KEY)

    with pytest.raises(ValueError, match="tillsammans"):
        Settings.from_env()

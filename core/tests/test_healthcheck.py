import pytest

from homecontrol.healthcheck import health_url


@pytest.mark.parametrize(
    ("host", "url"),
    [
        ("0.0.0.0", "http://127.0.0.1:8000/health"),
        ("::", "http://127.0.0.1:8000/health"),
        ("127.0.0.1", "http://127.0.0.1:8000/health"),
        ("192.168.1.20", "http://192.168.1.20:8000/health"),
        ("fd00::1", "http://[fd00::1]:8000/health"),
    ],
)
def test_asks_the_address_the_core_listens_on(host: str, url: str) -> None:
    assert health_url(host, "8000") == url

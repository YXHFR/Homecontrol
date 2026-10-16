"""Dockers hälsokontroll: `python -m homecontrol.healthcheck` frågar kärnan om den är igång.

Kärnan lyssnar på HOMECONTROL_HOST och HOMECONTROL_PORT. Lyssnar den på alla
adresser frågar kontrollen på datorns egen adress.
"""

import os
import sys
import urllib.request


def health_url(host: str, port: str) -> str:
    if host in ("", "0.0.0.0", "::"):  # noqa: S104 – kärnan lyssnar på alla adresser
        host = "127.0.0.1"
    elif ":" in host:
        host = f"[{host}]"  # en IPv6-adress
    return f"http://{host}:{port}/health"


def main() -> None:
    url = health_url(
        os.environ.get("HOMECONTROL_HOST", "127.0.0.1"), os.environ.get("HOMECONTROL_PORT", "8000")
    )
    try:
        urllib.request.urlopen(url, timeout=2)  # noqa: S310 – adressen är kärnans egen
    except OSError as error:
        sys.exit(f"Kärnan svarar inte på {url}: {error}")


if __name__ == "__main__":
    main()

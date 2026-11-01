#!/usr/bin/env python3
"""Startar webbinstalleraren på den här datorn: http://localhost:8765

Kör det från mappen du packade upp (starta.bat och starta.sh gör det åt dig):
    python3 starta.py

Sidan serveras bara till den här datorn (127.0.0.1), inte till hemnätet.
Webbläsaren räknar localhost som säker, så Chrome och Edge får prata med
kortet över USB (Web Serial) utan https. Allt som sidan behöver finns i
mappen, så det fungerar utan internet.

Stäng fönstret (eller tryck Ctrl+C) när du är klar.

Bara Pythons standardbibliotek används.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import ClassVar

HOST = "127.0.0.1"
# Inte 8000: där svarar Homecontrol själv.
PORT = 8765
FOLDER = Path(__file__).resolve().parent


class Handler(http.server.SimpleHTTPRequestHandler):
    # Python gissar filtypen från Windows register, där .js ibland står som
    # text/plain. Då vägrar webbläsaren köra ESP Web Tools. Därför står de här.
    extensions_map: ClassVar[dict[str, str]] = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json",
        ".bin": "application/octet-stream",
        ".txt": "text/plain; charset=utf-8",
    }

    def end_headers(self) -> None:
        # Alltid den nya firmware när en ny version har packats upp i mappen.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


def open_browser(url: str) -> None:
    """Edge finns på alla Windows-datorer och kan prata med USB. Annars standardwebbläsaren."""
    if sys.platform == "win32":
        try:
            # Bara vår egen adress på localhost, ingen indata från någon annan.
            os.startfile("microsoft-edge:" + url)  # noqa: S606
            return
        except OSError:
            pass
    webbrowser.open(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Startar webbinstalleraren på den här datorn.")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true", help="öppna inte webbläsaren")
    args = parser.parse_args(argv)

    handler = functools.partial(Handler, directory=str(FOLDER))
    try:
        server = http.server.ThreadingHTTPServer((HOST, args.port), handler)
    except OSError:
        print(
            f"Port {args.port} används redan. Kanske är installeraren redan igång i ett annat\n"
            f"fönster: öppna då http://localhost:{args.port} i Chrome eller Edge.\n"
            "Annars: stäng det andra fönstret och försök igen."
        )
        return 1

    url = f"http://localhost:{args.port}/"
    print(
        f"Webbinstalleraren är igång på {url}\n"
        "Öppna adressen i Chrome eller Edge (inte Firefox eller Safari).\n"
        "Låt fönstret vara öppet medan du installerar. Stäng det när du är klar.",
        flush=True,
    )
    if not args.no_browser:
        threading.Timer(0.5, open_browser, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

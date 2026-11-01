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
import socket
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

    def send_head(self):
        # Bara sidor som öppnats som localhost får svar. Annars kunde en
        # främmande webbsida läsa mappen genom att låta sitt eget namn peka
        # på 127.0.0.1 (DNS-rebinding).
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        if host not in (f"localhost:{port}", f"127.0.0.1:{port}"):
            self.send_error(403, "Öppna sidan som http://localhost")
            return None
        return super().send_head()

    def list_directory(self, path):
        # Visa inte vad som mer finns i mappen.
        self.send_error(404)
        return None

    def end_headers(self) -> None:
        # Alltid den nya firmware när en ny version har packats upp i mappen.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


class Server(http.server.ThreadingHTTPServer):
    # Ingen annan får lyssna på samma port. På Windows kan annars två
    # installerare (till exempel en gammal och en ny version) svara på samma
    # adress, och kortet kan få den gamla firmware.
    allow_reuse_address = False

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


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
        server = Server((HOST, args.port), handler)
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

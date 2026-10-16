"""Vem som får prata med kärnans API: bara sidor från kärnan själv, på en känd adress.

Kärnan har inga konton; alla i hemnätet har samma åtkomst. Kontrollerna
hindrar i stället främmande webbsidor, som någon i hemmet råkar besöka, från
att läsa hemmet eller ändra det genom webbläsaren.
"""

import ipaddress
from collections.abc import Mapping, Set
from urllib.parse import urlsplit


def trusted(headers: Mapping[str, str], allowed_hosts: Set[str]) -> bool:
    """Om anropet kommer från kärnans egna sidor (eller från ett skript utan webbläsare).

    `allowed_hosts` är värdnamn utöver localhost och IP-adresser som kärnan får nås på.
    """
    host = headers.get("host")
    if host is None or not _known_host(host, allowed_hosts):
        # Skyddar mot DNS rebinding: en främmande sida som pekar sitt eget
        # namn på kärnans adress har ett namn som inte står i listan.
        return False
    origin = headers.get("origin")
    # Webbläsare skickar Origin med WebSocket och med allt som ändrar något;
    # saknas den är det t.ex. ett skript eller en vanlig bild på sidan.
    return origin is None or urlsplit(origin).netloc.lower() == host.lower()


def _known_host(host: str, allowed_hosts: Set[str]) -> bool:
    hostname = urlsplit(f"//{host}").hostname
    if hostname is None:
        return False
    if hostname == "localhost" or hostname in allowed_hosts:
        return True
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True

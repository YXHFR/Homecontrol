"""Hemmodellen som läsbar fil (JSON), för export, import och exempelhemmet.

Filen ser ut så här, med hela hemmet under "home":

    {
      "format": "homecontrol-hem",
      "version": 1,
      "home": {"name": "...", "floors": [...]}
    }

Punkter skrivs som [x, y] i meter, med origo uppe till vänster. Allt
kontrolleras när filen läses, så en trasig fil ger ett begripligt fel i
stället för ett halvt hem.
"""

import json
import re
from importlib import resources

from pydantic import ValidationError

from homecontrol.home import Home

FORMAT = "homecontrol-hem"
VERSION = 1

# En punkt ([x, y]) på en egen rad blir lättare att läsa än två tal på var sin rad.
_POINT = re.compile(r"\[\s*(-?[\d.eE+-]+),\s*(-?[\d.eE+-]+)\s*\]")


class HomeFileError(ValueError):
    """Filen gick inte att läsa som ett hem. Meddelandet säger varför."""


def dumps(home: Home) -> str:
    document = {
        "format": FORMAT,
        "version": VERSION,
        "home": home.model_dump(mode="json"),
    }
    text = json.dumps(document, indent=2, ensure_ascii=False)
    return _POINT.sub(r"[\1, \2]", text) + "\n"


def loads(text: str) -> Home:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise HomeFileError(f"Filen är inte giltig JSON: {error}") from None
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise HomeFileError(f'Filen är inte en hemfil (saknar "format": "{FORMAT}")')
    if document.get("version") != VERSION:
        raise HomeFileError(
            f"Filen har version {document.get('version')!r}, men bara version {VERSION} stöds"
        )
    unknown = set(document) - {"format", "version", "home"}
    if unknown:
        raise HomeFileError(f"Okända fält i filen: {', '.join(sorted(unknown))}")
    try:
        return Home.model_validate(document.get("home"))
    except ValidationError as error:
        raise HomeFileError(_describe(error)) from None


def example_home() -> Home:
    """Ett påhittat hem i två våningar som följer med kärnan."""
    return loads(resources.files("homecontrol").joinpath("example_home.json").read_text("utf-8"))


def _describe(error: ValidationError) -> str:
    lines = [f"Hemmet i filen är inte giltigt ({error.error_count()} fel):"]
    for detail in error.errors():
        where = ".".join(str(part) for part in detail["loc"]) or "home"
        lines.append(f"  {where}: {detail['msg']}")
    return "\n".join(lines)

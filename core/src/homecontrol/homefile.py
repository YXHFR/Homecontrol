"""Hemmodellen som läsbar fil (JSON), för export, import och exempelhemmet.

Filen ser ut så här, med hela hemmet under "home":

    {
      "format": "homecontrol-hem",
      "version": 1,
      "home": {"name": "...", "floors": [...]}
    }

Databasen sparar samma dokument (se store.py), så formatet har en enda
version. Versionen höjs när en äldre fil inte längre kan läsas som den är,
inte när ett nytt fält som får saknas läggs till (som våningens plan_image).
Punkter skrivs som [x, y] i meter, med origo uppe till vänster.
Allt kontrolleras när filen läses, så en trasig fil ger ett begripligt fel
i stället för ett halvt hem.
"""

import json
import re
from importlib import resources
from typing import Any

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from homecontrol.home import Home

FORMAT = "homecontrol-hem"
VERSION = 1

# Punkter ([x, y]) och korta listor med ord ("gips", "betong", …) blir lättare
# att läsa på en rad än med ett värde per rad.
_POINT = re.compile(r"\[\s*(-?[\d.eE+-]+),\s*(-?[\d.eE+-]+)\s*\]")
_WORDS = re.compile(r'\[\s*("[^"\\\n]*"(?:,\s*"[^"\\\n]*")*)\s*\]')

# Vad listorna i filen heter i felmeddelanden, med en sak i taget.
_ITEM_NAMES = {
    "floors": "våning",
    "rooms": "rum",
    "zones": "zon",
    "nodes": "nod",
    "lamps": "lampa",
    "signal_sources": "signalkälla",
    "polygon": "hörn",
    "walls": "vägg",
    "capabilities": "förmåga",
}


class HomeFileError(ValueError):
    """Filen gick inte att läsa som ett hem. Meddelandet säger varför."""


def to_document(home: Home) -> dict[str, Any]:
    return {"format": FORMAT, "version": VERSION, "home": home.model_dump(mode="json")}


def from_document(document: object) -> Home:
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
        raise HomeFileError(_describe(error, document.get("home"))) from None


def dumps(home: Home) -> str:
    text = json.dumps(to_document(home), indent=2, ensure_ascii=False)
    text = _POINT.sub(r"[\1, \2]", text)
    text = _WORDS.sub(lambda m: "[" + re.sub(r",\s*", ", ", m.group(1)) + "]", text)
    return text + "\n"


def loads(text: str) -> Home:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise HomeFileError(f"Filen är inte giltig JSON: {error}") from None
    return from_document(document)


def example_home() -> Home:
    """Ett påhittat hem i två våningar som följer med kärnan."""
    return loads(resources.files("homecontrol").joinpath("example_home.json").read_text("utf-8"))


def _describe(error: ValidationError, home: object) -> str:
    lines = [f"Hemmet i filen är inte giltigt ({error.error_count()} fel):"]
    for detail in error.errors():
        lines.append(f"  {_where(detail['loc'], home)}: {_what(detail)}")
    return "\n".join(lines)


def _where(location: tuple[int | str, ...], home: object) -> str:
    """Var i filen felet finns, till exempel: våning 1 ("entreplan") › rum 2 ("kok") › hörn 1."""
    parts: list[str] = []
    data = home
    previous: int | str | None = None
    for key in location:
        if isinstance(key, int) and isinstance(previous, str) and previous in _ITEM_NAMES:
            label = f"{_ITEM_NAMES[previous]} {key + 1}"
            data = data[key] if isinstance(data, list) and key < len(data) else None
            if isinstance(data, dict) and isinstance(data.get("id"), str):
                label += f' ("{data["id"]}")'
            parts.append(label)
        elif isinstance(key, int):
            parts.append({0: "x", 1: "y"}.get(key, str(key + 1)))
            data = None
        else:
            if key not in _ITEM_NAMES:
                parts.append(key)
            data = data.get(key) if isinstance(data, dict) else None
        previous = key
    return " › ".join(parts) or "home"


def _what(detail: ErrorDetails) -> str:
    """Felet på svenska, så långt det går."""
    context = detail.get("ctx", {})
    match detail["type"]:
        case "missing":
            return "saknas"
        case "extra_forbidden":
            return "är ett okänt fält"
        case "string_pattern_mismatch":
            return (
                "får bara innehålla a–z, siffror, - och _ "
                "(inte å, ä, ö, mellanslag eller stora bokstäver)"
            )
        case "enum":
            return "ska vara en av " + str(context.get("expected", "")).replace(" or ", " eller ")
        case "float_type" | "float_parsing" | "finite_number":
            return "ska vara ett tal"
        case "int_type" | "int_parsing" | "int_from_float":
            return "ska vara ett heltal"
        case "bool_type" | "bool_parsing":
            return "ska vara true eller false"
        case "string_type":
            return "ska vara text"
        case "string_too_short":
            return "får inte vara tomt"
        case "string_too_long":
            return f"får vara högst {context.get('max_length')} tecken"
        case "greater_than_equal":
            return f"ska vara minst {context.get('ge')}"
        case "greater_than":
            return f"ska vara större än {context.get('gt')}"
        case "less_than_equal":
            return f"får vara högst {context.get('le')}"
        case "less_than":
            return f"ska vara mindre än {context.get('lt')}"
        case "list_type" | "tuple_type":
            return "ska vara en lista"
        case "model_type" | "dict_type" | "model_attributes_type":
            return "ska vara ett objekt ({ … })"
        case "value_error":
            return str(detail["msg"]).removeprefix("Value error, ")
        case _:
            return str(detail["msg"])

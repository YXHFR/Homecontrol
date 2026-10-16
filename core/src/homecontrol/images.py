"""Bilder av planritningar: vilka format som tas emot och hur en sparad bild ser ut."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StoredImage:
    media_type: str
    """En av image/png, image/jpeg och image/webp."""
    data: bytes


def image_type(data: bytes) -> str | None:
    """Bildens format, känt på de första byten och inte på vad avsändaren påstår.

    Bilden visas i webbläsaren och ska inte kunna vara något annat än en bild.
    Andra format ger None.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None

#!/usr/bin/env python3
"""Skapar dörrklockans ljud, ett enkelt dingdong, från grunden (inga inspelningar).

Ljudet är två toner (E5 och C5) med övertoner som klingar av, som en
klassisk dörrklocka. Det är skapat för Homecontrol och har samma licens som
repot (Apache-2.0).

Användning:
    python3 scripts/make_dingdong.py dingdong.wav
    ffmpeg -i dingdong.wav -codec:a libmp3lame -b:a 64k -ac 1 \\
        core/src/homecontrol/sounds/dingdong.mp3

Skriptet använder bara standardbiblioteket.
"""

from __future__ import annotations

import argparse
import math
import struct
import wave

RATE = 22050
"""Bildpunkter per sekund. Räcker gott för en dörrklocka och ger en liten fil."""
TONES = ((659.25, 0.0), (523.25, 0.6))
"""Tonerna (Hz) och när de börjar (sekunder): ding, sedan dong."""
LENGTH_SECONDS = 2.2
DECAY_SECONDS = 0.45
"""Hur fort tonerna klingar av (tidskonstanten)."""
OVERTONES = ((1.0, 1.0), (2.0, 0.35), (3.0, 0.12))
"""Övertoner (gånger grundtonen) och hur starka de är."""


def samples() -> list[float]:
    """Ljudet som tal mellan -1 och 1."""
    count = round(RATE * LENGTH_SECONDS)
    out = [0.0] * count
    for frequency, start in TONES:
        first = round(start * RATE)
        for i in range(first, count):
            t = (i - first) / RATE
            # En kort insvängning (5 ms) så att tonen inte klickar.
            envelope = min(1.0, t / 0.005) * math.exp(-t / DECAY_SECONDS)
            out[i] += envelope * sum(
                strength * math.sin(2 * math.pi * frequency * multiple * t)
                for multiple, strength in OVERTONES
            )
    loudest = max(abs(value) for value in out)
    return [0.8 * value / loudest for value in out]


def write_wav(path: str) -> None:
    with wave.open(path, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(RATE)
        file.writeframes(b"".join(struct.pack("<h", round(v * 32767)) for v in samples()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wav", help="WAV-filen att skriva")
    write_wav(parser.parse_args().wav)


if __name__ == "__main__":
    main()

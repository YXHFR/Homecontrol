"""LD2461-komponentens ramtolkning, prövad på datorn med påhittade ramar.

Komponentens riktiga C++-kod (firmware/components/ld2461) byggs mot små
låtsade delar av ESPHome (ld2461_host/) och matas med byte som från radarn.
Ramarna följer formatet i komponentens typedefs.h: huvudet FF EE DD, två
byte längd, kommandot 07, X och Y för fem mål (en byte med tecken var, i
0,1 m), en kontrollsumma och slutet DD EE FF. Att radarn verkligen skickar
så är inte provat (se firmware/components/ld2461/README.md).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

HOST = Path(__file__).parent / "ld2461_host"
COMPONENT = Path(__file__).resolve().parents[2] / "firmware" / "components" / "ld2461"

Targets = list[tuple[float, float]]


@pytest.fixture(scope="module")
def program(tmp_path_factory: pytest.TempPathFactory) -> Path:
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ saknas")
    out = tmp_path_factory.mktemp("ld2461") / "ld2461_host"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Werror",
            "-Wno-unused-variable",
            "-DUSE_SENSOR",
            "-DUSE_BINARY_SENSOR",
            "-DUSE_TEXT_SENSOR",
            f"-I{HOST}",
            f"-I{COMPONENT}",
            str(HOST / "main.cpp"),
            str(COMPONENT / "ld2461.cpp"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


def frame(*targets: tuple[int, int]) -> bytes:
    """En ram med mål i decimeter. Mål som inte anges är 0, 0."""
    data = bytearray()
    for x, y in [*targets, *[(0, 0)] * (5 - len(targets))]:
        data += bytes([x & 0xFF, y & 0xFF])
    return bytes([0xFF, 0xEE, 0xDD, 0x00, 0x0B, 0x07, *data, 0x00, 0xDD, 0xEE, 0xFF])


def run(program: Path, *chunks: bytes) -> list[tuple[bool, Targets]]:
    """Ger för varje bit: om en ny ram lästes och de fem målens X och Y i mm."""
    lines = "".join(" ".join(f"{byte:02x}" for byte in chunk) + "\n" for chunk in chunks)
    result = subprocess.run([str(program)], input=lines, capture_output=True, text=True, check=True)
    readings = []
    for line in result.stdout.splitlines():
        fresh, *values = line.split()
        numbers = [float(value) for value in values]
        readings.append((fresh == "1", list(zip(numbers[::2], numbers[1::2], strict=True))))
    return readings


def test_two_people_are_read_in_mm_with_sign(program: Path) -> None:
    [(fresh, targets)] = run(program, frame((15, 20), (-5, 30)))

    assert fresh
    assert targets == [(1500, 2000), (-500, 3000), (0, 0), (0, 0), (0, 0)]


def test_five_people(program: Path) -> None:
    five = [(-20, 10), (-10, 20), (0, 30), (10, 20), (20, 10)]

    [(_, targets)] = run(program, frame(*five))

    assert targets == [(x * 100, y * 100) for x, y in five]


def test_everyone_leaving_clears_the_targets(program: Path) -> None:
    """En tom ram börjar med 00. Originalet kastade den, och sista läget låg kvar."""
    [_, (fresh, targets)] = run(program, frame((15, 20)), frame())

    assert fresh
    assert targets == [(0, 0)] * 5


def test_a_person_straight_ahead_is_read(program: Path) -> None:
    [(fresh, targets)] = run(program, frame((0, 25)))

    assert fresh
    assert targets[0] == (0, 2500)


def test_bytes_like_the_frame_markers_are_coordinates(program: Path) -> None:
    """0xDD och 0xFF i målen är -3,5 m och -0,1 m, inte början eller slutet på en ram."""
    [(_, targets)] = run(program, frame((-35, 30), (-1, 20)))

    assert targets[:2] == [(-3500, 3000), (-100, 2000)]


def test_a_frame_split_over_two_reads_and_junk_before_it(program: Path) -> None:
    whole = frame((15, 20))

    [first, second] = run(program, b"\x12\x34" + whole[:9], whole[9:])

    assert first == (False, [(0, 0)] * 5)
    assert second == (True, [(1500, 2000), (0, 0), (0, 0), (0, 0), (0, 0)])


def test_a_frame_with_a_missing_byte_is_ignored(program: Path) -> None:
    whole = frame((-5, 30))
    broken = whole[:8] + whole[9:]

    [_, (fresh, targets)] = run(program, frame((15, 20)), broken)

    assert not fresh
    assert targets[0] == (1500, 2000)


def test_a_long_stream_without_frames_does_not_break_the_next_frame(program: Path) -> None:
    [(fresh, targets)] = run(program, bytes(range(256)) * 3 + frame((15, 20)))

    assert fresh
    assert targets[0] == (1500, 2000)

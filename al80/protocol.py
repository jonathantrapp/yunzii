"""Packet builders for the AL80 screen. Pure functions, no I/O.

Wire format (from snackdriven/al80-lcd and techno1731/yunzii-al80-unlocked):

A 64-byte raw-HID report:
    [0]     opcode: 0x40 announce, 0x41 data, 0x42 finish
    [1..2]  byte offset of this chunk, little-endian
    [3]     payload length (at most 56)
    [4..5]  additive checksum of every other byte, little-endian
    [6]     0 (the keyboard echoes the report back with 0x55 here as an ACK)
    [7..]   payload, relayed unchanged to the display module

A display-module command inside the payload:
    A5 5A <type> <length hi> <length lo> <crc hi> <crc lo> <data...>
    crc = CRC-16/MODBUS over <type> <length hi> <length lo>

This module can only build screen packets. It has no way to emit the
0xB0-0xB7 firmware-update opcodes.
"""
from __future__ import annotations

import datetime as _dt
from typing import Iterable, NamedTuple

WIDTH = 96
HEIGHT = 160            # picture page, GIF page, startup animation
STRIP_HEIGHT = 64       # animated strip on the home page
FRAME_BYTES = WIDTH * HEIGHT * 2

REPORT_SIZE = 64
CHUNK = 56              # payload bytes per report
BANK = 1024             # GIF frames are sent in banks; offsets restart per bank

OP_ANNOUNCE, OP_DATA, OP_FINISH = 0x40, 0x41, 0x42
ACK_OK, ACK_BUSY = 0x55, 0x0F

PK_CONN, PK_OS, PK_CAPS, PK_NUM, PK_GUI_LOCK = 0x01, 0x02, 0x03, 0x04, 0x05
PK_BATT_LEVEL, PK_BATT_STATE = 0x06, 0x07
PK_TIME, PK_DATE = 0x09, 0x0A
PK_GO_HOME, PK_ADD_PIC, PK_NEXT_PIC, PK_DEL_PIC, PK_GO_GIF = 0x0B, 0x0C, 0x0D, 0x0E, 0x0F
PK_EVENT, PK_FRAME_LEN, PK_GIF_NUM, PK_GIF_FRAME = 0x10, 0x11, 0x12, 0x13

PICTURE_SLOTS = 16       # what the vendor app clears; the real count is unconfirmed


class GifTarget(NamedTuple):
    mode: int
    height: int
    max_frames: int


GIF_TARGETS = {
    "boot": GifTarget(0, HEIGHT, 64),         # startup animation
    "page": GifTarget(1, HEIGHT, 160),        # GIF page
    "home": GifTarget(2, STRIP_HEIGHT, 42),   # strip on the home page
}


class Step(NamedTuple):
    """One report to send, and how long the module needs before the next."""
    report: bytes
    pause: float


def crc16_modbus(data: Iterable[int]) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def report(op: int, payload: bytes | Iterable[int] = b"", offset: int = 0,
           length: int | None = None) -> bytes:
    payload = bytes(payload)
    if len(payload) > CHUNK:
        raise ValueError(f"payload of {len(payload)} bytes does not fit one report")
    if op not in (OP_ANNOUNCE, OP_DATA, OP_FINISH):
        raise ValueError(f"opcode {op:#x} is not a screen opcode")
    length = len(payload) if length is None else length
    head = bytes((op, offset & 0xFF, (offset >> 8) & 0xFF, length))
    checksum = (sum(head) + sum(payload)) & 0xFFFF
    body = head + bytes((checksum & 0xFF, checksum >> 8, 0)) + payload
    return body + bytes(REPORT_SIZE - len(body))


def command(kind: int, data: bytes | Iterable[int] = b"", declared: int | None = None) -> bytes:
    """A display-module command header, with any short data inline."""
    data = bytes(data)
    declared = len(data) if declared is None else declared
    head = bytes((kind, declared >> 8, declared & 0xFF))
    crc = crc16_modbus(head)
    return b"\xA5\x5A" + head + bytes((crc >> 8, crc & 0xFF)) + data


FINISH = report(OP_FINISH, length=CHUNK)


def data_reports(blob: bytes) -> list[bytes]:
    return [report(OP_DATA, blob[off:off + CHUNK], offset=off)
            for off in range(0, len(blob), CHUNK)]


# ---------------------------------------------------------------- streams
def simple_stream(kind: int, data: Iterable[int] = ()) -> list[Step]:
    """A command with no bulk data: a view switch, or a one-byte home-page value."""
    return [Step(report(OP_ANNOUNCE, command(kind, data)), 0.03), Step(FINISH, 0.03)]


def view_stream(page: str) -> list[Step]:
    kinds = {"home": PK_GO_HOME, "gif": PK_GO_GIF, "next_picture": PK_NEXT_PIC}
    return simple_stream(kinds[page])


def clock_stream(now: _dt.datetime, twelve_hour: bool = False, repeat: int = 3) -> list[Step]:
    """Set the home-page clock and date.

    The module draws whatever hour it is given, so sending 1-12 gives a
    12-hour clock. The vendor app sends the pair three times.
    """
    hour = (now.hour % 12 or 12) if twelve_hour else now.hour
    values = (
        (PK_TIME, (hour, now.minute, now.second)),
        (PK_DATE, (now.year % 100, now.isoweekday(), now.month, now.day)),
    )
    steps: list[Step] = []
    for kind, data in values:
        steps += [Step(report(OP_ANNOUNCE, command(kind, declared=len(data))), 0.06),
                  Step(report(OP_DATA, data), 0.06),
                  Step(FINISH, 0.06)]
    return steps * repeat


def readout_stream(a: int, b: int, c: int) -> list[Step]:
    """Put three two-digit numbers where the home page shows hours, minutes, seconds.

    Experimental: the module may refuse hours over 23 or minutes over 59. It
    also counts the last field up once a second, so this is resent often.
    """
    values = [max(0, min(99, int(v))) for v in (a, b, c)]
    return [Step(report(OP_ANNOUNCE, command(PK_TIME, declared=3)), 0.03),
            Step(report(OP_DATA, values), 0.03),
            Step(FINISH, 0.03)]


def gauge_stream(percent: int) -> list[Step]:
    """Set the home page's battery gauge to a value. One tiny packet, no image.

    Experimental: the keyboard also reports its real battery level to the
    module, so it may overwrite this.
    """
    return simple_stream(PK_BATT_LEVEL, [max(0, min(100, int(percent)))])


def picture_stream(pixels: bytes, announce_settle: float = 0.3,
                   setup_settle: float = 0.03) -> list[Step]:
    """Store a 96x160 still and show it. Both settles are needed or it never appears."""
    if len(pixels) != FRAME_BYTES:
        raise ValueError(f"a picture is {FRAME_BYTES} bytes, got {len(pixels)}")
    steps = [Step(report(OP_ANNOUNCE, command(PK_EVENT, [0x01])), announce_settle),
             Step(report(OP_DATA, command(PK_ADD_PIC, declared=len(pixels))), setup_settle)]
    steps += [Step(r, 0.0) for r in data_reports(pixels)]
    steps.append(Step(FINISH, 0.03))
    return steps


def gif_stream(frames: list[bytes], target: str, fps: int) -> list[Step]:
    """Store an animation in one of the three GIF slots."""
    mode, height, max_frames = GIF_TARGETS[target]
    if not 1 <= len(frames) <= max_frames:
        raise ValueError(f"'{target}' holds 1 to {max_frames} frames, got {len(frames)}")
    size = WIDTH * height * 2
    fps = max(1, min(60, int(fps)))
    steps = [Step(report(OP_ANNOUNCE, command(PK_GIF_NUM, [mode, 0])), 0.03),
             Step(report(OP_DATA, command(PK_GIF_FRAME, [mode, 0])), 0.03)]
    for index, frame in enumerate(frames):
        if len(frame) != size:
            raise ValueError(f"frame {index} is {len(frame)} bytes, expected {size}")
        # The module writes its flash on the first and every 16th frame.
        steps.append(Step(report(OP_DATA, command(PK_EVENT, [0x02, mode, index])),
                          3.0 if index % 16 == 0 else 0.03))
        steps.append(Step(report(OP_DATA, command(PK_FRAME_LEN, declared=size)), 0.0))
        for base in range(0, size, BANK):
            bank = data_reports(frame[base:base + BANK])
            steps += [Step(r, 0.0) for r in bank[:-1]]
            steps.append(Step(bank[-1], 0.03))
    steps += [Step(report(OP_DATA, command(PK_GIF_NUM, [mode, len(frames)])), 0.03),
              Step(report(OP_DATA, command(PK_GIF_FRAME, [mode, fps])), 0.03),
              Step(FINISH, 0.03)]
    return steps


def clear_pictures_stream(count: int = PICTURE_SLOTS) -> list[Step]:
    return simple_stream(PK_DEL_PIC) * count


def clear_gif_stream() -> list[Step]:
    return simple_stream(PK_GIF_NUM, [1]) + simple_stream(PK_GIF_FRAME, [2])


# ---------------------------------------------------------------- pixels
def rgb888_to_rgb565(raw: bytes) -> bytes:
    """Packed RGB888 bytes to RGB565, high byte first, row by row."""
    out = bytearray(len(raw) // 3 * 2)
    j = 0
    for i in range(0, len(raw), 3):
        value = ((raw[i] >> 3) << 11) | ((raw[i + 1] >> 2) << 5) | (raw[i + 2] >> 3)
        out[j] = value >> 8
        out[j + 1] = value & 0xFF
        j += 2
    return bytes(out)

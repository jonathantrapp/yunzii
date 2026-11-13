"""USB transport for the AL80 screen, plus a stand-in for running with no keyboard."""
from __future__ import annotations

import time
from typing import Callable, Iterable

from . import protocol as p

# Factory identity, and the Apple identity some custom firmware presents.
USB_IDS = ((0x28E9, 0x30AF), (0x05AC, 0x029C))
RAW_USAGE_PAGE, RAW_USAGE = 0xFF60, 0x61

ACK_TIMEOUT_MS = 500
MAX_TRIES = 4


class DeviceError(Exception):
    pass


def find_path():
    """Path of the keyboard's raw-HID interface, or None when it is not plugged in."""
    import hid
    for vid, pid in USB_IDS:
        for info in hid.enumerate(vid, pid):
            if (info.get("usage_page"), info.get("usage")) == (RAW_USAGE_PAGE, RAW_USAGE):
                return info["path"]
    return None


class Device:
    """The real keyboard. Only one program can hold this interface at a time."""

    name = "AL80 (USB)"

    def __init__(self):
        import hid
        path = find_path()
        if path is None:
            raise DeviceError("AL80 not found on USB. It must be wired, and VIA or the "
                              "YUNZII screen site must be closed.")
        self._dev = hid.device()
        try:
            self._dev.open_path(path)
        except OSError as exc:
            raise DeviceError(f"could not open the AL80: {exc}") from exc

    def close(self):
        try:
            self._dev.close()
        except OSError:
            pass

    def _drain(self):
        while self._dev.read(p.REPORT_SIZE, 1):
            pass

    def send_report(self, report: bytes):
        """Write one report and wait for the keyboard to echo it with the ACK byte.

        A lost ACK is retried: each data report carries its own offset, so
        sending it twice cannot corrupt the picture.
        """
        for _ in range(MAX_TRIES):
            try:
                self._dev.write(b"\x00" + report)   # leading report ID 0
                deadline = time.monotonic() + ACK_TIMEOUT_MS / 1000
                while time.monotonic() < deadline:
                    echo = self._dev.read(p.REPORT_SIZE, 50)
                    if not echo or len(echo) < 7 or bytes(echo[:3]) != report[:3]:
                        continue
                    if echo[6] == p.ACK_OK:
                        return
                    if echo[6] == p.ACK_BUSY:
                        time.sleep(0.005)
                        break
            except (OSError, ValueError) as exc:
                raise DeviceError(f"lost the keyboard: {exc}") from exc
        raise DeviceError("the keyboard stopped acknowledging reports")

    def send(self, steps: Iterable[p.Step], progress: Callable[[int, int], None] | None = None):
        steps = list(steps)
        self._drain()
        for count, step in enumerate(steps, 1):
            self.send_report(step.report)
            if step.pause:
                time.sleep(step.pause)
            if progress and (count % 50 == 0 or count == len(steps)):
                progress(count, len(steps))


class FakeDevice:
    """Accepts every report and keeps what a real module would have stored.

    Used by the tests and by `--dry-run`, so the app can be tried with no keyboard.
    """

    name = "dry run (no keyboard)"

    def __init__(self, realtime: bool = False):
        self.realtime = realtime
        self.reports: list[bytes] = []
        self.picture: bytes | None = None
        self.gifs: dict[int, list[bytes]] = {}
        self.clock: tuple | None = None
        self.values: dict[int, int] = {}
        self.view = "home"
        self._pending: tuple | None = None   # (kind, declared, buffer, gif mode)
        self._gif_mode = 0
        self._bank_base = 0
        self._received = 0

    def close(self):
        pass

    def send_report(self, report: bytes):
        if len(report) != p.REPORT_SIZE:
            raise DeviceError("bad report size")
        op, offset, length = report[0], report[1] | report[2] << 8, report[3]
        body = report[:4] + report[6:]
        if (sum(body) & 0xFFFF) != (report[4] | report[5] << 8):
            raise DeviceError("bad checksum")
        self.reports.append(report)
        if op == p.OP_FINISH:
            return
        payload = report[7:7 + length]
        if self._pending:
            self._data(offset, payload)
        elif payload[:2] == b"\xA5\x5A" and len(payload) >= 7:
            self._command(payload)

    def _command(self, payload: bytes):
        kind, declared = payload[2], payload[3] << 8 | payload[4]
        if p.crc16_modbus(payload[2:5]) != (payload[5] << 8 | payload[6]):
            raise DeviceError("bad command CRC")
        data = payload[7:]
        if kind in (p.PK_ADD_PIC, p.PK_FRAME_LEN) or (kind in (p.PK_TIME, p.PK_DATE) and not data):
            self._pending = (kind, declared, bytearray(declared))
            self._bank_base = 0
            self._received = 0
        elif kind == p.PK_EVENT and data[:1] == b"\x02":
            self._gif_mode = data[1]
        elif kind == p.PK_GIF_NUM and data[1:2] == b"\x00":
            self.gifs[data[0]] = []
        elif kind == p.PK_GO_HOME:
            self.view = "home"
        elif kind == p.PK_GO_GIF:
            self.view = "gif"
        elif len(data) == 1:
            self.values[kind] = data[0]

    def _data(self, offset: int, payload: bytes):
        kind, declared, buf = self._pending
        if kind == p.PK_FRAME_LEN:
            # GIF banks: offsets restart at 0 for every 1024-byte bank.
            if offset == 0 and self._received:
                self._bank_base += p.BANK
            offset += self._bank_base
        buf[offset:offset + len(payload)] = payload
        self._received += len(payload)
        if self._received < declared:
            return
        self._pending = None
        if kind == p.PK_ADD_PIC:
            self.picture, self.view = bytes(buf), "picture"
        elif kind == p.PK_FRAME_LEN:
            self.gifs.setdefault(self._gif_mode, []).append(bytes(buf))
        elif kind == p.PK_TIME:
            self.clock = tuple(buf)

    def send(self, steps: Iterable[p.Step], progress: Callable[[int, int], None] | None = None):
        steps = list(steps)
        for count, step in enumerate(steps, 1):
            self.send_report(step.report)
            if self.realtime:
                time.sleep(step.pause + 0.001)   # about 1 ms per report on real USB
            if progress and (count % 50 == 0 or count == len(steps)):
                progress(count, len(steps))


def open_device(dry_run: bool = False):
    return FakeDevice(realtime=True) if dry_run else Device()

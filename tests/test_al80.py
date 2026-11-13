"""Hardware-free tests. Byte vectors are from captures of the vendor app,
published in snackdriven/al80-lcd (AL80_KNOWLEDGE_BASE.md)."""
import datetime as dt
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from al80 import panels, protocol as p
from al80.app import Engine, Handler, clean_settings, DEFAULTS, load_gif_frames
from al80.device import DeviceError, FakeDevice


def hx(text: str) -> bytes:
    return bytes.fromhex(text)


class Packets(unittest.TestCase):
    def test_crc_vectors(self):
        self.assertEqual(p.crc16_modbus([0x09, 0, 0x03]), 0xC3E1)   # time
        self.assertEqual(p.crc16_modbus([0x10, 0, 0x01]), 0xC5B1)   # picture announce
        self.assertEqual(p.crc16_modbus([0x12, 0, 0x02]), 0x0450)   # GIF
        self.assertEqual(p.crc16_modbus([0x0A, 0, 0x04]), 0x0150)   # date
        self.assertEqual(p.crc16_modbus([0x0B, 0, 0x00]), 0x0200)   # home

    def test_time_announce(self):
        steps = p.clock_stream(dt.datetime(2026, 11, 12, 15, 4, 5), twelve_hour=True, repeat=1)
        self.assertEqual(steps[0].report[:14], hx("40 00 00 07 F6 02 00 A5 5A 09 00 03 C3 E1"))
        self.assertEqual(steps[1].report[:10], hx("41 00 00 03 50 00 00 03 04 05"))   # 3 pm -> 3
        self.assertEqual(steps[2].report[:5], hx("42 00 00 38 7A"))
        self.assertEqual(steps[4].report[7:11], bytes([26, 4, 11, 12]))   # Thursday = 4
        full_day = p.clock_stream(dt.datetime(2026, 11, 12, 15, 4, 5), repeat=1)
        self.assertEqual(full_day[1].report[7], 15)
        midnight = p.clock_stream(dt.datetime(2026, 11, 12, 0, 4, 5), twelve_hour=True, repeat=1)
        self.assertEqual(midnight[1].report[7], 12)

    def test_picture_stream(self):
        steps = p.picture_stream(bytes(p.FRAME_BYTES))
        self.assertEqual(steps[0].report[:15], hx("40 00 00 08 CF 02 00 A5 5A 10 00 01 C5 B1 01"))
        self.assertEqual(steps[1].report[:14], hx("41 00 00 07 21 03 00 A5 5A 0C 78 00 C3 93"))
        self.assertEqual((steps[0].pause, steps[1].pause), (0.3, 0.03))
        blocks = steps[2:-1]
        self.assertEqual(len(blocks), 549)                       # 548 full + one 32-byte tail
        self.assertEqual(blocks[0].report[:6], hx("41 00 00 38 79 00"))
        self.assertEqual(blocks[1].report[:6], hx("41 38 00 38 B1 00"))
        self.assertEqual(blocks[-1].report[1:4], hx("E0 77 20"))  # offset 0x77E0, 32 bytes
        self.assertEqual(steps[-1].report, p.FINISH)
        self.assertTrue(all(len(s.report) == 64 for s in steps))

    def test_home_view(self):
        self.assertEqual(p.view_stream("home")[0].report[:14],
                         hx("40 00 00 07 53 01 00 A5 5A 0B 00 00 02 00"))

    def test_gif_stream(self):
        frame = bytes(p.WIDTH * p.STRIP_HEIGHT * 2)
        steps = p.gif_stream([frame, frame], "home", 20)
        self.assertEqual(steps[0].report[7:16], hx("A5 5A 12 00 02 04 50 02 00"))
        headers = [s for s in steps if s.report[7:10] == hx("A5 5A 10")]
        self.assertEqual(headers[0].report[3:5], hx("0A 95"))    # captured checksums
        self.assertEqual(headers[1].report[3:5], hx("0A 96"))
        self.assertEqual(headers[0].report[12:17], hx("04 30 02 02 00"))
        self.assertEqual((headers[0].pause, headers[1].pause), (3.0, 0.03))
        data = [s for s in steps if s.report[0] == p.OP_DATA and s.report[7:9] != hx("A5 5A")]
        self.assertEqual(len(data), 2 * 228)                     # 12 banks of 19 blocks per frame
        self.assertEqual(data[18].report[1:4], hx("F0 03 10"))   # 16-byte bank tail at 0x3F0
        self.assertEqual(data[19].report[1:3], hx("00 00"))      # offset restarts per bank
        self.assertEqual(steps[-3].report[14:16], bytes([2, 2]))   # slot, frame count
        self.assertEqual(steps[-2].report[14:16], bytes([2, 20]))  # slot, fps

    def test_limits(self):
        with self.assertRaises(ValueError):
            p.picture_stream(bytes(10))
        with self.assertRaises(ValueError):
            p.gif_stream([bytes(p.WIDTH * p.STRIP_HEIGHT * 2)] * 43, "home", 10)
        with self.assertRaises(ValueError):
            p.report(0xB1)          # firmware-update opcodes cannot be built

    def test_rgb565(self):
        self.assertEqual(p.rgb888_to_rgb565(bytes([255, 0, 0, 0, 255, 0, 0, 0, 255])),
                         hx("F8 00 07 E0 00 1F"))


class RoundTrip(unittest.TestCase):
    def test_picture_arrives_intact(self):
        pixels = bytes((i * 7) % 256 for i in range(p.FRAME_BYTES))
        device = FakeDevice()
        device.send(p.picture_stream(pixels))
        self.assertEqual(device.picture, pixels)
        self.assertEqual(device.view, "picture")

    def test_gif_arrives_intact(self):
        size = p.WIDTH * p.STRIP_HEIGHT * 2
        frames = [bytes((i * k) % 256 for i in range(size)) for k in (3, 5, 11)]
        device = FakeDevice()
        device.send(p.gif_stream(frames, "home", 12))
        self.assertEqual(device.gifs[2], frames)

    def test_clock_gauge_and_checksum(self):
        device = FakeDevice()
        device.send(p.clock_stream(dt.datetime(2026, 1, 2, 23, 59, 58)))
        self.assertEqual(device.clock, (23, 59, 58))
        device.send(p.gauge_stream(250))
        self.assertEqual(device.values[p.PK_BATT_LEVEL], 100)
        broken = bytearray(p.FINISH)
        broken[4] ^= 1
        with self.assertRaises(DeviceError):
            device.send_report(bytes(broken))


class Rendering(unittest.TestCase):
    CTX = {"now": dt.datetime(2026, 11, 12, 22, 42), "history": {"cpu": [10, 50, 30], "gpu": [5, 9]},
           "sensors": {"cpu": 37, "gpu": 81, "ram": 58, "cpu_temp": 62, "fps": 144},
           "settings": dict(DEFAULTS), "weather": None}

    def test_panels_are_panel_sized_and_stable(self):
        for name, draw in panels.PANELS.items():
            first, second = draw(self.CTX), draw(self.CTX)
            self.assertEqual(first.size, (96, 160), name)
            self.assertEqual(first.tobytes(), second.tobytes(), name)   # same input, no upload
        weather = dict(self.CTX, weather={"temp": 48, "text": "Partly cloudy", "high": 52, "low": 39})
        self.assertEqual(panels.clock(weather).size, (96, 160))
        self.assertEqual(panels.test_card(7, 20).size, (96, 160))
        self.assertNotEqual(panels.test_card(7, 20).tobytes(), panels.test_card(8, 20).tobytes())
        for level in ("info", "ok", "warn", "error"):
            card = panels.alert({"title": "x" * 60, "lines": ["y" * 60], "level": level, "at": 0}, 600)
            self.assertEqual(card.size, (96, 160))

    def test_gif_loading_thins_long_animations(self):
        frames = [Image.new("RGB", (40, 40), (i, 0, 0)) for i in range(60)]
        buf = io.BytesIO()
        frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=50)
        out, fps = load_gif_frames(buf.getvalue(), "home", "cover", None)
        self.assertEqual(len(out), 42)
        self.assertEqual(len(out[0]), 96 * 64 * 2)
        self.assertTrue(1 <= fps <= 60)

    def test_settings_are_validated(self):
        merged = clean_settings(DEFAULTS, {"mode": "nonsense", "dwell": 0, "min_interval": -5,
                                           "rotation": ["clock", "bogus"], "unknown": 1,
                                           "weather_lat": "42.7", "gauge": "cpu"})
        self.assertEqual(merged["mode"], DEFAULTS["mode"])
        self.assertEqual((merged["dwell"], merged["min_interval"]), (3, 0.0))
        self.assertEqual(merged["rotation"], ["clock"])
        self.assertNotIn("unknown", merged)
        self.assertEqual((merged["weather_lat"], merged["gauge"]), (42.7, "cpu"))


class EngineAndServer(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.engine = Engine(dry_run=True, state_dir=Path(self.tmp.name))
        self.engine.device = FakeDevice()           # instant, unlike the realtime dry-run device
        self.engine.settings.update(mode="badge", clock_sync=False, min_interval=0.0)
        handler = type("H", (Handler,), {"engine": self.engine})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, path, body=None, headers=None, host=None):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data, headers or {})
        if host:
            request.add_header("Host", host)
        with urllib.request.urlopen(request, timeout=5) as r:
            raw = r.read()
            return json.loads(raw) if r.headers["Content-Type"] == "application/json" else raw

    def test_uploads_only_when_the_frame_changes(self):
        for _ in range(3):
            self.engine._tick()
        self.assertEqual(self.engine.session_uploads, 1)
        shown = Image.frombytes("RGB", (96, 160), b"".join(
            bytes(((v >> 11) << 3, ((v >> 5) & 63) << 2, (v & 31) << 3))
            for v in (int.from_bytes(self.engine.device.picture[i:i + 2], "big")
                      for i in range(0, p.FRAME_BYTES, 2))))
        self.assertEqual(shown.size, (96, 160))
        self.engine.update_settings({"badge_big": "JT"})
        self.engine._tick()
        self.assertEqual(self.engine.session_uploads, 2)
        self.assertEqual(json.loads((Path(self.tmp.name) / "state.json").read_text())
                         ["settings"]["badge_big"], "JT")

    def test_minimum_interval_holds_frames_back(self):
        self.engine.settings["min_interval"] = 3600
        self.engine._tick()
        self.engine.update_settings({"badge_big": "ZZ"})
        self.engine._tick()
        self.assertEqual(self.engine.session_uploads, 1)

    def test_alert_takes_over_then_expires(self):
        self.call("/api/alert", {"title": "Synth done", "level": "ok", "seconds": 30})
        self.engine._tick()
        self.assertEqual(self.engine.current_panel, "alert")
        self.engine.alert["until"] = time.time() - 1
        self.engine._tick()
        self.assertEqual(self.engine.current_panel, "badge")

    def test_gauge_and_pushed_sensors(self):
        self.assertEqual(self.call("/api/sensors", {"cpu": 73, "gpu_temp": 66, "junk": "x"})["taken"],
                         ["cpu", "gpu_temp"])
        self.engine.update_settings({"mode": "home", "gauge": "cpu"})
        self.engine._tick()
        self.assertEqual(self.engine.device.values[p.PK_BATT_LEVEL], 73)
        self.assertEqual(self.call("/api/state")["sensors"]["gpu_temp"], 66)

    def test_api_preview_actions_and_upload(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.call("/")                                   # no web page any more
        self.engine._tick()
        self.assertEqual(Image.open(io.BytesIO(self.call("/api/preview.png"))).size, (96, 160))
        self.call("/api/action", {"action": "view_home"})
        self.engine._tick()
        self.assertEqual((self.engine.device.view, self.engine.settings["mode"]), ("home", "off"))
        buf = io.BytesIO()
        Image.new("RGB", (300, 200), (0, 200, 0)).save(buf, "PNG")
        self.call("/api/upload?target=picture", buf.getvalue())
        self.engine._tick()
        self.assertEqual(self.engine.settings["mode"], "image")
        self.assertEqual(self.engine.device.picture[:2], bytes.fromhex("0640"))

    def test_live_home_page_never_uploads_a_picture(self):
        self.engine.update_settings({"mode": "home", "gauge": "gpu", "home_readout": True})
        self.engine.sensors.push({"cpu": 37, "gpu": 81, "gpu_temp": 62})
        for _ in range(4):
            self.engine._tick()
        device = self.engine.device
        self.assertEqual((device.view, device.picture, self.engine.session_uploads), ("home", None, 0))
        self.assertEqual(device.values[p.PK_BATT_LEVEL], 81)
        self.assertEqual(device.clock, (37, 81, 62))
        self.assertFalse(any(r[7:10] == bytes.fromhex("A55A0C") for r in device.reports))
        # Turning the readout off hands the clock back to the real time.
        self.engine.update_settings({"home_readout": False, "clock_sync": True, "twelve_hour": False})
        self.engine._tick()
        self.assertEqual(device.clock[:2], (dt.datetime.now().hour, dt.datetime.now().minute))
        # An alert is the one thing that uploads; afterwards the home page comes back.
        self.engine.set_alert({"title": "done", "seconds": 30})
        self.engine._tick()
        self.assertEqual((device.view, self.engine.session_uploads), ("picture", 1))
        self.engine.clear_alert()
        self.engine._tick()
        self.assertEqual(device.view, "home")

    def test_storage_test_and_its_answers(self):
        self.engine.start_storage_test(count=5)
        self.engine._tick()
        self.assertEqual((self.engine.storage_test, self.engine.session_uploads), ("waiting", 5))
        self.assertEqual(self.engine.settings["mode"], "off")
        self.engine._tick()
        self.assertEqual(self.engine.session_uploads, 5)        # nothing more while waiting
        self.assertEqual(Engine(dry_run=True, state_dir=Path(self.tmp.name)).storage_test, "waiting")
        self.engine.record_storage_result("first")
        self.assertEqual((self.engine.settings["storage"], self.engine.settings["min_interval"]), ("ram", 1.0))
        self.engine.record_storage_result("last")
        self.assertEqual((self.engine.settings["storage"], self.engine.settings["min_interval"]), ("flash", 30.0))
        self.engine.record_storage_result("all")
        self.assertEqual((self.engine.settings["storage"], self.engine.storage_test), ("untested", ""))

    def test_other_sites_are_refused(self):
        for kwargs in ({"headers": {"Origin": "https://evil.example"}}, {"host": "evil.example"}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.call("/api/alert", {"title": "x"}, **kwargs)
            self.assertEqual(caught.exception.code, 403)
        self.assertIsNone(self.engine.alert)


if __name__ == "__main__":
    unittest.main()

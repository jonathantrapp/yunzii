"""The background engine and its local web control panel.

One thread owns the keyboard. It draws the current panel a few times a
second, and uploads a frame only when the pixels differ from the frame on
the screen and the minimum interval has passed. The web server only reads
state and queues work for that thread.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import queue
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageSequence

from . import panels, protocol as p
from .device import DeviceError, open_device
from .sensors import Sensors

STATE_DIR = Path.home() / ".al80"
WEB_DIR = Path(__file__).parent / "web"
DEFAULT_PORT = 7380
MAX_BODY = 20 * 1024 * 1024

DEFAULTS = {
    "mode": "rotate",              # rotate, off, or one panel id
    "rotation": ["system", "clock"],
    "dwell": 20,                   # seconds per panel when rotating
    "min_interval": 5.0,           # seconds between uploads; see docs/REFRESH.md
    "twelve_hour": True,
    "clock_sync": True,
    "gauge": "off",                # off, cpu, gpu, ram: drives the home-page battery gauge
    "footer": "",
    "badge_big": "AL",
    "badge_line1": "AL80",
    "badge_line2": "KEYBOARD",
    "weather_lat": None,           # weather is off until a location is set
    "weather_lon": None,
    "weather_unit": "F",
}
MODES = ("rotate", "off", "system", "clock", "badge", "image", "external")
WEATHER_TEXT = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog",
                48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 61: "Light rain",
                63: "Rain", 65: "Heavy rain", 66: "Icy rain", 67: "Icy rain", 71: "Light snow",
                73: "Snow", 75: "Heavy snow", 77: "Snow", 80: "Showers", 81: "Showers",
                82: "Downpour", 85: "Snow showers", 86: "Snow showers", 95: "Storm",
                96: "Storm", 99: "Storm"}


def clean_settings(current: dict, patch: dict) -> dict:
    """Merge a settings patch, keeping only known keys with sane values."""
    out = dict(current)
    for key, value in patch.items():
        if key not in DEFAULTS:
            continue
        if key == "mode":
            if value in MODES:
                out[key] = value
        elif key == "rotation":
            if isinstance(value, list):
                out[key] = [v for v in value if v in panels.PANELS or v in ("image", "external")]
        elif key == "dwell":
            out[key] = max(3, min(3600, int(value)))
        elif key == "min_interval":
            out[key] = max(0.0, min(3600.0, float(value)))
        elif key in ("twelve_hour", "clock_sync"):
            out[key] = bool(value)
        elif key == "gauge":
            if value in ("off", "cpu", "gpu", "ram"):
                out[key] = value
        elif key in ("weather_lat", "weather_lon"):
            out[key] = None if value in (None, "") else float(value)
        elif key == "weather_unit":
            out[key] = "C" if value == "C" else "F"
        else:
            out[key] = str(value)[:40]
    return out


class Engine:
    def __init__(self, dry_run: bool = False, state_dir: Path = STATE_DIR):
        self.dry_run = dry_run
        self.state_dir = state_dir
        self.lock = threading.RLock()
        self.sensors = Sensors()
        self.jobs: queue.Queue = queue.Queue()
        self.stop = threading.Event()

        self.settings = dict(DEFAULTS)
        self.lifetime_uploads = 0
        self._load()

        self.device = None
        self.device_error = "starting"
        self._next_connect = 0.0
        self.alert: dict | None = None
        self.external: Image.Image | None = None
        self.user_image: Image.Image | None = None
        self.weather: dict | None = None
        self._weather_at = 0.0
        self._weather_for = None

        self.preview_png = panels.to_png(panels.placeholder("starting"))
        self.current_panel = ""
        self.session_uploads = 0
        self.last_upload_ms = 0.0
        self.busy = ""                 # description of a long job, with progress
        self._sent: bytes | None = None
        self._sent_at = 0.0
        self._preview_pixels: bytes | None = None
        self._last_sensors: dict = {}
        self._gauge_sent = None
        self._gauge_at = 0.0
        self._clock_at = 0.0
        self._saved_at = time.monotonic()
        try:
            self.user_image = Image.open(self.state_dir / "image.png").convert("RGB")
        except OSError:
            pass

    # ------------------------------------------------------------ persistence
    def _load(self):
        try:
            data = json.loads((self.state_dir / "state.json").read_text())
        except (OSError, ValueError):
            return
        self.settings = clean_settings(DEFAULTS, data.get("settings", {}))
        self.lifetime_uploads = int(data.get("lifetime_uploads", 0))

    def save(self):
        with self.lock:
            data = {"settings": self.settings, "lifetime_uploads": self.lifetime_uploads}
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            (self.state_dir / "state.json").write_text(json.dumps(data, indent=2))
        except OSError:
            pass
        self._saved_at = time.monotonic()

    # ------------------------------------------------------------ called by the web server
    def update_settings(self, patch: dict):
        with self.lock:
            self.settings = clean_settings(self.settings, patch)
        self.save()

    def set_alert(self, info: dict):
        seconds = max(3, min(3600, float(info.get("seconds", 30))))
        with self.lock:
            self.alert = {"title": str(info.get("title", "Alert"))[:40],
                          "lines": [str(x)[:40] for x in info.get("lines", [])][:3],
                          "level": info.get("level", "info"),
                          "at": time.time(), "until": time.time() + seconds}

    def clear_alert(self):
        with self.lock:
            self.alert = None

    def set_user_image(self, img: Image.Image, fit: str):
        fitted = panels.from_image(img, fit)
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            fitted.save(self.state_dir / "image.png")
        except OSError:
            pass
        with self.lock:
            self.user_image = fitted
            self.settings["mode"] = "image"
        self.save()

    def set_external(self, img: Image.Image):
        with self.lock:
            self.external = panels.from_image(img, "contain")

    def queue_steps(self, label: str, steps: list, then=None):
        self.jobs.put((label, steps, then))

    def snapshot(self) -> dict:
        with self.lock:
            interval = max(self.settings["min_interval"], self.last_upload_ms / 1000)
            return {
                "connected": self.device is not None,
                "device": getattr(self.device, "name", ""),
                "device_error": self.device_error,
                "dry_run": self.dry_run,
                "panel": self.current_panel,
                "busy": self.busy,
                "settings": self.settings,
                "session_uploads": self.session_uploads,
                "lifetime_uploads": self.lifetime_uploads,
                "last_upload_ms": round(self.last_upload_ms),
                "seconds_per_frame": round(interval, 2) if self.last_upload_ms else None,
                "alert": self.alert,
                "sensors": {k: round(v, 1) for k, v in self._last_sensors.items()},
                "has_image": self.user_image is not None,
                "has_external": self.external is not None,
                "labels": panels.LABELS,
            }

    # ------------------------------------------------------------ engine thread
    def run(self):
        while not self.stop.is_set():
            try:
                self._tick()
            except DeviceError as exc:
                self._drop_device(str(exc))
            self.stop.wait(0.25)
        self.save()
        if self.device:
            self.device.close()

    def _drop_device(self, reason: str):
        if self.device:
            self.device.close()
        with self.lock:
            self.device, self.device_error, self.busy = None, reason, ""
        self._sent = None
        self._gauge_sent = None
        self._next_connect = time.monotonic() + 3

    def _connect(self):
        if self.device or time.monotonic() < self._next_connect:
            return
        try:
            device = open_device(self.dry_run)
        except (DeviceError, ImportError) as exc:
            self.device_error = str(exc)
            self._next_connect = time.monotonic() + 3
            return
        with self.lock:
            self.device, self.device_error = device, ""
        self._clock_at = 0.0

    def _send(self, steps, label: str = ""):
        def progress(done, total):
            if label:
                self.busy = f"{label} {100 * done // total}%"
        try:
            self.device.send(steps, progress)
        finally:
            self.busy = ""

    def _choose_panel(self, now: float) -> str:
        st = self.settings
        if self.alert and self.alert["until"] > time.time():
            return "alert"
        self.alert = None
        if st["mode"] == "rotate":
            names = [n for n in st["rotation"] if self._available(n)]
            return names[int(now // st["dwell"]) % len(names)] if names else "system"
        return st["mode"]

    def _available(self, name: str) -> bool:
        if name == "image":
            return self.user_image is not None
        if name == "external":
            return self.external is not None
        return name in panels.PANELS

    def _render(self, name: str, readings: dict) -> Image.Image | None:
        if name == "off":
            return None
        if name == "alert":
            return panels.alert(self.alert, time.time())
        if name == "image":
            return self.user_image or panels.placeholder("no picture yet")
        if name == "external":
            return self.external or panels.placeholder("nothing pushed yet")
        ctx = {"now": dt.datetime.now(), "sensors": readings, "settings": self.settings,
               "history": self.sensors.history, "weather": self.weather}
        return panels.PANELS[name](ctx)

    def _tick(self):
        now = time.monotonic()
        self._connect()
        self._refresh_weather(now)
        readings = self.sensors.read()

        with self.lock:
            self._last_sensors = readings
            name = self._choose_panel(now)
            image = self._render(name, readings)
            self.current_panel = name
        shown = image or panels.placeholder("screen left alone")
        pixels = p.rgb888_to_rgb565(shown.tobytes())
        if pixels != self._preview_pixels:
            self._preview_pixels = pixels
            self.preview_png = panels.to_png(shown)

        if now - self._saved_at > 60:
            self.save()
        if not self.device:
            return

        # Work queued by the control panel: uploads, view switches.
        try:
            label, steps, then = self.jobs.get_nowait()
        except queue.Empty:
            pass
        else:
            self._send(steps, label)
            self._sent = None
            if then:
                then()
            return

        st = self.settings
        if st["clock_sync"] and now - self._clock_at > 600:
            self._send(p.clock_stream(dt.datetime.now(), st["twelve_hour"], repeat=1))
            self._clock_at = now

        if st["gauge"] in readings and now - self._gauge_at >= 0.25:
            value = round(readings[st["gauge"]])
            if value != self._gauge_sent:
                self._send(p.gauge_stream(value))
                self._gauge_sent, self._gauge_at = value, now

        if image is None or pixels == self._sent:
            return
        if self._sent is not None and now - self._sent_at < st["min_interval"]:
            return
        started = time.monotonic()
        self._send(p.picture_stream(pixels))
        with self.lock:
            self.last_upload_ms = (time.monotonic() - started) * 1000
            self.session_uploads += 1
            self.lifetime_uploads += 1
        self._sent, self._sent_at = pixels, time.monotonic()

    # ------------------------------------------------------------ weather (opt-in)
    def _refresh_weather(self, now: float):
        st = self.settings
        where = (st["weather_lat"], st["weather_lon"], st["weather_unit"])
        if None in where[:2]:
            self.weather = None
            return
        if where == self._weather_for and now - self._weather_at < 900:
            return
        self._weather_for, self._weather_at = where, now
        threading.Thread(target=self._fetch_weather, args=where, daemon=True).start()

    def _fetch_weather(self, lat, lon, unit):
        query = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min", "forecast_days": 1,
            "timezone": "auto", "temperature_unit": "celsius" if unit == "C" else "fahrenheit"})
        try:
            with urllib.request.urlopen("https://api.open-meteo.com/v1/forecast?" + query, timeout=10) as r:
                data = json.load(r)
            self.weather = {"temp": data["current"]["temperature_2m"],
                            "text": WEATHER_TEXT.get(data["current"]["weather_code"], ""),
                            "high": data["daily"]["temperature_2m_max"][0],
                            "low": data["daily"]["temperature_2m_min"][0]}
        except (OSError, ValueError, KeyError, IndexError):
            pass


def load_gif_frames(data: bytes, target: str, fit: str, fps: int | None):
    """Decode an image or GIF into RGB565 frames for one of the GIF slots."""
    _, height, limit = p.GIF_TARGETS[target]
    source = Image.open(io.BytesIO(data))
    frames, durations = [], []
    for frame in ImageSequence.Iterator(source):
        durations.append(frame.info.get("duration", 0) or 100)
        frames.append(panels.from_image(frame, fit, height))
    if len(frames) > limit:   # keep the whole animation by dropping frames evenly
        keep = [round(i * (len(frames) - 1) / (limit - 1)) for i in range(limit)]
        scale = len(frames) / limit
        frames = [frames[i] for i in keep]
        durations = [d * scale for d in durations[:limit]]
    if not fps:
        fps = round(1000 / (sum(durations) / len(durations)))
    return [p.rgb888_to_rgb565(f.tobytes()) for f in frames], max(1, min(60, fps))


# ---------------------------------------------------------------- web server
class Handler(BaseHTTPRequestHandler):
    engine: Engine
    server_version = "al80"

    def log_message(self, *args):
        pass

    def _reply(self, code: int, body: bytes | dict, kind: str = "application/json"):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _local(self) -> bool:
        """Refuse requests that a web page on another site could have caused."""
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in allowed}

    def _body(self) -> bytes:
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_BODY:
            raise ValueError("body too large")
        return self.rfile.read(size)

    def do_GET(self):
        if not self._local():
            return self._reply(403, {"error": "local requests only"})
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            return self._reply(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            return self._reply(200, self.engine.snapshot())
        if path == "/api/preview.png":
            return self._reply(200, self.engine.preview_png, "image/png")
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        if not self._local():
            return self._reply(403, {"error": "local requests only"})
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        try:
            body = self._body()
            result = self._post(url.path, query, body)
        except (ValueError, KeyError, OSError) as exc:
            return self._reply(400, {"error": str(exc)})
        if result is None:
            return self._reply(404, {"error": "not found"})
        self._reply(200, result)

    def _post(self, path: str, query: dict, body: bytes):
        eng = self.engine
        if path == "/api/settings":
            eng.update_settings(json.loads(body))
            return {"ok": True}
        if path == "/api/sensors":
            return {"ok": True, "taken": eng.sensors.push(json.loads(body))}
        if path == "/api/alert":
            info = json.loads(body) if body else {}
            if info.get("clear"):
                eng.clear_alert()
            else:
                eng.set_alert(info)
            return {"ok": True}
        if path == "/api/frame":
            eng.set_external(Image.open(io.BytesIO(body)))
            if query.get("show") == "1":
                eng.update_settings({"mode": "external"})
            return {"ok": True}
        if path == "/api/upload":
            target, fit = query.get("target", "picture"), query.get("fit", "cover")
            if target == "picture":
                eng.set_user_image(Image.open(io.BytesIO(body)), fit)
                return {"ok": True}
            fps = int(query["fps"]) if query.get("fps") else None
            frames, fps = load_gif_frames(body, target, fit, fps)
            then = None
            if target == "page":
                eng.update_settings({"mode": "off"})
                then = lambda: eng.queue_steps("", p.view_stream("gif"))
            elif target == "home":
                eng.update_settings({"mode": "off"})
                then = lambda: eng.queue_steps("", p.view_stream("home"))
            eng.queue_steps(f"Uploading {len(frames)} frames", p.gif_stream(frames, target, fps), then)
            return {"ok": True, "frames": len(frames), "fps": fps}
        if path == "/api/action":
            action = json.loads(body).get("action")
            now = dt.datetime.now()
            streams = {
                "clock": lambda: p.clock_stream(now, eng.settings["twelve_hour"]),
                "view_home": lambda: p.view_stream("home"),
                "view_gif": lambda: p.view_stream("gif"),
                "next_picture": lambda: p.view_stream("next_picture"),
                "clear_pictures": p.clear_pictures_stream,
                "clear_gif": p.clear_gif_stream,
            }
            if action not in streams:
                raise ValueError("unknown action")
            if action.startswith(("view_", "next_", "clear_")):
                eng.update_settings({"mode": "off"})   # or the next frame would cover it
            eng.queue_steps("", streams[action]())
            return {"ok": True}
        return None


def serve(port: int = DEFAULT_PORT, dry_run: bool = False, open_browser: bool = True):
    engine = Engine(dry_run=dry_run)
    handler = type("BoundHandler", (Handler,), {"engine": engine})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=engine.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/"
    print(f"AL80 control panel: {url}" + ("  (dry run, no keyboard)" if dry_run else ""))
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop.set()
        thread.join(timeout=5)
        server.server_close()

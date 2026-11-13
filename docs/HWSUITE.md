# Feeding the screen from HwSuite or any other program

The app listens on `http://127.0.0.1:7380` and accepts requests only from this computer. HwSuite's
code was not available when this was written, so this is the seam to connect it: HwSuite sends
plain HTTP requests and needs no USB code of its own.

## Send readings

Any subset of `cpu`, `gpu`, `ram`, `vram`, `disk` (percent), `cpu_temp`, `gpu_temp` (°C) and `fps`.
A value is used for 10 seconds, so send about once a second. Without this, the app reads CPU, RAM
and disk itself and GPU from `nvidia-smi`. It cannot read CPU temperature on Windows.

```
curl -X POST http://127.0.0.1:7380/api/sensors -H "Content-Type: application/json" ^
     -d "{\"cpu\": 37, \"cpu_temp\": 62, \"gpu\": 81, \"gpu_temp\": 70, \"fps\": 144}"
```

```python
import json, urllib.request
def push(readings):
    urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:7380/api/sensors", json.dumps(readings).encode(),
        {"Content-Type": "application/json"}), timeout=1).close()
```

## Show an alert card

```
curl -X POST http://127.0.0.1:7380/api/alert -H "Content-Type: application/json" ^
     -d "{\"title\": \"GPU 90C\", \"lines\": [\"fans at 100%\"], \"level\": \"warn\", \"seconds\": 60}"
```

`level` is `info`, `ok`, `warn` or `error`. `{"clear": true}` dismisses it.

## Draw the whole screen yourself

POST any image (ideally a 96 × 160 PNG). `?show=1` also switches the screen to it.

```
curl -X POST "http://127.0.0.1:7380/api/frame?show=1" --data-binary @frame.png
```

## Other endpoints

| Request | Does |
|---|---|
| `GET /api/state` | connection, current panel, settings, upload counts, readings |
| `GET /api/preview.png` | the current frame |
| `POST /api/settings` | change settings, e.g. `{"mode": "system", "min_interval": 5}` |
| `POST /api/action` | `{"action": "view_home"}`, `view_gif`, `next_picture`, `clock`, `clear_pictures`, `clear_gif` |
| `POST /api/upload?target=picture` | image body. `target` can also be `page`, `home` or `boot` for a GIF |

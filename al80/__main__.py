"""Command line for the AL80 screen.

    python -m al80 [gui] [--dry-run] [--hidden]   the app: a window plus a tray icon
    python -m al80 serve [--dry-run]       the same engine with no window
    python -m al80 picture FILE            send one still to the picture page
    python -m al80 gif FILE [--where W]    send an animation (page, home or boot)
    python -m al80 clock [--24h]           set the keyboard's clock
    python -m al80 view home|gif|next      switch what the screen shows
    python -m al80 gauge N                 set the home-page gauge to N percent
    python -m al80 readout A B C           show three numbers in place of the clock
    python -m al80 bench [-n N]            time N picture uploads
    python -m al80 probe                   list the keyboard's USB interfaces
    python -m al80 alert TITLE [LINE...]   show an alert card (needs the app running)
    python -m al80 run -- COMMAND...       run a command, then show how it ended

The one-shot commands need the keyboard to themselves, so quit the app first.
`alert` and `run` are the opposite: they talk to the running app.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import protocol as p
from .app import DEFAULT_PORT


def _send(steps, label=None):
    from .device import Device, DeviceError
    try:
        device = Device()
    except DeviceError as exc:
        sys.exit(str(exc))
    except ImportError:
        sys.exit("the hidapi package is missing: pip install hidapi")
    def progress(done, total):
        if label:
            print(f"\r{label}: {100 * done // total}%", end="", flush=True)
    started = time.monotonic()
    try:
        device.send(steps, progress)
    except DeviceError as exc:
        sys.exit(f"\n{exc}")
    finally:
        device.close()
    if label:
        print()
    return time.monotonic() - started


def _post_alert(port: int, info: dict) -> bool:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/alert", json.dumps(info).encode(),
                                     {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=3).close()
        return True
    except (urllib.error.URLError, OSError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(prog="al80", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = parser.add_subparsers(dest="action")

    for name in ("gui", "serve"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--dry-run", action="store_true", help="run with no keyboard")
        if name == "gui":
            cmd.add_argument("--hidden", action="store_true", help="start in the tray")

    picture = sub.add_parser("picture")
    picture.add_argument("file")
    picture.add_argument("--fit", choices=("cover", "contain"), default="cover")

    gif = sub.add_parser("gif")
    gif.add_argument("file")
    gif.add_argument("--where", choices=tuple(p.GIF_TARGETS), default="page")
    gif.add_argument("--fps", type=int)
    gif.add_argument("--fit", choices=("cover", "contain"), default="cover")

    sub.add_parser("clock").add_argument("--24h", dest="full_day", action="store_true")
    sub.add_parser("view").add_argument("page", choices=("home", "gif", "next"))
    sub.add_parser("gauge").add_argument("percent", type=int)
    sub.add_parser("readout").add_argument("values", type=int, nargs=3)
    sub.add_parser("bench").add_argument("-n", type=int, default=5)
    sub.add_parser("probe")

    alert = sub.add_parser("alert")
    alert.add_argument("title")
    alert.add_argument("lines", nargs="*")
    alert.add_argument("--level", choices=("info", "ok", "warn", "error"), default="info")
    alert.add_argument("--seconds", type=float, default=30)

    run = sub.add_parser("run")
    run.add_argument("command", nargs=argparse.REMAINDER)

    args = parser.parse_args(argv)

    if args.action in (None, "gui"):
        from .gui import run as run_window
        run_window(args.port, getattr(args, "dry_run", False), getattr(args, "hidden", False))

    elif args.action == "serve":
        from .app import serve as run_engine
        run_engine(args.port, args.dry_run)

    elif args.action == "picture":
        from PIL import Image
        from .panels import from_image
        pixels = p.rgb888_to_rgb565(from_image(Image.open(args.file), args.fit).tobytes())
        print(f"sent in {_send(p.picture_stream(pixels), 'uploading'):.2f} s")

    elif args.action == "gif":
        from .app import load_gif_frames
        with open(args.file, "rb") as handle:
            frames, fps = load_gif_frames(handle.read(), args.where, args.fit, args.fps)
        print(f"{len(frames)} frames at {fps} fps")
        _send(p.gif_stream(frames, args.where, fps), "uploading")
        if args.where != "boot":
            _send(p.view_stream("gif" if args.where == "page" else "home"))

    elif args.action == "clock":
        _send(p.clock_stream(dt.datetime.now(), twelve_hour=not args.full_day))

    elif args.action == "view":
        _send(p.view_stream({"next": "next_picture"}.get(args.page, args.page)))

    elif args.action == "gauge":
        _send(p.gauge_stream(args.percent))

    elif args.action == "readout":
        _send(p.readout_stream(*args.values))

    elif args.action == "bench":
        times = []
        for i in range(args.n):
            shade = 255 * i // max(1, args.n - 1)
            pixel = p.rgb888_to_rgb565(bytes((shade, 80, 255 - shade)))
            times.append(_send(p.picture_stream(pixel * (p.WIDTH * p.HEIGHT))))
            print(f"frame {i + 1}: {times[-1]:.2f} s")
        print(f"average {sum(times) / len(times):.2f} s per frame")

    elif args.action == "probe":
        try:
            import hid
        except ImportError:
            sys.exit("the hidapi package is missing: pip install hidapi")
        from .device import USB_IDS
        found = [info for vid, pid in USB_IDS for info in hid.enumerate(vid, pid)]
        if not found:
            sys.exit("no AL80 on USB")
        for info in found:
            mark = "  <- screen" if (info["usage_page"], info["usage"]) == (0xFF60, 0x61) else ""
            print(f"{info['vendor_id']:04X}:{info['product_id']:04X} interface {info['interface_number']} "
                  f"usage {info['usage_page']:#06x}/{info['usage']:#04x} {info['product_string']}{mark}")

    elif args.action == "alert":
        ok = _post_alert(args.port, {"title": args.title, "lines": args.lines,
                                     "level": args.level, "seconds": args.seconds})
        if not ok:
            sys.exit("the app is not running: start it with start.bat")

    elif args.action == "run":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            sys.exit("give a command to run, after --")
        started = time.monotonic()
        code = subprocess.call(command)
        minutes, seconds = divmod(int(time.monotonic() - started), 60)
        took = f"{minutes} min {seconds} s" if minutes else f"{seconds} s"
        _post_alert(args.port, {"title": "Done" if code == 0 else f"Failed ({code})",
                                "lines": [" ".join(command)[:40], took],
                                "level": "ok" if code == 0 else "error", "seconds": 120})
        sys.exit(code)


if __name__ == "__main__":
    main()

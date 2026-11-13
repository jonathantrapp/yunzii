"""What gets drawn on the 96x160 picture page.

Every panel is a function taking a context dict and returning a 96x160 RGB
image. Panels draw only whole numbers and whole minutes where they can, so
an unchanged reading produces an identical frame and no upload.
"""
from __future__ import annotations

import datetime as dt
import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .protocol import HEIGHT, WIDTH

BG = (10, 12, 20)
PANEL = (24, 28, 44)
FG = (235, 238, 245)
DIM = (130, 138, 160)
ACCENT = (80, 200, 255)
GREEN = (90, 220, 120)
AMBER = (255, 180, 60)
RED = (255, 80, 90)
PURPLE = (170, 110, 255)

LEVELS = {"info": ((16, 40, 90), ACCENT), "ok": ((14, 70, 40), GREEN),
          "warn": ((90, 60, 10), AMBER), "error": ((90, 16, 24), RED)}

_FONTS = {
    (False, False): ("DejaVuSans.ttf", "segoeui.ttf", "arial.ttf"),
    (True, False): ("DejaVuSans-Bold.ttf", "segoeuib.ttf", "arialbd.ttf"),
    (False, True): ("DejaVuSansMono.ttf", "consola.ttf", "cour.ttf"),
    (True, True): ("DejaVuSansMono-Bold.ttf", "consolab.ttf", "courbd.ttf"),
}


@lru_cache(maxsize=None)
def font(size: int, bold: bool = False, mono: bool = False):
    for name in _FONTS[(bold, mono)]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _new(bg=BG) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    return img, ImageDraw.Draw(img)


def _center(d, y, text, f, fill=FG):
    x0, _, x1, _ = d.textbbox((0, 0), text, font=f)
    d.text(((WIDTH - (x1 - x0)) // 2 - x0, y), text, font=f, fill=fill)


def _fit(d, text: str, f, width: int = WIDTH - 8) -> str:
    """Shorten text with an ellipsis until it fits the panel."""
    if d.textlength(text, font=f) <= width:
        return text
    while text and d.textlength(text + "…", font=f) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _bar(d, x, y, w, h, frac, color):
    d.rectangle([x, y, x + w - 1, y + h - 1], fill=PANEL)
    fill = max(0, min(w, round(w * frac)))
    if fill:
        d.rectangle([x, y, x + fill - 1, y + h - 1], fill=color)


def _gradient(img, top, bottom):
    d = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        d.line([0, y, WIDTH, y], fill=tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))


def _heat(percent: float):
    return GREEN if percent < 60 else AMBER if percent < 85 else RED


def _time_text(now: dt.datetime, twelve_hour: bool) -> tuple[str, str]:
    if twelve_hour:
        return f"{now.hour % 12 or 12}:{now.minute:02d}", "AM" if now.hour < 12 else "PM"
    return f"{now.hour:02d}:{now.minute:02d}", ""


# ---------------------------------------------------------------- panels
def system(ctx) -> Image.Image:
    img, d = _new()
    s = ctx["sensors"]
    clock, _ = _time_text(ctx["now"], ctx["settings"]["twelve_hour"])
    d.text((4, 3), "SYSTEM", font=font(10, bold=True), fill=ACCENT)
    d.text((91 - d.textlength(clock, font=font(9, mono=True)), 4), clock, font=font(9, mono=True), fill=DIM)

    rows = [(label, s[key], s.get(temp)) for label, key, temp in
            (("CPU", "cpu", "cpu_temp"), ("GPU", "gpu", "gpu_temp"), ("RAM", "ram", None),
             ("VRAM", "vram", None), ("DISK", "disk", None)) if key in s][:4]
    y = 19
    for label, value, temp in rows:
        d.text((4, y), label, font=font(9, bold=True), fill=DIM)
        if temp is not None:
            d.text((34, y), f"{round(temp)}°", font=font(9), fill=_heat(temp))
        text = f"{round(value)}%"
        d.text((92 - d.textlength(text, font=font(9, mono=True)), y), text, font=font(9, mono=True), fill=FG)
        _bar(d, 4, y + 12, 88, 5, value / 100, _heat(value))
        y += 23
    if not rows:
        _center(d, 60, "no readings", font(9), DIM)
        _center(d, 74, "pip install psutil", font(8), DIM)

    # One-minute history, CPU over GPU.
    top = max(y + 2, 114)
    d.rectangle([4, top, 91, 156], fill=PANEL)
    for key, color in (("gpu", PURPLE), ("cpu", GREEN)):
        series = list(ctx["history"].get(key, ()))
        if len(series) > 1:
            span = 155 - top - 2
            pts = [(91 - (len(series) - 1 - i) * 2, 155 - v / 100 * span) for i, v in enumerate(series)]
            d.line(pts, fill=color, width=1)
    if "fps" in s:
        d.text((7, top + 2), f"{round(s['fps'])} fps", font=font(8, bold=True), fill=FG)
    return img


def clock(ctx) -> Image.Image:
    img, d = _new()
    _gradient(img, (14, 22, 48), (6, 8, 16))
    now = ctx["now"]
    text, suffix = _time_text(now, ctx["settings"]["twelve_hour"])
    _center(d, 8, now.strftime("%a %d %b").upper(), font(10, bold=True), DIM)
    _center(d, 24, text, font(30 if len(text) < 5 else 28, bold=True), FG)
    if suffix:
        _center(d, 58, suffix, font(9, bold=True), ACCENT)
    d.line([12, 74, 83, 74], fill=PANEL)

    weather = ctx.get("weather")
    if weather:
        _center(d, 82, f"{round(weather['temp'])}°", font(22, bold=True), FG)
        _center(d, 110, _fit(d, weather["text"], font(9)), font(9), DIM)
        _center(d, 124, f"H {round(weather['high'])}°  L {round(weather['low'])}°", font(9), FG)
    else:
        s = ctx["sensors"]
        y = 84
        for label, key in (("CPU", "cpu"), ("GPU", "gpu"), ("RAM", "ram")):
            if key in s:
                d.text((10, y), label, font=font(9, bold=True), fill=DIM)
                _bar(d, 38, y + 3, 48, 5, s[key] / 100, _heat(s[key]))
                y += 16
    _center(d, 144, _fit(d, ctx["settings"]["footer"], font(8)), font(8), DIM)
    return img


def badge(ctx) -> Image.Image:
    img, d = _new()
    _gradient(img, (20, 20, 30), (60, 0, 20))
    st = ctx["settings"]
    big = st["badge_big"][:4]
    _center(d, 26, big, font(40 if len(big) <= 2 else 26, bold=True), (255, 60, 80))
    _center(d, 84, _fit(d, st["badge_line1"], font(14, bold=True)), font(14, bold=True), FG)
    _center(d, 104, _fit(d, st["badge_line2"], font(8, bold=True)), font(8, bold=True), DIM)
    for x in range(0, WIDTH, 8):
        d.rectangle([x, 150, x + 3, 153], fill=(255, 60, 80))
    return img


def alert(info: dict, now: float) -> Image.Image:
    """A card that takes over the screen: a finished build, a failed job, a reminder."""
    dark, bright = LEVELS.get(info.get("level"), LEVELS["info"])
    img, d = _new()
    _gradient(img, dark, (8, 8, 12))
    level = info.get("level", "info")
    if level in ("warn", "error"):
        d.polygon([(48, 12), (74, 56), (22, 56)], fill=bright)
        d.rectangle([46, 26, 50, 44], fill=(20, 10, 10))
        d.rectangle([46, 48, 50, 52], fill=(20, 10, 10))
    elif level == "ok":
        d.ellipse([26, 12, 70, 56], fill=bright)
        d.line([(36, 35), (45, 44), (60, 26)], fill=(10, 30, 16), width=5)
    else:
        d.ellipse([26, 12, 70, 56], fill=bright)
        d.rectangle([46, 22, 50, 26], fill=(10, 20, 40))
        d.rectangle([46, 30, 50, 46], fill=(10, 20, 40))
    title = str(info.get("title", "ALERT")).upper()
    size = next((n for n in (10, 9, 8) if d.textlength(title, font=font(n, bold=True)) <= WIDTH - 6), 8)
    _center(d, 66, _fit(d, title, font(size, bold=True)), font(size, bold=True), FG)
    y = 84
    for line in list(info.get("lines", []))[:3]:
        _center(d, y, _fit(d, str(line), font(9)), font(9), (220, 220, 230))
        y += 12
    age = int(now - info.get("at", now))
    when = "just now" if age < 60 else f"{age // 60} min ago"
    d.rounded_rectangle([14, 130, 82, 148], radius=4, fill=(245, 245, 250))
    _center(d, 133, when, font(9, bold=True), dark)
    return img


def test_card(number: int, total: int) -> Image.Image:
    """A numbered card for the storage test. Each number gets its own colour."""
    hue = [(200, 60, 70), (60, 140, 220), (70, 170, 90), (200, 150, 40), (150, 90, 210)][number % 5]
    img, d = _new(tuple(c // 4 for c in hue))
    d.rectangle([0, 0, WIDTH - 1, 22], fill=hue)
    _center(d, 5, "STORAGE TEST", font(9, bold=True), (255, 255, 255))
    _center(d, 44, str(number), font(54, bold=True), FG)
    _center(d, 122, f"card {number} of {total}", font(9), DIM)
    return img


def home_card(lines: list[tuple[str, str]]) -> Image.Image:
    """Preview shown while the keyboard draws its own home page."""
    img, d = _new()
    _center(d, 8, "LIVE HOME PAGE", font(9, bold=True), ACCENT)
    _center(d, 22, "no picture uploads", font(8), DIM)
    y = 46
    for label, value in lines:
        d.text((6, y), label, font=font(8, bold=True), fill=DIM)
        d.text((6, y + 11), _fit(d, value, font(10, bold=True, mono=True), WIDTH - 12),
               font=font(10, bold=True, mono=True), fill=FG)
        y += 32
    _center(d, 144, "drawn by the keyboard", font(8), DIM)
    return img


def from_image(source: Image.Image, fit: str = "cover", height: int = HEIGHT) -> Image.Image:
    """Any picture, scaled to the panel."""
    rgba = source.convert("RGBA")
    canvas = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
    canvas.alpha_composite(rgba)
    method = ImageOps.fit if fit == "cover" else ImageOps.pad
    return method(canvas.convert("RGB"), (WIDTH, height), Image.LANCZOS)


def placeholder(text: str) -> Image.Image:
    img, d = _new()
    _center(d, 70, _fit(d, text, font(9)), font(9), DIM)
    return img


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


PANELS = {"system": system, "clock": clock, "badge": badge}
LABELS = {"rotate": "Rotate", "system": "System monitor", "clock": "Clock", "badge": "Name badge",
          "image": "Your picture", "external": "Frame from another program",
          "home": "Live home page (no uploads)", "off": "Leave the screen alone", "alert": "Alert"}

#!/usr/bin/env python3
"""Render YUNZII AL80 screen mockups at native resolution.

Panel facts (from community RE, see docs/PLAN.md):
  * picture / GIF page : 96 x 160 px, portrait, RGB565 big-endian, row-major
  * main (home) page   : 96 x 64 px animated region, max 42 frames

Every frame is quantised to RGB565 so the preview matches what the panel can
actually show. Outputs go to mockups/:
  native/*.png      exact panel-size images (what you would upload)
  native/*.gif      animated options
  native/*.rgb565   raw big-endian RGB565 frame bytes (30,720 B for 96x160)
  preview_*.png     4x nearest-neighbour previews + a contact sheet
"""
from __future__ import annotations

import math
import random
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 96, 160          # picture / GIF page
MW, MH = 96, 64         # main page animated strip
SCALE = 4

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "mockups"
NATIVE = OUT / "native"

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans"
    if mono:
        name += "Mono"
    if bold:
        name += "-Bold"
    return ImageFont.truetype(str(FONT_DIR / f"{name}.ttf"), size)


# ---------------------------------------------------------------- palette
BG = (10, 12, 20)
PANEL = (24, 28, 44)
FG = (235, 238, 245)
DIM = (130, 138, 160)
ACCENT = (80, 200, 255)
GREEN = (90, 220, 120)
AMBER = (255, 180, 60)
RED = (255, 80, 90)
PURPLE = (170, 110, 255)


# ---------------------------------------------------------------- helpers
def to_rgb565(img: Image.Image) -> Image.Image:
    """Quantise an RGB image to the 5-6-5 colour grid the panel uses."""
    img = img.convert("RGB")
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = px[x, y]
            r5, g6, b5 = r >> 3, g >> 2, b >> 3
            px[x, y] = ((r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2))
    return img


def rgb565_bytes(img: Image.Image) -> bytes:
    """Big-endian RGB565, row-major, top-left origin (the AL80 wire format)."""
    out = bytearray()
    for r, g, b in img.convert("RGB").get_flattened_data():
        out += struct.pack(">H", ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
    return bytes(out)


def ctext(d: ImageDraw.ImageDraw, y: int, s: str, f, fill=FG, w: int = W) -> None:
    x0, _, x1, _ = d.textbbox((0, 0), s, font=f)
    d.text(((w - (x1 - x0)) // 2 - x0, y), s, font=f, fill=fill)


def bar(d, x, y, w, h, frac, color, track=PANEL):
    d.rectangle([x, y, x + w - 1, y + h - 1], fill=track)
    fw = max(0, min(w, round(w * frac)))
    if fw:
        d.rectangle([x, y, x + fw - 1, y + h - 1], fill=color)


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def vgrad(img, top, bottom, box=None):
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box or (0, 0, img.width - 1, img.height - 1)
    for y in range(y0, y1 + 1):
        d.line([x0, y, x1, y], fill=lerp(top, bottom, (y - y0) / max(1, y1 - y0)))


def new(w=W, h=H, bg=BG):
    return Image.new("RGB", (w, h), bg)


# ---------------------------------------------------------------- 96x160 stills
def clock_weather():
    img = new()
    vgrad(img, (14, 22, 48), (6, 8, 16))
    d = ImageDraw.Draw(img)
    ctext(d, 6, "THU 12 NOV", font(10, bold=True), DIM)
    ctext(d, 20, "10:42", font(30, bold=True), FG)
    ctext(d, 54, "AM", font(9, bold=True), ACCENT)
    d.line([12, 70, 83, 70], fill=PANEL, width=1)
    # sun + cloud icon
    d.ellipse([18, 80, 42, 104], fill=AMBER)
    d.ellipse([30, 92, 54, 110], fill=(220, 225, 235))
    d.ellipse([40, 86, 62, 108], fill=(220, 225, 235))
    d.rectangle([34, 100, 58, 110], fill=(220, 225, 235))
    d.text((64, 84), "48°", font=font(14, bold=True), fill=FG)
    ctext(d, 114, "Partly cloudy", font(9), DIM)
    ctext(d, 126, "H 52°  L 39°", font(9), FG)
    # tiny hourly strip
    temps = [44, 47, 50, 52, 49, 45]
    for i, t in enumerate(temps):
        x = 10 + i * 14
        h = (t - 40) * 1.6
        d.rectangle([x, 156 - h, x + 8, 155], fill=lerp(ACCENT, AMBER, (t - 44) / 8))
    return img


def system_monitor():
    img = new()
    d = ImageDraw.Draw(img)
    f_l, f_v = font(9, bold=True), font(9, mono=True)
    d.text((4, 3), "SYSTEM", font=font(10, bold=True), fill=ACCENT)
    d.text((60, 4), "62°C", font=f_v, fill=AMBER)
    rows = [("CPU", 0.37, GREEN), ("GPU", 0.81, AMBER), ("RAM", 0.58, ACCENT), ("SSD", 0.22, PURPLE)]
    y = 20
    for name, frac, col in rows:
        d.text((4, y), name, font=f_l, fill=DIM)
        d.text((66, y), f"{int(frac*100):>3}%", font=f_v, fill=FG)
        bar(d, 4, y + 12, 88, 5, frac, col)
        y += 24
    # CPU sparkline
    d.text((4, 118), "CPU 60s", font=font(8), fill=DIM)
    random.seed(3)
    pts, v = [], 0.4
    for i in range(46):
        v = min(0.95, max(0.05, v + random.uniform(-0.12, 0.12)))
        pts.append((4 + i * 2, 156 - v * 26))
    d.rectangle([4, 130, 91, 157], fill=PANEL)
    d.line(pts, fill=GREEN, width=1)
    return img


def now_playing():
    img = new()
    d = ImageDraw.Draw(img)
    # placeholder album art: generated pattern
    art = Image.new("RGB", (80, 80))
    vgrad(art, (255, 90, 140), (90, 60, 220))
    ad = ImageDraw.Draw(art)
    for r in range(70, 0, -14):
        ad.ellipse([40 - r // 2, 40 - r // 2, 40 + r // 2, 40 + r // 2], outline=(255, 255, 255), width=1)
    ad.ellipse([34, 34, 46, 46], fill=(20, 20, 30))
    img.paste(art, (8, 6))
    ctext(d, 92, "Midnight Drive", font(10, bold=True), FG)
    ctext(d, 106, "Neon Coast", font(9), DIM)
    bar(d, 8, 124, 80, 3, 0.42, GREEN)
    d.text((8, 130), "1:37", font=font(8, mono=True), fill=DIM)
    d.text((66, 130), "3:52", font=font(8, mono=True), fill=DIM)
    # controls
    cx = 48
    d.polygon([(cx - 26, 146), (cx - 18, 141), (cx - 18, 151)], fill=DIM)
    d.rectangle([cx - 4, 140, cx - 1, 152], fill=FG)
    d.rectangle([cx + 2, 140, cx + 5, 152], fill=FG)
    d.polygon([(cx + 26, 146), (cx + 18, 141), (cx + 18, 151)], fill=DIM)
    return img


def agenda():
    img = new()
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W - 1, 22], fill=(40, 30, 80))
    d.text((4, 5), "TODAY", font=font(10, bold=True), fill=FG)
    d.text((58, 6), "3 left", font=font(8), fill=(200, 190, 255))
    events = [
        ("11:00", "Lab mtg", PURPLE, True),
        ("13:30", "CSCI 4210", ACCENT, False),
        ("16:00", "Office hrs", GREEN, False),
        ("19:00", "Gym", AMBER, False),
    ]
    y = 28
    for t, title, col, now in events:
        if now:
            d.rectangle([2, y - 2, W - 3, y + 24], fill=PANEL)
        d.rectangle([4, y, 6, y + 21], fill=col)
        d.text((11, y), t, font=font(8, mono=True), fill=DIM)
        d.text((11, y + 10), title, font=font(10, bold=now), fill=FG)
        y += 30
    ctext(d, 150, "in 18 min", font(8, bold=True), PURPLE)
    return img


def pomodoro(frac=0.64, label="FOCUS", mins="16:02"):
    img = new()
    d = ImageDraw.Draw(img)
    ctext(d, 6, label, font(11, bold=True), RED)
    box = [10, 26, 86, 102]
    d.ellipse(box, outline=PANEL, width=7)
    d.arc(box, -90, -90 + 360 * frac, fill=RED, width=7)
    ctext(d, 54, mins, font(15, bold=True, mono=True), FG)
    ctext(d, 112, "Session 3 of 4", font(9), DIM)
    for i in range(4):
        x = 24 + i * 14
        d.ellipse([x, 128, x + 8, 136], fill=RED if i < 2 else (PANEL if i > 2 else (150, 50, 60)))
    ctext(d, 146, "Fn+Esc: pause", font(8), DIM)
    return img


def layer_indicator():
    img = new()
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W - 1, 40], fill=(20, 60, 50))
    ctext(d, 4, "LAYER", font(8, bold=True), (150, 230, 200))
    ctext(d, 14, "FN 1", font(17, bold=True), FG)
    keys = [("F1", "Mute"), ("F2", "Vol-"), ("F3", "Vol+"), ("F7", "Prev"),
            ("F8", "Play"), ("F9", "Next"), ("8", "Pic"), ("0", "GIF")]
    y = 46
    for k, v in keys:
        d.rounded_rectangle([4, y, 26, y + 12], radius=2, fill=PANEL, outline=(60, 70, 95))
        ctext(d, y + 1, k, font(8, bold=True), FG, w=31)
        d.text((32, y + 1), v, font=font(9), fill=DIM)
        y += 14
    return img


def alert_card():
    img = new()
    vgrad(img, (90, 16, 24), (20, 6, 10))
    d = ImageDraw.Draw(img)
    # warning triangle
    d.polygon([(48, 14), (74, 58), (22, 58)], fill=AMBER)
    d.rectangle([46, 28, 50, 46], fill=(40, 10, 10))
    d.rectangle([46, 50, 50, 54], fill=(40, 10, 10))
    ctext(d, 68, "BUILD FAILED", font(10, bold=True), FG)
    ctext(d, 84, "yunzii / main", font(9), (255, 190, 190))
    ctext(d, 96, "test_protocol", font(9, mono=True), (255, 190, 190))
    d.rounded_rectangle([12, 118, 84, 138], radius=4, fill=(255, 255, 255))
    ctext(d, 122, "2 min ago", font(9, bold=True), (120, 20, 30))
    return img


def name_badge():
    img = new()
    vgrad(img, (20, 20, 30), (60, 0, 20))
    d = ImageDraw.Draw(img)
    # pixel monogram
    ctext(d, 26, "JT", font(40, bold=True), (255, 60, 80))
    ctext(d, 84, "RPI", font(14, bold=True), FG)
    ctext(d, 104, "ENGINEERING", font(8, bold=True), DIM)
    for i in range(0, W, 8):
        d.rectangle([i, 150, i + 3, 153], fill=(255, 60, 80))
    return img


# ---------------------------------------------------------------- animations
def starfield_frames(n=24):
    random.seed(7)
    stars = [(random.uniform(-1, 1), random.uniform(-1, 1), random.uniform(0.1, 1)) for _ in range(70)]
    frames = []
    for f in range(n):
        img = new(bg=(2, 2, 8))
        d = ImageDraw.Draw(img)
        for sx, sy, sz in stars:
            z = (sz - f / n) % 1 + 0.05
            x, y = W / 2 + sx / z * 20, H / 2 + sy / z * 30
            if 0 <= x < W and 0 <= y < H:
                c = int(255 * (1 - z))
                r = 0 if z > 0.4 else 1
                x, y = int(x), int(y)
                d.rectangle([x, y, x + r, y + r], fill=(c, c, min(255, c + 40)))
        frames.append(img)
    return frames


def pomodoro_frames(n=12):
    return [pomodoro(0.64 + i * 0.01, mins=f"16:{2 - i % 3:02d}") for i in range(n)]


def main_wave_frames(n=24):
    """96x64 main-page strip: smooth colour wave (seamless loop)."""
    frames = []
    for f in range(n):
        img = new(MW, MH, (0, 0, 0))
        px = img.load()
        t = f / n * 2 * math.pi
        for y in range(MH):
            for x in range(MW):
                v = math.sin(x / 9 + t) + math.sin(y / 7 - t) + math.sin((x + y) / 13 + t)
                h = (v + 3) / 6
                px[x, y] = (int(40 + 60 * h), int(20 + 120 * (1 - h)), int(120 + 120 * h))
        frames.append(img)
    return frames


def main_eq_frames(n=16):
    """96x64 main-page strip: audio-equaliser bars."""
    random.seed(11)
    phases = [random.uniform(0, 2 * math.pi) for _ in range(12)]
    frames = []
    for f in range(n):
        img = new(MW, MH, (4, 4, 12))
        d = ImageDraw.Draw(img)
        for i, ph in enumerate(phases):
            h = int(10 + 44 * (0.5 + 0.5 * math.sin(ph + f / n * 2 * math.pi * (1 + i % 3))))
            x = 3 + i * 8
            for yy in range(0, h, 3):
                d.rectangle([x, 60 - yy, x + 5, 61 - yy], fill=lerp(GREEN, RED, yy / 54))
        frames.append(img)
    return frames


def main_ticker_frames(n=32):
    """96x64 main-page strip: scrolling ticker text."""
    msg = "  CPU 37%  •  48°F Troy  •  Next: Lab mtg 11:00  •  "
    f = font(14, bold=True)
    tw = int(ImageDraw.Draw(new()).textlength(msg, font=f))
    frames = []
    for i in range(n):
        img = new(MW, MH, (12, 12, 24))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, MW - 1, 12], fill=(30, 30, 60))
        d.text((3, 1), "LIVE", font=font(9, bold=True), fill=RED)
        off = -int(i / n * tw)
        d.text((off, 26), msg + msg, font=f, fill=FG)
        frames.append(img)
    return frames


# ---------------------------------------------------------------- output
def save_still(name, img):
    img = to_rgb565(img)
    img.save(NATIVE / f"{name}.png")
    (NATIVE / f"{name}.rgb565").write_bytes(rgb565_bytes(img))
    return img


def save_anim(name, frames, fps):
    frames = [to_rgb565(f) for f in frames]
    frames[0].save(NATIVE / f"{name}.gif", save_all=True, append_images=frames[1:],
                   duration=int(1000 / fps), loop=0, disposal=1)
    big = [f.resize((f.width * SCALE, f.height * SCALE), Image.NEAREST) for f in frames]
    big[0].save(OUT / f"preview_{name}.gif", save_all=True, append_images=big[1:],
                duration=int(1000 / fps), loop=0, disposal=1)
    return frames[0]


def contact_sheet(items, cols, cell_w, cell_h, path, title):
    pad, label_h, title_h = 16, 22, 40
    rows = math.ceil(len(items) / cols)
    sheet = Image.new("RGB", (pad + cols * (cell_w + pad), title_h + rows * (cell_h + label_h + pad) + pad), (32, 34, 40))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 10), title, font=font(18, bold=True), fill=FG)
    for i, (name, img) in enumerate(items):
        r, c = divmod(i, cols)
        x = pad + c * (cell_w + pad)
        y = title_h + r * (cell_h + label_h + pad)
        big = img.resize((img.width * SCALE, img.height * SCALE), Image.NEAREST)
        # bezel
        d.rectangle([x - 3, y - 3, x + big.width + 2, y + big.height + 2], fill=(0, 0, 0))
        sheet.paste(big, (x, y))
        d.text((x, y + big.height + 6), name, font=font(13), fill=DIM)
    sheet.save(path)


def main():
    NATIVE.mkdir(parents=True, exist_ok=True)
    stills = [
        ("1 clock + weather", "clock_weather", clock_weather()),
        ("2 system monitor", "system_monitor", system_monitor()),
        ("3 now playing", "now_playing", now_playing()),
        ("4 agenda", "agenda", agenda()),
        ("5 pomodoro", "pomodoro", pomodoro()),
        ("6 layer / keymap", "layer_indicator", layer_indicator()),
        ("7 alert card", "alert_card", alert_card()),
        ("8 name badge", "name_badge", name_badge()),
    ]
    sheet_items = [(label, save_still(slug, img)) for label, slug, img in stills]
    sheet_items.append(("9 starfield GIF", save_anim("starfield", starfield_frames(), 15)))
    contact_sheet(sheet_items, 5, W * SCALE, H * SCALE, OUT / "preview_picture_page.png",
                  "AL80 picture / GIF page  -  96 x 160 RGB565 (shown 4x)")

    strips = [
        ("A colour wave (24f)", save_anim("main_wave", main_wave_frames(), 20)),
        ("B equaliser (16f)", save_anim("main_eq", main_eq_frames(), 15)),
        ("C ticker (32f)", save_anim("main_ticker", main_ticker_frames(), 12)),
    ]
    contact_sheet(strips, 3, MW * SCALE, MH * SCALE, OUT / "preview_main_page.png",
                  "AL80 main page strip  -  96 x 64, max 42 frames (shown 4x)")
    print("wrote", sorted(p.name for p in OUT.rglob("*") if p.is_file()))


if __name__ == "__main__":
    main()

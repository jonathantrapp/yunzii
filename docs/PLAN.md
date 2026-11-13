# YUNZII AL80 screen: reverse-engineering and display plan

## 1. What is already known

Other people have already reverse-engineered most of this screen. Read their work before
capturing anything ourselves:

- [snackdriven/al80-lcd](https://github.com/snackdriven/al80-lcd): full protocol write-up
  (`AL80_KNOWLEDGE_BASE.md`), HID captures, and custom vial-qmk firmware.
- [snackdriven/al80-studio](https://github.com/snackdriven/al80-studio): WebHID browser app
  plus a Node host that rotates clock, weather and now-playing cards.
- [techno1731/yunzii-al80-unlocked](https://github.com/techno1731/yunzii-al80-unlocked): open
  firmware, a Python CLI (`al80_screen.py`) and notes on the display module.

| Fact | Value |
|---|---|
| USB IDs | VID `0x28E9`, PID `0x30AF` |
| LCD interface | raw-HID usage page `0xFF60`, usage `0x61` (the VIA interface), 64-byte reports. Only one app can hold it at a time. |
| **Picture / GIF page** | **96 × 160 px, portrait**, RGB565 big-endian, row-major, top-left origin. 30,720 B per frame. |
| **Main (home) page strip** | **96 × 64 px** animated region (GIF mode 2), max 42 frames. The clock and status widgets are drawn by the module's own firmware. |
| GIF limits | GIF page: up to 160 frames. Startup animation: up to 64 frames. FPS is set per upload (1–60). |
| Packet flow | `0x40` announce → `0x41` data blocks (56 B payload) → `0x42` finish. 16-bit additive checksum on every packet, CRC16-MODBUS on announces. |
| Display module | Separate MCU on USART3 (460800 or 921600 baud). The keyboard only relays packets to it, which is why the screen keeps working on custom firmware. |
| Danger zone | `0xB0–0xB7` are bootloader/DFU commands. Never send them by hand. |

### Constraints that shape what we display

1. **No partial updates.** Every change means a full 96×160 frame upload, which takes a few
   seconds and must be ACK-gated or the image bands. Plan for refreshes about once a minute,
   not 30 fps.
2. **The home clock is firmware-drawn.** We can set the time and date, but not its font or
   background colour. Custom layouts go on the picture page or in the 96×64 strip.
3. **Flash wear is unknown.** Pictures are stored in a ring of slots, and nobody has confirmed
   whether each upload writes flash. Refresh live cards sparingly until we test this.
4. **Wired only** for screen uploads. On 2.4G or Bluetooth the screen can't be updated.
5. **Small text.** At 96 px wide, about 12 characters per line at 9 px is the practical limit.
   Bold sans, high contrast, and one idea per screen.

## 2. Reverse-engineering steps for our unit

1. **Identify firmware.** Note the stock firmware version (Ripple vs V0119), and back up the
   factory image as described in `yunzii-al80-unlocked/docs/flashing.md` before any flashing.
2. **Confirm the interface.** On Linux, `lsusb -d 28e9:30af -v` and `/sys/class/hidraw/*`
   should show four HID interfaces. Find the one with usage page `0xFF60`.
3. **Replay a known-safe op.** Send the clock packet (announce `0x40`, time `0x41`, finish
   `0x42`) with hidapi. This checks our checksum code against captures without touching image slots.
4. **Upload one still** (`mockups/native/*.rgb565`) to the picture page with ACK gating. Confirm
   orientation and colour order on the real panel.
5. **Upload one GIF** to the GIF page with the required pacing: 3 s after frame 0 and every
   16th frame, 30 ms after every other frame, and 30 ms between 1024-byte banks.
6. **Open questions worth answering** (help the community too):
   - Does a picture upload write flash? Upload, power-cycle, and see if it survives.
   - What is the real full-screen GIF playback rate?
   - Read the chip markings on the display board.

## 3. What to display

Mockups for each option are in `mockups/`. Regenerate them with
`python3 tools/render_mockups.py`.

### Picture / GIF page (96 × 160)

| # | Option | Refresh need | Fit for this panel |
|---|---|---|---|
| 1 | Clock + weather | 1/min | Good. A 12-hour clock with a weather card, which the stock home page can't do. |
| 2 | System monitor (CPU/GPU/RAM/temp + sparkline) | 1/min | Good, if a minute of latency is OK. |
| 3 | Now playing (art + title + progress) | per track | Very good. Changes rarely, art looks great at 80×80. |
| 4 | Today's agenda | per event | Very good. Changes rarely, high value. |
| 5 | Pomodoro / focus timer | 1/min | OK. Show minutes only, not seconds. |
| 6 | Layer / Fn cheat-sheet | per layer change | Good, but needs custom firmware to push on layer change. |
| 7 | Alert card (CI failed, email, etc.) | on event | Very good. Interrupts the rotation, then returns. |
| 8 | Name badge / logo | never | Zero wear, always works. |
| 9 | Ambient GIF (starfield) | never | Zero wear. Uses the GIF page, up to 160 frames. |

### Main page strip (96 × 64, ≤ 42 frames)

| | Option | Notes |
|---|---|---|
| A | Colour wave | Seamless loop, looks good next to the firmware clock. |
| B | Equaliser bars | Decorative. Can't follow real audio, because the frames are pre-baked. |
| C | Ticker | Weak. 42 frames is too few for smooth scrolling of long text. Prefer a static info card. |

### Recommended setup

- **Home page:** the firmware clock plus the colour-wave strip (A). Set once, no ongoing writes.
- **Picture page:** a host-driven rotation of **agenda → now-playing → clock/weather**, refreshed
  only when the content changes, with **alert cards** preempting it.
- **GIF page:** an ambient loop (starfield or your own pixel art) for when you're away.

Start by uploading the static options (8, 9, A). They test the pipeline without flash-wear worries.
Then build the live host once persistence has been tested.

# How fast the screen can update

Nothing here has been measured on our own keyboard yet. The numbers come from the community
write-ups linked in [PLAN.md](PLAN.md).

## The earlier "once a minute" was caution, not a limit

A full 96 × 160 picture is 551 USB reports. Sent back to back, each waiting only for the keyboard's
acknowledgement, that takes about 0.55 s, plus two fixed pauses the screen needs (0.3 s and 0.03 s).
So the real ceiling is **about one full frame per second**.

The catch is storage. A picture upload stores the picture in one of 16 slots on the screen's own
chip, and stored pictures are meant to survive a power cut, so each upload is very likely a write to
flash memory. Flash wears out. Nobody has published how many writes this chip survives or whether it
spreads them across the slots. Typical flash is rated for 10,000 to 100,000 rewrites per block.

| Refresh | Uploads in an 8-hour day | Days to reach 160,000 uploads | Days to reach 1.6 million |
|---|---|---|---|
| every 1 s | 28,800 | 6 | 56 |
| every 5 s (app default) | 5,760 | 28 | 278 |
| every 30 s | 960 | 167 | 4.6 years |
| every 60 s | 480 | 333 | 9 years |

160,000 is 16 slots × 10,000 rewrites and 1.6 million is 16 slots × 100,000. Both are guesses built
on typical flash, not facts about this chip. Those counts assume a frame is uploaded at every
interval, which is true for the system monitor and far too pessimistic for the clock (one upload a
minute) or a still picture (one upload, ever).

## Ways to update faster

1. **Upload only on change** (built). The app compares each new frame with the one on the screen and
   skips the upload when they match. Panels round to whole percent and whole minutes for this reason.
2. **Home-page gauge** (built, experimental). The home page's battery gauge takes a one-byte value
   in a tiny packet. The app can feed it CPU, GPU or RAM load several times a second, with no picture
   upload at all. Unknown: the keyboard also reports its real battery level, and may overwrite ours.
3. **The firmware clock** (built). The home page counts seconds by itself. The app only resyncs it
   every 10 minutes.
4. **Pre-made animation** (built). A GIF uploaded once plays on the screen by itself at up to 60
   frames a second: 160 frames on the GIF page, 42 in the home-page strip.
5. **Shorter pauses** (not tried). The 0.3 s pause is "what worked", not a measured minimum.
6. **Sending only the changed blocks** (not tried). Each block carries its own position, so a
   partial upload might work. If it does, a small change would take a few milliseconds of data. It
   would still store a picture each time.
7. **Custom keyboard firmware.** The keyboard's main chip can pass a frame to the screen in about a
   third of a second. This removes USB from the path but not the storage write.

No command that draws straight to the screen without storing has been found.

## Two tests to run first on the real keyboard

1. `python -m al80 bench` uploads five solid colours and prints the time for each. This gives our own
   seconds-per-frame figure.
2. Upload a picture, unplug the keyboard, plug it back in, and press Fn+8. If the picture is still
   there, uploads are stored permanently and the wear question is real.

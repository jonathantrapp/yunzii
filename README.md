# yunzii

A local app that drives the YUNZII AL80 keyboard's built-in screen (96 × 160 picture page, 96 × 64 home-page strip).

**Not yet tried on a real keyboard.** The packet code is checked against published captures of the vendor app, and the app has been run end to end against a stand-in device.

## Run it (Windows)

```
pip install -r requirements.txt
start.bat
```

Plug the keyboard in by cable, and close VIA and the YUNZII screen site first. The control panel opens at http://127.0.0.1:7380. To try it with no keyboard: `start.bat --dry-run`.

## What it does

- Shows a system monitor, a clock (with optional weather), a name badge, your own picture, or a rotation of them.
- Uploads a frame only when it changes, no more often than the refresh setting.
- Shows alert cards on request: `python -m al80 alert "Synth done" --level ok`, or `python -m al80 run -- make test` to run a command and show how it ended.
- Sends pictures and GIFs to the picture page, GIF page, home-page strip or startup animation.
- Takes readings, alerts and whole frames from other programs over local HTTP.

`python -m al80 --help` lists the one-shot commands.

## Read next

- [`docs/REFRESH.md`](docs/REFRESH.md): how fast the screen can update, and the storage-wear risk of doing so.
- [`docs/HWSUITE.md`](docs/HWSUITE.md): connecting HwSuite or any other program.
- [`docs/PLAN.md`](docs/PLAN.md): protocol facts, sources, and the first steps on real hardware.

## Layout

- `al80/protocol.py`: packet builders. `al80/device.py`: USB transport. `al80/panels.py`: drawing. `al80/app.py`: engine and web server.
- `tests/`: `python -m unittest discover -s tests`
- `tools/render_mockups.py` and `mockups/`: the first design mockups.

Protocol knowledge comes from [snackdriven/al80-lcd](https://github.com/snackdriven/al80-lcd) and [techno1731/yunzii-al80-unlocked](https://github.com/techno1731/yunzii-al80-unlocked) (MIT). Not affiliated with YUNZII.

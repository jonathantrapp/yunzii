# yunzii

Tools for the YUNZII AL80 keyboard's built-in LCD (96 × 160 RGB565 picture page and 96 × 64 main-page strip).

- [`docs/PLAN.md`](docs/PLAN.md): known protocol facts, reverse-engineering steps, and display plan.
- [`tools/render_mockups.py`](tools/render_mockups.py): renders screen options at native resolution (needs Pillow).
- [`mockups/`](mockups/): rendered output. `native/` holds panel-size PNG/GIF files plus raw `.rgb565` frames, and `preview_*` files are 4× enlargements.

```
python3 tools/render_mockups.py
```

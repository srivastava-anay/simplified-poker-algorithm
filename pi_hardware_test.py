"""DisplayIO smoke test for the Raspberry Pi ILI9341 and three buttons.

Wiring expected:
- ILI9341 SPI0: SCK GPIO11, MOSI GPIO10, MISO GPIO9, CS GPIO8/CE0
- ILI9341 control: DC GPIO25, RST GPIO24, power from 3.3V, common GND
- Buttons: GPIO5, GPIO6, GPIO13 to GND, using internal pull-ups
"""

from __future__ import annotations

import sys
import time

WIDTH = 320
HEIGHT = 240

BUTTONS = {
    "J": 5,
    "K": 6,
    "L": 13,
}

BG = 0x0E1416
PANEL = 0x192427
GREEN = 0x41AF6D
RED = 0xDB4D4D
BLUE = 0x4F9FD8
GOLD = 0xF1C75B
INK = 0xEDF2E8
MUTED = 0x91A0A3


def import_hardware() -> tuple[object, object, object, object, object, object, object]:
    try:
        import board
        import digitalio
        import displayio
        import terminalio
        import adafruit_ili9341
        from adafruit_display_text import label
        from fourwire import FourWire
    except ImportError as exc:
        print("Missing DisplayIO test dependencies.")
        print(f"Python executable: {sys.executable}")
        print(f"Import error: {exc!r}")
        print("Install them on the Pi with:")
        print("  python -m pip install adafruit-blinka-displayio")
        print("  python -m pip install adafruit-circuitpython-ili9341")
        print("  python -m pip install adafruit-circuitpython-display-text")
        raise SystemExit(1) from exc
    return board, digitalio, displayio, terminalio, adafruit_ili9341, label, FourWire


def make_display(
    board: object,
    displayio: object,
    adafruit_ili9341: object,
    FourWire: object,
) -> object:
    displayio.release_displays()
    spi = board.SPI()
    display_bus = FourWire(
        spi,
        command=board.D25,
        chip_select=board.CE0,
        reset=board.D24,
    )
    return adafruit_ili9341.ILI9341(display_bus, width=WIDTH, height=HEIGHT)


def make_buttons(board: object, digitalio: object) -> dict[str, object]:
    buttons = {}
    for label_text, pin in BUTTONS.items():
        button = digitalio.DigitalInOut(getattr(board, f"D{pin}"))
        button.direction = digitalio.Direction.INPUT
        button.pull = digitalio.Pull.UP
        buttons[label_text] = button
    return buttons


def solid_tile(displayio: object, width: int, height: int, color: int) -> object:
    bitmap = displayio.Bitmap(width, height, 1)
    palette = displayio.Palette(1)
    palette[0] = color
    return displayio.TileGrid(bitmap, pixel_shader=palette)


def add_text(
    group: object,
    label_module: object,
    terminalio: object,
    text: str,
    x: int,
    y: int,
    color: int,
    scale: int = 1,
) -> object:
    text_group = type(group)(scale=scale, x=x, y=y)
    text_area = label_module.Label(terminalio.FONT, text=text, color=color)
    text_group.append(text_area)
    group.append(text_group)
    return text_area


def build_scene(
    displayio: object,
    terminalio: object,
    label_module: object,
) -> tuple[object, dict[str, tuple[object, object]]]:
    splash = displayio.Group()

    bg = solid_tile(displayio, WIDTH, HEIGHT, BG)
    splash.append(bg)

    header = solid_tile(displayio, WIDTH, 32, PANEL)
    splash.append(header)
    add_text(splash, label_module, terminalio, "ILI9341 + BUTTON TEST", 10, 10, GOLD, 1)

    inner = solid_tile(displayio, 296, 70, 0x104432)
    inner.x = 12
    inner.y = 44
    splash.append(inner)
    add_text(splash, label_module, terminalio, "Screen OK", 28, 70, INK, 2)
    add_text(splash, label_module, terminalio, "Press J / K / L", 28, 99, MUTED, 1)

    button_widgets: dict[str, tuple[object, object]] = {}
    colors = {"J": RED, "K": GREEN, "L": BLUE}
    y0 = 136
    width = 92
    gap = 10
    for index, key in enumerate(("J", "K", "L")):
        tile = solid_tile(displayio, width, 70, PANEL)
        tile.x = 12 + index * (width + gap)
        tile.y = y0
        splash.append(tile)
        add_text(splash, label_module, terminalio, key, tile.x + 10, y0 + 20, INK, 2)
        state_label = add_text(
            splash,
            label_module,
            terminalio,
            "UP",
            tile.x + 10,
            y0 + 54,
            INK,
            1,
        )
        button_widgets[key] = (tile, state_label)

    return splash, button_widgets


def update_button_widgets(
    button_widgets: dict[str, tuple[object, object]],
    states: dict[str, bool],
) -> None:
    colors = {"J": RED, "K": GREEN, "L": BLUE}
    for key, pressed in states.items():
        tile, state_label = button_widgets[key]
        tile.pixel_shader[0] = colors[key] if pressed else PANEL
        state_label.text = "DOWN" if pressed else "UP"
        state_label.color = BG if pressed else INK


def main() -> int:
    board, digitalio, displayio, terminalio, adafruit_ili9341, label_module, FourWire = (
        import_hardware()
    )
    display = make_display(board, displayio, adafruit_ili9341, FourWire)
    buttons = make_buttons(board, digitalio)
    splash, button_widgets = build_scene(displayio, terminalio, label_module)
    display.root_group = splash

    print("Running DisplayIO ILI9341/button test. Press Ctrl+C to stop.")
    print("Buttons are active-low: GPIO -> button -> GND.")
    print(f"Display size reported by driver: {display.width}x{display.height}")
    for key, pin in BUTTONS.items():
        print(f"  {key}: GPIO{pin}")

    last_states: dict[str, bool] | None = None
    try:
        while True:
            states = {key: not button.value for key, button in buttons.items()}
            if states != last_states:
                parts = [
                    f"{key}={'DOWN' if pressed else 'UP'}"
                    for key, pressed in states.items()
                ]
                print(f"Buttons: {', '.join(parts)}", flush=True)
                update_button_widgets(button_widgets, states)
                last_states = states.copy()
            time.sleep(0.03)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

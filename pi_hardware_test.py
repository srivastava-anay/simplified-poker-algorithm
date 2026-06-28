"""Smoke test for the Raspberry Pi ILI9341 display and three buttons.

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
    "J / LEFT": 5,
    "K / MID": 6,
    "L / RIGHT": 13,
}

BG = (14, 20, 22)
PANEL = (25, 36, 39)
GREEN = (65, 175, 109)
RED = (219, 77, 77)
BLUE = (79, 159, 216)
GOLD = (241, 199, 91)
INK = (237, 242, 232)
MUTED = (145, 160, 163)


def import_hardware() -> tuple[object, object, object, object, object]:
    try:
        import board
        import digitalio
        from adafruit_rgb_display import ili9341
        from gpiozero import Button
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        print("Missing Pi display/button dependencies.")
        print(f"Python executable: {sys.executable}")
        print(f"Import error: {exc!r}")
        print("Install them on the Pi with:")
        print("  python -m pip install adafruit-circuitpython-rgb-display gpiozero pillow")
        raise SystemExit(1) from exc
    return board, digitalio, ili9341, Button, (Image, ImageDraw, ImageFont)


def load_font(image_font: object, size: int) -> object:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for path in candidates:
        try:
            return image_font.truetype(path, size)
        except OSError:
            pass
    return image_font.load_default()


def make_display(board: object, digitalio: object, ili9341: object) -> object:
    cs = digitalio.DigitalInOut(board.CE0)
    dc = digitalio.DigitalInOut(board.D25)
    rst = digitalio.DigitalInOut(board.D24)
    spi = board.SPI()
    return ili9341.ILI9341(
        spi,
        cs=cs,
        dc=dc,
        rst=rst,
        baudrate=32_000_000,
        rotation=90,
    )


def draw_screen(
    display: object,
    image_module: object,
    image_draw: object,
    fonts: dict[str, object],
    button_states: dict[str, bool],
    tick: int,
) -> None:
    image = image_module.new("RGB", (WIDTH, HEIGHT), BG)
    draw = image_draw.Draw(image)

    draw.rectangle((0, 0, WIDTH, 32), fill=PANEL)
    draw.text((10, 7), "ILI9341 + BUTTON TEST", font=fonts["title"], fill=GOLD)
    draw.text((250, 9), f"{tick:04d}", font=fonts["small"], fill=MUTED)

    draw.rectangle((12, 44, WIDTH - 12, 114), outline=GREEN, width=3)
    draw.text((28, 60), "Screen OK", font=fonts["large"], fill=INK)
    draw.text((28, 92), "Press J / K / L", font=fonts["body"], fill=MUTED)

    colors = [RED, GREEN, BLUE]
    y0 = 136
    button_w = 92
    gap = 10
    for index, (label, pressed) in enumerate(button_states.items()):
        x0 = 12 + index * (button_w + gap)
        x1 = x0 + button_w
        fill = colors[index] if pressed else PANEL
        outline = colors[index]
        draw.rectangle((x0, y0, x1, y0 + 70), fill=fill, outline=outline, width=3)
        text_fill = BG if pressed else INK
        state = "DOWN" if pressed else "UP"
        draw.text((x0 + 10, y0 + 12), label.split()[0], font=fonts["large"], fill=text_fill)
        draw.text((x0 + 10, y0 + 44), state, font=fonts["body"], fill=text_fill)

    display.image(image)


def main() -> int:
    board, digitalio, ili9341, Button, pil = import_hardware()
    Image, ImageDraw, ImageFont = pil
    display = make_display(board, digitalio, ili9341)
    buttons = {
        label: Button(pin, pull_up=True, bounce_time=0.03)
        for label, pin in BUTTONS.items()
    }
    fonts = {
        "title": load_font(ImageFont, 17),
        "large": load_font(ImageFont, 24),
        "body": load_font(ImageFont, 16),
        "small": load_font(ImageFont, 13),
    }

    print("Running ILI9341/button test. Press Ctrl+C to stop.")
    print("Buttons are active-low: GPIO -> button -> GND.")
    tick = 0
    try:
        while True:
            states = {label: button.is_pressed for label, button in buttons.items()}
            draw_screen(display, Image, ImageDraw, fonts, states, tick)
            tick += 1
            time.sleep(0.08)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

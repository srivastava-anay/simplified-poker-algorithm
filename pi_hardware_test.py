"""Smoke test for the Raspberry Pi ILI9341 display and three buttons.

Wiring expected:
- ILI9341 SPI0: SCK GPIO11, MOSI GPIO10, MISO GPIO9, CS GPIO8/CE0
- ILI9341 control: DC GPIO25, RST GPIO24, power from 3.3V, common GND
- Buttons: GPIO5, GPIO6, GPIO13 to GND, using internal pull-ups
"""

from __future__ import annotations

import argparse
import sys
import time

WIDTH = 320
HEIGHT = 240
ROTATION = 90

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
NUMPY = None
NUMPY_IMPORT_ATTEMPTED = False


def import_hardware() -> tuple[object, object, object, object]:
    try:
        import board
        import digitalio
        from adafruit_rgb_display import ili9341
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        print("Missing Pi display/button dependencies.")
        print(f"Python executable: {sys.executable}")
        print(f"Import error: {exc!r}")
        print("Install them on the Pi with:")
        print("  python -m pip install adafruit-circuitpython-rgb-display pillow")
        raise SystemExit(1) from exc
    return board, digitalio, ili9341, (Image, ImageDraw, ImageFont)


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
        width=WIDTH,
        height=HEIGHT,
        rotation=ROTATION,
    )


def make_buttons(board: object, digitalio: object) -> dict[str, object]:
    buttons = {}
    for label, pin in BUTTONS.items():
        button = digitalio.DigitalInOut(getattr(board, f"D{pin}"))
        button.direction = digitalio.Direction.INPUT
        button.pull = digitalio.Pull.UP
        buttons[label] = button
    return buttons


def draw_screen(
    display: object,
    image_module: object,
    image_draw: object,
    fonts: dict[str, object],
    button_states: dict[str, bool],
    tick: int,
) -> float:
    width = display.width
    height = display.height
    image = image_module.new("RGB", (width, height), BG)
    draw = image_draw.Draw(image)

    draw.rectangle((0, 0, width, 32), fill=PANEL)
    draw.text((10, 7), "ILI9341 + BUTTON TEST", font=fonts["title"], fill=GOLD)
    draw.text((width - 70, 9), f"{tick:04d}", font=fonts["small"], fill=MUTED)

    draw.rectangle((12, 44, width - 12, 114), outline=GREEN, width=3)
    draw.text((28, 60), "Screen OK", font=fonts["large"], fill=INK)
    draw.text((28, 92), "Press J / K / L", font=fonts["body"], fill=MUTED)

    colors = [RED, GREEN, BLUE]
    y0 = height - 104
    gap = 10
    button_w = max(40, (width - 24 - gap * 2) // 3)
    for index, (label, pressed) in enumerate(button_states.items()):
        x0 = 12 + index * (button_w + gap)
        x1 = x0 + button_w
        fill = colors[index] if pressed else PANEL
        outline = colors[index]
        draw.rectangle((x0, y0, x1, height - 12), fill=fill, outline=outline, width=3)
        text_fill = BG if pressed else INK
        state = "DOWN" if pressed else "UP"
        draw.text((x0 + 10, y0 + 12), label.split()[0], font=fonts["large"], fill=text_fill)
        draw.text((x0 + 10, y0 + 44), state, font=fonts["body"], fill=text_fill)

    start = time.monotonic()
    send_image_fast(display, image)
    return time.monotonic() - start


def rgb888_to_rgb565(image: object) -> bytes:
    global NUMPY, NUMPY_IMPORT_ATTEMPTED
    if not NUMPY_IMPORT_ATTEMPTED:
        NUMPY_IMPORT_ATTEMPTED = True
        try:
            import numpy
        except ImportError:
            NUMPY = None
            print("NumPy not installed; using slow Python RGB565 conversion.")
            print("Install it with: python -m pip install numpy")
        else:
            NUMPY = numpy
    if NUMPY is not None:
        array = NUMPY.asarray(image.convert("RGB"), dtype=NUMPY.uint16)
        red = array[:, :, 0]
        green = array[:, :, 1]
        blue = array[:, :, 2]
        rgb565 = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        return rgb565.byteswap().tobytes()

    data = image.convert("RGB").tobytes()
    output = bytearray(len(data) // 3 * 2)
    out_index = 0
    for index in range(0, len(data), 3):
        red = data[index]
        green = data[index + 1]
        blue = data[index + 2]
        value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        output[out_index] = value >> 8
        output[out_index + 1] = value & 0xFF
        out_index += 2
    return bytes(output)


def send_image_fast(display: object, image: object) -> None:
    block = getattr(display, "_block", None)
    if block is None:
        raise RuntimeError("Display driver does not expose _block for fast transfer")
    width, height = image.size
    block(0, 0, width - 1, height - 1, rgb888_to_rgb565(image))


def send_image(display: object, image: object, image_module: object) -> None:
    transform = getattr(display, "_poker_test_transform", None)
    if transform == "rotate_90":
        display.image(image.transpose(image_module.Transpose.ROTATE_90))
        return
    if transform == "rotate_270":
        display.image(image.transpose(image_module.Transpose.ROTATE_270))
        return

    try:
        display.image(image)
        setattr(display, "_poker_test_transform", "none")
        return
    except ValueError as original_error:
        for name, transpose in (
            ("rotate_90", image_module.Transpose.ROTATE_90),
            ("rotate_270", image_module.Transpose.ROTATE_270),
        ):
            rotated = image.transpose(transpose)
            try:
                display.image(rotated)
                setattr(display, "_poker_test_transform", name)
                print(
                    f"Display accepted {name}: frame {image.size} -> {rotated.size}"
                )
                return
            except ValueError:
                pass
        print(f"Display rejected frame size: {image.size}")
        raise original_error


def benchmark_display(display: object, image_module: object) -> None:
    width = display.width
    height = display.height
    print(f"Benchmarking display path at {width}x{height}")
    print(f"Display class: {type(display)!r}")
    print(f"Configured baudrate: {getattr(display, '_baudrate', 'unknown')}")

    image = image_module.new("RGB", (width, height), (255, 0, 0))
    start = time.monotonic()
    send_image(display, image, image_module)
    normal_elapsed = time.monotonic() - start
    print(f"display.image(PIL RGB full frame): {normal_elapsed:.3f}s")

    block = getattr(display, "_block", None)
    if block is None:
        print("display._block is unavailable; cannot raw-transfer benchmark.")
        return

    start = time.monotonic()
    raw = rgb888_to_rgb565(image)
    convert_elapsed = time.monotonic() - start
    start = time.monotonic()
    block(0, 0, width - 1, height - 1, raw)
    fast_elapsed = time.monotonic() - start
    print(f"custom RGB565 conversion: {convert_elapsed:.3f}s")
    print(f"custom RGB565 _block transfer: {fast_elapsed:.3f}s")

    raw = b"\x07\xe0" * (width * height)
    start = time.monotonic()
    try:
        block(0, 0, width - 1, height - 1, raw)
    except Exception as exc:  # noqa: BLE001 - this is a hardware diagnostic.
        print(f"display._block raw RGB565 failed: {exc!r}")
        return
    raw_elapsed = time.monotonic() - start
    kib = len(raw) / 1024
    print(f"display._block raw RGB565 full frame: {raw_elapsed:.3f}s ({kib:.0f} KiB)")
    if raw_elapsed > 1.0:
        print("Raw SPI transfer is slow; focus on SPI/backend/config.")
    else:
        print("Raw SPI transfer is OK; display.image/Pillow conversion is the bottleneck.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Measure full-frame PIL and raw RGB565 display transfer times.",
    )
    args = parser.parse_args()

    board, digitalio, ili9341, pil = import_hardware()
    Image, ImageDraw, ImageFont = pil
    display = make_display(board, digitalio, ili9341)
    if args.benchmark:
        benchmark_display(display, Image)
        return 0

    buttons = make_buttons(board, digitalio)
    fonts = {
        "title": load_font(ImageFont, 17),
        "large": load_font(ImageFont, 24),
        "body": load_font(ImageFont, 16),
        "small": load_font(ImageFont, 13),
    }

    print("Running ILI9341/button test. Press Ctrl+C to stop.")
    print("Buttons are active-low: GPIO -> button -> GND.")
    print(f"Display size reported by driver: {display.width}x{display.height}")
    print("Watching GPIO button states:")
    for label, pin in BUTTONS.items():
        print(f"  {label}: GPIO{pin}")
    tick = 0
    last_states: dict[str, bool] | None = None
    last_raw_print = 0.0
    last_draw = 0.0
    try:
        while True:
            now = time.monotonic()
            states = {label: not button.value for label, button in buttons.items()}
            state_changed = states != last_states
            if states != last_states:
                parts = [
                    f"{label.split()[0]}={'DOWN' if pressed else 'UP'}"
                    for label, pressed in states.items()
                ]
                print(f"Buttons: {', '.join(parts)}", flush=True)
                last_states = states.copy()
            if now - last_raw_print >= 1.0:
                raw_parts = [
                    f"GPIO{BUTTONS[label]}={'LOW' if not button.value else 'HIGH'}"
                    for label, button in buttons.items()
                ]
                print(f"Raw pins: {', '.join(raw_parts)}", flush=True)
                last_raw_print = now
            if state_changed or now - last_draw >= 1.0:
                elapsed = draw_screen(display, Image, ImageDraw, fonts, states, tick)
                print(f"Display update: {elapsed:.2f}s", flush=True)
                tick += 1
                last_draw = time.monotonic()
            time.sleep(0.03)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

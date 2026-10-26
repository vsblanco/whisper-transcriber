"""Draw the app icon (a rounded blue square with a sound wave) as icon.ico and icon.icns."""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent


def draw(size: int) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([s * 0.04, s * 0.04, s * 0.96, s * 0.96], radius=s * 0.22, fill=(47, 111, 191, 255))
    bars = [0.30, 0.55, 0.85, 0.60, 0.40, 0.70, 0.35]
    w = s * 0.075
    gap = s * 0.035
    total = len(bars) * w + (len(bars) - 1) * gap
    x = (s - total) / 2
    for h in bars:
        hh = s * 0.55 * h
        d.rounded_rectangle([x, s / 2 - hh / 2, x + w, s / 2 + hh / 2], radius=w / 2, fill=(255, 255, 255, 255))
        x += w + gap
    return img


if __name__ == "__main__":
    big = draw(1024)
    big.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(HERE / "icon.icns")
    big.resize((256, 256), Image.LANCZOS).save(HERE / "icon.png")
    print("icons written")

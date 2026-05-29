"""Generate the Knight Rider launcher icon.

Design: KITT's red "Larson scanner" bar — a row of segmented red cells,
white-hot in the centre, fading to deep red at the edges, with a soft glow —
sitting on the app's Tesla-dark tile (#0A0A0B). Instantly reads as Knight
Rider and matches the in-app accent (#C8102E).

Emits two PNGs into assets/icon/:
  - icon.png            1024² full-bleed (legacy Android + iOS)
  - icon_foreground.png 1024² transparent, content kept inside the adaptive
                        safe zone (Android adaptive foreground)

Run:  python tool/gen_icon.py
"""
import os
from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024
BG = (10, 10, 11, 255)        # #0A0A0B app background
DIM = (70, 8, 16)             # deep red, edge cells
HOT = (255, 70, 70)           # near white-hot core
ACCENT = (200, 16, 46)        # #C8102E app accent (for glow)

OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "icon")
os.makedirs(OUT, exist_ok=True)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def draw_scanner(canvas_size, bar_w_frac):
    """Return an RGBA layer (transparent) with the scanner bar centred."""
    layer = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))

    cells = 9
    bar_w = canvas_size * bar_w_frac
    gap = bar_w * 0.018
    cell_w = (bar_w - gap * (cells - 1)) / cells
    cell_h = cell_w * 2.6
    radius = cell_w * 0.42

    x0 = (canvas_size - bar_w) / 2
    y0 = (canvas_size - cell_h) / 2

    # Glow layer (drawn fat + blurred, behind the crisp cells).
    glow = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    mid = (cells - 1) / 2
    for i in range(cells):
        t = 1.0 - abs(i - mid) / mid          # 1 at centre, 0 at edges
        cx = x0 + i * (cell_w + gap)
        # brighter, fatter glow toward the centre
        pad = cell_w * (0.6 + 0.5 * t)
        alpha = int(40 + 150 * t)
        gd.rounded_rectangle(
            [cx - pad, y0 - pad, cx + cell_w + pad, y0 + cell_h + pad],
            radius=radius + pad,
            fill=(ACCENT[0], ACCENT[1], ACCENT[2], alpha),
        )
    glow = glow.filter(ImageFilter.GaussianBlur(canvas_size * 0.035))
    layer = Image.alpha_composite(layer, glow)

    # Crisp cells on top.
    d = ImageDraw.Draw(layer)
    for i in range(cells):
        t = 1.0 - abs(i - mid) / mid
        # ease so the centre stays hot and edges drop off fast
        t = t ** 1.6
        col = lerp(DIM, HOT, t)
        cx = x0 + i * (cell_w + gap)
        d.rounded_rectangle(
            [cx, y0, cx + cell_w, y0 + cell_h],
            radius=radius,
            fill=(col[0], col[1], col[2], 255),
        )
    return layer


def build_full():
    img = Image.new("RGBA", (SIZE, SIZE), BG)
    # subtle radial vignette: darker corners
    vig = Image.new("L", (SIZE, SIZE), 0)
    vd = ImageDraw.Draw(vig)
    vd.ellipse([-SIZE * 0.2, -SIZE * 0.2, SIZE * 1.2, SIZE * 1.2], fill=60)
    vig = vig.filter(ImageFilter.GaussianBlur(SIZE * 0.12))
    glowtile = Image.new("RGBA", (SIZE, SIZE), (ACCENT[0], ACCENT[1], ACCENT[2], 255))
    img = Image.composite(glowtile, img, vig.point(lambda p: int(p * 0.25)))
    img = Image.alpha_composite(img, draw_scanner(SIZE, 0.74))
    img.save(os.path.join(OUT, "icon.png"))
    print("wrote icon.png")


def build_foreground():
    # Transparent; keep scanner well inside the adaptive safe zone (~60%).
    fg = draw_scanner(SIZE, 0.56)
    fg.save(os.path.join(OUT, "icon_foreground.png"))
    print("wrote icon_foreground.png")


if __name__ == "__main__":
    build_full()
    build_foreground()

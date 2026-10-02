"""Generate Instagram highlight covers (1080x1920) in the KhabarBawaal style.

Everything sits inside the centre circle so Instagram's round crop keeps it.
    python make_highlights.py   ->  assets/highlights/*.png
"""
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text())
YELLOW, RED, BG, WHITE = CONFIG["accent_color"], CONFIG["tag_color"], "#0d0d0d", "#ffffff"
W, H = 1080, 1920
CX, CY, R = W // 2, H // 2, 430
S = 4  # icons are drawn at 4x and scaled down for smooth edges
ICON = 360


def icon_canvas():
    img = Image.new("RGBA", (ICON * S, ICON * S), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def bezier(p0, p1, p2, p3, n=40):
    return [(
        (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t ** 2 * p2[0] + t ** 3 * p3[0],
        (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t ** 2 * p2[1] + t ** 3 * p3[1],
    ) for t in (i / n for i in range(n + 1))]


def flame(scale, ox, oy):
    """Flame outline in a 100x100 box, mapped to pixels."""
    m = lambda x, y: (ox + x * scale, oy + y * scale)
    pts = []
    pts += bezier(m(50, 100), m(18, 100), m(8, 72), m(22, 50))
    pts += bezier(m(22, 50), m(30, 62), m(36, 60), m(36, 52))
    pts += bezier(m(36, 52), m(32, 30), m(48, 12), m(56, 0))
    pts += bezier(m(56, 0), m(60, 22), m(92, 40), m(88, 70))
    pts += bezier(m(88, 70), m(86, 90), m(70, 100), m(50, 100))
    return pts


def icon_viral():
    img, d = icon_canvas()
    u = ICON * S / 100
    d.polygon(flame(u * 0.9, u * 5, u * 2), fill=YELLOW)
    d.polygon(flame(u * 0.45, u * 28, u * 47), fill=RED)
    return img


def icon_politics():
    img, d = icon_canvas()
    u = ICON * S / 100
    d.polygon([(50 * u, 6 * u), (8 * u, 30 * u), (92 * u, 30 * u)], fill=YELLOW)           # roof
    d.rectangle((12 * u, 32 * u, 88 * u, 38 * u), fill=YELLOW)                              # beam
    for x in (16, 34, 52, 70):
        d.rectangle(((x + 1) * u, 41 * u, (x + 11) * u, 80 * u), fill=YELLOW)               # columns
    d.rectangle((10 * u, 83 * u, 90 * u, 89 * u), fill=YELLOW)                              # step
    d.rectangle((4 * u, 91 * u, 96 * u, 97 * u), fill=YELLOW)                               # base
    d.ellipse((44 * u, 15 * u, 56 * u, 27 * u), fill=RED)                                   # emblem
    return img


def icon_cricket():
    img, d = icon_canvas()
    u = ICON * S / 100
    bat = Image.new("RGBA", img.size, (0, 0, 0, 0))
    b = ImageDraw.Draw(bat)
    b.rounded_rectangle((40 * u, 30 * u, 60 * u, 96 * u), radius=6 * u, fill=YELLOW)        # blade
    b.rounded_rectangle((46.5 * u, 4 * u, 53.5 * u, 32 * u), radius=3 * u, fill=YELLOW)     # handle
    for y in (8, 14, 20, 26):
        b.rectangle((46.5 * u, y * u, 53.5 * u, (y + 2) * u), fill=BG)                      # grip
    bat = bat.rotate(-35, resample=Image.BICUBIC, center=(50 * u, 55 * u))
    img.alpha_composite(bat)
    d.ellipse((66 * u, 66 * u, 92 * u, 92 * u), fill=RED)                                   # ball
    d.arc((70 * u, 66 * u, 98 * u, 94 * u), 125, 235, fill=WHITE, width=int(1.6 * u))       # seam
    return img


def icon_bollywood():
    img, d = icon_canvas()
    u = ICON * S / 100
    d.rounded_rectangle((8 * u, 40 * u, 92 * u, 92 * u), radius=5 * u, fill=YELLOW)         # board
    clap = Image.new("RGBA", img.size, (0, 0, 0, 0))
    c = ImageDraw.Draw(clap)
    c.rectangle((8 * u, 24 * u, 92 * u, 36 * u), fill=YELLOW)                               # clapper
    for x in range(10, 92, 16):
        c.polygon([(x * u, 36 * u), ((x + 8) * u, 36 * u), ((x + 14) * u, 24 * u), ((x + 6) * u, 24 * u)], fill=BG)
    clap = clap.rotate(14, resample=Image.BICUBIC, center=(8 * u, 36 * u))
    img.alpha_composite(clap)
    d.rectangle((8 * u, 40 * u, 92 * u, 52 * u), fill=YELLOW)
    for x in range(10, 92, 16):
        d.polygon([(x * u, 52 * u), ((x + 8) * u, 52 * u), ((x + 14) * u, 40 * u), ((x + 6) * u, 40 * u)], fill=BG)
    d.polygon([(42 * u, 60 * u), (42 * u, 84 * u), (62 * u, 72 * u)], fill=RED)             # play
    return img


def cover(label: str, icon: Image.Image) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.ellipse((CX - R, CY - R, CX + R, CY + R), outline=YELLOW, width=16)
    icon = icon.resize((ICON, ICON), Image.LANCZOS)
    img.paste(icon, (CX - ICON // 2, CY - ICON // 2 - 90), icon)
    fnt = ImageFont.truetype(str(ROOT / "assets" / "fonts" / "Anton-Regular.ttf"), 120)
    d.text((CX, CY + 205), label.upper(), font=fnt, fill=WHITE, anchor="mm")
    d.rectangle((CX - 90, CY + 290, CX + 90, CY + 302), fill=RED)
    return img


if __name__ == "__main__":
    out = ROOT / "assets" / "highlights"
    out.mkdir(parents=True, exist_ok=True)
    covers = {"Viral": icon_viral(), "Politics": icon_politics(),
              "Cricket": icon_cricket(), "Bollywood": icon_bollywood()}
    for i, (label, icon) in enumerate(covers.items(), 1):
        cover(label, icon).save(out / f"{i}-{label.lower()}.png")
    # Preview sheet, cropped to the circle area like Instagram shows it.
    sheet = Image.new("RGB", (4 * 460 + 60, 520), "#222222")
    for i, label in enumerate(covers):
        c = Image.open(out / f"{i + 1}-{label.lower()}.png").crop((CX - R - 10, CY - R - 10, CX + R + 10, CY + R + 10))
        c = c.resize((440, 440))
        mask = Image.new("L", c.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 439, 439), fill=255)
        sheet.paste(c, (30 + i * 460, 40), mask)
    sheet.save(out / "_preview.png")
    print(f"Saved {len(covers)} covers to {out}")

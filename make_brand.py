"""Generate assets/logo.png (card watermark) and the app icons from config.json.

Replace assets/logo.png with your own logo any time; re-run this only if you
change page_name and want new default artwork.
"""
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text())
FONT = str(ROOT / "assets" / "fonts" / "Anton-Regular.ttf")
ACCENT = CONFIG["accent_color"]
RED = CONFIG["tag_color"]

# "FilmyKhabar" -> ["FILMY", "KHABAR"]; colour the two halves differently.
parts = [p.upper() for p in re.findall(r"[A-Z0-9]?[a-z0-9]+|[A-Z0-9]+(?![a-z])", CONFIG["page_name"])] \
    or [CONFIG["page_name"].upper()]
first, rest = parts[0], "".join(parts[1:])


def logo() -> None:
    fnt = ImageFont.truetype(FONT, 150)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    w1, w2 = probe.textlength(first, font=fnt), probe.textlength(rest, font=fnt)
    pad = 50
    img = Image.new("RGBA", (int(w1 + w2 + pad * 2 + 10), 230), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=40, fill=(0, 0, 0, 200))
    d.rectangle((pad, img.height - 34, img.width - pad, img.height - 24), fill=RED)
    d.text((pad, 18), first, font=fnt, fill="white")
    d.text((pad + w1 + 10, 18), rest, font=fnt, fill=ACCENT)
    img.save(ROOT / "assets" / "logo.png")


def icon(size: int, path: Path) -> None:
    img = Image.new("RGB", (size, size), "#0d0d0d")
    d = ImageDraw.Draw(img)
    initials = (first[:1] + (rest[:1] or first[1:2])).upper()
    fnt = ImageFont.truetype(FONT, int(size * 0.55))
    d.text((size / 2, size * 0.47), initials, font=fnt, fill=ACCENT, anchor="mm")
    d.rectangle((size * 0.2, size * 0.8, size * 0.8, size * 0.84), fill=RED)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


if __name__ == "__main__":
    logo()
    icons = ROOT / "docs" / "icons"
    icon(180, icons / "apple-touch-icon.png")
    icon(192, icons / "icon-192.png")
    icon(512, icons / "icon-512.png")
    manifest = {
        "name": f"{CONFIG['page_name']} Studio",
        "short_name": f"{CONFIG['page_name']}",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": "#0d0d0d",
        "theme_color": "#0d0d0d",
        "icons": [
            {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    (ROOT / "docs" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("Wrote assets/logo.png, docs/icons/* and docs/manifest.json")

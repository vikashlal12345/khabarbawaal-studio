"""5-second "Tag that friend" Reel: 1080x1920 video with moving text, no music
(the owner adds a trending song in Instagram).

    render(post, theme_index, out_mp4, out_cover)   post = {"card_text", "highlight"}

Text stays inside Instagram's safe zone (Reels buttons cover the bottom and right edge).
"""
from __future__ import annotations

import os
import shutil
import subprocess

from PIL import Image, ImageDraw

from generate import CONFIG, FUN_THEMES, LOGO_FILE, font, norm, printable, wrap_words

RW, RH, FPS, SECONDS = 1080, 1920, 30, 5
LEFT, RIGHT = 80, RW - 150              # right side holds Instagram's like/comment buttons
TEXT_TOP, TEXT_BOTTOM = 560, 1420
HOOK_Y, FOOT_Y = 360, 1540


def ffmpeg_exe() -> str:
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg   # local Mac testing: pip install imageio-ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def ease_out_back(t: float) -> float:
    """0 -> 1 with a small overshoot (a 'pop')."""
    t = max(0.0, min(1.0, t))
    c = 1.70158
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def text_layer(lines: list[list[str]], fnt, line_h: int, fg: str, hi: str, highlights: set[str]) -> list[Image.Image]:
    """One transparent image per text line (so each line can pop in on its own)."""
    out = []
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    space = probe.textlength(" ", font=fnt)
    for line in lines:
        w = int(sum(probe.textlength(w_, font=fnt) for w_ in line) + space * (len(line) - 1)) + 8
        img = Image.new("RGBA", (w, line_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        x = 0
        for word in line:
            d.text((x, 0), word, font=fnt, fill=hi if norm(word) in highlights else fg)
            x += d.textlength(word, font=fnt) + space
        out.append(img)
    return out


def pill(text: str, fnt, bg: str, fg: str, pad=(36, 18), arrow: bool = False) -> Image.Image:
    """Rounded label; arrow=True adds a hand-drawn down arrow (the fonts have no arrow glyph)."""
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = probe.textlength(text, font=fnt)
    asc, desc = fnt.getmetrics()
    aw = int(asc * 0.8) if arrow else 0
    img = Image.new("RGBA", (int(tw + pad[0] * 2 + (aw + 24 if arrow else 0)), asc + desc + pad[1] * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=18, fill=bg)
    d.text((pad[0], pad[1]), text, font=fnt, fill=fg)
    if arrow:
        x, cy = pad[0] + tw + 24, img.height // 2
        d.polygon([(x, cy - aw * 0.25), (x + aw, cy - aw * 0.25), (x + aw / 2, cy + aw * 0.45)], fill=fg)
    return img


def paste_scaled(frame: Image.Image, layer: Image.Image, x: int, y: int, scale: float, alpha: float) -> None:
    """Paste layer centred on its normal box, scaled and faded."""
    if alpha <= 0 or scale <= 0.01:
        return
    w, h = max(1, int(layer.width * scale)), max(1, int(layer.height * scale))
    img = layer.resize((w, h), Image.BICUBIC) if (w, h) != layer.size else layer
    if alpha < 1:
        img = img.copy()
        img.putalpha(img.getchannel("A").point(lambda a: int(a * alpha)))
    frame.alpha_composite(img, (int(x + (layer.width - w) / 2), int(y + (layer.height - h) / 2)))


def render(post: dict, theme_index: int, out_mp4: str, out_cover: str) -> None:
    bg, pattern, fg, hi, tag_bg, tag_fg = FUN_THEMES[theme_index % len(FUN_THEMES)]

    # Background: faint "HAHA" wallpaper, taller than the screen so it can drift slowly.
    wall = Image.new("RGBA", (RW, RH + 400), bg)
    d = ImageDraw.Draw(wall)
    pf = font("Anton-Regular.ttf", 150)
    for row, y in enumerate(range(-40, wall.height, 175)):
        x = -90 * (row % 3)
        while x < RW:
            d.text((x, y), "HAHA", font=pf, fill=pattern)
            x += d.textlength("HAHA  ", font=pf)
    logo = None
    if LOGO_FILE.exists():
        logo = Image.open(LOGO_FILE).convert("RGBA")
        logo.thumbnail((420, 96))

    # Main text: writer's line breaks kept, wrapped and shrunk to fit the safe zone.
    paragraphs = [p.split() for p in printable(post["card_text"]).split("\n") if p.strip()]
    highlights = {norm(w) for phrase in post.get("highlight", []) for w in phrase.split()}
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    for size in range(88, 44, -4):
        fnt = font("Poppins-Bold.ttf", size)
        line_h, gap = int(size * 1.3), int(size * 0.5)
        blocks = [wrap_words(p, fnt, RIGHT - LEFT, probe) for p in paragraphs]
        total = sum(len(b) * line_h for b in blocks) + gap * (len(blocks) - 1)
        orphan = any(len(b) > 1 and len(b[-1]) == 1 for b in blocks)   # one word alone on a line
        if total <= TEXT_BOTTOM - TEXT_TOP and not orphan:
            break
    lines, ys, y = [], [], TEXT_TOP + (TEXT_BOTTOM - TEXT_TOP - total) // 2
    for block in blocks:
        for line in block:
            lines.append(line); ys.append(y); y += line_h
        y += gap
    line_imgs = text_layer(lines, fnt, line_h, fg, hi, highlights)

    hook = pill("TAG THAT FRIEND", font("Anton-Regular.ttf", 92), tag_bg, tag_fg, pad=(40, 14))
    foot = pill("TAG KARO USKO", font("Poppins-Bold.ttf", 60), tag_bg, tag_fg, pad=(44, 22), arrow=True)
    handle = font("Poppins-SemiBold.ttf", 42)

    # Timeline (seconds): hook drops 0-0.5, lines pop one by one 0.5-2.9,
    # highlighted words pulse ~3.2, footer slides up 3.6, then hold (Reels loop).
    n = len(lines)
    step = min(0.55, 2.4 / max(n, 1))
    ffmpeg = subprocess.Popen(
        [ffmpeg_exe(), "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{RW}x{RH}", "-r", str(FPS), "-i", "-",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",   # silent track: some apps reject video-only files
         "-shortest", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-profile:v", "high", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", out_mp4],
        stdin=subprocess.PIPE)
    cover = None
    for i in range(FPS * SECONDS):
        t = i / FPS
        frame = wall.crop((0, int(t * 40), RW, int(t * 40) + RH))   # slow upward drift
        if logo:
            frame.alpha_composite(logo, (60, 150))

        k = ease_out_back(t / 0.5)
        frame.alpha_composite(hook, ((RW - hook.width) // 2, int(HOOK_Y - 260 * (1 - k))))

        for j, (img, ly) in enumerate(zip(line_imgs, ys)):
            p = (t - 0.5 - j * step) / 0.35
            pulse = 1.0
            if any(norm(w) in highlights for w in lines[j]) and 3.1 < t < 3.5:
                pulse = 1 + 0.06 * (1 - abs((t - 3.3) / 0.2))
            paste_scaled(frame, img, LEFT, ly, (0.6 + 0.4 * ease_out_back(p)) * pulse, ease_out(p * 1.5))

        k = ease_out((t - 3.6) / 0.4)
        if k > 0:
            fy = int(FOOT_Y + 200 * (1 - k))
            frame.alpha_composite(foot, ((RW - foot.width) // 2, fy))
            ImageDraw.Draw(frame).text((RW // 2, fy + foot.height + 50), CONFIG["handle"],
                                       font=handle, fill=fg, anchor="mm")
        rgb = frame.convert("RGB")
        if i == FPS * SECONDS - 1:
            cover = rgb
        ffmpeg.stdin.write(rgb.tobytes())
    ffmpeg.stdin.close()
    if ffmpeg.wait() != 0:
        raise RuntimeError("ffmpeg failed")
    cover.save(out_cover, "JPEG", quality=88, optimize=True)

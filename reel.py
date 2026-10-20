"""Reels: 1080x1920 videos, no music (the owner adds a trending song in Instagram).

    make(kind, state, feed)   the robot's entry (all 9 daily posts are Reels, schedule.SPECIALS): reel_devotion,
                              reel_top5_am / reel_top5 (Top 5 in ~30 s), reel_clip_am / reel_clip "Tag that friend",
                              reel_news_am / reel_news_pm / reel_news (news slideshows), reel_politics, reel_meme

Three styles (each writes the MP4 and returns still frames for the proofreader):
    clip_reel(clip, start, dur, hook, text, highlight, out_mp4)
        real video clip filling the screen, outlined meme text popping in on top
    meme_reel(clip, start, dur, setup, punch, punch_at, highlight, out_mp4, hold)
        classic meme layout: white screen, text above the clip; setup text counts up,
        punchline swaps in at punch_at seconds; a short clip freezes on its last frame for hold seconds
    news_reel(slides, end_text, highlight, tag, out_mp4)
        news photos zooming/sliding with story lines, ending on a punchline card
        slides = [(photo, credit, text), ...]

Clips come from Mixkit (no sign-up); only clips under its Free licence are used. Text stays inside Instagram's safe
zone (Reels buttons cover the bottom and the right edge).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from generate import CONFIG, LOGO_FILE, RIGHT_LENS, font, norm, printable, wrap_words

RW, RH, FPS = 1080, 1920, 30
LEFT, RIGHT = 70, RW - 150              # right side holds Instagram's like/comment buttons
YELLOW, RED = CONFIG["accent_color"], CONFIG["tag_color"]


def ffmpeg_exe() -> str:
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg   # local Mac testing: pip install imageio-ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ---------------------------------------------------------------- video in / out

def has_audio(path: str) -> bool:
    return "Audio:" in subprocess.run([ffmpeg_exe(), "-i", path], capture_output=True, text=True).stderr


class Encoder:
    """Feed PIL frames in, get an Instagram-friendly MP4 (H.264 + audio: the source clip's own
    sound when audio_from is given and has sound, else a silent track)."""

    def __init__(self, out_mp4: str, audio_from: str | None = None, audio_start: float = 0.0,
                 size: tuple[int, int] = (RW, RH)):
        sound = (["-ss", str(audio_start), "-i", audio_from] if audio_from and has_audio(audio_from)
                 else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])   # some apps reject video-only files
        self.p = subprocess.Popen(
            [ffmpeg_exe(), "-y", "-loglevel", "error",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", str(FPS), "-i", "-",
             *sound, "-map", "0:v", "-map", "1:a",
             "-shortest", "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
             "-profile:v", "high", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out_mp4],
            stdin=subprocess.PIPE)

    def add(self, frame: Image.Image) -> None:
        self.p.stdin.write(frame.convert("RGB").tobytes())

    def close(self) -> None:
        self.p.stdin.close()
        if self.p.wait() != 0:
            raise RuntimeError("ffmpeg failed")


def clip_frames(path: str, start: float, dur: float, w: int, h: int, crop_x: float = 0.5, hold: float = 0.0):
    """Frames of a clip, scaled to cover w x h (crop centred at crop_x of the width), 30 fps;
    hold = seconds the last frame stays on after the clip (a clip shorter than the text needs)."""
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
          f"crop={w}:{h}:(iw-{w})*{crop_x}:(ih-{h})/2,fps={FPS}")
    if hold > 0:
        vf += f",tpad=stop_mode=clone:stop_duration={hold:.2f}"
    p = subprocess.Popen([ffmpeg_exe(), "-loglevel", "error", "-ss", str(start), "-t", str(dur), "-i", path,
                          "-vf", vf, "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size = w * h * 3
    while True:
        raw = p.stdout.read(size)
        if len(raw) < size:
            break
        yield Image.frombytes("RGB", (w, h), raw)
    p.wait()


def video_stills(path: str, start: float, secs: float, width: int = 960):
    """Whole frames of a video (no crop), `width` px wide, 30 fps: the official video under a news hook."""
    w, h, _ = video_info(path)
    h2 = int(h * width / w) // 2 * 2
    p = subprocess.Popen([ffmpeg_exe(), "-loglevel", "error", "-ss", str(start), "-t", str(secs), "-i", path,
                          "-vf", f"scale={width}:{h2},fps={FPS}", "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    size = width * h2 * 3
    while True:
        raw = p.stdout.read(size)
        if len(raw) < size:
            break
        yield Image.frombytes("RGB", (width, h2), raw)
    p.wait()


def video_info(path: str) -> tuple[int, int, float]:
    """(width, height, seconds) as shown (phone videos are rotated by ffmpeg automatically)."""
    import re
    info = subprocess.run([ffmpeg_exe(), "-i", path], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info)
    secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    size = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", info)
    w, h = (int(size.group(1)), int(size.group(2))) if size else (RW, RH)
    if re.search(r"rotation of -?90|rotate\s*:\s*-?90|displaymatrix: rotation of -?90", info):
        w, h = h, w
    return w, h, secs


# ---------------------------------------------------------------- drawing helpers

def ease_out_back(t: float) -> float:
    """0 -> 1 with a small overshoot (a 'pop')."""
    t = max(0.0, min(1.0, t))
    c = 1.70158
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def fit_lines(text: str, max_w: int, max_h: int, sizes=range(88, 40, -4), name="Poppins-Bold.ttf"):
    """Wrap text (keeping its line breaks) at the biggest size that fits; a lone last word
    gets company from the line above."""
    paragraphs = [p.split() for p in printable(text).split("\n") if p.strip()]
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    for size in sizes:
        fnt = font(name, size)
        line_h = int(size * 1.28)
        blocks = [wrap_words(p, fnt, max_w, probe) for p in paragraphs]
        for b in blocks:
            if len(b) > 1 and len(b[-1]) == 1 and len(b[-2]) > 2:
                b[-1].insert(0, b[-2].pop())
        lines = [line for b in blocks for line in b]
        if len(lines) * line_h <= max_h:
            break
    return lines, fnt, line_h


def line_images(lines, fnt, line_h, fg, hi, highlights, stroke=0, stroke_fill="#000000"):
    """One transparent image per line (so each line can pop in on its own)."""
    out = []
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    space = probe.textlength(" ", font=fnt)
    for line in lines:
        w = int(sum(probe.textlength(x, font=fnt) for x in line) + space * (len(line) - 1)) + 2 * stroke + 8
        img = Image.new("RGBA", (w, line_h + 2 * stroke), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        x = stroke
        for word in line:
            d.text((x, stroke), word, font=fnt, fill=hi if norm(word) in highlights else fg,
                   stroke_width=stroke, stroke_fill=stroke_fill)
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


def paste_pop(frame: Image.Image, layer: Image.Image, x: int, y: int, p: float) -> None:
    """Paste layer at (x, y) popping in: p goes 0 -> 1 (scale 0.6 -> 1 with overshoot, fade in)."""
    if p <= 0:
        return
    scale, alpha = 0.6 + 0.4 * ease_out_back(p), ease_out(p * 1.5)
    w, h = max(1, int(layer.width * scale)), max(1, int(layer.height * scale))
    img = layer.resize((w, h), Image.BICUBIC) if (w, h) != layer.size else layer
    if alpha < 1:
        img = img.copy()
        img.putalpha(img.getchannel("A").point(lambda a: int(a * alpha)))
    frame.alpha_composite(img, (int(x + (layer.width - w) / 2), int(y + (layer.height - h) / 2)))


def logo_img(max_w=360, max_h=84):
    if not LOGO_FILE.exists():
        return None
    logo = Image.open(LOGO_FILE).convert("RGBA")
    logo.thumbnail((max_w, max_h))
    return logo


def handle_tag(frame: Image.Image, y: int, fill="#FFFFFF", stroke=3) -> None:
    ImageDraw.Draw(frame).text((RW // 2, y), CONFIG["handle"], font=font("Poppins-SemiBold.ttf", 40),
                               fill=fill, anchor="mm", stroke_width=stroke, stroke_fill="#000000")


HOOK_SECS, FOLLOW_SECS, COVER_SECS = 1.8, 1.5, 2.2


def hook_layer(text: str, highlight: set[str]) -> Image.Image:
    """🪝 The opening line (owner, 14 Oct 2026: viewers decide in under a second; our Reels used to open on an
    empty text area and the logo): 4-8 words, big and bold, white with a thick outline, yellow highlights."""
    # max 500 px tall: a 4-line hook shrinks a little instead of covering @khabarbawaal (owner, 15 Oct 2026)
    lines, fnt, lh = fit_lines(printable(text), RIGHT - LEFT + 60, 500, sizes=range(118, 64, -4), name="Anton-Regular.ttf")
    imgs = line_images(lines, fnt, lh, "#FFFFFF", YELLOW, highlight, stroke=9)
    layer = Image.new("RGBA", (RW, len(imgs) * lh + 80), (0, 0, 0, 0))
    for j, img in enumerate(imgs):
        layer.alpha_composite(img, ((RW - img.width) // 2, 40 + j * lh))
    return layer


COVER_PINK = "#FF3D8B"


def auto_highlight(text: str) -> set[str]:
    """Names in the hook (Capitalised words after the first, and the first word if it is a name-like word of 4+
    letters) get the pink colour (owner's own posts: 'Kriti Sanon ka palatwaar', pink on the name)."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]+", text)
    out = {norm(w) for w in words[1:] if w[:1].isupper() and len(w) > 2}
    if words and words[0][:1].isupper() and len(words[0]) > 3:
        out.add(norm(words[0]))
    return out


def cover_hook_frame(photo: Image.Image, hook: str, tag: str, credit: str, t: float, highlight: set[str] | None = None,
                     dur: float = 2.5) -> Image.Image:
    """⭐ The standard opening of every photo Reel (owner, 19 Oct 2026: his own posts, big photo + big hook, got 14.4K
    and 6.4K views): ONE sharp photo fills the screen (slow zoom), a dark fade at the bottom, a huge Anton hook with
    the names in pink, a red tag badge top-left, the photo credit and the handle."""
    fx, fy = photo.info.get("focus", (0.5, None))   # where the person's face is (pick_cover_photo), 0-1
    photo = photo.convert("RGB")
    z = 1.0 + 0.06 * min(1.0, t / dur)
    k = max(RW / photo.width, RH / photo.height) * z
    big = photo.resize((int(photo.width * k) + 1, int(photo.height * k) + 1), Image.BICUBIC)
    x0 = max(0, min(big.width - RW, int(fx * big.width - RW / 2)))   # centre the crop on the face
    y0 = int((big.height - RH) * 0.28) if fy is None else max(0, min(big.height - RH, int(fy * big.height - RH * 0.38)))
    frame = big.crop((x0, y0, x0 + RW, y0 + RH)).convert("RGBA")
    shade = Image.new("RGBA", (RW, 900), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(900):
        sd.line((0, y, RW, y), fill=(0, 0, 0, int(235 * (y / 900) ** 1.5)))
    frame.alpha_composite(shade, (0, RH - 900))
    lines, fnt, lh = fit_lines(printable(hook), RIGHT - LEFT + 60, 560, sizes=range(118, 64, -4), name="Anton-Regular.ttf")
    imgs = line_images(lines, fnt, lh, "#FFFFFF", COVER_PINK, highlight if highlight is not None else auto_highlight(hook),
                       stroke=9)
    y = RH - 330 - len(imgs) * lh
    for j, img in enumerate(imgs):
        frame.alpha_composite(img, ((RW - img.width) // 2, y + j * lh))
    if tag:
        frame.alpha_composite(pill(printable(tag).upper(), font("Anton-Regular.ttf", 56), RED, "#FFFFFF", pad=(30, 8)), (50, 150))
    if credit:
        ImageDraw.Draw(frame).text((50, RH - 120), printable(credit)[:60], font=font("Poppins-SemiBold.ttf", 24), fill="#CCCCCC")
    handle_tag(frame, 1790)
    return frame


def hook_frame(photo: Image.Image, layer: Image.Image, t: float) -> Image.Image:
    """The hook screen at time t: the whole photo (news photos are wide) in a window over a blurred copy of itself
    with a quick punch-in zoom, the big hook line under it from the very first frame, no logo or label yet."""
    photo = photo.convert("RGB")
    k = max(RW / photo.width, RH / photo.height)
    bg = photo.resize((int(photo.width * k) + 1, int(photo.height * k) + 1)).crop((0, 0, RW, RH))
    frame = Image.blend(bg.filter(ImageFilter.GaussianBlur(50)), Image.new("RGB", (RW, RH), "#000000"), 0.75).convert("RGBA")
    win_h = 820
    z = max(RW / photo.width, win_h / photo.height) * (1.12 - 0.12 * ease_out(min(1.0, t / 0.45)))
    big = photo.resize((int(photo.width * z) + 1, int(photo.height * z) + 1), Image.BICUBIC)
    x0, y0 = (big.width - RW) // 2, int((big.height - win_h) * 0.3)
    frame.paste(big.crop((x0, y0, x0 + RW, y0 + win_h)), (0, 240))
    frame.alpha_composite(layer, (0, 240 + win_h + 40))
    handle_tag(frame, 1700)
    return frame


SERIES_KIND = ""   # the Reel being made (make() sets it): its end card names its daily slot


def series_line(kind: str) -> tuple[str, str]:
    """📅 A reason to follow (owner, 16 Oct 2026: 15,000 views in 30 days but 17 followers): each Reel names its own
    daily slot, e.g. 'Roz raat 8:30 baje naya Bachpan'. Returns (end-card text, caption follow line)."""
    import schedule
    def at(k: str) -> str:
        h, m = schedule.SPECIALS.get(k, (0, 0))
        part = "subah" if h < 12 else "dopahar" if h < 16 else "shaam" if h < 20 else "raat"
        return f"{part} {(h - 1) % 12 + 1}{':%02d' % m if m else ''} baje"
    if kind == "reel_devotion":
        return f"Roz {at(kind)} aaj ke Bhagwan", f"roz {at(kind)} aaj ke Bhagwan ka aashirwad 🙏"
    if kind in NEWS_KINDS:
        return "Roz ki viral khabar, sabse pehle", "roz ki viral khabar, sabse pehle 🔥"
    if kind in BREAK_KINDS:
        return "Roz ki breaking khabar, sabse pehle", "roz ki breaking khabar, sabse pehle ⚡"
    if kind in CLIP_KINDS or kind == "reel_meme":
        return "Roz naye memes: dosto ko tag karo", "roz naye memes, dosto ko tag karo 😂"
    if kind == "reel_poll":
        return f"Roz {at(kind)} naya poll", f"roz {at(kind)} naya poll, vote karna mat bhoolna 🗳️"
    if kind == "reel_politics":
        return f"Roz {at(kind)} naya Reality Check", f"roz {at(kind)} naya Reality Check 🎯"
    if kind == "reel_bachpan":
        return f"Roz {at(kind)} naya Bachpan", f"roz {at(kind)} naya Bachpan, miss mat karna 🧒"
    if kind == "reel_rotate":
        return f"Roz {at(kind)} naya comedy", f"roz {at(kind)} naya comedy: bachpan, memes, tag karo 😂"
    if kind == "reel_official":
        return f"Roz {at(kind)} neta ka kadak bayan", f"roz {at(kind)} neta ka kadak bayan 🇮🇳"
    return "roz ki khabar + memes", "roz ki khabar + memes 🔥"


def follow_frame(last: Image.Image) -> Image.Image:
    """End card on every Reel (owner, 14 Oct 2026: ~200 people see a Reel, almost none follow): the last frame
    dimmed, 'Follow @khabarbawaal' + the Reel's daily series line (series_line)."""
    frame = last.convert("RGBA")
    dim = Image.new("RGBA", (RW, RH), (12, 12, 12, 225))
    frame.alpha_composite(dim)
    head = pill(f"Follow {CONFIG['handle']}", font("Anton-Regular.ttf", 96), YELLOW, "#111111", pad=(44, 18))
    text, size = series_line(SERIES_KIND)[0], 52
    sub = pill(text, font("Poppins-Bold.ttf", size), RED, "#FFFFFF", pad=(36, 14))
    while sub.width > RW - 90 and size > 34:   # a long series line shrinks to fit
        size -= 4
        sub = pill(text, font("Poppins-Bold.ttf", size), RED, "#FFFFFF", pad=(36, 14))
    y = (RH - head.height - sub.height - 40) // 2
    frame.alpha_composite(head, ((RW - head.width) // 2, y))
    frame.alpha_composite(sub, ((RW - sub.width) // 2, y + head.height + 40))
    return frame


def add_follow(enc, last: Image.Image, secs: float = FOLLOW_SECS) -> None:
    """Writes the follow end card (FOLLOW_SECS) after a Reel's last frame."""
    card = follow_frame(last)
    for _ in range(int(secs * FPS)):
        enc.add(card)


# ---------------------------------------------------------------- 1. clip + meme text

OPENER_SECS = 1.5        # 😂 joke Reels open on the trigger alone (owner, 16 Oct 2026: 48% skipped the joke hook)
SATIRE_OPEN_SECS = 2.0   # 🎯 satire opens like the news (73% skipped the meme-text opening)


def clip_reel(clip: str, start: float, dur: float, hook: str, text: str, highlight: list[str],
              out_mp4: str, crop_x: float = 0.5, text_top: int = 960, hold: float = 0.0,
              foot: str = "TAG KARO USKO", opener: str = "", framed: bool = False) -> list[Image.Image]:
    """The last `hold` seconds of dur freeze the clip's last frame (clip shorter than the reading time).
    opener: 2-5 words shown BIG alone over the clip for the first OPENER_SECS, then the banner + full text get
    their whole `dur` to be read. Returns the frames to proofread: [cover] (all text shown) (+ the opener)."""
    highlights = {norm(w) for phrase in highlight for w in phrase.split()}
    lines, fnt, line_h = fit_lines(text, RIGHT - LEFT, 470, sizes=range(80, 40, -4))
    imgs = line_images(lines, fnt, line_h, "#FFFFFF", YELLOW, highlights, stroke=7)
    size = 84
    hook_img = pill(hook, font("Anton-Regular.ttf", size), RED, "#FFFFFF", pad=(38, 12))
    while hook_img.width > RW - 90 and size > 48:   # a long banner ('BACHPAN ME AISA KAUN KIYA?') shrinks to fit
        size -= 4
        hook_img = pill(hook, font("Anton-Regular.ttf", size), RED, "#FFFFFF", pad=(38, 12))
    foot = pill(foot, font("Poppins-Bold.ttf", 46), YELLOW, "#111111", pad=(34, 16), arrow=True)
    logo = logo_img()
    # Soft dark fade behind the text so it reads on any footage.
    shade = Image.new("RGBA", (RW, 900), (0, 0, 0, 0))
    ImageDraw.Draw(shade).rectangle((0, 120, RW, 780), fill=(0, 0, 0, 120))
    shade = shade.filter(ImageFilter.GaussianBlur(60))
    if framed:
        return framed_reel(clip, start, dur, hook_img, imgs, line_h, foot, out_mp4, opener, highlights)
    enc, cover, frame, opened = Encoder(out_mp4), None, None, None
    lead = OPENER_SECS if opener else 0.0
    if lead:   # the clip runs on under the opener; whatever it lacks at the end freezes on its last frame
        layer = hook_layer(opener, highlights)
        hold = max(0.0, dur + lead - (video_info(clip)[2] - start - 0.2))
    for i, frame in enumerate(clip_frames(clip, start, dur + lead - hold, RW, RH, crop_x, hold)):
        t = i / FPS - lead
        if t < 0:   # 🪝 the opener alone, big, over the clip
            frame = frame.convert("RGBA")
            frame.alpha_composite(Image.new("RGBA", (RW, RH), (0, 0, 0, 90)))
            frame.alpha_composite(layer, (0, (RH - layer.height) // 2))
            handle_tag(frame, 1730)
            if opened is None and t >= -lead / 2:
                opened = frame.convert("RGB")
            enc.add(frame)
            continue
        if t < 0.45:   # 🪝 quick punch-in; banner + text are on screen from the first frame (owner, 14 Oct 2026)
            z = 1.12 - 0.12 * ease_out(t / 0.45)
            big = frame.resize((int(RW * z), int(RH * z)), Image.BICUBIC)
            frame = big.crop(((big.width - RW) // 2, (big.height - RH) // 2, (big.width + RW) // 2, (big.height + RH) // 2))
        frame = frame.convert("RGBA")
        frame.alpha_composite(shade, (0, text_top - 300))
        if logo:
            frame.alpha_composite(logo, (60, 150))
        frame.alpha_composite(hook_img, ((RW - hook_img.width) // 2, text_top - 150))
        for j, img in enumerate(imgs):
            frame.alpha_composite(img, (LEFT, text_top + j * line_h))
        k = ease_out((t - (dur - 1.6)) / 0.4)
        if k > 0:
            fy = int(text_top + len(lines) * line_h + 40 + 120 * (1 - k))
            frame.alpha_composite(foot, ((RW - foot.width) // 2, fy))
        handle_tag(frame, 1730)
        if t >= dur - 1.0 and cover is None:
            cover = frame.convert("RGB")
        enc.add(frame)
    add_follow(enc, frame)
    enc.close()
    return [cover or frame.convert("RGB")] + ([opened] if opened else [])


FRAME_W, FRAME_H = 1020, 574   # 🖼️ photos behind a joke sit whole in this window (owner, 16 Oct 2026)


def framed_reel(clip: str, start: float, dur: float, banner: Image.Image, imgs: list[Image.Image], line_h: int,
                foot: Image.Image, out_mp4: str, opener: str, highlights: set[str]) -> list[Image.Image]:
    """Tag that friend / Bachpan when the picture is PHOTOS, not a video (owner, 16 Oct 2026: 'blur image and half
    photo, not attractive'): dark screen, banner + the joke on top, the photos whole and sharp in a rounded window
    (FRAME_W x FRAME_H, slow zoom), 'TAG KARO USKO', the handle; the opener first, big, above the window."""
    win_y = 470 + len(imgs) * line_h + 50
    mask = Image.new("L", (FRAME_W, FRAME_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, FRAME_W - 1, FRAME_H - 1), radius=28, fill=255)
    logo = logo_img()
    base = Image.new("RGBA", (RW, RH), "#101010")
    if logo:
        base.alpha_composite(logo, (60, 150))
    lead = OPENER_SECS if opener else 0.0
    layer = hook_layer(opener, highlights) if opener else None
    hold = max(0.0, dur + lead - (video_info(clip)[2] - start - 0.2))
    enc, cover, frame, opened = Encoder(out_mp4), None, None, None
    for i, shot in enumerate(clip_frames(clip, start, dur + lead - hold, FRAME_W, FRAME_H, hold=hold)):
        t = i / FPS - lead
        frame = base.copy()
        if t < 0:   # 🪝 the opener alone, big, above the photo
            frame.alpha_composite(layer, (0, max(260, win_y - layer.height - 10)))
        else:
            frame.alpha_composite(banner, ((RW - banner.width) // 2, 330))
            for j, img in enumerate(imgs):
                frame.alpha_composite(img, (LEFT, 470 + j * line_h))
        frame.paste(shot, ((RW - FRAME_W) // 2, win_y), mask)
        k = ease_out((t - (dur - 1.6)) / 0.4) if t >= 0 else 0
        if k > 0:
            frame.alpha_composite(foot, ((RW - foot.width) // 2, int(win_y + FRAME_H + 40 + 80 * (1 - k))))
        handle_tag(frame, min(1760, win_y + FRAME_H + 170))
        if t < 0 and opened is None and t >= -lead / 2:
            opened = frame.convert("RGB")
        if t >= dur - 1.0 and cover is None:
            cover = frame.convert("RGB")
        enc.add(frame)
    add_follow(enc, frame)
    enc.close()
    return [cover or frame.convert("RGB")] + ([opened] if opened else [])


# ---------------------------------------------------------------- 2. classic meme layout

def meme_reel(clip: str, start: float, dur: float, setup: list[str], punch: str, punch_at: float,
              highlight: list[str], out_mp4: str, hold: float = 0.0, opener: str = "") -> list[Image.Image]:
    """White screen, text on top, clip in the middle. setup = short items shown one by one
    (e.g. alarm times), punch replaces them at punch_at seconds. The last `hold` seconds of dur
    freeze the clip's last frame (the clip is shorter than the reading time).
    Returns the frames to proofread: [setup complete, cover (punchline)]."""
    highlights = {norm(w) for phrase in highlight for w in phrase.split()}
    clip_w, clip_h = RW, int(RW * 9 / 16)
    clip_y, text_bottom = 1000, 960
    s_lines, s_fnt, s_lh = fit_lines(" ".join(setup), RIGHT - LEFT, 480, sizes=range(76, 40, -4))
    p_lines, p_fnt, p_lh = fit_lines(punch, RIGHT - LEFT, 480, sizes=range(96, 44, -4))
    p_imgs = line_images(p_lines, p_fnt, p_lh, "#111111", RED, highlights)
    logo = logo_img()
    enc, before, cover, frame, opened = Encoder(out_mp4), None, None, None, None
    lead = OPENER_SECS if opener else 0.0   # 🪝 the trigger alone, big, for the first 1.5 s (owner, 16 Oct 2026)
    if lead:
        layer = hook_layer(opener, highlights)
        hold = max(0.0, dur + lead - (video_info(clip)[2] - start - 0.2))
    for i, shot in enumerate(clip_frames(clip, start, dur + lead - hold, clip_w, clip_h, hold=hold)):
        t = i / FPS - lead
        frame = Image.new("RGBA", (RW, RH), "#FFFFFF")
        if logo:
            frame.alpha_composite(logo, ((RW - logo.width) // 2, 150))
        if t < 0:
            frame.alpha_composite(layer, (0, text_bottom - layer.height + 30))
            frame.paste(shot, (0, clip_y))
            handle_tag(frame, clip_y + clip_h + 70, fill="#111111", stroke=0)
            if opened is None and t >= -lead / 2:
                opened = frame.convert("RGB")
            enc.add(frame)
            continue
        if t < punch_at:
            shown = setup   # all at once (owner, 15 Oct 2026: no word-by-word text in any Reel)
            ls, fnt, lh = fit_lines(" ".join(shown), RIGHT - LEFT, 480, sizes=[s_fnt.size])
            y0 = text_bottom - len(s_lines) * s_lh
            for j, img in enumerate(line_images(ls, fnt, lh, "#111111", RED, highlights)):
                frame.alpha_composite(img, (LEFT, y0 + j * lh))
        else:
            y0 = text_bottom - len(p_lines) * p_lh
            for j, img in enumerate(p_imgs):
                frame.alpha_composite(img, (LEFT, y0 + j * p_lh))
        # Small zoom-punch on the clip at the punchline.
        z = 1 + 0.08 * max(0.0, 1 - abs(t - punch_at - 0.15) / 0.25) if t >= punch_at else 1
        if z > 1:
            big = shot.resize((int(clip_w * z), int(clip_h * z)), Image.BICUBIC)
            x0, y0 = (big.width - clip_w) // 2, (big.height - clip_h) // 2
            shot = big.crop((x0, y0, x0 + clip_w, y0 + clip_h))
        frame.paste(shot, (0, clip_y))
        handle_tag(frame, clip_y + clip_h + 70, fill="#111111", stroke=0)
        if t < punch_at:
            before = frame
        elif t >= punch_at + 0.8 and cover is None:
            cover = frame.convert("RGB")
        enc.add(frame)
    add_follow(enc, frame)
    enc.close()
    return [(before or frame).convert("RGB"), cover or frame.convert("RGB")] + ([opened] if opened else [])


# ---------------------------------------------------------------- 1b. the owner's own video

def own_reel(clip: str, dur: float, text: str, highlight: list[str], out_mp4: str) -> list[Image.Image]:
    """The owner's video as a Reel: full screen (a wide video sits on a blurred copy of itself),
    hook text popping in at the top, logo and handle; the video's own sound is kept.
    Returns the frames to proofread: [cover] (text shown)."""
    highlights = {norm(w) for phrase in highlight for w in phrase.split()}
    w, h, _ = video_info(clip)
    wide = w > h * 0.8   # landscape or square: don't crop away the sides
    fit_h = 2 * round(RW * h / w / 2) if wide else RH
    lines, fnt, line_h = fit_lines(text, RIGHT - LEFT, 430, sizes=range(84, 44, -4))
    imgs = line_images(lines, fnt, line_h, "#FFFFFF", YELLOW, highlights, stroke=7)
    logo = logo_img()
    shade = Image.new("RGBA", (RW, 760), (0, 0, 0, 0))
    ImageDraw.Draw(shade).rectangle((0, 0, RW, 640), fill=(0, 0, 0, 110))
    shade = shade.filter(ImageFilter.GaussianBlur(60))
    text_top = 300
    enc, cover, frame = Encoder(out_mp4, audio_from=clip), None, None
    for i, shot in enumerate(clip_frames(clip, 0, dur, RW, fit_h)):
        t = i / FPS
        if wide:   # blurred, darkened copy as the background (blurred small = fast)
            bg = shot.resize((RW // 10, RH // 10)).filter(ImageFilter.GaussianBlur(3)).resize((RW, RH))
            frame = Image.blend(bg, Image.new("RGB", (RW, RH), "#000000"), 0.45).convert("RGBA")
            frame.paste(shot, (0, (RH - fit_h) // 2 + 120))
        else:
            frame = shot.convert("RGBA")
        frame.alpha_composite(shade, (0, text_top - 160))
        if logo:
            frame.alpha_composite(logo, (60, 150))
        for j, img in enumerate(imgs):
            frame.alpha_composite(img, (LEFT, text_top + j * line_h))
        handle_tag(frame, 1730)
        if t >= min(1.8, dur * 0.4) and cover is None:
            cover = frame.convert("RGB")
        enc.add(frame)
    enc.close()
    return [cover or frame.convert("RGB")]


# ---------------------------------------------------------------- 3. news photo slideshow

def news_reel(slides: list[tuple[Image.Image, str, str, bool]], end_text: str, highlight: list[str], tag: str,
              out_mp4: str, end: float = 3.0, hook: str = "",
              hook_video: tuple[str, float, float] | None = None, hook_photo: Image.Image | None = None) -> list[Image.Image]:
    """slides = [(photo, credit, text, whole)]; whole=True (X post screenshots) shows the picture
    uncropped instead of zooming. Each photo stays long enough to read its line in full: 2.5 s + 0.25 s
    a word, 4-8.5 s; end card 3 s (owner, 11 Oct 2026: 16/21/13-word lines = a 20 s Reel; 12 Oct: +1 s).
    Numbers are highlighted too. Returns the frames to proofread: one per photo + the end card."""
    highlights = {norm(w) for phrase in highlight for w in phrase.split()}
    photo_h, photo_y = 760, 300
    prepared = []
    for photo, credit, text, whole in slides:
        photo = photo.convert("RGB")
        k = max(RW / photo.width, RH / photo.height)
        bg = photo.resize((int(photo.width * k) + 1, int(photo.height * k) + 1)).crop((0, 0, RW, RH))
        bg = Image.blend(bg.filter(ImageFilter.GaussianBlur(40)), Image.new("RGB", (RW, RH), "#000000"), 0.55)
        lines, fnt, lh = fit_lines(text, RIGHT - LEFT, 420, sizes=range(72, 40, -4))
        nums = {norm(w) for line in lines for w in line if any(c.isdigit() for c in w)}
        per = min(8.5, max(4.0, read_secs(text, 2.5)))
        if whole:   # fit inside the photo window, on a dark card
            card = Image.new("RGB", (RW, photo_h), "#111111")
            fit = photo.copy()
            fit.thumbnail((RW - 40, photo_h - 40), Image.LANCZOS)
            card.paste(fit, ((RW - fit.width) // 2, (photo_h - fit.height) // 2))
            photo = card
        prepared.append((bg, photo, credit, line_images(lines, fnt, lh, "#FFFFFF", YELLOW, highlights | nums, stroke=5),
                         lh, per, whole))
    starts = [sum(p[5] for p in prepared[:n]) for n in range(len(prepared) + 1)]
    tag_img = pill(printable(tag).upper(), font("Anton-Regular.ttf", 64), RED, "#FFFFFF", pad=(30, 8))
    e_lines, e_fnt, e_lh = fit_lines(end_text, RIGHT - LEFT, 700, sizes=range(96, 48, -4))
    e_imgs = line_images(e_lines, e_fnt, e_lh, "#111111", RED, highlights)
    follow_text, fsize = f"Follow {CONFIG['handle']}: {series_line(SERIES_KIND)[0].lower()}", 40
    follow = pill(follow_text, font("Poppins-Bold.ttf", fsize), "#111111", YELLOW, pad=(30, 16))
    while follow.width > RW - 80 and fsize > 26:   # always fits on screen (16 Oct 2026: cut off -> proofreader blocked)
        fsize -= 2
        follow = pill(follow_text, font("Poppins-Bold.ttf", fsize), "#111111", YELLOW, pad=(30, 16))
    logo = logo_img()
    total = int((starts[-1] + end) * FPS)
    enc, checks, frame, hook_check = Encoder(out_mp4), [], None, []
    if hook:   # 🪝 the hook screen first (owner, 14 Oct 2026): first photo + a big line from the first frame
        layer = hook_layer(hook, highlights)
        # hook_video = (path, start, secs): the official video plays under the line instead (🎥 10 PM Reel)
        stills = video_stills(*hook_video) if hook_video else None
        still = hook_photo if hook_photo is not None else slides[0][0]
        for i in range(int((hook_video[2] if hook_video else COVER_SECS) * FPS)):
            still = next(stills, still) if stills else still
            frame = hook_frame(still, layer, i / FPS) if hook_video else \
                cover_hook_frame(still, hook, tag, f"Photo: {slides[0][1]}" if slides[0][1] else "", i / FPS, dur=COVER_SECS)
            if i == int(0.6 * FPS):
                hook_check.append(frame.convert("RGB"))
            enc.add(frame)
    for i in range(total):
        t = i / FPS
        n = sum(t >= s for s in starts[1:])
        if n < len(prepared):
            lt = t - starts[n]
            bg, photo, credit, imgs, lh, per, whole = prepared[n]
            frame = bg.convert("RGBA")
            # Ken Burns: photo fills a 1080 x photo_h window and slowly zooms in; later photos slide in.
            k = max(RW / photo.width, photo_h / photo.height) * (1.0 if whole else 1.0 + 0.10 * lt / per)
            big = photo.resize((int(photo.width * k) + 1, int(photo.height * k) + 1), Image.BICUBIC)
            x0, y0 = (big.width - RW) // 2, int((big.height - photo_h) * 0.35)
            frame.paste(big.crop((x0, y0, x0 + RW, y0 + photo_h)),
                        (int(RW * (1 - ease_out(lt / 0.35))) if n else 0, photo_y))
            ImageDraw.Draw(frame).text((RW - 160, photo_y + photo_h - 16), f"Photo: {credit}",
                                       font=font("Poppins-SemiBold.ttf", 24), fill="#FFFFFF", anchor="rd",
                                       stroke_width=2, stroke_fill="#000000")
            if logo:
                frame.alpha_composite(logo, (60, 150))
            frame.alpha_composite(tag_img, (RW - tag_img.width - 60, 140))
            for j, img in enumerate(imgs):
                frame.alpha_composite(img, (LEFT, photo_y + photo_h + 60 + j * lh))
            if len(checks) == n and lt >= per - 0.3:
                checks.append(frame.convert("RGB"))
        else:
            lt = t - starts[-1]
            frame = Image.new("RGBA", (RW, RH), YELLOW)
            y0 = (RH - len(e_imgs) * e_lh - 200) // 2
            for j, img in enumerate(e_imgs):
                frame.alpha_composite(img, (LEFT, y0 + j * e_lh))
            frame.alpha_composite(follow, ((RW - follow.width) // 2, int(y0 + len(e_imgs) * e_lh + 80)))
            handle_tag(frame, 1600, fill="#111111", stroke=0)
        enc.add(frame)
    enc.close()
    return checks + [frame.convert("RGB")] + hook_check   # hook last: slide numbers stay the photo numbers


# ---------------------------------------------------------------- the robot
# 8 AM reel_clip_am + 1 PM reel_clip, 7 PM reel_meme, 8 PM reel_news (schedule.py). Jokes: 3 options, the
# 'young Indian' judge picks (like fun posts), the AI picks a matching Mixkit clip by title.
# News: the AI picks today's most visual story from our own news posts; photos are collected
# and checked like carousel photos. Every Reel is proofread (still frames). If anything fails,
# make() returns False and the robot tries again (generate.REEL_TRIES); no Reel = no post in that slot.

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
CLIP_SECONDS, MEME_SECONDS = 8.5, 10.0     # shortest Tag that friend / meme Reel; longer text gets more time
CLIP_MAX = 12.0


def read_secs(text: str, base: float = 1.5, per_word: float = 0.25) -> float:
    """Time on screen to read a block of text in full: base + 0.25 s a word (owner, 11 Oct 2026, all Reels;
    12 Oct 2026: "a couple of seconds more, not double": callers add 1 s a text screen, 2 s to Tag that friend)."""
    return base + per_word * len(text.split())
CLIP_KINDS = ("reel_clip", "reel_clip_am", "reel_comedy")   # clip Reels: Tag that friend + 🎭 comedy (rotating slot)

REEL_RULES = """This joke becomes a short Instagram REEL: a free stock video clip (Mixkit) with our text on top.
The clip must be something a stock-video site has: everyday people doing everyday things (lying in bed \
with a phone, eating, laughing, shocked face, running late, working on a laptop, dancing, sleeping, cooking, \
studying, waiting...). No celebrities, brands or specific Indian places in the clip. Prefer clips with Indian / \
South Asian people (owner, 14 Oct 2026): make the first search 'indian ...' (e.g. 'indian man laughing'). For festival, \
fasting or religious jokes never a clip with meat, alcohol or smoking.
clip_search: 3 different searches for the stock site, 1-3 plain English words each, most specific first (e.g. ["texting in bed", "man phone bed", "lazy"]). Simple words find more clips. Prefer jokes whose moment a stock clip can actually show.
clip_wanted: one line describing the ideal clip.
caption, pin_comment and hashtags follow the same rules as fun posts (exactly 5 hashtags, include #reelsindia); the \
caption's first line carries the searchable keywords (the trend's name, e.g. 'Navratri garba', 'Gemini 4')."""

SONGS = {"type": "array", "description": "2 Indian + 2 English songs that fit this Reel's moment, copied exactly "
         "from today's song lists",
         "items": {"type": "object", "properties": {
             "title": {"type": "string"}, "artist": {"type": "string"},
             "lang": {"type": "string", "enum": ["hindi", "english"], "description": "hindi = any Indian song"},
             "mood": {"type": "string", "description": "Max 6 words: why it fits this Reel, e.g. 'sad hook = UPI fail'"}},
             "required": ["title", "artist", "lang", "mood"], "additionalProperties": False}}

_COMMON = {
    "songs": SONGS,
    "versus": {"type": "boolean", "description": "true only if the people/things in 'about' are rivals or being "
               "compared (e.g. Hopr vs Tinder, Ram vs Ravan); false for co-stars, a person and his own team/film"},
    "opener": {"type": "string", "description": "2-5 words shown BIG alone for the first 1.5 s, before the joke: the "
               "relatable trigger that makes people stop scrolling, e.g. 'Diwali safai ka asli sach', 'Exam se ek raat "
               "pehle', 'Mummy ka phone aaya'. No emojis."},
    "about": {"type": "array", "description": "The specific real things this joke is about, so their photos can be "
              "shown (owner, 15 Oct 2026: a Ramayana trailer meme showed Thai dancers): a film/show, person, team, "
              "brand or event, e.g. Ramayana (2026 film) + Ranbir Kapoor. EMPTY for festivals, seasons, weather and "
              "everyday desi moments (they get a stock video).", "items": {"type": "object", "properties": {
                  "name": {"type": "string", "description": "Short name shown on screen, e.g. 'Ranbir', 'Ramayana'"},
                  "kind": {"type": "string", "enum": ["film_show", "person", "team", "party", "brand", "event_place", "app"]},
                  "lookup": {"type": "string", "description": "person/team/party: exact English Wikipedia page title; "
                             "app: its App Store name; film_show/brand/event_place: a short English news search for "
                             "today's photos of it, e.g. 'Ramayana trailer launch Bengaluru'"}},
                  "required": ["name", "kind", "lookup"], "additionalProperties": False}},
    "based_on": {"type": "string", "description": "The trend/topic or everyday moment this joke is about"},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-3 words copied exactly from the text to colour"},
    "clip_search": {"type": "array", "items": {"type": "string"}},
    "clip_wanted": {"type": "string"},
    "photo_query": {"type": "string", "description": "If the joke names a real app, brand, person, film or event: a "
                    "short English news search for real photos of it (e.g. 'Hopr dating app Bengaluru Tinder'); "
                    "empty for an everyday moment"},
    "caption": {"type": "string", "description": "Hinglish, first line with the searchable keywords, ends with a "
                "question people answer in the comments (e.g. 'Tumhare group mein kaun hai aisa? 👇')"},
    "pin_comment": {"type": "string", "description": "A funny follow-up line + a share nudge to the right person, e.g. "
                    "'Us dost ko bhejo jo ye karta hai 👇'"},
    "hashtags": {"type": "array", "items": {"type": "string"}},
}
CLIP_OPTION = {"type": "object", "properties": {
    **_COMMON,
    "text": {"type": "string", "description": "Text shown under a big 'TAG THAT FRIEND' banner, describing that "
             "friend, e.g. \"Wo dost jo bed se text karta hai:\\n'Bhai nikal gaya, 5 min mein pahunch raha hoon'\". "
             "Max 18 words, no emojis, \\n line breaks allowed."}},
    "required": list(_COMMON) + ["text"], "additionalProperties": False}
MEME_OPTION = {"type": "object", "properties": {
    **_COMMON,
    "setup": {"type": "array", "items": {"type": "string"}, "description":
              "2-6 short pieces (max 12 words in total) that appear one after another while the clip plays, "
              "building up, e.g. [\"Alarm:\", \"6:00,\", \"6:05,\", \"6:10,\", \"6:15...\"]. No emojis."},
    "punch": {"type": "string", "description": "Punchline (max 10 words, no emojis) that replaces the setup at the "
              "clip's reaction moment, e.g. \"Me at 9:45 jab office 10 baje hai\". highlight words come from here."}},
    "required": list(_COMMON) + ["setup", "punch"], "additionalProperties": False}

NEWS_TAGS = ["VIRAL", "BREAKING", "BOLLYWOOD", "CRICKET", "SPORTS", "TECH", "INDIA", "WORLD", "POLITICS", "MONEY"]
NEWS_OPTION = {"type": "object", "properties": {
    "pick": {"type": "integer", "description": "Story number from the list"},
    "hook": {"type": "string", "description": "🪝 Opening line on the first photo, 4-8 words, Hinglish, no emojis: the "
             "most shocking/curious fact of the story so people stop scrolling, e.g. '8 mahine. 1 ladka. Poori nadi "
             "saaf.' or 'Is case ne poore desh ko hila diya'. Biggest name first, then the surprise: 'Salman ne Karisma ke "
             "saath shoot se mana kiya'. A fact from the story, not clickbait."},
    "tag": {"type": "string", "enum": NEWS_TAGS},
    "end_text": {"type": "string", "description": "Last card, max 14 words, no emojis: a relatable Hinglish reaction "
                 "(\"Aur hum? Uber mein baithke bhi nervous ho jaate hain\") or, for serious news, the punchy takeaway."},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-2 words copied exactly from end_text"},
    "caption": {"type": "string", "description": "Hinglish: hook line with the searchable keywords (names, film/event, "
                "e.g. 'Drishyam 3 box office collection'), 2-3 short fact lines, then a question that makes people take a "
                "side ('Salman sahi the? Haan ya Na? Comment karo 👇'). 1-3 emojis."},
    "pin_comment": {"type": "string", "description": "Pinned first comment, max 20 words, no hashtags: a short share "
                    "nudge to the right person, e.g. 'Kisi Salman fan ko bhejo 👇'"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5, include #reelsindia"},
    "photo_query": {"type": "string", "description": "Short English news-search query that finds photos of this exact story"},
    "wiki_title": {"type": "string", "description": "English Wikipedia title of the main person/team/place, or empty"},
    "songs": SONGS},
    "required": ["pick", "hook", "tag", "end_text", "highlight", "caption", "pin_comment", "hashtags", "photo_query", "wiki_title",
                 "songs"],
    "additionalProperties": False}


def options_schema(option: dict, n: str) -> dict:
    return {"type": "object", "properties": {"options": {"type": "array", "items": option, "description": n}},
            "required": ["options"], "additionalProperties": False}


def unescape(s: str) -> str:
    return s.replace("\\n", "\n").strip()


VIRAL_FILE = Path(__file__).parent / "state" / "viral.json"   # tools/views_from_recording.py --save


def viral_memory(note: str = "") -> str:
    """Our own winners and flops (views from the owner's Instagram screen recordings), shown to every picker and
    writer so they aim for what worked (owner, 15 Oct 2026: a Salman Khan reveal got 1.6k views + shares)."""
    try:
        data = json.loads(VIRAL_FILE.read_text())
    except Exception:
        return ""
    top, weak = data.get("top", [])[:10], data.get("weak", [])[:5]
    recorded = sorted(data.get("recorded", []), key=lambda t: -t["views"])[:5]
    if not top and not recorded:
        return ""
    line = lambda t: f"- {t['views']} views: {t['title']}" + (f" ({t['note']})" if t.get("note") else "")
    return ("\n\nWhat went viral for us (aim for SIMILAR stories, hooks and angles, never the same stories again" +
            (f"; {note}" if note else "") +
            "; the page's rules above always come first, e.g. no matches or topics the owner ruled out):" + ("\nOur best Reels (approx. views, last 30 days):\n" + "\n".join(line(t) for t in top) if top else "") +
            ("\nThe owner's exact numbers:\n" + "\n".join(line(t) for t in recorded) if recorded else "") +
            ("\nOur weakest Reels (avoid stories and angles like these):\n" + "\n".join(line(t) for t in weak) if weak else ""))


BUZZ_SCHEMA = {"type": "object", "properties": {"topics": {"type": "array", "items": {"type": "object", "properties": {
    "topic": {"type": "string", "description": "Short, e.g. 'Gemini 4 launch', 'Navratri Day 4 garba nights'"},
    "why": {"type": "string", "description": "One line from the headlines/trends: what happened, why people talk about it"},
    "buzz": {"type": "integer", "description": "1-10: how many young Indians across India are talking about it today"}},
    "required": ["topic", "why", "buzz"], "additionalProperties": False}}},
    "required": ["topics"], "additionalProperties": False}


def buzz_topics(state: dict) -> list[dict]:
    """The topics young Indians across India are talking about right now, buzz 7+ (owner, 14 Oct 2026: two 7 PM
    memes in a row were about matches nobody cared about; Google's list is mostly score searches, X's mostly
    wishes and hashtag campaigns). One AI request reads Google + X trends + the feeds' headlines and rates them."""
    import fun
    import generate as g
    heads = [f"[{i['category']}] {i['title']}" for i in g.fetch_candidates(set())][:45]
    trends = ("Google searches today: " + "; ".join(fun.google_trends()) + "\n\nX (Twitter) trends, hours trended: "
              + "; ".join(f"{t} ({h} h)" for t, h in fun.x_trends()[:70]))
    used = (state.get("fun_topics", []) + state.get("reel_topics", []))[-20:]
    out = fun.claude_json(
        "You track what young Indians (18-30, all over India) are talking about TODAY: what friends would bring up "
        "in a group chat. Counts most: news about the BIGGEST stars (Salman, Shah Rukh, Virat, Dhoni...), big Bollywood/OTT/celebrity moments, viral internet moments, festivals and their "
        "rituals, India's big cricket moments (Team India, IPL), new phones/apps/AI launches, prices, exams, weather, "
        "national news that affects everyone. Skip: matches without Team India or domestic matches, scorecard "
        "searches, share prices, region-only topics, deaths/crimes/accidents/disasters, organised hashtag campaigns, "
        "devotional wishes, foreign football. Use only facts the headlines/trends show.",
        f"{fun.today_context()}\n\n{trends}\n\nToday's headlines:\n" + "\n".join(f"- {h}" for h in heads)
        + "\n\nTopics our recent Reels already used (skip them):\n" + ("\n".join(f"- {u}" for u in used) or "(none)")
        + "\n\nList the 6 biggest talking points right now, highest buzz first.",
        BUZZ_SCHEMA, CONFIG.get("membership_model", "sonnet"), purpose="buzz topics")["topics"]
    for t in out:
        print(f"  buzz {t['buzz']}: {t['topic']}: {t['why'][:90]}")
    return [t for t in out if t["buzz"] >= 7][:6]


def write_joke(kind: str, state: dict, evergreen: bool = False, topic: str = "", notes: str = "") -> dict:
    """evergreen=True: for 📦 Ready Reels, posted any day this week, so no trends, news or dates.
    topic: the owner's idea from ➕ Create (all 3 options are about it)."""
    import fun
    import generate as g
    system = fun.FUN_SYSTEM.format(page=CONFIG["page_name"]) + "\n\n" + fun.JOKE_RULES + "\n\n" + REEL_RULES
    style = ("Style: DESI COMEDY: a short, funny, relatable desi-life situation told in 2 lines: the setup, then a "
             "deadpan punchline (family, office, college, trains, weddings, phones, food). Not 'that friend' and not "
             "a meme template. The text is the whole joke." if kind == "reel_comedy" else
             "Style: TAG THAT FRIEND: the text describes that one friend everyone has."
             if kind in CLIP_KINDS else
             "Style: classic meme Reel: a setup that builds up, then a punchline (\"Me at...\", \"Me after...\", "
             "\"POV: ...\") landing on a REACTION clip (shocked, panicking, laughing, crying, facepalm, dancing...).")
    songs = trending_songs(state)
    backup = ""   # a second round if no joke passes the judge
    if topic:   # notes: what the named apps are + the latest news about the topic (build_joke_reel)
        context = (f"{fun.today_context()}\n\nThe page owner asked for a Reel about: {topic}\nIt is the owner's own "
                   "joke idea: keep its premise and the names in it." + (f"\n\n{notes}" if notes else ""))
        ask = ("Write exactly 3 different jokes, all about the owner's idea (different angles). Hashtags: the names "
               "in the owner's idea first (e.g. #hopr #tinder), then high-reach ones, exactly 5.")
    elif evergreen:
        context = ("This Reel may be posted on ANY day in the next week: NO trends, news, festivals, dates, "
                   "weather or day names. Only timeless relatable desi-life moments.")
        ask = "Write exactly 3 different jokes, each a timeless relatable desi-life moment (different situations)."
    else:
        topics = buzz_topics(state)
        context = (f"{fun.today_context()}\n\nWhat young Indians across India are talking about right now "
                   "(buzz 1-10, why):\n" + ("\n".join(f"- {t['topic']} (buzz {t['buzz']}): {t['why']}" for t in topics)
                                          or "(nothing big today)"))
        backup = "Write exactly 3 different jokes, each a relatable desi-life moment (different situations)."
        # Today's big talking points first (owner, 12 + 14 Oct 2026); everyday desi life only as backup.
        ask = ("Write exactly 3 different jokes, each on a DIFFERENT topic from the list above (highest buzz first)."
               if topics else backup)
    lead = (f"{style}\n\n{context}" +
            "\n\nTopics already used recently (don't reuse):\n" +
            ("\n".join(f"- {t}" for t in (state.get("fun_topics", []) + state.get("reel_topics", []))[-30:]) or "(none)") +
            "\n\nRecent Reels (don't repeat ideas):\n" + ("\n".join(f"- {r}" for r in state.get("recent_reels", [])[-20:]) or "(none)")
            + ("" if evergreen else viral_memory("for jokes: pick the big star/film talk of the day when you can")))
    model = CONFIG.get("membership_model", "sonnet")
    option = CLIP_OPTION if kind in CLIP_KINDS else MEME_OPTION
    if kind in CLIP_KINDS:
        text_of = lambda o: f"TAG THAT FRIEND: {o['text']}  [video: {o['clip_wanted']}]"
    else:
        text_of = lambda o: f"{' '.join(o['setup'])} -> {o['punch']}  [video: {o['clip_wanted']}]"
    def write(a: str) -> list[dict]:
        options = fun.claude_json(system, f"{lead}\n\n{a}" + songs_prompt(songs),
                                  options_schema(option, "Exactly 3 different jokes"), model,
                                  purpose="reel writing")["options"][:3]
        for o in options:
            o["caption"] = unescape(o["caption"])
            if kind in CLIP_KINDS:
                o["text"] = unescape(o["text"])
            else:
                o["punch"] = unescape(o["punch"])
        return options

    # Best Reels, never fail (owner, 15 Oct 2026): trend jokes -> one rewrite with the judge's reasons -> the
    # desi-life backup round. The owner's own idea is always made (the best option even below the judge's bar).
    asks = list(dict.fromkeys(x for x in (ask, backup) if x))
    for a in asks:
        try:
            best = fun.judge_best(write(a), text_of, model, fallback=bool(topic))
            break
        except fun.NoGoodJoke as e:
            if a == ask and not topic and e.scores:
                print("  jokes below the judge's bar: rewriting once with the judge's reasons")
                notes = "\n".join(f"- Option {s['option']}: understandable {s['understandable']}, funny "
                                  f"{s['funny']}, fresh {s['fresh']}: {s['why']}" for s in e.scores)
                try:
                    best = fun.judge_best(write(f"{a}\n\nA strict judge (a young Indian on Instagram) rejected your "
                                                f"first 3 jokes:\n{notes}\nWrite 3 new ones that fix this."),
                                          text_of, model)
                    break
                except fun.NoGoodJoke:
                    pass
            if a == asks[-1]:
                raise
            print("  no trend joke was clear and funny enough: relatable desi-life jokes instead")
    print(f"  reel joke based on: {best['based_on']}")
    roman_only(best, ["caption", "pin_comment"])
    best["songs"] = checked_songs(best.get("songs", []), songs)
    return best


SONG_WHERE: dict[str, str] = {}   # song title (lower case) -> where it charts, e.g. "Shazam India #3, Spotify India #5"
RECENT_SONGS: list[str] = []      # songs suggested for the last Reels (state 'songs_recent')


def _jiosaavn(params: dict) -> list:
    import requests
    r = requests.get("https://www.jiosaavn.com/api.php", headers=UA, timeout=20,
                     params={"_format": "json", "_marker": "0", "api_version": "4", "ctx": "web6dot0", **params})
    return r.json()


def _saavn_song(s: dict) -> tuple[str, str]:
    import html
    artists = s.get("more_info", {}).get("artistMap", {}).get("primary_artists", [])[:2]
    return (html.unescape(s["title"]),
            html.unescape(", ".join(a["name"] for a in artists) or s.get("subtitle", "").split(" - ")[0]))


def _spotify(playlist: str) -> list[tuple[str, str]]:
    """A Spotify chart playlist through its public embed page (no account)."""
    import re
    import requests
    page = requests.get(f"https://open.spotify.com/embed/playlist/{playlist}", headers=UA, timeout=20).text
    data = json.loads(re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S).group(1))
    return [(t["title"], t["subtitle"]) for t in data["props"]["pageProps"]["state"]["data"]["entity"]["trackList"]]


def _apple(country: str) -> list[tuple[str, str]]:
    """Apple Music's most played songs (Apple's free RSS feed; new releases show up here first)."""
    import requests
    r = requests.get(f"https://rss.applemarketingtools.com/api/v2/{country}/music/most-played/50/songs.json",
                     headers=UA, timeout=20)
    return [(x["name"], x["artistName"]) for x in r.json()["feed"]["results"]]


def _shazam(country: str) -> list[tuple[str, str]]:
    """Shazam's Top 200 for a country: the songs people are looking up right now (often heard in Reels)."""
    import html
    import re
    import requests
    page = requests.get(f"https://www.shazam.com/charts/top-200/{country}", headers=UA, timeout=25).text
    out = []
    for item in re.findall(r'data-test-id="songItem"(.*?)(?=data-test-id="songItem"|$)', page, re.S):
        title = re.search(r'charts_userevent_list_songTitle" aria-label="([^"]*)"', item)
        artist = re.search(r'charts_userevent_list_artistName" aria-label="([^"]*)"', item)
        if title and artist:
            out.append((html.unescape(title.group(1)), html.unescape(artist.group(1))))
    return out[:100]


def trending_songs(state: dict | None = None) -> dict[str, list[tuple[str, str]]]:
    """Songs to suggest for a Reel (the owner adds the music in Instagram), from free charts, no accounts
    (owner, 13 Oct 2026: the JioSaavn list alone kept giving the same old songs). {lang: [(title, artist)]}:
    hindi = Indian songs: Shazam Top 200 India (most looked-up), Spotify Top 50 India + Top Songs India (week),
    Apple Music most played India (new releases), JioSaavn's Reels playlists and trending;
    english = Spotify Top 50 Global, Apple Music most played US, JioSaavn English trending.
    Sources are merged rank by rank (each one's #1 first), duplicates removed."""
    sources = {"hindi": [("Shazam India", lambda: _shazam("india")),
                         ("Spotify India today", lambda: _spotify("37i9dQZEVXbLZ52XmnySJg")),
                         ("Spotify India week", lambda: _spotify("37i9dQZEVXbMWDif5SCBJq")),
                         ("Apple Music India", lambda: _apple("in")),
                         ("JioSaavn Insta Viral", lambda: [_saavn_song(x) for x in _jiosaavn(
                             {"__call": "playlist.getDetails", "listid": "1223482895", "n": 60, "p": 1})["list"]]),
                         ("JioSaavn Reels", lambda: [_saavn_song(x) for x in _jiosaavn(
                             {"__call": "playlist.getDetails", "listid": "1269241159", "n": 60, "p": 1})["list"]]),
                         ("JioSaavn trending", lambda: [_saavn_song(x) for x in _jiosaavn(
                             {"__call": "content.getTrending", "entity_type": "song", "entity_language": "hindi"})
                             if x.get("type") == "song"])],
               "english": [("Spotify Global today", lambda: _spotify("37i9dQZEVXbMDoHDwVN2tF")),
                           ("Apple Music US", lambda: _apple("us")),
                           ("JioSaavn trending", lambda: [_saavn_song(x) for x in _jiosaavn(
                               {"__call": "content.getTrending", "entity_type": "song", "entity_language": "english"})
                               if x.get("type") == "song"])]}
    RECENT_SONGS[:] = (state or {}).get("songs_recent", [])[-40:]
    out = {}
    for lang, srcs in sources.items():
        lists = []
        for name, get in srcs:
            try:
                lists.append((name, get()))
            except Exception as e:
                print(f"  ! songs from {name} failed: {str(e)[:80]}")
        merged, seen = [], set()
        for rank in range(max((len(l) for _, l in lists), default=0)):
            for name, songs in lists:
                if rank < len(songs):
                    title, artist = songs[rank]
                    key = title.lower().strip()
                    SONG_WHERE[key] = (SONG_WHERE.get(key, "") + f", {name} #{rank + 1}").strip(", ")
                    if key not in seen:
                        seen.add(key)
                        merged.append((title, artist))
        out[lang] = merged[:70 if lang == "hindi" else 35]
    return out


def songs_prompt(songs: dict) -> str:
    if not any(songs.values()):
        return ""
    lists = "\n".join(("Indian songs" if lang == "hindi" else "English songs") + ":\n" + "\n".join(
        f"- {t} | {a}   [{SONG_WHERE.get(t.lower().strip(), '')}]" for t, a in songs[lang]) for lang in songs if songs[lang])
    recent = ("\nSuggested for recent Reels (a trending song may repeat when it fits, but at least 2 of your 4 must "
              "not be in this list): " + "; ".join(dict.fromkeys(RECENT_SONGS))) if RECENT_SONGS else ""
    return ("\n\nsongs: the owner adds music in Instagram. Suggest 2 Indian + 2 English songs from these lists (today's "
            "charts: what India looks up on Shazam and plays on Spotify / Apple Music, and JioSaavn's Reels playlists; "
            "[...] says where each one charts). Pick songs whose hook, lyrics or vibe FIT THIS Reel's moment (a sad or "
            "dramatic line for a fail, hype for a win, romantic for love, calm for serious news...); among songs that "
            "fit, prefer the ones high on the charts and new. In mood, say in max 6 words why it fits (e.g. 'sad hook "
            "= UPI fail'). Copy title and artist exactly; no other songs." + recent + "\n" + lists)


def chart_songs(state: dict) -> list[dict]:
    """2 Indian + 2 English songs from the top of today's charts not suggested recently (Reels without AI writing)."""
    songs = trending_songs(state)
    return [{"title": t, "artist": a, "lang": lang, "mood": "high on today's charts"} for lang in songs
            for t, a in ([x for x in songs[lang] if x[0] not in RECENT_SONGS][:2] or songs[lang][:2])]


def slides_reel(slides: list[Image.Image], secs: list[float], out_mp4: str) -> None:
    """📦 A finished photo post as a Reel (owner, 14 Oct 2026: photo posts get 0-5 views, Reels ~117): each slide
    fills the width on a blurred copy of itself with a slow zoom, the cover (big headline) is the hook from the
    first frame, then the follow card."""
    enc, frame = Encoder(out_mp4), None
    for img, dur in zip(slides, secs):
        img = img.convert("RGB")
        k = max(RW / img.width, RH / img.height)
        bg = img.resize((int(img.width * k) + 1, int(img.height * k) + 1)).crop((0, 0, RW, RH))
        bg = Image.blend(bg.filter(ImageFilter.GaussianBlur(40)), Image.new("RGB", (RW, RH), "#000000"), 0.45)
        fit = img.resize((RW, int(img.height * RW / img.width)), Image.LANCZOS)
        n = max(1, int(dur * FPS))
        for i in range(n):
            z = 1.0 + 0.04 * i / max(n - 1, 1)
            big = fit.resize((int(fit.width * z), int(fit.height * z)), Image.BICUBIC)
            x0 = (big.width - RW) // 2
            frame = bg.copy().convert("RGBA")
            frame.paste(big.crop((x0, 0, x0 + RW, big.height)), (0, (RH - big.height) // 2))
            enc.add(frame)
    add_follow(enc, frame)
    enc.close()


def checked_songs(picked: list[dict], songs: dict) -> list[dict]:
    """Keep only songs that are really on today's lists (max 2 per language), with the list's spelling."""
    pool = {t.lower(): (t, a) for lang in songs for t, a in songs[lang]}
    out, seen = [], set()
    for s in picked:
        lang = s.get("lang", "")
        match = pool.get(s.get("title", "").strip().lower())
        if match and match[0] not in seen and lang in ("hindi", "english") and sum(o["lang"] == lang for o in out) < 2:
            seen.add(match[0])
            out.append({"title": match[0], "artist": match[1], "lang": lang, "mood": s.get("mood", "")[:40]})
    return out


def roman_only(post: dict, fields: list[str]) -> None:
    """Captions must be English (Roman) letters only: if Hindi, Urdu or other non-Roman script slipped in, the AI rewrites those fields."""
    import re
    import fun
    bad = [f for f in fields if re.search(r"[\u0590-\u08FF\u0900-\u0DFF]", post.get(f, ""))]
    if not bad:
        return
    schema = {"type": "object", "properties": {f: {"type": "string"} for f in bad},
              "required": bad, "additionalProperties": False}
    fixed = fun.claude_json("Rewrite each text exactly, but write every Hindi (Devanagari) word in English (Roman) "
                            "letters, Hinglish style. Keep emojis, line breaks and everything else unchanged.",
                            json.dumps({f: post[f] for f in bad}, ensure_ascii=False), schema,
                            CONFIG.get("membership_model", "sonnet"), purpose="roman letters")
    for f in bad:
        if not re.search(r"[\u0590-\u08FF\u0900-\u0DFF]", fixed.get(f, "")):
            post[f] = fixed[f]
            print(f"  {f}: Hindi script rewritten in English letters")


def mixkit_search(query: str) -> list[tuple[str, str]]:
    """(clip id, title) pairs from Mixkit's free stock-video search."""
    import html
    import re
    import requests
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    if not slug:
        return []
    import time
    page = ""
    for wait in (0, 8, 20):   # Mixkit answers 429 to quick repeated requests: wait and retry
        time.sleep(wait)
        try:
            r = requests.get(f"https://mixkit.co/free-stock-video/{slug}/", headers=UA, timeout=25)
        except Exception as e:
            print(f"  ! Mixkit search '{query}' failed: {e}")
            continue
        if r.status_code != 429:
            page = r.text
            break
    else:
        print(f"  ! Mixkit search '{query}': too many requests")
    out = []
    for m in re.finditer(r'<div class="item-grid-video-player.*?</div>\s*</div>', page, re.S):
        v = re.search(r"assets\.mixkit\.co/videos/(\d+)/", m.group(0))
        t = re.search(r'title="([^"]+)"|alt="([^"]+)"', m.group(0))
        if v and t:
            out.append((v.group(1), html.unescape(t.group(1) or t.group(2))))
    return out


def clip_license(cid: str, known: dict) -> str:
    """The clip's licence as Mixkit labels it on its own page: "Free", "Mixkit Restricted License",
    or "" if it can't be read (treated as not free). known = remembered licences (state)."""
    import re
    import time
    import requests
    if cid in known:
        return known[cid]
    for wait in (0, 8, 20):   # Mixkit answers 429 to quick repeated requests: wait and retry
        time.sleep(wait)
        try:
            r = requests.get(f"https://mixkit.co/free-stock-video/x-{cid}/", headers=UA, timeout=20)
        except Exception:
            continue
        if r.status_code == 429:
            continue
        m = re.search(rf'-{cid}/#video".{{0,3000}}?"copyrightNotice":"([^"]*)"', r.text, re.S)
        if m:
            known[cid] = m.group(1)
            return known[cid]
        return ""
    return ""


CLIP_URLS: dict[str, str] = {}   # Pixabay clip id -> its MP4 link (found while searching)


def pixabay_videos(query: str) -> list[tuple[str, str]]:
    """Pixabay free videos (free for business use, no credit needed; free key in PIXABAY_API_KEY): [(id, title)]."""
    import requests
    key = os.environ.get("PIXABAY_API_KEY", "")
    if not key:
        return []
    try:
        res = requests.get("https://pixabay.com/api/videos/", timeout=20,
                           params={"key": key, "q": query[:90], "per_page": 30, "safesearch": "true"}).json().get("hits", [])
    except Exception as e:
        print(f"  ! Pixabay search '{query}' failed: {e}")
        return []
    out = []
    for v in res:
        f = v.get("videos", {}).get("large") or v.get("videos", {}).get("medium") or {}
        if not f.get("url") or (v.get("duration") or 0) < 4:
            continue
        cid = f"pb{v['id']}"
        CLIP_URLS[cid] = f["url"]
        out.append((cid, v.get("tags", query)))
    return out


def shorter(query: str) -> list[str]:
    """A search and its shorter forms (Mixkit only matches short, simple words): 'indian woman choosing clothes'
    -> 'woman choosing clothes' -> 'choosing clothes' -> 'clothes'."""
    words = [w for w in query.lower().split() if w != "indian"]
    return list(dict.fromkeys([query] + [" ".join(words[i:]) for i in range(len(words)) if words[i:]]))


FAKE_CLIP = re.compile(r"(?i)\b(3d|cgi|animat\w*|cartoon\w*|render\w*|illustrat\w*|motion graphics?|abstract)\b")
CLIP_FILES: dict[str, str] = {}   # clip id -> file downloaded while pick_clip looked at it (download_clip reuses it)


def pick_clip(post: dict, state: dict, _retry: bool = False) -> str:
    """See pick_clip_once; when no clip fits, ONE more search with simpler, everyday words (owner, 16 Oct 2026:
    videos keep people watching better than photos) before the caller falls back to photos."""
    try:
        return pick_clip_once(post, state)
    except RuntimeError as e:
        if _retry or not post.get("clip_wanted"):
            raise
        import fun
        schema = {"type": "object", "properties": {"searches": {"type": "array", "items": {"type": "string"},
                  "description": "3 searches, 1-3 common English words each"}}, "required": ["searches"],
                  "additionalProperties": False}
        try:
            simple = fun.claude_json("Give 3 very simple stock-video searches (1-3 common English words) that show the "
                                     "same everyday scene or feeling in a generic way, e.g. 'woman cooking', 'family "
                                     "festival', 'man laughing phone'.", f"Wanted clip: {post['clip_wanted']}\nTried: "
                                     f"{post.get('clip_search', [])}", schema, CONFIG.get("membership_model", "sonnet"),
                                     purpose="clip search retry")["searches"][:3]
        except Exception:
            raise e
        print(f"  ! {e}: one more video search with simpler words {simple}")
        return pick_clip({**post, "clip_search": simple}, state, _retry=True)


def pick_clip_once(post: dict, state: dict) -> str:
    """The best-fitting free stock clip for a joke (owner, 15 Oct 2026: 'best Reels, never fail'): Pixabay videos,
    then Mixkit (only its Free licence: Restricted forbids business social media). 3D/CGI/cartoon clips are skipped;
    the AI ranks the clips by title (fits 5/10 or better), then looks at frames of the best 3 (`look_at_clips`).
    Raises RuntimeError if none fits (the caller falls back to photos, then an AI picture, then a branded
    background). Returns the clip id (Pixabay 'pb…', Mixkit plain). (Pexels: no new API keys, 15 Oct 2026.)"""
    import fun
    import status
    status.step("Finding the best free clip", 4)
    used = set(state.get("reel_clips", []))
    known = state.setdefault("clip_licences", {})
    found: dict[str, str] = {}
    queries = list(dict.fromkeys(q.strip() for q in post["clip_search"] if q.strip()))
    if queries and "indian" not in queries[0].lower():   # Indian faces first (owner, 14 Oct 2026)
        queries.insert(0, f"indian {queries[0]}")
    backups = ["shocked", "surprised", "laughing", "panic", "facepalm"] if post.get("punch") else \
        ["friends laughing", "people talking", "indian people"]
    for q in queries:   # the big library first: it understands longer searches
        for cid, title in pixabay_videos(q)[:20]:
            if cid not in used:
                found.setdefault(cid, title)
        if len(found) >= 40:
            break
    for q in queries + backups:
        for short in shorter(q):   # a search with no results is retried shorter
            hits = mixkit_search(short)
            for cid, title in hits:
                if cid not in used and known.get(cid, "Free") == "Free":
                    found.setdefault(cid, title)
            if hits:
                break
        if len(found) >= 70:
            break
    fake = [cid for cid, title in found.items() if FAKE_CLIP.search(title)]
    found = {cid: title for cid, title in found.items() if cid not in fake}   # real footage only (owner, 15 Oct 2026)
    if fake:
        print(f"  skipped {len(fake)} 3D/CGI/cartoon clip(s)")
    if not found:
        raise RuntimeError(f"no stock clips for {post['clip_search']}")
    listing = "\n".join(f"{cid}: {title}" for cid, title in list(found.items())[:70])
    schema = {"type": "object", "properties": {"ranked": {"type": "array", "description": "Best 12 clips, best first",
              "items": {"type": "object", "properties": {"id": {"type": "string"}, "fits": {"type": "integer"}},
                        "required": ["id", "fits"], "additionalProperties": False}}},
              "required": ["ranked"], "additionalProperties": False}
    ranked = fun.claude_json("You pick stock video clips for funny Instagram Reels for Indian viewers. Rank by title "
                             "the 12 clips that best show the wanted moment; fits = 1-10. When two fit equally, "
                             "prefer the one whose title shows Indian / South Asian people. For a festival, fasting "
                             "or religious joke never a clip with meat, alcohol or smoking (fits = 1).",
                             f"Joke: {post.get('text') or ' '.join(post.get('setup', [])) + ' -> ' + post.get('punch', '')}\n"
                             f"Wanted clip: {post['clip_wanted']}\n\nClips (id: title):\n{listing}",
                             schema, CONFIG.get("membership_model", "sonnet"), purpose="clip pick")["ranked"]
    picks = []
    for r in ranked[:12]:   # Mixkit: many good clips are Restricted, keep going until 3 Free ones fit
        if r["id"] not in found or r["fits"] < 5 or r["id"] in picks:
            continue
        lic = "Free" if r["id"].startswith("pb") else clip_license(r["id"], known)
        if lic == "Free":
            print(f"  shortlisted clip {r['id']}: {found[r['id']]} (fits {r['fits']}/10 by title)")
            picks.append(r["id"])
            if len(picks) == 3:
                break
        else:
            print(f"  skipped clip {r['id']} ({found[r['id']]}): licence '{lic or 'unknown'}'")
    if not picks:
        raise RuntimeError("no stock clip fits this joke well enough")
    return look_at_clips(post, picks, found)


def look_at_clips(post: dict, picks: list[str], found: dict[str, str]) -> str:
    """The AI looks at 2 frames of each shortlisted clip and keeps the one that clearly shows the moment in real,
    bright, sharp footage (owner, 15 Oct 2026: a dull grey 3D 'person in bed' clip had been picked by its tags).
    Raises RuntimeError if none looks right. If the check itself fails, the best clip by title is used."""
    import tempfile
    import fun
    import atexit
    folder = tempfile.mkdtemp(prefix="clips_")
    atexit.register(shutil.rmtree, folder, True)
    shown = []
    for n, cid in enumerate(picks, 1):
        try:
            path, secs = download_clip(cid, folder)
        except Exception as e:
            print(f"  ! clip {cid}: {str(e)[:80]}")
            continue
        CLIP_FILES[cid] = path
        for k, at in enumerate((0.3, 0.7), 1):
            subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-ss", f"{secs * at:.2f}", "-i", path, "-frames:v", "1",
                            "-vf", "scale=480:-2", os.path.join(folder, f"clip{n}_{k}.jpg")], capture_output=True)
        if os.path.exists(os.path.join(folder, f"clip{n}_1.jpg")):
            shown.append((n, cid))
    if not shown:
        raise RuntimeError("the shortlisted clips couldn't be downloaded")
    schema = {"type": "object", "properties": {"clips": {"type": "array", "items": {"type": "object", "properties": {
        "clip": {"type": "integer"},
        "shows": {"type": "integer", "description": "1-10: how clearly the frames show the wanted moment"},
        "clear": {"type": "boolean", "description": "Real footage (not 3D/CGI/cartoon), bright and sharp, people or "
                  "objects easy to see; false if dark, blurry, blank, mostly grey or empty"},
        "why": {"type": "string"}}, "required": ["clip", "shows", "clear", "why"], "additionalProperties": False}}},
        "required": ["clips"], "additionalProperties": False}
    listing = "\n".join(f"Clip {n}: clip{n}_1.jpg and clip{n}_2.jpg (title: {found.get(cid, '')})" for n, cid in shown)
    try:
        checked = fun.claude_json(
            "You check stock video clips for funny Instagram Reels for young Indians. Open every frame with the Read "
            "tool. A clip is the background behind the joke's text: it must clearly show the wanted moment (or a very "
            "close one) in real, bright, sharp footage. Phones show the middle of the frame for full-screen Reels.",
            f"Joke: {post.get('text') or ' '.join(post.get('setup', [])) + ' -> ' + post.get('punch', '')}\n"
            f"Wanted clip: {post['clip_wanted']}\n\n{listing}",
            schema, CONFIG.get("membership_model", "sonnet"), purpose="clip look", folder=folder)["clips"]
    except Exception as e:
        print(f"  ! clip look failed ({str(e)[:80]}): best clip by title")
        return shown[0][1]
    by_n = dict(shown)
    for c in checked:
        if c["clip"] in by_n:
            print(f"  looked at clip {by_n[c['clip']]}: shows {c['shows']}/10, {'clear' if c['clear'] else 'NOT clear'}"
                  f" ({c['why'][:90]})")
    good = sorted((c for c in checked if c["clip"] in by_n and c["clear"] and c["shows"] >= 6), key=lambda c: -c["shows"])
    if not good:
        raise RuntimeError("no stock clip looks right for this joke")
    cid = by_n[good[0]["clip"]]
    print(f"  clip {cid}: {found.get(cid, '')} (looks {good[0]['shows']}/10)")
    return cid


def download_clip(cid: str, folder: str) -> tuple[str, float]:
    """A stock clip -> (path, seconds): Pixabay by the link found while searching, Mixkit 720p (else 1080p/360p)."""
    import re
    import requests
    path = os.path.join(folder, f"{cid}.mp4")
    links = [CLIP_URLS[cid]] if cid in CLIP_URLS else \
        [f"https://assets.mixkit.co/videos/{cid}/{cid}-{res}.mp4" for res in (720, 1080, 360)]
    if CLIP_FILES.get(cid, path) != path and os.path.exists(CLIP_FILES[cid]):
        shutil.copy(CLIP_FILES[cid], path)   # already downloaded while pick_clip looked at it
    else:
        for link in links:
            r = requests.get(link, headers=UA, timeout=120)
            if r.status_code == 200 and len(r.content) > 50_000:
                open(path, "wb").write(r.content)
                break
        else:
            raise RuntimeError(f"clip {cid} download failed")
    info = subprocess.run([ffmpeg_exe(), "-i", path], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info)
    secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    if secs < 3:
        raise RuntimeError(f"clip {cid} too short ({secs:.1f}s)")
    return path, secs


def save_reel(feed: list, state: dict, cover: Image.Image, mp4: str, post_id: str, entry: dict) -> None:
    from datetime import datetime, timezone
    import generate as g
    if entry.get("caption"):   # 📅 'Follow @khabarbawaal: roz raat 8:30 baje naya Bachpan 🧒' (owner, 16 Oct 2026)
        entry = {**entry, "caption": re.sub(rf"Follow {re.escape(CONFIG['handle'])} for daily [^\n]*",
                                            f"Follow {CONFIG['handle']}: {series_line(SERIES_KIND)[1]}", entry["caption"], count=1)}
    name = f"posts/{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{post_id}.mp4"
    (g.DOCS / "posts").mkdir(parents=True, exist_ok=True)
    shutil.copy(mp4, g.DOCS / name)
    state["songs_recent"] = (state.get("songs_recent", []) + [x["title"] for x in entry.get("songs", [])])[-40:]
    g.save_post(feed, state, cover, post_id, {"kind": "reel", "media": "reel", "video": name, **entry})
    import status
    status.done(f"Ready: {entry['headline'][:90]}", post=post_id)


def topic_articles(topic: str) -> str:
    """Today's news about a joke's trend (2 articles), so the proofreader can check its facts (dates, names)."""
    import carousel
    import create_post
    out = []
    for a in create_post.bing_articles(topic[:90], limit=4, newest=True)[:2]:
        text = carousel.article_text(carousel.page_html(a["url"]), limit=2500)
        out.append(f"({a['site']}, {a.get('date', '')[:10]}) {a['title']}\n{text}")
    return "\n\n".join(out)


APP_STOP = {"make", "create", "funny", "reel", "post", "meme", "using", "instead", "nowadays", "match", "making",
            "people", "with", "from", "about", "that", "this", "when", "they", "them", "their", "like", "your", "have"}


def named_apps(text: str) -> list[dict]:
    """Phone apps named in the owner's words, from the free App Store search (India): a word counts only when an
    app's name starts with it (e.g. 'hopr' -> 'Hopr: Carpool, Ride Share App')."""
    import requests
    out = []
    for word in dict.fromkeys(w for w in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", text) if w.lower() not in APP_STOP):
        if len(out) >= 3:
            break
        try:
            res = requests.get("https://itunes.apple.com/search", timeout=15, params={
                "term": word, "entity": "software", "country": "in", "limit": 3}).json().get("results", [])
        except Exception:
            continue
        app = next((x for x in res if re.split(r"[\s:\-–]", x.get("trackName", ""))[0].lower() == word.lower()), None)
        if app:
            out.append({"name": word.capitalize() if word.islower() else word, "title": app["trackName"],
                        "about": re.sub(r"\s+", " ", app.get("description", ""))[:220]})
    return out


UNDERSTAND_SCHEMA = {"type": "object", "properties": {
    "meaning": {"type": "string", "description": "One line: what the owner means and what the Reel should be about, "
                "e.g. 'childhood nostalgia: kids caught by mummy after a naughty act'"},
    "versus": {"type": "boolean", "description": "true only if the named things are rivals or being compared"},
    "named": {"type": "array", "description": "Real things the owner names ON PURPOSE (empty for an everyday moment, "
              "feeling or situation)", "items": {"type": "object", "properties": {
                  "name": {"type": "string", "description": "As the owner wrote it"},
                  "kind": {"type": "string", "enum": ["app", "person", "party", "team", "film_show", "brand", "event_place"]},
                  "lookup": {"type": "string", "description": "app: its App Store name; person/party/team/film_show: its "
                             "exact English Wikipedia page title; brand/event_place: a short English news search"}},
                  "required": ["name", "kind", "lookup"], "additionalProperties": False}}},
    "required": ["meaning", "versus", "named"], "additionalProperties": False}


def understand_topic(topic: str, apps: list[dict], news: str) -> dict:
    """What the owner's ➕ Create words mean, and which real things they name (owner, 15 Oct 2026: "you need to
    understand the context every time, it cannot be an app every time": 'bachpan' matched a school app and the Reel
    showed its screenshots). Picks the pictures: an everyday moment gets a video clip, named things their own pictures."""
    import fun
    found = "\n".join(f"- {a['name']}: an App Store app is called '{a['title']}'" for a in apps)
    return fun.claude_json(
        "You read a request from the owner of an Indian Hinglish meme page who wants a funny Reel. Understand what "
        "they mean. List only real things they name on purpose: apps, people, parties, teams, films/shows, brands, "
        "events or places. Ordinary Hindi/English words (bachpan, mummy, kaun, dost, office) are never apps or brands, "
        "even when an app shares the word.",
        f"Owner's words: {topic}\n\nWords that match an App Store app name (only a coincidence unless the owner "
        f"means that app):\n{found or '(none)'}\n\nToday's news about it:\n{news[:2000] or '(none)'}",
        UNDERSTAND_SCHEMA, CONFIG.get("membership_model", "sonnet"), purpose="reel topic meaning")


def stock_photos(query: str, want: int = 3) -> list[Image.Image]:
    """Free stock photos (Pixabay: free for business use, no credit needed) when no video clip fits."""
    import requests
    import generate as g
    links = []
    if os.environ.get("PIXABAY_API_KEY"):
        try:
            links += [p["largeImageURL"] for p in requests.get("https://pixabay.com/api/", timeout=20, params={
                "key": os.environ["PIXABAY_API_KEY"], "q": query[:90], "image_type": "photo", "per_page": 8,
                "safesearch": "true"}).json().get("hits", [])]
        except Exception as e:
            print(f"  ! Pixabay photos '{query}' failed: {e}")
    out = []
    for link in links:
        img = g.download_image(link)
        if img is not None:
            out.append(img.convert("RGB"))
        if len(out) >= want:
            break
    return out


JOKE_ART = ("Vibrant modern Indian comic illustration, bold clean lines, bright cheerful colours, expressive faces, "
            "Gen Z meme style, one clear funny moment, the main people large in the middle of the picture. ")


def ai_picture(scene: str, tmp: str, landscape: bool) -> tuple[Image.Image, str] | None:
    """🎨 A free AI picture of the joke's moment (the god-picture demos: Z-Image -> FLUX.2 -> FLUX, no account) when no
    clip or photo fits (owner, 15 Oct 2026: 'people don't like the plain KhabarBawaal page'). The AI checks it (hands,
    text, nothing off); 2 tries. Returns (picture, model) or None (busy / not fit)."""
    import random
    import fun
    folder = os.path.join(tmp, "joke_art")
    os.makedirs(folder, exist_ok=True)
    check = {"type": "object", "properties": {"ok": {"type": "boolean"}, "reason": {"type": "string"}},
             "required": ["ok", "reason"], "additionalProperties": False}
    for k in range(2):
        try:
            img, model = flux_image(JOKE_ART + scene.strip() + " Only the people described, ordinary young Indians, "
                                    "no celebrities or politicians, no logos or brands." + FLUX_AVOID,
                                    random.randint(1, 999999))
        except Exception as e:
            print(f"  ! AI picture: {e}")
            return None
        name = f"art{k + 1}.jpg"
        thumb = img.copy()
        thumb.thumbnail((576, 1024))
        thumb.save(os.path.join(folder, name), quality=85)
        ok = fun.claude_json("You check an AI picture for a funny Instagram Reel.",
                             f"Open {name} with the Read tool. It should show: {scene}\nIs it fit to post? Reject only "
                             "real problems: broken or extra hands, fingers, legs or faces, any text or letters, a "
                             "celebrity or politician, anything scary, offensive or revealing, a messy picture. Small "
                             "differences from the description are fine (extra people in the background, other "
                             "clothes, another pose or setting).",
                             check, CONFIG.get("membership_model", "sonnet"), folder=folder, purpose="joke art check")
        print(f"  AI picture {k + 1} ({model}): {'ok' if ok['ok'] else 'rejected'}: {ok['reason'][:100]}")
        if ok["ok"]:
            img = img.convert("RGB")
            if landscape:   # the meme window is wide: the middle band of the tall picture
                h = int(img.width * 9 / 16)
                y0 = max(0, int((img.height - h) * 0.45))
                img = img.crop((0, y0, img.width, y0 + h))
            return img, model
    return None


def fallback_visual(post: dict, size: tuple[int, int], tmp: str) -> tuple[str, float, str]:
    """No stock clip fits a joke: free stock photos -> news photos of the topic -> 🎨 an AI picture of the moment ->
    the branded background as the very last option (owner, 15 Oct 2026). Returns (clip path, seconds, note)."""
    import status
    query = post.get("photo_query", "").strip() or post.get("based_on", "") or " ".join(post.get("clip_search", [])[:1])
    status.step("No clip fits: finding photos or painting a picture", 4)
    photos, note = stock_photos(query) or topic_photos(query, want=3), "photos"
    if not photos:
        art = ai_picture(post.get("clip_wanted") or post.get("based_on", "") or query, tmp, landscape=size[0] > size[1])
        if art:
            photos, note = [art[0]], f"AI illustration ({art[1]})"
        else:
            photos, note = [brand_background()], "branded background"
    print(f"  backup picture: {note}")
    clip = os.path.join(tmp, "photos.mp4")
    photo_clip(photos, *size, 14.0, clip, fill=size[0] > size[1])
    return clip, 14.0, note


def brand_background() -> Image.Image:
    """The last resort behind a joke when no clip or photo is found: a clean dark gradient with a soft brand glow."""
    img = Image.new("RGB", (1200, 675), "#111111")
    d = ImageDraw.Draw(img)
    for y in range(675):
        d.line((0, y, 1200, y), fill=(20 + y // 25, 18 + y // 40, 26 + y // 18))
    glow = Image.new("RGB", (1200, 675), "#000000")
    ImageDraw.Draw(glow).ellipse((380, 120, 820, 560), fill=YELLOW)
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(140)), 0.25)
    logo = logo_img(520, 120)
    if logo:
        img.paste(logo, ((1200 - logo.width) // 2, (675 - logo.height) // 2), logo)
    return img


def app_photos(names: list[str], vs: bool = True) -> list[Image.Image]:
    """Apps the owner's idea names (owner, 14 Oct 2026: 'no photos or logo of Hopr and Tinder'): their official
    icons from the free App Store search (India), as a 'Hopr VS Tinder' card, then one screenshot of each."""
    import re
    import requests
    import generate as g
    found = []
    for name in [n.strip() for n in names if n.strip()][:3]:
        try:
            res = requests.get("https://itunes.apple.com/search", timeout=20, params={
                "term": name, "entity": "software", "country": "in", "limit": 5}).json().get("results", [])
        except Exception:
            continue
        app = next((r for r in res if name.lower() in r.get("trackName", "").lower()), None)
        icon = app and g.download_image(app.get("artworkUrl512", ""))
        if icon:
            big = [re.sub(r"/\d+x\d+bb\.(jpg|png)$", "/700x1400bb.jpg", u) for u in app.get("screenshotUrls", [])[:2]]
            shot = next((s for s in (g.download_image(u) for u in big) if s), None)   # the list gives 320 px previews
            found.append((name, icon.convert("RGBA"), shot))
    if not found:
        return []
    return [names_card([(name, icon) for name, icon, _ in found], vs)] + [shot.convert("RGB") for _, _, shot in found if shot]


def names_card(items: list[tuple[str, Image.Image]], vs: bool = True) -> Image.Image:
    """'A VS B' card: each picture in a rounded tile with its name under it (logos sit whole on white, photos are
    cropped to fill). vs=False (owner, 16 Oct 2026: 'Dipke VS CJP' for a leader and his own party): no 'VS'."""
    card = Image.new("RGB", (1200, 675), "#111111")
    d = ImageDraw.Draw(card)
    for y in range(675):
        d.line((0, y, 1200, y), fill=(17 + y // 30, 17, 17 + y // 25))
    size, gap = 300, 160
    x = (1200 - len(items) * size - (len(items) - 1) * gap) // 2
    for i, (name, pic) in enumerate(items):
        if pic.mode == "RGBA" and pic.getextrema()[3][0] < 250:   # a logo with a see-through background
            tile = Image.new("RGBA", (size, size), "#FFFFFF")
            logo = pic.copy()
            logo.thumbnail((size - 40, size - 40), Image.LANCZOS)
            tile.alpha_composite(logo, ((size - logo.width) // 2, (size - logo.height) // 2))
        else:
            tile = ImageOps.fit(pic.convert("RGBA"), (size, size), Image.LANCZOS, centering=(0.5, 0.3))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=66, fill=255)
        card.paste(tile, (x, 150), mask)
        d.text((x + size // 2, 150 + size + 50), printable(name)[:16], font=font("Poppins-Bold.ttf", 56), fill="#FFFFFF",
               anchor="mm")
        if i < len(items) - 1 and vs:
            d.text((x + size + gap // 2, 150 + size // 2), "VS", font=font("Anton-Regular.ttf", 110), fill=YELLOW, anchor="mm")
        x += size + gap
    return card


def name_card(name: str) -> Image.Image:
    """A red card with the name's first letter: a person/party without a free photo (never a random news photo,
    it could be someone else)."""
    img = Image.new("RGB", (400, 400), RED)
    ImageDraw.Draw(img).text((200, 200), printable(name)[:1].upper(), font=font("Anton-Regular.ttf", 220),
                             fill="#FFFFFF", anchor="mm")
    return img


def look_at_photos(photos: list[Image.Image], about: str, tmp: str) -> list[Image.Image]:
    """The AI looks at news photos before a joke Reel uses them and keeps only those that clearly show what the joke
    is about (owner, 15 Oct 2026: a Ramayana trailer meme showed Thai dancers). [] if the check fails."""
    import fun
    if not photos:
        return []
    folder = os.path.join(tmp, "photo_check")
    os.makedirs(folder, exist_ok=True)
    for n, img in enumerate(photos, 1):
        small = img.convert("RGB")
        small.thumbnail((640, 640))
        small.save(os.path.join(folder, f"photo{n}.jpg"), quality=85)
    schema = {"type": "object", "properties": {"keep": {"type": "array", "items": {"type": "integer"},
              "description": "Numbers of the photos to use, best first"}, "why": {"type": "string"}},
              "required": ["keep", "why"], "additionalProperties": False}
    try:
        res = fun.claude_json(
            "You check news photos for a funny Instagram Reel for young Indians. Open every photo with the Read tool. "
            "Keep only photos that clearly show the named film/show, person, team, brand or event (its stills, poster, "
            "cast at its event, the person): not a different film, country or person, not unrelated stock, not a "
            "collage or screenshot full of text, sharp enough for a phone screen.",
            f"The joke is about: {about}\n\nPhotos: " + ", ".join(f"photo{n}.jpg" for n in range(1, len(photos) + 1)),
            schema, CONFIG.get("membership_model", "sonnet"), purpose="photo look", folder=folder)
    except Exception as e:
        print(f"  ! photo look failed ({str(e)[:80]}): no news photos")
        return []
    keep = [photos[k - 1] for k in dict.fromkeys(res["keep"]) if 1 <= k <= len(photos)]
    print(f"  looked at {len(photos)} news photo(s): kept {len(keep)} ({res['why'][:100]})")
    return keep


def context_photos(things: list[dict], about: str, tmp: str, vs: bool = True) -> tuple[list[Image.Image], list[str]]:
    """Photos of what a joke Reel is about (owner, 15 Oct 2026: 'the post should have the context photos'):
    films/shows/events/brands -> today's news photos, checked by the AI (`look_at_photos`), first; then
    people/teams/parties -> their credited Wikipedia photos with an 'A VS B' card; apps -> App Store icons +
    screens. Returns (photos, caption credits); fewer than 2 photos -> the caller uses a stock clip."""
    news = []
    for t in [t for t in things if t["kind"] in ("film_show", "event_place", "brand")][:2]:
        news += topic_photos(t["lookup"] or t["name"], want=4)
    news = look_at_photos(news, about, tmp)[:3]
    pics, credits = named_photos([t for t in things if t["kind"] in ("app", "person", "team", "party")], vs=vs)
    return (news + pics)[:5], credits


def named_photos(named: list[dict], fill_missing: bool = False, vs: bool = True) -> tuple[list[Image.Image], list[str]]:
    """Pictures of the real things the owner's ➕ Create idea names, by what they are (owner, 15 Oct 2026): apps ->
    App Store icons + screenshots; people/parties/teams/films -> their free Wikipedia pictures (credited, like the
    poll; never a random news photo, it could be someone else); brands/events -> news photos.
    fill_missing: a person/party without a free Wikipedia picture gets a `name_card` on the 'VS' card.
    Returns (pictures, caption credits)."""
    apps = [n["name"] for n in named if n["kind"] == "app"][:3]   # the short name labels the logo ('Hopr')
    photos = app_photos(apps, vs) if apps else []
    wiki, credits = [], []
    for n in [n for n in named if n["kind"] in ("person", "party", "team", "film_show")][:3]:
        img, credit = wiki_thumb(n["lookup"] or n["name"], size=960)
        if img is not None:
            wiki.append((n["name"], img))
            credits.append(f"{printable(n['name'])}: {credit}")
        elif fill_missing:
            wiki.append((n["name"], name_card(n["name"])))
    if wiki:
        photos += [names_card(wiki, vs)] + [img.convert("RGB") for _, img in wiki
                                        if min(img.size) >= 500 and not (img.mode == "RGBA" and img.getextrema()[3][0] < 250)]
    for n in [n for n in named if n["kind"] in ("brand", "event_place")][:2]:
        if len(photos) < 4:
            photos += topic_photos(n["lookup"] or n["name"], want=2)
    return photos, credits


def topic_photos(query: str, want: int = 4) -> list[Image.Image]:
    """Real photos of what the owner's idea names (apps, brands, people, events): the main images of news
    articles about it, no repeats (➕ Create, owner 14 Oct 2026)."""
    import create_post
    import generate as g
    out, seen = [], set()
    for a in create_post.bing_articles(query[:90], limit=10):
        img = g.best_photo({"link": a["url"], "image": ""})
        if img is None or min(img.size) < 400:
            continue
        key = img.convert("L").resize((8, 8)).tobytes()
        if key not in seen:
            seen.add(key)
            out.append(img.convert("RGB"))
        if len(out) >= want:
            break
    return out


def photo_clip(photos: list[Image.Image], w: int, h: int, secs: float, out_mp4: str, fill: bool = False) -> None:
    """A 'clip' made of real photos, one after another with a slow zoom, for the joke layouts: the meme window
    (w x h landscape: photo fills it) or full screen (whole photo on a blurred copy of itself)."""
    enc, per = Encoder(out_mp4, size=(w, h)), secs / len(photos)
    for i in range(int(secs * FPS)):
        t = i / FPS
        n = min(len(photos) - 1, int(t / per))
        img, z = photos[n], 1.0 + 0.06 * (t - n * per) / per
        if fill:   # framed windows: the photo fills it (top-biased crop), never a blurred copy (owner, 16 Oct 2026)
            k = max(w / img.width, h / img.height) * z
            big = img.resize((int(img.width * k) + 1, int(img.height * k) + 1), Image.BICUBIC)
            x0, y0 = (big.width - w) // 2, int((big.height - h) * 0.3)
            frame = big.crop((x0, y0, x0 + w, y0 + h))
        elif h > w:
            k = max(w / img.width, h / img.height)
            frame = Image.blend(img.resize((int(img.width * k) + 1, int(img.height * k) + 1)).crop((0, 0, w, h))
                                .filter(ImageFilter.GaussianBlur(40)), Image.new("RGB", (w, h), "#000000"), 0.5)
            fit = img.resize((int(w * z), int(img.height * w / img.width * z)), Image.BICUBIC)
            frame.paste(fit.crop(((fit.width - w) // 2, 0, (fit.width - w) // 2 + w, fit.height)),
                        (0, (h - fit.height) // 2))
        elif img.height > img.width:   # an app screenshot: the whole screen, centred on a blurred copy
            k = max(w / img.width, h / img.height)
            frame = Image.blend(img.resize((int(img.width * k) + 1, int(img.height * k) + 1)).crop((0, 0, w, h))
                                .filter(ImageFilter.GaussianBlur(30)), Image.new("RGB", (w, h), "#000000"), 0.5)
            fit = img.resize((int(img.width * (h - 20) / img.height * z), int((h - 20) * z)), Image.BICUBIC)
            frame.paste(fit, ((w - fit.width) // 2, (h - fit.height) // 2))
        else:
            k = max(w / img.width, h / img.height) * z
            big = img.resize((int(img.width * k) + 1, int(img.height * k) + 1), Image.BICUBIC)
            x0, y0 = (big.width - w) // 2, int((big.height - h) * 0.3)
            frame = big.crop((x0, y0, x0 + w, y0 + h))
        enc.add(frame)
    enc.close()


def build_joke_reel(kind: str, state: dict, tmp: str, evergreen: bool = False,
                    topic: str = "") -> tuple[Image.Image, str, dict]:
    """Write, judge, find a clip, render and proofread a joke Reel.
    Returns (cover, mp4 path in tmp, feed fields); also notes the clip/topic in state."""
    import generate as g
    import proofread
    import status
    status.step("Writing 3 jokes", 2)
    notes, named, versus = "", [], True
    if topic:   # owner, 15 Oct 2026: "understand the context every time, it cannot be an app every time"
        apps = named_apps(topic)    # App Store names matching a word: ordinary words ('bachpan') match too
        search = re.sub(r"(?i)^\s*(please\s+)?(make|create|bana\w*)\s+(a|an|ek)?\s*(funny\s+)?(reel|post|meme)\s+"
                        r"(of|on|about|for|ki|ka|pe|par)?\s*", "", topic)
        news = topic_articles(search)
        sense = understand_topic(topic, apps, news)
        named, versus = sense["named"], sense.get("versus", True)
        meant = {n["name"].lower() for n in named if n["kind"] == "app"} | \
                {n["lookup"].lower() for n in named if n["kind"] == "app"}
        apps = [a for a in apps if a["name"].lower() in meant or any(a["name"].lower() in m for m in meant)]
        print(f"  owner means: {sense['meaning']} | named: {[(n['name'], n['kind']) for n in named]}")
        notes = (f"What the owner means: {sense['meaning']}" +
                 (("\nReal things the owner names: " + "; ".join(f"{n['name']} ({n['kind'].replace('_', '/')})"
                                                              for n in named)) if named else "") +
                 (("\nApps named in the idea (App Store; owner, 14 Oct 2026: Hopr is a carpool app, the writer thought "
                   "it was a dating app): " + "; ".join(f"{a['name']} = {a['title']}: {a['about']}" for a in apps))
                  if apps else "") +
                 (f"\n\nLatest news about it (use these facts, invent none):\n{news[:3000]}" if news and named else ""))
    post = write_joke(kind, state, evergreen, topic, notes)
    status.step(f"Judge picked: {post['based_on'][:80]}", 3)
    # ➕ Create: pictures of the real things the idea names (owner, 14 Oct 2026: "no photos or logo of Hopr and
    # Tinder"); an everyday moment ('bachpan me aisa kaun kiya?') gets a video clip like the daily jokes.
    art_note = ""
    # Daily trend jokes (owner, 15 Oct 2026): what the writer says the joke is about; ➕ Create: what the owner named.
    joke_text = " ".join([post.get("text", ""), " ".join(post.get("setup", [])), post.get("punch", ""), post.get("opener", "")])
    things = named or ([] if evergreen else [t for t in post.get("about", [])
                                              if named_in([t["name"], t["lookup"]], joke_text)])   # only what the joke names
    photos, credits = context_photos(things, f"{post['based_on']} ({', '.join(t['name'] for t in things)})", tmp,
                                     vs=versus if named else post.get("versus", True)) if things else ([], [])
    clip = secs = cid = real = None
    framed = False   # 🖼️ photos (not a video) behind a Tag that friend joke: the framed layout

    def video():   # a stock clip; none fits: photos -> 🎨 AI picture -> branded background
        nonlocal clip, secs, cid, real, art_note, framed
        try:
            cid = pick_clip(post, state)
            (clip, secs), real = download_clip(cid, tmp), False
        except RuntimeError as e:
            print(f"  ! {e}")
            clip, secs, art_note = fallback_visual(post, (FRAME_W, FRAME_H) if kind in CLIP_KINDS else (RW, 608), tmp)
            real, cid, framed = True, None, kind in CLIP_KINDS
    if len(photos) >= 2:
        status.step(f"Real photos: {', '.join(t['name'] for t in things)[:60]}", 4)
        # As long as the Reel's text, every photo the same time, at least 2 s each (owner, 15 Oct 2026: the
        # Ramayana meme's 4th photo was never reached)
        secs = (min(max(CLIP_SECONDS, read_secs(post["text"], 4.5)), CLIP_MAX) if kind in CLIP_KINDS else
                min(8.5, max(MEME_SECONDS * 0.55, read_secs(" ".join(post["setup"]), 2.5))) +
                min(8.5, max(MEME_SECONDS * 0.45, read_secs(post["punch"], 2.5))))
        photos = photos[:max(2, int(secs // 2))]
        clip, cid, real, framed = os.path.join(tmp, "photos.mp4"), None, True, kind in CLIP_KINDS
        photo_clip(photos, *((FRAME_W, FRAME_H) if kind in CLIP_KINDS else (RW, 608)), secs, clip, fill=True)
        print(f"  real photos: {len(photos)} of {[t['name'] for t in things]}")
    else:
        photos, credits = [], []
        video()

    def caption_of(d):   # Wikipedia pictures need their photographer + licence (owner, 15 Oct 2026)
        return g.fun_caption({**d, "caption": d["caption"].strip() + (
            "\n\nPhotos (Wikimedia Commons): " + "; ".join(credits) if credits else "")})
    out = os.path.join(tmp, "reel.mp4")
    songs = post.pop("songs", [])   # kept out of the proofreader's text
    # Long enough to read the (proofread) text in full; a short stock clip caps Tag that friend (memes freeze).
    if kind in CLIP_KINDS:
        def render(d):   # + 1 s for the TAG THAT FRIEND banner and the lines popping in, + 2 s to read (12 Oct)
            dur = min(max(CLIP_SECONDS, read_secs(d["text"], 4.5)), CLIP_MAX)
            start = 0.0 if real else max(0.0, min(1.0, secs - dur - 0.2))
            hold = max(0.0, dur - (secs - 0.2 - start))   # a short clip freezes on its last frame
            return clip_reel(clip, start, dur, "DESI COMEDY" if kind == "reel_comedy" else "TAG THAT FRIEND", d["text"],
                             d["highlight"], out, hold=hold,
                             opener=d.get("opener", ""), framed=framed)
    else:
        def render(d):   # build-up, then the punchline: each 2.5 s + 0.25 s a word (owner, 12 Oct 2026: +1 s)
            setup_t = min(8.5, max(MEME_SECONDS * 0.55, read_secs(" ".join(d["setup"]), 2.5)))
            punch_t = min(8.5, max(MEME_SECONDS * 0.45, read_secs(d["punch"], 2.5)))
            dur = setup_t + punch_t
            start = 0.0 if real else max(0.0, secs - dur - 0.2)      # reactions usually come late in a clip
            hold = max(0.0, dur - (secs - 0.2))     # a short clip freezes on its last frame, the text keeps its time
            return meme_reel(clip, start, dur, d["setup"], d["punch"], round(setup_t, 2), d["highlight"], out, hold,
                             opener=d.get("opener", ""))
    status.step("Making the video + proofreading", 5)
    if topic:   # the owner's idea is a joke premise, not a news claim: the proofreader keeps it (14 Oct 2026)
        check = (f"THE PAGE OWNER'S OWN JOKE IDEA (a premise, not a news claim: keep its premise and the names in it, "
                 f"including in hashtags; check only spellings and any other stated facts): {topic}\n\n{notes}")
    else:
        check = "" if evergreen else topic_articles(post["based_on"])
    try:
        frames, post, proof = proofread.run(render, post, caption_of, check, strict=True)
    except proofread.Private as e:   # e.g. a school app screenshot with a child's name: a video clip instead
        if not photos:
            raise
        print(f"  ! {e}: a video clip instead of the real photos")
        photos, credits = [], []
        video()
        frames, post, proof = proofread.run(render, post, caption_of, check, strict=True)
    if art_note.startswith("AI"):   # Meta asks for its "AI info" label on AI pictures
        proof = {**proof, "state": "check", "issues": proof.get("issues", []) + [
            f"🎨 The picture is an {art_note}: turn on Instagram's 'AI info' label when posting."]}
    text = post["text"] if kind in CLIP_KINDS else f"{' '.join(post['setup'])} {post['punch']}"
    if cid:
        state["reel_clips"] = (state.get("reel_clips", []) + [cid])[-80:]
    state["reel_topics"] = (state.get("reel_topics", []) + [post["based_on"]])[-60:]
    state["recent_reels"] = (state.get("recent_reels", []) + [printable(text).replace("\n", " ")])[-60:]
    return frames[-1], out, {
        "headline": printable(text).replace("\n", " "),
        "tag": "🎬 REEL · " + ("DESI COMEDY" if kind == "reel_comedy" else "TAG THAT FRIEND" if kind in CLIP_KINDS else "MEME"),
        "caption": g.limit_hashtags(caption_of(post)), "source": "KhabarBawaal Original", "source_url": "",
        "pin_comment": post.get("pin_comment", ""), "songs": songs, "proof": proof}


def make_joke_reel(kind: str, state: dict, feed: list, today: str, tmp: str) -> None:
    cover, mp4, entry = build_joke_reel(kind, state, tmp)
    save_reel(feed, state, cover, mp4, f"{kind}-{today}", entry)


ROTATE_FORMATS = ("bachpan", "friend", "meme", "comedy")   # 7:45 PM, one per day (owner, 19 Oct 2026)


def rotate_format(today: str) -> str:
    """4-day cycle starting Tue 20 Oct 2026 with Bachpan: Bachpan -> Tag that friend -> Meme -> Comedy."""
    from datetime import date
    return ROTATE_FORMATS[(date.fromisoformat(today).toordinal() - date(2026, 10, 20).toordinal()) % 4]


def make_rotate_reel(state: dict, feed: list, today: str, tmp: str) -> None:
    """🎭 7:45 PM rotating slot (owner, 19 Oct 2026: 'try alternate options bachpan / Tag a friend / meme / comedy'),
    saved as reel_rotate-<date> whichever format it is."""
    fmt = rotate_format(today)
    print(f"  rotating slot today: {fmt}")
    if fmt == "bachpan":
        return make_bachpan_reel(state, feed, today, tmp, style_name="moment", save_id=f"reel_rotate-{today}")
    kind = {"friend": "reel_clip", "meme": "reel_meme", "comedy": "reel_comedy"}[fmt]
    cover, mp4, entry = build_joke_reel(kind, state, tmp, evergreen=(kind == "reel_comedy"))   # comedy: everyday life only
    save_reel(feed, state, cover, mp4, f"reel_rotate-{today}", entry)


BACHPAN_STYLES = {   # owner, 15 Oct 2026: both styles, on alternate days (bachpan_style)
    "moment": {"banner": "BACHPAN ME AISA KAUN KIYA?", "foot": "COMMENT KARO: MAINE BHI",
               "tag": "🎬 REEL · BACHPAN ME AISA KAUN KIYA",
               "text": "ONE specific, funny, very relatable thing Indian kids did or believed: a childhood myth, a naughty "
                       "trick, a desi home/school moment that makes people say 'arre ye toh maine bhi kiya!' and comment.",
               "field": "The childhood moment shown under the BACHPAN ME AISA KAUN KIYA? banner",
               "caption": "ask people to comment 'MAINE BHI' / their own bachpan story"},
    "friend": {"banner": "TAG THAT FRIEND", "foot": "TAG KARO USKO", "tag": "🎬 REEL · BACHPAN · TAG THAT FRIEND",
               "text": "'Wo dost jo bachpan mein...': that one childhood friend (or cousin/sibling) everyone had and the "
                       "funny thing they always did, e.g. \"Wo dost jo bachpan mein bijli jaate hi sabse zor se chillata "
                       "tha: 'Hooooo!' Phir bijli aate hi wahi 'Hooooo!'\" (the owner's favourite: already posted, never "
                       "reuse it). \\n line breaks allowed.",
               "field": "Text under a big 'TAG THAT FRIEND' banner, starting 'Wo dost jo bachpan mein'",
               "caption": "ask people to tag that friend"},
}
BACHPAN_SYSTEM = """You write the daily bachpan nostalgia Reel ('{banner}' style) for {page}, an Indian \
Instagram page for 18-34s who grew up in the 90s/2000s. Hinglish in English letters only, never Devanagari.
text = {text} Max 18 words, no emojis, '...' before the funny twist is fine. Known across India (not one region), \
harmless (nothing dangerous a kid could copy: no stunts, jumping, fire, medicines, electricity), no hate.
The moment must be easy to show with a generic free stock video (an object or simple action, e.g. a pencil \
sharpener, an apple, a TV remote, a sweets box, a school bag, a kid hiding under a blanket): clip_search = 3 short \
English searches for it, clip_wanted = what the clip should show.
Use 90s/2000s references everyone knows (Shaktimaan, Doordarshan, Parle-G, Nokia snake, Tazo, Pogo...) when they \
fit: nostalgia gets shared.
caption: 2-3 Hinglish lines that {caption}. pin_comment: a funny follow-up line + a share nudge ('Bachpan ke dost ko \
bhejo 👇'). hashtags: exactly 5 \
(#bachpan #90skids style + high-reach)."""


def bachpan_style(today: str) -> str:
    """Alternate days (owner, 15 Oct 2026): 'friend' on 16 Oct 2026, 'moment' on 17 Oct, and so on."""
    from datetime import date
    return "friend" if date.fromisoformat(today).toordinal() % 2 else "moment"


def make_bachpan_reel(state: dict, feed: list, today: str, tmp: str, style_name: str = "", save_id: str = "") -> None:
    """🧒 8:30 PM bachpan Reel (owner, 15 Oct 2026): one relatable 90s/2000s Indian childhood moment under a big red
    banner over a clip that shows it. Two styles on alternate days (`bachpan_style`): 'BACHPAN ME AISA KAUN KIYA?'
    (the moment itself, 'COMMENT KARO: MAINE BHI') and TAG THAT FRIEND ('Wo dost jo bachpan mein...', the owner's
    favourite). A new moment every day in either style (state.bachpan_recent). Weak jokes get one rewrite with the
    judge's reasons, then the slot tries again."""
    import fun
    import generate as g
    import proofread
    import status
    style = BACHPAN_STYLES[style_name or bachpan_style(today)]
    status.step(f"Writing 3 childhood moments ({style['banner']})", 2)
    songs = trending_songs(state)
    recent = state.get("bachpan_recent", [])
    option = {**CLIP_OPTION, "properties": {**CLIP_OPTION["properties"], "text": {"type": "string", "description":
              style["field"] + ", max 18 words, no emojis"}}}
    model = CONFIG.get("membership_model", "sonnet")

    def write(extra: str = "") -> list[dict]:
        options = fun.claude_json(
            BACHPAN_SYSTEM.format(page=CONFIG["page_name"], **style),
            "Moments already used (never repeat these or the same idea):\n" +
            ("\n".join(f"- {r}" for r in recent[-40:]) or "(none)") +
            "\n\nWrite exactly 3 different childhood moments (different kinds: a myth kids believed, a naughty trick, "
            "a home or school moment)." + extra + viral_memory("for tone and shareability") + songs_prompt(songs)
            + "\nSongs: nostalgic picks fit best.",
            options_schema(option, "Exactly 3 different childhood moments"), model, purpose="reel writing")["options"][:3]
        for o in options:
            o["caption"], o["text"] = unescape(o["caption"]), unescape(o["text"])
        return options
    text_of = lambda o: f"{style['banner']}: {o['text']}  [video: {o['clip_wanted']}]"
    try:
        best = fun.judge_best(write(), text_of, model)
    except fun.NoGoodJoke as e:
        if not e.scores:
            raise
        print("  moments below the judge's bar: rewriting once with the judge's reasons")
        notes = "\n".join(f"- Option {x['option']}: understandable {x['understandable']}, funny {x['funny']}, "
                          f"fresh {x['fresh']}: {x['why']}" for x in e.scores)
        best = fun.judge_best(write(f"\n\nA strict judge (a young Indian on Instagram) rejected your first 3:\n{notes}"
                                    "\nWrite 3 new ones that fix this."), text_of, model)
    print(f"  bachpan moment: {best['text']}")
    status.step(f"Judge picked: {best['text'][:80]}", 3)
    roman_only(best, ["caption", "pin_comment"])
    picked_songs = checked_songs(best.pop("songs", []), songs)
    cid, real, art_note, framed = None, False, "", False
    try:
        cid = pick_clip(best, state)
        clip, secs = download_clip(cid, tmp)
    except RuntimeError as e:   # no fitting clip: photos -> 🎨 AI picture -> branded background, framed
        print(f"  ! {e}")
        clip, secs, art_note = fallback_visual(best, (FRAME_W, FRAME_H), tmp)
        real, framed = True, True
    out = os.path.join(tmp, "reel.mp4")

    def render(d):   # one screen like Tag that friend: 4.5 s + 0.25 s a word, 8.5-12 s
        dur = min(max(CLIP_SECONDS, read_secs(d["text"], 4.5)), CLIP_MAX)
        start = 0.0 if real else max(0.0, min(1.0, secs - dur - 0.2))
        hold = max(0.0, dur - (secs - 0.2 - start))   # a short clip freezes on its last frame
        return clip_reel(clip, start, dur, style["banner"], d["text"], d["highlight"], out, hold=hold, foot=style["foot"],
                         opener=d.get("opener", ""), framed=framed)
    status.step("Making the video + proofreading", 5)
    frames, best, proof = proofread.run(render, best, g.fun_caption, "", strict=True)
    if art_note.startswith("AI"):   # Meta asks for its "AI info" label on AI pictures
        proof = {**proof, "state": "check", "issues": proof.get("issues", []) + [
            f"🎨 The picture is an {art_note}: turn on Instagram's 'AI info' label when posting."]}
    text = printable(best["text"]).replace("\n", " ")
    if cid:
        state["reel_clips"] = (state.get("reel_clips", []) + [cid])[-80:]
    state["bachpan_recent"] = (recent + [text])[-60:]
    state["recent_reels"] = (state.get("recent_reels", []) + [text])[-60:]
    save_reel(feed, state, frames[-1], out, save_id or f"reel_bachpan-{today}", {
        "headline": text, "tag": style["tag"],
        "caption": g.limit_hashtags(g.fun_caption(best)), "source": "KhabarBawaal Original", "source_url": "",
        "pin_comment": best.get("pin_comment", ""), "songs": picked_songs, "proof": proof})


NEWS_SYSTEM = """You make a VIRAL NEWS REEL for {page}, an Indian Instagram page for 18-34s (Hinglish, \
English letters only, never Devanagari): ONE single story people are talking about right now, told over its real \
photos (owner, 15 Oct 2026: 'like the Bittu Tabahi one': one man cleaned a river for 8 months). From the latest \
stories pick the 3 MOST VIRAL / TRENDING, best first: trending on X or Google in India, covered by many sites, \
viral human-interest moments (shocking, inspiring, heart-warming, unbelievable), big Bollywood/OTT/cricket moments, \
with strong visuals. A story everyone will want to watch to the end and share. Rank stories about the BIGGEST \
names young Indians know first (owner, 15 Oct 2026: a Salman Khan + Karisma Kapoor reveal got 1.6k views and many \
shares, most Reels ~200): A-list actors (Salman, Shah Rukh, Aamir, Akshay, Ranbir, Alia, Deepika...), top cricketers \
(Virat, Dhoni, Rohit...), the PM, with a reveal, clash, behind-the-scenes moment or 90s/2000s nostalgia.
Only stories most people across India care about (owner, 14 Oct 2026: Indian stories got ~227 views, foreign or niche \
celebrity news ~15): Indian news, Bollywood/OTT, Team India and IPL, viral Indian moments, big national stories. Skip \
foreign news unless it is about India or Indians, foreign sports and niche or foreign celebrity news.
Rules: real facts only, no invented quotes. Never make jokes about deaths, accidents, crimes or disasters. \
Politics: supportive of the right wing (BJP, NDA), critical of the left and opposition; never pick a story \
that embarrasses the BJP. No hate against any community. """ + RIGHT_LENS


NEWS_KINDS = ("reel_news_early", "reel_news_am", "reel_news_noon", "reel_news_aft", "reel_news_pm", "reel_news",
              "reel_news_night")     # 🔥 viral news Reels: 7:30, 10:30 AM, 1:30, 7:30 PM (the rest by hand; owner, 15 Oct 2026)
TOP5_KINDS = {"reel_top5_am": "night_roundup", "reel_top5": "top10_day"}   # Top 5 Reel -> its specials.TOP10 settings


def recent_news(state: dict, today: str) -> tuple[list[dict], str]:
    """Stories our news Reels covered in the last 2 days (owner, 16 Oct 2026: the Imran Khan notice came twice, from
    two different sites) and the prompt text that tells the pickers to skip them."""
    from datetime import date, timedelta
    since = (date.fromisoformat(today) - timedelta(days=2)).isoformat()
    recent = [r for r in state.get("reel_news_recent", []) if r.get("date", "") >= since]
    text = ("\n\nStories our news Reels already covered in the last 2 days (skip the SAME story, even from another "
            "site, unless there is a big new development):\n" + "\n".join(f"- {r['title']}" for r in recent[-25:])
            if recent else "")
    return recent, text


def make_news_reel(kind: str, state: dict, feed: list, today: str, tmp: str) -> None:
    """🎬 The most viral, visual story of the last hours (the news feeds' lookback_hours) as a photo slideshow
    (owner, 13 Oct 2026: all posts are Reels now; it used to pick from the day's news carousels).
    10 AM / 5 PM: a 📅 planned preview (calendar_plan) due now is the story instead."""
    import calendar_plan
    import create_post
    import fun
    import generate as g
    import schedule
    import status
    used = state.get("reel_news_used", [])
    event = calendar_plan.due(state) if kind in schedule.PREVIEW_KINDS else None
    if event:
        status.step(f"📅 Planned preview: {event['topic'][:60]}", 2)
        event["done"] = True
        stories = [{"id": "", "title": a["title"], "link": a["url"], "source": a["site"], "summary": "", "category": "preview"}
                   for a in create_post.bing_articles(event["search"], limit=8, newest=True) if a.get("title")]
        intro = f"Today's planned preview: {event['topic']}. {event['brief']}\nPick the stories that preview it best.\n\n"
    else:
        status.step("Picking the most viral, visual story", 2)
        stories = [i for i in g.fetch_candidates(set(state.get("seen", []))) if i["link"] not in used][:40]
        intro = ""
    if not stories:
        raise RuntimeError("no fresh news stories to make a Reel from")
    listing = "\n".join(f"{n}. [{s['category']} | {s['source']}] {s['title']}"
                        + (f"\n   {s['summary'][:160]}" if s["summary"] else "") for n, s in enumerate(stories, 1))
    trends = "\n".join(f"- {t} ({h} h)" for t, h in fun.x_trends()[:30]) or "(unavailable)"
    searches = "\n".join(f"- {t}" for t in fun.google_trends()) or "(unavailable)"
    songs = trending_songs(state)
    options = fun.claude_json(NEWS_SYSTEM.format(page=CONFIG["page_name"]),
                              f"{fun.today_context()}\n\n{intro}What India is talking about on X (Twitter) today "
                              f"(hours trending):\n{trends}\n\nWhat India is searching on Google today:\n{searches}"
                              f"\n\nStories:\n{listing}{viral_memory()}{recent_news(state, today)[1]}"
                              f"\n\nGive your top 3 picks, the most viral first."
                              + songs_prompt(songs),
                              options_schema(NEWS_OPTION, "Top 3 picks, best first"),
                              CONFIG.get("membership_model", "sonnet"), purpose="reel news pick")["options"]
    for opt in options:
        if not 1 <= opt["pick"] <= len(stories):
            continue
        story = stories[opt["pick"] - 1]
        item = {"link": story["link"], "source": story["source"], "title": story["title"]}
        print(f"  news reel: {story['title']}")
        status.step(f"Collecting photos: {story['title'][:70]}", 3)
        photos = news_photos(item, opt)
        if len(photos) >= 2:
            break
        print(f"  only {len(photos)} usable photo(s): trying the next story")
    else:
        raise RuntimeError("no story had 2+ good photos")
    cover, out, entry = finish_with_quote({**item, "summary": story.get("summary", "")}, opt, photos, songs, tmp, None,
                                          f"{story['title']}. {story.get('summary', '')}")
    state["reel_news_used"] = (used + [story["link"]])[-80:]
    state["reel_news_recent"] = (recent_news(state, today)[0] + [{"title": story["title"], "date": today}])[-40:]
    if story["id"]:
        state["seen"] = state.get("seen", []) + [story["id"]]   # the other news Reels skip it
    save_reel(feed, state, cover, out, f"{kind}-{today}", {**entry, "headline": story["title"]})


# ---------------------------------------------------------------- ⚡ Breaking card Reels (9 AM, 12 PM, 3 PM)

BREAK_KINDS = ("reel_break_am", "reel_break_noon", "reel_break_aft")
BREAK_TAGS = ["BREAKING", "UPDATE", "FILM NEWS", "VIRAL", "CRICKET", "POLITICS", "BOLLYWOOD"]
BREAK_PINK = "#FF3D8B"
BREAK_OPTION = {"type": "object", "properties": {
    "pick": {"type": "integer", "description": "Story number from the list"},
    "tag": {"type": "string", "enum": BREAK_TAGS},
    "hook": {"type": "string", "description": "4-9 words, huge on the photo: the biggest name first, then the shock "
             "('Kriti Sanon ka palatwaar', 'Gurmeet-Isha ka kissing poster ne machaya internet par shor'). Hinglish, "
             "no emojis, a fact from the story"},
    "headline": {"type": "string", "description": "Max 6 words in the details frame's red bar, e.g. 'KRITI SANON KA "
                 "BADA ACTION'. No emojis"},
    "bullets": {"type": "array", "items": {"type": "string"}, "description": "3-4 short lines (max 10 words each), "
                "the key facts of the story in plain Hinglish, no emojis, each supported by the story text"},
    "question": {"type": "string", "description": "Max 9 words, a question that takes a side ('Sahi hai ya galat?')"},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-3 words copied exactly from the "
                  "hook, shown in pink"},
    "caption": {"type": "string", "description": "Hinglish: first line with the searchable keywords (names + event), "
                "2-3 short fact lines, then the side-taking question. 1-3 emojis."},
    "pin_comment": {"type": "string", "description": "Max 20 words, no hashtags: ends with a share nudge like 'Kisi "
                    "___ fan ko bhejo 👇'"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5, include #reelsindia"},
    "photo_query": {"type": "string", "description": "Short English news search that finds photos of this exact story"},
    "wiki_title": {"type": "string", "description": "English Wikipedia title of the main person, or empty"},
    "songs": SONGS},
    "required": ["pick", "tag", "hook", "headline", "bullets", "question", "highlight", "caption", "pin_comment",
                 "hashtags", "photo_query", "wiki_title", "songs"], "additionalProperties": False}
BREAK_WRITE = {"type": "object", "properties": {k: v for k, v in BREAK_OPTION["properties"].items()
                                                if k not in ("pick", "photo_query", "wiki_title", "songs")},
               "required": [k for k in BREAK_OPTION["required"] if k not in ("pick", "photo_query", "wiki_title", "songs")],
               "additionalProperties": False}
BREAK_EXTRA = """
This is a BREAKING CARD REEL (owner, 19 Oct 2026: his own posts of this kind got 14,400 and 6,400 views): frame 1 = one \\
big striking photo of the star with a huge hook, frame 2 = the news details. Pick the story that is BREAKING right now \\
(today, the last hours) about the BIGGEST names (A-list actors, top cricketers, the PM, viral personalities): police \\
action, a controversy, a clash, a reveal, a viral moment, a big announcement. Follow-ups on a story we already ran are \\
welcome ONLY with a genuinely new development (name it in the hook), e.g. 'Tiger Shroff firing: bike mil gayi'. Never \\
a story about a death, accident or tragedy told for shock; never invent facts: every bullet must be in the story text."""


def breaking_reel(first: Image.Image, second: Image.Image, credit: str, tag: str, hook: str, headline: str,
                  bullets: list[str], question: str, highlight: list[str], out_mp4: str) -> list[Image.Image]:
    """⚡ Frame 1 (2.5 s): the star's photo fills the screen, a huge hook in colour, the BREAKING tag. Frame 2: the
    photo smaller on top, a red headline bar, 3-4 bullet facts, a side-taking question. Then the Follow card.
    Returns [frame 1, frame 2] to proofread."""
    highlights = {norm(w) for phrase in highlight for w in phrase.split()}
    first, second = first.convert("RGB"), second.convert("RGB")
    h_lines, h_fnt, h_lh = fit_lines(printable(hook), RIGHT - LEFT + 60, 560, sizes=range(118, 64, -4), name="Anton-Regular.ttf")
    h_imgs = line_images(h_lines, h_fnt, h_lh, "#FFFFFF", BREAK_PINK, highlights, stroke=9)
    tag_img = pill(printable(tag).upper(), font("Anton-Regular.ttf", 56), RED, "#FFFFFF", pad=(30, 8))
    shade = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(900):
        sd.line((0, RH - 900 + y, RW, RH - 900 + y), fill=(0, 0, 0, int(235 * (y / 900) ** 1.5)))
    cred = font("Poppins-SemiBold.ttf", 24)
    logo = logo_img()
    # frame 2 content
    head_size, head_f = 66, font("Anton-Regular.ttf", 66)
    while head_f.getlength(printable(headline).upper()) > RW - 2 * LEFT - 50 and head_size > 40:
        head_size -= 4
        head_f = font("Anton-Regular.ttf", head_size)
    head_img = pill(printable(headline).upper(), head_f, RED, "#FFFFFF", pad=(26, 10))
    blocks = []
    for b in bullets[:4]:
        ls, bf, blh = fit_lines(printable(b), RIGHT - LEFT - 70, 150, sizes=range(46, 34, -2))
        blocks.append((line_images(ls, bf, blh, "#FFFFFF", YELLOW, set(), stroke=0), blh))
    q_lines, q_fnt, q_lh = fit_lines(printable(question), RIGHT - LEFT, 160, sizes=range(54, 38, -2))
    q_imgs = line_images(q_lines, q_fnt, q_lh, "#7CFF9B", "#7CFF9B", set(), stroke=0)
    win_w, win_h = 1000, 520
    mask = Image.new("L", (win_w, win_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, win_w - 1, win_h - 1), radius=26, fill=255)
    words = len(" ".join(bullets[:4] + [question]).split())
    d1, d2 = 2.5, min(9.0, max(6.0, read_secs(" ".join(bullets[:4] + [question]), 2.5)))
    enc, frame, c1, c2 = Encoder(out_mp4), None, None, None

    def cover_of(img: Image.Image, w: int, h: int, z: float, cy: float = 0.28) -> Image.Image:
        k = max(w / img.width, h / img.height) * z
        big = img.resize((int(img.width * k) + 1, int(img.height * k) + 1), Image.BICUBIC)
        x0, y0 = (big.width - w) // 2, int((big.height - h) * cy)
        return big.crop((x0, y0, x0 + w, y0 + h))
    for i in range(int(d1 * FPS)):
        frame = cover_hook_frame(first, hook, tag, credit, i / FPS, highlights or None, d1)
        if i == int(1.2 * FPS):
            c1 = frame.convert("RGB")
        enc.add(frame)
    for i in range(int(d2 * FPS)):
        z = 1.0 + 0.05 * (i / FPS) / d2
        frame = Image.new("RGBA", (RW, RH), "#0E0E12")
        if logo:
            frame.alpha_composite(logo, (60, 110))
        frame.alpha_composite(tag_img, (RW - tag_img.width - 60, 120))
        frame.paste(cover_of(second, win_w, win_h, z), ((RW - win_w) // 2, 250), mask)
        y = 250 + win_h + 40
        frame.alpha_composite(head_img, ((RW - head_img.width) // 2, y))
        y += head_img.height + 40
        d = ImageDraw.Draw(frame)
        for imgs, lh in blocks:
            d.ellipse((LEFT, y + lh // 2 - 11, LEFT + 22, y + lh // 2 + 11), fill=YELLOW)
            for n, img in enumerate(imgs):
                frame.alpha_composite(img, (LEFT + 50, y + n * lh))
            y += len(imgs) * lh + 22
        y += 14
        for n, img in enumerate(q_imgs):
            frame.alpha_composite(img, (LEFT, y + n * q_lh))
        d.text((50, 1700), printable(credit)[:60], font=cred, fill="#888888")
        handle_tag(frame, 1790)
        if i == int(d2 * FPS) - 2:
            c2 = frame.convert("RGB")
        enc.add(frame)
    add_follow(enc, frame)
    enc.close()
    return [c1 or frame.convert("RGB"), c2 or frame.convert("RGB")]


def make_breaking_reel(kind: str, state: dict, feed: list, today: str, tmp: str) -> None:
    """⚡ 9 AM / 12 PM / 3 PM (owner, 19 Oct 2026: 'replace with this type of breaking news'): the biggest BREAKING
    story of the last hours about the biggest names, as a breaking card Reel (`breaking_reel`), like his own Kriti
    Sanon / Anjali Arora / Tiger Shroff posts. Proofread strictly; fails -> the normal news Reel backup."""
    import fun
    import generate as g
    import proofread
    import status
    used = state.get("reel_news_used", [])
    status.step("Picking the biggest breaking story", 2)
    stories = [i for i in g.fetch_candidates(set(state.get("seen", []))) if i["link"] not in used][:45]
    if not stories:
        raise RuntimeError("no fresh news stories to make a Reel from")
    listing = "\n".join(f"{n}. [{s['category']} | {s['source']}] {s['title']}"
                        + (f"\n   {s['summary'][:160]}" if s["summary"] else "") for n, s in enumerate(stories, 1))
    trends = "\n".join(f"- {t} ({h} h)" for t, h in fun.x_trends()[:30]) or "(unavailable)"
    songs = trending_songs(state)
    options = fun.claude_json(NEWS_SYSTEM.format(page=CONFIG["page_name"]) + BREAK_EXTRA,
                              f"{fun.today_context()}\n\nTrending on X today (hours):\n{trends}\n\nStories:\n{listing}"
                              f"{viral_memory()}{recent_news(state, today)[1]}\n\nGive your top 3 picks, the most "
                              "breaking first." + songs_prompt(songs),
                              options_schema(BREAK_OPTION, "Top 3 picks, best first"),
                              CONFIG.get("membership_model", "sonnet"), purpose="breaking pick")["options"]
    cands = []   # up to 3 stories with a sharp photo; the next one is tried when a Reel can't be proofread
    for o in options:
        if not 1 <= o["pick"] <= len(stories):
            continue
        st = stories[o["pick"] - 1]
        item = {"link": st["link"], "source": st["source"], "title": st["title"]}
        status.step(f"Collecting photos: {st['title'][:70]}", 3)
        ph = news_photos(item, o)
        good = [x for x in ph if min(x[0].size) >= 480]
        if good:
            cands.append((o, st, good, item))
        else:
            print(f"  no sharp photo for: {st['title'][:70]}")
    if not cands:
        raise RuntimeError("no breaking story had a sharp photo")
    last_err = None
    for opt, story, photos, it in cands:
        try:
            return _breaking_build(kind, state, feed, today, tmp, opt, story, photos, it, songs)
        except proofread.Unverified as e:   # e.g. every photo implies an injury the story doesn't state: next story
            print(f"  ! breaking card for '{story['title'][:50]}' blocked ({str(e)[:100]}): next story")
            last_err = e
    raise last_err


def _breaking_build(kind, state, feed, today, tmp, opt, story, photos, it, songs) -> None:
    import fun
    import generate as g
    import proofread
    import status
    used = state.get("reel_news_used", [])
    # the first frame wants a tall, sharp photo of the star; the second frame another one
    photos.sort(key=lambda x: (x[0].height / x[0].width < 0.9, -min(x[0].size)))
    cover_i = pick_cover_photo([x[0] for x in photos], story["title"], tmp)   # 📸 photo contest
    other_i = next((k for k in range(len(photos)) if k != cover_i), cover_i)
    import carousel
    facts = carousel.story_text(it["link"], it["title"], story.get("summary", ""))
    # Second writing step from the FULL story (19 Oct 2026: written from the headline alone it invented a bystander,
    # a date and a name): same fields, only facts that are in the text
    status.step("Writing the card from the full story", 4)
    final = fun.claude_json(NEWS_SYSTEM.format(page=CONFIG["page_name"]) + BREAK_EXTRA,
                            f"{fun.today_context()}\n\nWrite the breaking card for THIS story, using ONLY facts that are "
                            f"in the text below (no names, numbers, dates or injuries that are not there).\n\n"
                            f"Headline: {story['title']}\n\nFull story:\n{facts[:5500]}", BREAK_WRITE,
                            CONFIG.get("membership_model", "sonnet"), purpose="breaking card write")
    opt = {**opt, **final}
    opt["hook"] = pick_hook(story["title"], facts, opt["hook"],   # 🪝 hook contest: only names that are on the card
                            card_text=" ".join([opt["headline"]] + opt["bullets"] + [opt["question"]]))
    opt["caption"] = unescape(opt["caption"])
    roman_only(opt, ["caption", "pin_comment", "hook", "headline", "question"])
    bl = {"bullets_text": "\n".join(opt["bullets"])}
    roman_only(bl, ["bullets_text"])
    opt["bullets"] = [x for x in bl["bullets_text"].split("\n") if x.strip()][:4]
    songs_picked = checked_songs(opt.pop("songs", []), songs)
    out = os.path.join(tmp, "reel.mp4")

    def render(d):   # d["p1"], d["p2"]: which photo is on frame 1 / frame 2 (a doubtful photo is swapped, not the Reel)
        a_, b_ = photos[min(d["p1"], len(photos) - 1)], photos[min(d["p2"], len(photos) - 1)]
        return breaking_reel(a_[0], b_[0], f"Photo: {a_[1]}", d["post"]["tag"], d["post"]["hook"], d["post"]["headline"],
                             d["post"]["bullets"], d["post"]["question"], d["post"]["highlight"], out)

    def swap(d, numbers):   # the proofreader doubts a photo: use the next one for that frame
        k = "p1" if 1 in numbers else "p2"
        return {**d, k: d[k] + 1} if d[k] + 1 < len(photos) else None
    status.step("Making the video + proofreading", 5)
    frames, data, proof = proofread.run(render, {"post": opt, "p1": cover_i, "p2": other_i},
                                        lambda d: g.full_caption(d["post"], it), facts, drop=swap, strict=True)
    post = data["post"]
    state["reel_news_used"] = (used + [story["link"]])[-80:]
    state["reel_news_recent"] = (recent_news(state, today)[0] + [{"title": story["title"], "date": today}])[-40:]
    if story["id"]:
        state["seen"] = state.get("seen", []) + [story["id"]]
    save_reel(feed, state, frames[0], out, f"{kind}-{today}", {
        "headline": story["title"], "tag": "🎬 REEL · ⚡ BREAKING CARD", "caption": g.limit_hashtags(g.full_caption(post, it)),
        "source": story["source"], "source_url": story["link"], "pin_comment": post.get("pin_comment", ""),
        "songs": songs_picked, "proof": proof})


# ---------------------------------------------------------------- 🎥🔊 10 PM: official video news

OFFICIAL_X = ("narendramodi", "AmitShah", "rajnathsingh", "PMOIndia", "BJP4India", "JPNadda", "DrSJaishankar",
              "nitin_gadkari", "myogiadityanath", "Dev_Fadnavis", "himantabiswa", "PIB_India",
              # right-leaning public voices on their OWN accounts (owner, 16 Oct 2026: Anupam Kher's video hitting
              # back at protesters got 450k views on another page)
              "AnupamPKher", "SirPareshRawal", "KanganaTeam", "vivekagnihotri", "pradip103", "Shehzad_Ind",
              "sambitswaraj", "amitmalviya", "annamalai_k", "Tejasvi_Surya", "smritiirani", "KapilMishra_IND")
# Most newsworthy first; their own accounts only: never news agencies, TV channels, podcasts or films. 24 accounts:
# X allows 30 profile reads per 15 minutes from one address.
OFFICIAL_SECS = 4.5   # the video piece under the hook line


def official_videos(used: set[str], handles: tuple[str, ...] = OFFICIAL_X, hours: int = 20) -> list[dict]:
    """Videos these accounts posted on X in the last `hours` (their public profile timelines, free, no login),
    newest first: [{id, handle, text, at, secs, mp4}]."""
    import time
    from datetime import datetime, timedelta, timezone
    import requests
    since, out = datetime.now(timezone.utc) - timedelta(hours=hours), []
    for handle in handles:
        try:
            r = requests.get(f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}",
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            if r.status_code == 429:   # X's limit: wait if it resets soon, else use what we have
                wait = int(r.headers.get("x-rate-limit-reset", "0") or 0) - time.time()
                if 0 < wait < 180:
                    print(f"  X limit reached: waiting {wait:.0f} s")
                    time.sleep(wait + 2)
                    r = requests.get(r.url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
                if r.status_code == 429:
                    print(f"  ! X limit reached after {handles.index(handle)} accounts: using those")
                    break
            html = r.text
            m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
            entries = json.loads(m.group(1))["props"]["pageProps"]["timeline"]["entries"] if m else []
        except Exception as e:
            print(f"  ! X @{handle}: {str(e)[:60]}")
            entries = []
        for e in entries:
            t = e.get("content", {}).get("tweet", {})
            try:
                at = datetime.strptime(t.get("created_at", ""), "%a %b %d %H:%M:%S %z %Y")
            except ValueError:
                continue
            vids = [x for x in t.get("extended_entities", {}).get("media", []) if x.get("type") == "video"]
            info = vids[0].get("video_info", {}) if vids else {}
            mp4s = [v for v in info.get("variants", []) if v.get("content_type") == "video/mp4"]
            secs = info.get("duration_millis", 0) / 1000
            if at < since or not mp4s or secs < 10 or t.get("id_str") in used:
                continue
            out.append({"id": t["id_str"], "handle": handle, "at": at, "secs": secs,
                        "text": re.sub(r"https://t\.co/\S+", "", t.get("full_text") or t.get("text") or "").strip(),
                        "mp4": max(mp4s, key=lambda v: v.get("bitrate", 0))["url"]})
        time.sleep(0.4)
    return sorted(out, key=lambda v: v["at"], reverse=True)[:25]


def speech_start(path: str, tmp: str, about: str) -> float | None:
    """The AI looks at a frame every 2 s of the first 40 s and picks where the speaker is clearly talking (after
    any title card); None if the video has no clear speaker or carries a TV channel / news agency logo."""
    import fun
    _, _, secs = video_info(path)
    folder = os.path.join(tmp, "speech")
    os.makedirs(folder, exist_ok=True)
    times = [t for t in range(0, int(min(40, secs - OFFICIAL_SECS - 1)), 2)]
    for n, t in enumerate(times, 1):
        subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-ss", str(t), "-i", path, "-frames:v", "1",
                        "-vf", "scale=480:-2", os.path.join(folder, f"f{n}.jpg")], capture_output=True)
    schema = {"type": "object", "properties": {
        "ok": {"type": "boolean", "description": "A real speaker clearly talking, no TV channel / news agency logo"},
        "frame": {"type": "integer", "description": "The first frame number where the speaker is clearly talking "
                  "(face visible, no title card or text slate)"},
        "why": {"type": "string"}}, "required": ["ok", "frame", "why"], "additionalProperties": False}
    try:
        res = fun.claude_json("You check an official video before a short news Reel uses 4-5 seconds of it. Open every "
                              "frame with the Read tool.", f"The video: {about}\nFrames (one every 2 s from the "
                              f"start): " + ", ".join(f"f{n}.jpg = {t} s" for n, t in enumerate(times, 1)),
                              schema, CONFIG.get("membership_model", "sonnet"), purpose="video look", folder=folder)
    except Exception as e:
        print(f"  ! video look failed: {str(e)[:80]}")
        return None
    print(f"  video look: {'ok' if res['ok'] else 'NOT ok'}, frame {res['frame']} ({res['why'][:100]})")
    if not res["ok"] or not 1 <= res["frame"] <= len(times):
        return None
    return times[res["frame"] - 1] + 0.5


QUOTE_SCHEMA = {"type": "object", "properties": {
    "speech": {"type": "boolean", "description": "True if the story is mainly about something a person SAID or how they "
               "reacted on camera (remark, statement, interview, press conference, speech)"},
    "person": {"type": "string"},
    "handle": {"type": "string", "description": "That person's own X handle without @ if you are sure (e.g. "
               "'prakashraaj'), else ''"},
    "party_handle": {"type": "string", "description": "The official X handle of the party / organisation they speak for "
                     "(e.g. 'INCIndia', 'AamAadmiParty', 'BJP4India', 'AITCofficial'; a state unit like 'INCPunjab' if "
                     "the event is in that state), '' if none or unsure"},
    "search": {"type": "string", "description": "Short English news search for that exact moment"},
    "side": {"type": "string", "enum": ["government_right", "left_opposition", "neutral"], "description": "Where the "
             "person stands for this page (left/opposition includes left-leaning activists and celebrities)"},
    "remark": {"type": "string", "description": "The exact remark/reaction the story is about, in a few words"}},
    "required": ["speech", "person", "handle", "party_handle", "search", "side", "remark"], "additionalProperties": False}
PARTY_X = {"bjp4india", "incindia", "incsandesh", "aamaadmiparty", "aitcofficial", "samajwadiparty", "rjdforindia",
           "cpimspeak", "ncpspeaks", "shivsenaubt_", "tdpofficial", "ysrcparty", "brsparty", "jmmjharkhand",
           "arivalayam", "jduonline"}   # parties post their own press conferences: they want them spread
QUOTE_SECS = 6.0   # the exact moment's clip in a news Reel (owner, 16 Oct 2026: party press-conference videos)


def quote_video(title: str, facts: str, tmp: str) -> dict | None:
    """🎥 For a story about what someone SAID (owner, 16 Oct 2026: 'first search for the video where Prakash Raj said
    it'): the person's OWN X video of that moment (used as the hook video, with the voice) or a news agency /
    channel X post with it in the story's articles (used as a credited screenshot + its text as the proof of the
    quote; never re-uploaded: copyright, Instagram's repost rule). Returns {own, handle, id, text, line, path,
    start | shot} or None."""
    import carousel
    import create_post
    import fun
    import requests
    model = CONFIG.get("membership_model", "sonnet")
    try:
        q = fun.claude_json("You read an Indian news story.", f"Story: {title}\n\n{facts[:1500]}", QUOTE_SCHEMA, model,
                            purpose="quote video")
    except Exception as e:
        print(f"  ! quote check failed: {str(e)[:80]}")
        return None
    if not q["speech"]:
        return None
    handle = q["handle"].strip().lstrip("@")
    # Allowed as video (owner, 16 Oct 2026): the person's own account and their party's official account; agencies
    # and TV channels only as a screenshot
    handles = [h for h in (handle, q["party_handle"].strip().lstrip("@")) if h]
    allowed = {h.lower() for h in handles} | PARTY_X
    cands = [dict(v, own=True) for v in (official_videos(set(), tuple(handles), hours=96) if handles else [])][:10]
    seen = {c["id"] for c in cands}
    for a in create_post.bing_articles(q["search"] or title, limit=6, newest=True):
        page = carousel.page_html(a["url"]) or ""
        for tid in list(dict.fromkeys(re.findall(r"(?:twitter|x)\.com/\w+/status/(\d+)", page)))[:6]:
            d = carousel.tweet_data(tid) if tid not in seen else None
            vids = [m for m in (d or {}).get("mediaDetails", []) if m.get("type") == "video"]
            if not vids:
                continue
            seen.add(tid)
            mp4s = [v for v in vids[0].get("video_info", {}).get("variants", []) if v.get("content_type") == "video/mp4"]
            who = d["user"]["screen_name"]
            cands.append({"id": tid, "handle": who, "own": who.lower() in allowed,
                          "text": d.get("text", ""), "mp4": max(mp4s, key=lambda v: v.get("bitrate", 0))["url"] if mp4s else ""})
        if len(cands) >= 12:
            break
    if not cands:
        print(f"  quote video: none found for {q['person']}")
        return None
    schema = {"type": "object", "properties": {
        "pick": {"type": "integer", "description": "The post whose video shows THIS exact remark, 0 if none"},
        "evidence": {"type": "string", "description": "Words from that post's text that show it is the same remark "
                     "and the same event ('' if none)"},
        "line": {"type": "string", "description": "Max 12 words, Hinglish: what the video shows, for the screen"},
        "why": {"type": "string"}}, "required": ["pick", "evidence", "line", "why"], "additionalProperties": False}
    listing = "\n".join(f"{n}. @{c['handle']}{' (own / party account)' if c['own'] else ''}: {c['text'][:200]}"
                        for n, c in enumerate(cands, 1))
    try:
        res = fun.claude_json("Pick the X post whose video shows this EXACT remark at this exact event. The post text "
                              "must clearly describe the same remark (owner, 16 Oct 2026: the robot once picked the "
                              "same person's different speech). Another statement, speech or event by the same person "
                              "= 0. When unsure, 0.", f"Story: {title}\nPerson: {q['person']}\nThe remark: "
                              f"{q['remark']}\n\nX posts with a video:\n{listing}",
                              schema, model, purpose="quote video pick")
    except Exception as e:
        print(f"  ! quote video pick failed: {str(e)[:80]}")
        return None
    if not 1 <= res["pick"] <= len(cands) or not res["evidence"].strip():
        print(f"  quote video: no post shows the moment ({res['why'][:80]})")
        return None
    c = cands[res["pick"] - 1]
    out = {"own": c["own"], "handle": c["handle"], "id": c["id"], "text": c["text"], "line": res["line"],
           "side": q["side"]}
    print(f"  quote video: @{c['handle']} ({'own video' if c['own'] else 'screenshot'}): {c['text'][:80]}")
    if c["own"] and c["mp4"]:
        path = os.path.join(tmp, f"quote{c['id']}.mp4")
        open(path, "wb").write(requests.get(c["mp4"], timeout=120).content)
        start = speech_start(path, tmp, f"@{c['handle']}: {c['text'][:200]}")
        if start is not None:
            return {**out, "path": path, "start": start}
    shot = carousel.tweet_screenshot(c["id"])
    return {**out, "own": False, "shot": shot} if shot is not None else None


def add_voice(mp4: str, source: str, start: float, out: str, only: float | None = None) -> None:
    """The speaker's own voice under the whole Reel (owner, 15 Oct 2026: 'B', inside the file so it is posted as it
    is): the source's sound from `start`, evened out, 48 kHz, fading in and out. only = seconds: the voice only for
    that long (their own clip), then silence (a left/opposition speaker the Reel calls out: never their message
    under our whole Reel; owner, 16 Oct 2026)."""
    _, _, secs = video_info(mp4)
    voice = min(secs, only) if only else secs
    r = subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", mp4, "-ss", f"{start:.2f}", "-t", f"{voice:.2f}",
                        "-i", source, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                        "-ar", "48000", "-af", f"loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=in:st=0:d=0.2,"
                        f"afade=t=out:st={max(0.0, voice - (0.4 if only else 1.2)):.2f}:d={0.4 if only else 1.2},"
                        f"apad=whole_dur={secs:.2f}",   # silence up to the video's length, then stop (an open-ended
                        "-t", f"{secs:.2f}", "-movflags", "+faststart", out],   # apad never ended: 16 Oct 2026)
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(f"adding the voice failed: {r.stderr[-200:]}")


SPEECH_MAX, SPEECH_MIN = 60.0, 25.0   # the speech part of the 9:30 PM Reel (owner's examples: 48-90 s)
DRAMA_SONGS = ["Sam Dam Dand Bhed: Kailash Kher", "Toofan (KGF Chapter 2): Ravi Basrur", "Arjan Vailly (Animal): "
               "Bhupinder Babbal", "Aarambh Hai Prachand (Gulaal): Piyush Mishra", "Dhurandhar Title Track: Shashwat "
               "Sachdev", "Lehra Do (83): Arijit Singh", "Teri Mitti (Kesari): B Praak", "Kar Har Maidaan Fateh "
               "(Sanju): Sukhwinder Singh", "Ziddi Dil (Mary Kom): Vishal Dadlani", "Sultan Title Track: Sukhwinder "
               "Singh", "Rang De Basanti Title Track: Daler Mehndi", "Bharat Ka Bachcha Bachcha Jai Shri Ram Bolega: "
               "Lakhbir Singh Lakha"]   # dramatic / patriotic tracks to play LOW under the voice (owner, 16 Oct 2026)
SPEECH_OPTION = {"type": "object", "properties": {
    "headline": {"type": "array", "items": {"type": "string"}, "description": "2 lines on top: line 1 = 2-4 words of "
                 "punch in caps feel (e.g. 'RESPECT SIR!', 'KADAK JAWAB', 'SEEDHA VAAR'), line 2 = who + what in max "
                 "40 characters (e.g. 'Anupam Kher ka protesters ko jawab'); Hinglish, no emojis, clearly in favour "
                 "of Modi/BJP/NDA"},
    "from_seg": {"type": "integer", "description": "First transcript segment of the clip (0 if no transcript)"},
    "to_seg": {"type": "integer", "description": "Last transcript segment: the clip is the most compelling "
               "CONTINUOUS 25-60 s with the key message, starting at the start of a sentence (0 if no transcript)"},
    "caption": {"type": "string", "description": "Hinglish: first line with the searchable keywords (who + topic), "
                "2-3 short news lines (what he said, why it matters, the opposition angle in the page's line), "
                "then a side-taking question like 'Aap sehmat ho? Comment karo 👇'. 1-3 emojis."},
    "pin_comment": {"type": "string", "description": "Pinned first comment, max 20 words, no hashtags, ends with a "
                    "share nudge like 'Ye message sabko bhejo 🇮🇳'"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5, include #reelsindia"},
    "songs": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "artist": {"type": "string"}, "lang": {"type": "string", "enum": ["hindi", "english"]},
        "mood": {"type": "string", "description": "Max 6 words: why it fits, e.g. 'attack mode, play it low'"}},
        "required": ["title", "artist", "lang", "mood"], "additionalProperties": False},
        "description": "4 tracks from the given list that fit the speech's mood"}},
    "required": ["headline", "from_seg", "to_seg", "caption", "pin_comment", "hashtags", "songs"],
    "additionalProperties": False}


def transcribe(path: str, start: float, secs: float) -> list[dict]:
    """🗣️ Free speech-to-text (faster-whisper 'large-v3-turbo' on the computer itself, no account; owner, 16 Oct 2026:
    subtitles for the 9:30 PM speech Reel; the small model garbled Hindi). Installed on first use. Returns segments
    [{start, end, text, words: [(start, end, word)]}] with times in the video; [] if it isn't available."""
    import sys
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "faster-whisper"], capture_output=True)
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            print("  ! speech-to-text not available: no subtitles")
            return []
    wav = path + ".wav"
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{secs:.2f}", "-i", path, "-vn",
                    "-ac", "1", "-ar", "16000", wav], capture_output=True)
    try:
        model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
        segs, info = model.transcribe(wav, language="hi", word_timestamps=True, vad_filter=True)
        out = [{"start": x.start + start, "end": x.end + start, "text": x.text.strip(),
                "words": [(w.start + start, w.end + start, w.word.strip()) for w in (x.words or [])]} for x in segs]
    except Exception as e:
        print(f"  ! speech-to-text failed: {str(e)[:100]}")
        return []
    print(f"  transcript: {len(out)} segment(s), {sum(len(x['words']) for x in out)} words")
    return out


def subtitle_lines(segs: list[dict], tmp_model: str = "") -> list[tuple[float, float, str]]:
    """Short phrases (max 5 words / 2.2 s) of the chosen segments, then the AI writes each in Roman Hinglish (the
    fonts can't draw Devanagari; obvious speech-to-text slips are corrected, the meaning is never changed)."""
    import fun
    phrases, cur = [], []
    for x in segs:
        for w in x["words"]:
            if cur and (len(cur) >= 5 or w[1] - cur[0][0] > 2.2 or w[0] - cur[-1][1] > 0.6):
                phrases.append(cur)
                cur = []
            cur.append(w)
        if cur:
            phrases.append(cur)
            cur = []
    if not phrases:
        return []
    schema = {"type": "object", "properties": {"lines": {"type": "array", "items": {"type": "string"},
              "description": "Exactly one Roman Hinglish line per phrase, same order"}},
              "required": ["lines"], "additionalProperties": False}
    listing = "\n".join(f"{n}. {' '.join(w[2] for w in ph)}" for n, ph in enumerate(phrases, 1))
    try:
        lines = fun.claude_json("Write each phrase of a Hindi speech transcript in Roman Hinglish letters (e.g. 'Naxalvad "
                                "khatam ho chuka hai'), for subtitles. Fix only obvious speech-to-text spelling slips "
                                "(e.g. 'desh' heard as 'dekh'); never add, drop or change the meaning of words. Keep "
                                "English words in English. One line per phrase, same count and order.",
                                f"Phrases:\n{listing}", schema, CONFIG.get("membership_model", "sonnet"),
                                purpose="subtitles")["lines"]
    except Exception as e:
        print(f"  ! subtitles failed: {str(e)[:80]}")
        return []
    if len(lines) != len(phrases):
        print(f"  ! subtitles: {len(lines)} lines for {len(phrases)} phrases, skipped")
        return []
    return [(ph[0][0], ph[-1][1], printable(roman_text(line))) for ph, line in zip(phrases, lines)]


def roman_text(text: str) -> str:
    """Drops any non-Roman script left in a subtitle (the fonts can't draw it)."""
    return re.sub(r"[֐-ࣿऀ-෿]+", "", text).strip()


def speech_reel(src: str, start: float, secs: float, headline: list[str], subs: list[tuple[float, float, str]],
                out_mp4: str) -> list[Image.Image]:
    """🎥🔊 9:30 PM layout (owner, 16 Oct 2026, like @bold_nationx7's Anupam Kher Reel: 450k views): a bold 2-line
    headline on top (yellow punch + white who/what), the speaker's video in the middle, subtitles of his words in
    sync with the voice (the one place text follows speech, owner's OK), @khabarbawaal under it, then the follow
    card. Returns [a frame with the headline + a subtitle] to proofread."""
    vid_y, vid_h = 430, 1140
    over = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    logo = logo_img()
    if logo:
        small = logo.copy()
        small.thumbnail((260, 90))
        over.alpha_composite(small, (RW - small.width - 50, 70))
    lines = [printable(x).strip() for x in headline if x.strip()][:2] or [""]
    y = 180
    for n, line in enumerate(lines):
        size = 78 if n == 0 else 60
        f = font("Anton-Regular.ttf", size)
        while f.getlength(line) > RW - 2 * LEFT and size > 36:
            size -= 4
            f = font("Anton-Regular.ttf", size)
        d.text((RW // 2, y), line.upper() if n == 0 else line, font=f, fill=YELLOW if n == 0 else "#FFFFFF",
               anchor="mt", stroke_width=3, stroke_fill="#000000")
        y += int(size * 1.25)
    sub_font = font("Poppins-Bold.ttf", 54)
    handle = font("Poppins-SemiBold.ttf", 32)
    d.text((RW // 2, vid_y + vid_h + 40), CONFIG["handle"], font=handle, fill="#CCCCCC", anchor="mt")
    enc, frame, check = Encoder(out_mp4), None, None
    for i, shot in enumerate(clip_frames(src, start, secs, RW, vid_h)):
        t = start + i / FPS
        frame = Image.new("RGBA", (RW, RH), "#000000")
        frame.paste(shot, (0, vid_y))
        frame.alpha_composite(over)
        line = next((txt for a, b, txt in subs if a - 0.05 <= t <= b + 0.35), "")
        if line:
            ls, sf, lh = fit_lines(line, RW - 160, 200, sizes=range(54, 36, -4))
            pad = 20
            box_h = len(ls) * lh + 2 * pad
            box_y = vid_y + vid_h - box_h - 60
            fd = ImageDraw.Draw(frame)
            for j, words in enumerate(ls):
                txt = " ".join(words)
                w = sf.getlength(txt)
                fd.rounded_rectangle(((RW - w) // 2 - pad, box_y + j * lh, (RW + w) // 2 + pad, box_y + (j + 1) * lh + 2 * pad - 8),
                                     radius=14, fill=(0, 0, 0, 200))
                fd.text((RW // 2, box_y + j * lh + pad), txt, font=sf, fill="#FFFFFF", anchor="mt")
            if check is None and i > FPS:
                check = frame.convert("RGB")
        enc.add(frame)
    if frame is None:
        raise RuntimeError("the speaker's video could not be read")
    add_follow(enc, frame)
    enc.close()
    return [check or frame.convert("RGB")]


def make_official_reel(state: dict, feed: list, today: str, tmp: str) -> None:
    """🎥🔊 9:30 PM (owner, 15-16 Oct 2026, like @geowiredaily's Modi Reel and @bold_nationx7's Anupam Kher Reel):
    the strongest 25-60 s of a leader's or right-leaning public figure's OWN video from their X account, with his
    voice (posted as it is; dramatic music suggested to play low), a bold headline on top and subtitles of his words.
    Never news agencies, TV channels, podcasts or films. No fresh video today -> the best unused one of the last 3
    days -> only then a normal 🔥 viral news Reel."""
    import carousel
    import create_post
    import fun
    import generate as g
    import proofread
    import requests
    import status
    status.step("Finding speech videos on X (leaders + right-leaning voices)", 2)
    used = set(state.get("reel_official_used", []))
    vids = official_videos(used, hours=72)
    print(f"  speech videos (3 days): {[(v['handle'], round(v['secs'])) for v in vids]}")
    ranked, search = [], ""
    if vids:
        heads = "\n".join(f"- {i['title']}" for i in g.fetch_candidates(set())[:25])
        trends = ", ".join(t for t, _ in fun.x_trends()[:20])
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        listing = "\n".join(f"{n}. @{v['handle']} ({(now - v['at']).total_seconds() / 3600:.0f} h ago, {v['secs']:.0f} s): "
                            f"{v['text'][:220]}" for n, v in enumerate(vids, 1))
        schema = {"type": "object", "properties": {
            "ranked": {"type": "array", "items": {"type": "integer"}, "description": "Up to 3 video numbers, best "
                       "first; empty if none fits"},
            "search": {"type": "string", "description": "Short English news search for articles on the best one"},
            "why": {"type": "string"}}, "required": ["ranked", "search", "why"], "additionalProperties": False}
        res = fun.claude_json(
            NEWS_SYSTEM.format(page=CONFIG["page_name"]) + "\n\nNow rank speaker videos for tonight's 9:30 PM Reel: "
            "a leader, minister or right-leaning public figure SPEAKING on camera (speech, statement, press "
            "conference, interview, a selfie message) on one of today's big India debates, with a strong message: "
            "hitting back at the opposition or protesters, a big announcement, a moving appeal. Prefer the last 20 "
            "hours; older (up to 3 days) only if nothing today is strong. Skip greetings, festival/devotional wishes, "
            "condolences, ads, promos and montages without a speaker. Never a video that attacks or embarrasses the "
            "BJP/NDA. The biggest face and the most talked-about debate first.",
            f"Today's headlines:\n{heads}\n\nTrending on X: {trends}\n\nSpeaker videos:\n{listing}{viral_memory()}"
            f"{recent_news(state, today)[1]}", schema, CONFIG.get("membership_model", "sonnet"),
            purpose="official video pick")
        ranked, search = [n for n in res["ranked"] if 1 <= n <= len(vids)][:3], res["search"]
        print(f"  ranked: {ranked} ({res['why'][:120]})")
    v = src = start = None
    for n in ranked:   # the first one with a clear speaker
        cand = vids[n - 1]
        status.step(f"Speaker video: @{cand['handle']}", 3)
        path = os.path.join(tmp, f"official{n}.mp4")
        open(path, "wb").write(requests.get(cand["mp4"], timeout=120).content)
        at = speech_start(path, tmp, f"@{cand['handle']}: {cand['text'][:200]}")
        if at is not None and video_info(path)[2] - at >= 12:
            v, src, start = cand, path, at
            break
    if v is None:
        status.step("No fitting speaker video: a viral news Reel instead", 3)
        return make_news_reel("reel_official", state, feed, today, tmp)
    length = video_info(src)[2]
    status.step("Writing subtitles of his words", 4)
    segs = transcribe(src, start, min(120.0, length - start))
    article = None
    for a in create_post.bing_articles(search or v["text"][:80], limit=8, newest=True):
        article = create_post.read_article(a["url"])
        if article and article.get("title"):
            break
        article = None
    body = carousel.story_text(article["url"], article["title"], article.get("description", "")) if article else ""
    transcript = "\n".join(f"{n}. [{x['start'] - start:.0f}-{x['end'] - start:.0f} s] {x['text']}" for n, x in enumerate(segs))
    facts = (f"His own X post (@{v['handle']}): {v['text']}\n" + (f"Article: {article['title']} ({article['site']})\n"
             f"{body[:3000]}\n" if article else "") + (f"\nWhat he says in the video (speech-to-text, numbered "
             f"segments):\n{transcript}" if transcript else ""))
    status.step("Writing the headline", 4)
    opt = fun.claude_json(
        NEWS_SYSTEM.format(page=CONFIG["page_name"]).split("From today's stories")[0] + "Write tonight's 9:30 PM "
        "speaker Reel: his own video plays with his voice and subtitles under a bold headline. Clearly in favour of "
        "Modi, the BJP and the NDA (e.g. 'seedha vaar', 'kadak jawab', 'RESPECT SIR!') but only real facts and his "
        "real words; no invented quotes, no hate against any community. Pick the most compelling continuous part of "
        "his speech (from_seg..to_seg, 25-60 s). Songs: 4 from this list that fit the mood (they are played LOW "
        "under his voice): " + "; ".join(DRAMA_SONGS) + "\nRules:" + NEWS_SYSTEM.format(page=CONFIG["page_name"]).split("Rules:")[1],
        f"{fun.today_context()}\n\n{facts}", SPEECH_OPTION, CONFIG.get("membership_model", "sonnet"),
        purpose="speech reel script")
    opt["caption"] = unescape(opt["caption"])
    roman_only(opt, ["caption", "pin_comment"])
    lines = {"headline": "\n".join(opt["headline"])}
    roman_only(lines, ["headline"])
    opt["headline"] = [x for x in lines["headline"].split("\n") if x.strip()][:2]
    if segs:   # the chosen part of the speech, at least 25 s, at most 60 s
        a_, b_ = sorted((max(0, min(opt["from_seg"], len(segs) - 1)), max(0, min(opt["to_seg"], len(segs) - 1))))
        while b_ < len(segs) - 1 and segs[b_]["end"] - segs[a_]["start"] < SPEECH_MIN:
            b_ += 1
        while b_ > a_ and segs[b_]["end"] - segs[a_]["start"] > SPEECH_MAX:
            b_ -= 1
        clip_start, clip_end = segs[a_]["start"], segs[b_]["end"] + 0.3
        subs = subtitle_lines(segs[a_:b_ + 1])
    else:
        clip_start, clip_end, subs = start, start + min(30.0, length - start - 0.3), []
    secs = min(clip_end, length - 0.2) - clip_start
    songs = opt.pop("songs", [])
    out = os.path.join(tmp, "speech.mp4")
    source = article["site"] if article else f"@{v['handle']} on X"
    credit = f"Video + voice: @{v['handle']} on X"

    def caption_of(d):
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in d["hashtags"])
        src_line = f"\nSource: {source} (link pinned in comments 👇)" if article else ""
        return (f"{d['caption'].strip()}\n\n{credit}\n\nFollow {CONFIG['handle']} for daily updates.{src_line}"
                f"\n\n{tags}")
    status.step("Making the video + proofreading", 5)
    check_note = ("The SUBTITLES on the video are the speaker's own words (speech-to-text): they are his quote, not our "
                  "claim; check only their spelling.\n\n")
    frames, opt, proof = proofread.run(
        lambda d: speech_reel(src, clip_start, secs, d["headline"], subs, out), opt, caption_of,
        check_note + facts, strict=True)
    status.step("Adding his voice", 5)
    voiced = os.path.join(tmp, "voiced.mp4")
    add_voice(out, src, clip_start, voiced)
    state["reel_official_used"] = (list(used) + [v["id"]])[-60:]
    state["reel_news_recent"] = (recent_news(state, today)[0] + [{"title": v["text"][:140], "date": today}])[-40:]
    if article:
        state["reel_news_used"] = (state.get("reel_news_used", []) + [article["url"]])[-80:]
    save_reel(feed, state, frames[0], voiced, f"reel_official-{today}", {
        "headline": printable(" · ".join(opt["headline"])),
        "tag": "🎬 REEL · 🎥🔊 SPEAKER VIDEO", "caption": g.limit_hashtags(caption_of(opt)), "source": source,
        "source_url": article["url"] if article else f"https://x.com/{v['handle']}/status/{v['id']}",
        "pin_comment": opt.get("pin_comment", ""), "songs": songs, "proof": proof})


def make_top5_reel(kind: str, state: dict, feed: list, today: str, tmp: str) -> None:
    """🎬 7 AM / 9:15 PM: Top 5 in ~30 seconds: one news photo + one short line per story, then an end card
    (owner, 13 Oct 2026: the Top 5 carousels became Reels). Stories are picked like the old carousels."""
    import generate as g
    import proofread
    import schedule
    import specials
    import status
    src = TOP5_KINDS[kind]
    cfg = specials.TOP10[src]
    status.step("Picking the 5 biggest stories", 2)
    items = specials.gather(src)
    if len(items) < 5:
        raise RuntimeError("not enough stories for a Top 5")
    pick = specials.pick_top10(src, items)
    status.step("Collecting photos", 3)
    stories, photos = [], []
    for s in pick["stories"]:
        img = g.best_photo(s)
        if img is not None and min(img.size) >= 300:
            stories.append(s)
            photos.append(img)
    if len(stories) < 4:
        raise RuntimeError(f"only {len(stories)} of the Top 5 stories had a usable photo")
    data = {"stories": stories, "hook": pick["hook"], "hashtags": pick["hashtags"]}
    out = os.path.join(tmp, "reel.mp4")
    label = "RAAT KI TOP 5" if src == "night_roundup" else "TOP 5 NEWS"

    hook = "Raat bhar mein kya-kya hua? Top 5" if src == "night_roundup" else "Aaj ki 5 sabse badi khabrein"

    def render(d):
        slides = [(img, s["source"], f"{n}. {s['slide_title']}", False)
                  for n, (img, s) in enumerate(zip(photos, d["stories"]), 1)]
        return news_reel(slides, "Inme sabse badi khabar kaunsi? Number likho", ["Number likho"], label, out, hook=hook)
    article = "\n".join(f"- {s['title']}: {s['summary'][:300]}" for s in stories)
    status.step("Making the video + proofreading", 5)
    frames, data, proof = proofread.run(render, data, specials.top10_caption, article, strict=True)
    save_reel(feed, state, frames[0], out, f"{kind}-{today}", {
        "headline": f"{cfg['title'][0]} {cfg['title'][1]}: {schedule.ist_now():%d %b}", "tag": f"🎬 REEL · {label}",
        "caption": g.limit_hashtags(specials.top10_caption(data)),
        "source": ", ".join(dict.fromkeys(s["source"] for s in data["stories"]))[:80], "source_url": "",
        "pin_comment": "", "proof": proof, "songs": chart_songs(state)})


def news_photos(item: dict, opt: dict) -> list[tuple]:
    """Story photos checked like carousel photos, each with a short story line: [(img, credit, text, whole)]."""
    import carousel
    picked, _ = carousel.collect(item, {"headline": item["title"], "photo_query": opt["photo_query"],
                                        "wiki_title": opt["wiki_title"]}, [], want=4, max_words=12)
    return [(img, credit, text.strip(), kind == "tweet") for (kind, img, credit, _), text in picked if text.strip()]


def pick_cover_photo(photos: list[Image.Image], about: str, tmp: str) -> int:
    """📸 Photo contest (owner, 19 Oct 2026: 'the first image should be very attractive'): the AI looks at the
    candidate photos and picks the cover: a sharp close-up with a clear face or strong emotion that fits the story,
    not a crowd, a collage, a screenshot, or text-heavy. Returns the index (0 if there is one photo or the check fails)."""
    import fun
    if not photos:
        return 0
    folder = os.path.join(tmp, "cover_check")
    os.makedirs(folder, exist_ok=True)
    for n, img in enumerate(photos[:6], 1):
        small = img.convert("RGB")
        small.thumbnail((640, 640))
        small.save(os.path.join(folder, f"photo{n}.jpg"), quality=85)
    schema = {"type": "object", "properties": {"best": {"type": "integer", "description": "Photo number"},
              "face_x": {"type": "number", "description": "0-1: horizontal position of the main person's face in that "
                         "photo (0 = left edge, 1 = right edge)"},
              "face_y": {"type": "number", "description": "0-1: vertical position of the main person's face"},
              "why": {"type": "string"}}, "required": ["best", "face_x", "face_y", "why"], "additionalProperties": False}
    try:
        res = fun.claude_json("You pick the COVER photo of a short news Reel for young Indians. Open every photo with the "
                              "Read tool. The best cover stops a scroll: a sharp close-up of the main person with a clear "
                              "face and strong emotion, bright and high contrast, tall or square, showing the story's "
                              "star; never a crowd, a collage, a screenshot, a poster full of text or a blurry photo, and never a photo that "
                              "shows injuries, bandages, blood, a hospital, handcuffs, weapons or another event than the story "
                              "(it would suggest facts the story does not state).",
                              f"The story: {about}\nPhotos: " + ", ".join(f"photo{n}.jpg" for n in range(1, min(len(photos), 6) + 1)),
                              schema, CONFIG.get("membership_model", "sonnet"), purpose="cover photo", folder=folder)
    except Exception as e:
        print(f"  ! cover photo check failed ({str(e)[:80]}): first photo")
        return 0
    k = res["best"] - 1
    print(f"  cover photo: {res['best']} face at ({res['face_x']:.2f}, {res['face_y']:.2f}) ({res['why'][:80]})")
    k = k if 0 <= k < min(len(photos), 6) else 0
    photos[k].info["focus"] = (min(1.0, max(0.0, float(res["face_x"]))), min(1.0, max(0.0, float(res["face_y"]))))
    return k


HOOKS_SCHEMA = {"type": "object", "properties": {
    "hooks": {"type": "array", "items": {"type": "string"}, "description": "5 different hooks, 4-9 words each"},
    "best": {"type": "integer", "description": "Index (0-4) of the hook most likely to stop a scroll"}},
    "required": ["hooks", "best"], "additionalProperties": False}


def hook_names_ok(hook: str, card_text: str) -> bool:
    """Every name in the hook (Capitalised words, not the first word) must also be on the card (19-20 Oct 2026: the 2 PM
    hook named Vidyut Jammwal, the details were about Sharad Kelkar)."""
    low = card_text.lower()
    names = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", hook)[1:] if w[:1].isupper() and len(w) > 3]
    first = re.findall(r"[A-Za-z][A-Za-z'-]+", hook)[:1]
    if first and first[0][:1].isupper() and len(first[0]) > 3:
        names.append(first[0])
    return all(n.lower() in low for n in names)


def pick_hook(title: str, facts: str, current: str, tmp_note: str = "", card_text: str = "") -> str:
    """🪝 Hook contest (owner, 19 Oct 2026): the AI writes 5 hooks from the full story and picks the strongest, using
    the pattern of the owner's best posts: the biggest name first, then the shock or twist, a fact from the story
    (no invented details, no clickbait lies). Falls back to the current hook."""
    import fun
    try:
        res = fun.claude_json(
            "You write the opening line of a short Instagram news Reel for young Indians (Hinglish, English letters, no "
            "emojis, 4-9 words). The owner's best hooks: 'Salman ne Karisma ke saath shoot se mana kiya', 'Kriti Sanon ka "
            "palatwaar', 'Tiger Shroff firing: Kota mein teeno aaropi pakde gaye'. Biggest name FIRST, then the shock, "
            "twist or new development; a fact from the story only; concrete, never vague ('Ye dekh ke hairan reh jaoge' is "
            "bad). " + tmp_note + (" The hook must be about the SAME person and fact as the card below: no name that is "
                                    "not on the card." if card_text else ""),
            f"Headline: {title}\n\nStory:\n{facts[:3500]}\n\n" + (f"THE CARD (the hook must match it):\n{card_text}\n\n"
                                                                       if card_text else "") + f"Current hook: {current}{viral_memory()}",
            HOOKS_SCHEMA, CONFIG.get("membership_model", "sonnet"), purpose="hook contest")
        hooks = [h.strip() for h in res["hooks"] if h.strip()]
        order = ([hooks[res["best"]]] if 0 <= res["best"] < len(hooks) else []) + [h for k, h in enumerate(hooks) if k != res["best"]]
        best = next((h for h in order if not card_text or hook_names_ok(h, card_text)), current)   # only names on the card
        print(f"  hook: '{best}' (was '{current}')")
        return best or current
    except Exception as e:
        print(f"  ! hook contest failed ({str(e)[:80]})")
        return current


def finish_news_reel(item: dict, opt: dict, photos: list[tuple], songs: dict, tmp: str,
                     article: str | None = None, hook_video: tuple[str, float, float] | None = None
                     ) -> tuple[Image.Image, str, dict]:
    """Render + proofread a news Reel. Returns (cover, mp4 path, feed fields)."""
    import carousel
    import generate as g
    import proofread
    import status
    status.step("Making the video + proofreading", 5)
    opt["caption"] = unescape(opt["caption"])
    roman_only(opt, ["caption", "pin_comment", "end_text"])
    picked_songs = checked_songs(opt.pop("songs", []), songs)   # kept out of the proofreader's text
    cover = None
    if not hook_video and opt.get("hook"):   # ⭐ the standard cover: the best photo + the best hook (owner, 19 Oct 2026)
        cover = photos[pick_cover_photo([x[0] for x in photos], item["title"], tmp)][0]
        article_text = article if article else __import__("carousel").story_text(item["link"], item["title"], item.get("summary", ""))
        opt["hook"] = pick_hook(item["title"], article_text, opt["hook"],
                                card_text=" ".join([item["title"]] + [x[2] for x in photos] + [opt.get("end_text", "")]))
    photos = photos[:3]   # shorter Reels (owner, 14 Oct 2026): 3 photos max
    roman_only(opt, ["hook"])
    images = [(img, whole) for img, _, _, whole in photos]
    data = {"post": opt, "slides": [{"n": i, "credit": c, "text": t} for i, (_, c, t, _) in enumerate(photos)]}
    out = os.path.join(tmp, "reel.mp4")

    def render(d):
        return news_reel([(images[s["n"]][0], s["credit"], s["text"], images[s["n"]][1]) for s in d["slides"]],
                         d["post"]["end_text"], d["post"]["highlight"], d["post"]["tag"], out,
                         hook=d["post"].get("hook", ""), hook_video=hook_video, hook_photo=cover)

    def drop(d, numbers):   # proofreader flagged weak photos; keep at least 2
        keep = [s for i, s in enumerate(d["slides"], 1) if i not in numbers]
        return {**d, "slides": keep} if 2 <= len(keep) < len(d["slides"]) else None

    if article is None:
        article = carousel.story_text(item["link"], item["title"], item.get("summary", ""))
    caption_of = (lambda d: g.full_caption(d["post"], item)) if item["link"] else \
        (lambda d: f"{d['post']['caption'].strip()}\n\nFollow {CONFIG['handle']} for daily updates.\n\n"
                   + " ".join(t if t.startswith("#") else f"#{t}" for t in d["post"]["hashtags"]))
    frames, data, proof = proofread.run(render, data, caption_of, article, drop=drop, strict=True)
    post = data["post"]
    return frames[0], out, {
        "headline": item["title"], "tag": "🎬 REEL · " + post["tag"],
        "caption": g.limit_hashtags(caption_of(data)), "source": item["source"], "source_url": item["link"],
        "pin_comment": post.get("pin_comment", ""), "songs": picked_songs, "proof": proof}


def finish_with_quote(item: dict, opt: dict, photos: list[tuple], songs: dict, tmp: str, article: str | None,
                      facts: str) -> tuple[Image.Image, str, dict]:
    """finish_news_reel + 🎥 the video of the moment for a 'X said' story (quote_video): the person's own video
    opens the Reel with the voice under the whole Reel (posted as it is), or an agency/channel post is the first,
    credited screenshot and its text joins the facts the proofreader checks."""
    import carousel
    import status
    status.step("Looking for the video of the moment", 4)
    qv = quote_video(item["title"], facts, tmp)
    credit, hook_video = "", None
    if qv and qv["own"]:
        hook_video, credit = (qv["path"], qv["start"], QUOTE_SECS), f"Video + voice: @{qv['handle']} on X"
    elif qv:
        photos = [(qv["shot"], f"@{qv['handle']} on X", qv["line"], True)] + photos
        credit = f"Video: @{qv['handle']} on X"
    if qv:
        base = article if article is not None else carousel.story_text(item["link"], item["title"], item.get("summary", ""))
        article = f"{base}\n\nX post by @{qv['handle']} (with the video): {qv['text']}"
    cover, out, entry = finish_news_reel(item, opt, photos, songs, tmp, article=article, hook_video=hook_video)
    if hook_video:
        status.step("Adding the speaker's voice", 5)
        voiced = os.path.join(tmp, "voiced.mp4")
        if qv["side"] == "left_opposition":   # their words only in their clip, the rest is ours (song added as usual)
            add_voice(out, qv["path"], qv["start"], voiced, only=QUOTE_SECS)
            out = voiced
        else:
            add_voice(out, qv["path"], qv["start"], voiced)
            out, entry = voiced, {**entry, "songs": []}   # the voice is the sound: no song
    if credit:
        cap = entry["caption"]
        entry["caption"] = cap.replace("\n\nFollow ", f"\n\n{credit}\n\nFollow ", 1) if "\n\nFollow " in cap \
            else f"{cap}\n\n{credit}"
    return cover, out, entry


# ---------------------------------------------------------------- ➕ Create: a Reel for the owner's topic

TOPIC_STYLE = {"type": "object", "properties": {
    "style": {"type": "string", "enum": ["news", "tag_friend", "meme"],
              "description": "news: a news story or fact (photo slideshow); tag_friend: a 'Tag that friend' joke; "
                             "meme: a setup + punchline meme"}},
    "required": ["style"], "additionalProperties": False}


def topic_reel(text: str, article: dict | None, state: dict, tmp: str) -> tuple[Image.Image, str, dict]:
    """A Reel for whatever the owner typed in ➕ Create: news -> photo slideshow, a funny idea ->
    'Tag that friend' clip or meme. Returns (cover, mp4 path, feed fields)."""
    import fun
    import status
    status.step("Choosing the Reel style", 2)
    about = (f"{article.get('title', '')}. {article.get('description', '')}" if article else "") + (f" {text}" if text else "")
    # Only a link -> news. The owner's words decide otherwise: something funny is a joke Reel even when a related
    # article was found (owner, 14 Oct 2026: "funny reel about Hopr and Tinder" became a news Reel on a wrong story).
    style = "news" if not text else fun.claude_json(
        "Decide what kind of Instagram Reel fits the page owner's request. If they ask for something funny (a joke, "
        "meme, 'funny reel', a relatable moment), it is tag_friend or meme, even when a news article is attached.",
        f"Owner's words: {text}" + (f"\nAttached article: {article.get('title', '')}" if article else ""),
        TOPIC_STYLE, CONFIG.get("membership_model", "sonnet"), purpose="reel style")["style"]
    print(f"  Reel style: {style}")
    status.step({"news": "News photo slideshow", "tag_friend": "Tag that friend clip",
                 "meme": "Meme"}[style] + " Reel", 3)
    if style != "news":   # the joke is about the owner's words, not the attached article
        return build_joke_reel("reel_clip" if style == "tag_friend" else "reel_meme", state, tmp, topic=text.strip())
    songs = trending_songs(state)
    schema = {**NEWS_OPTION, "properties": {k: v for k, v in NEWS_OPTION["properties"].items() if k != "pick"},
              "required": [k for k in NEWS_OPTION["required"] if k != "pick"]}
    import carousel
    # The writer sees the same full story the proofreader checks (owner, 16 Oct 2026: from a 1-line Inshorts summary
    # it filled gaps with unsupported lines and the Reel was blocked)
    facts = carousel.story_text(article["url"], article["title"], article.get("description", "")) if article else ""
    story = ((f"The owner asked: {text}\n" if text else "") +
             (f"Headline: {article['title']}\nSite: {article['site']}\n{article.get('description', '')}"
              f"\n\nFull story (use ONLY these facts):\n{facts[:4500]}" if article else ""))
    opt = fun.claude_json(NEWS_SYSTEM.split("From today's stories")[0] +
                          "The owner sent ONE story: write the Reel for it.\n" +
                          "Rules:" + NEWS_SYSTEM.split("Rules:")[1],
                          f"{fun.today_context()}\n\n{story}" + songs_prompt(songs), schema,
                          CONFIG.get("membership_model", "sonnet"), purpose="reel news script")
    item = {"link": article["url"] if article else "", "source": article["site"] if article else "Your pick",
            "title": (article or {}).get("title") or text[:120]}
    status.step("Collecting photos", 4)
    item["summary"] = (article or {}).get("description", "")
    photos = news_photos(item, opt)
    if len(photos) < 2:
        raise RuntimeError(f"only {len(photos)} usable photo(s) found for this story")
    return finish_with_quote(item, opt, photos, songs, tmp, facts if article else text,
                             f"{item['title']}. {item['summary']} {text}")


# ---------------------------------------------------------------- 4. political satire + reality check (6 PM)

# Set by the owner (15 Oct 2026): REALITY CHECK 10 s, punchline + SEND THIS 5 s, follow 1 s; then hook, KYA HUA? and
# REALITY CHECK 1 s shorter each (~29 s in all).
SATIRE_FACTS_SECS, SATIRE_PUNCH_SECS, SATIRE_FOLLOW_SECS = 9.0, 5.0, 1.0


def satire_hook_secs(claim: list[str]) -> float:
    """The dig over the photos/clip: 1.5 s + 0.25 s a word, 4.5-6 s."""
    return min(max(4.5, read_secs(" ".join(claim), 1.5)), 6.0)


SATIRE_BASE_WORDS = (28, 23)   # KYA HUA? and REALITY CHECK words of the owner's OK'd Reel (15 Oct 2026, 9.4 s + 9 s)


def satire_part_secs(context: list[str], facts: list[dict]) -> tuple[float, float]:
    """KYA HUA? and REALITY CHECK seconds (owner, 15 Oct 2026: 'for today it looks good; if content is more, only
    increase 1 or 2 seconds accordingly'): KYA HUA? ~3.5 words/s + 1.4 s (fewer words = shorter); REALITY CHECK 9 s;
    each + up to 2 s (1 s per 3 words) above today's words."""
    ctx_words = len(" ".join(context).split())
    fact_words = sum(len(f["fact"].split()) for f in facts)
    d_ctx = min(max(2.5, ctx_words / 3.5 + 1.4), SATIRE_BASE_WORDS[0] / 3.5 + 1.4 + 2.0)
    d_facts = SATIRE_FACTS_SECS + min(2.0, max(0, fact_words - SATIRE_BASE_WORDS[1]) / 3)
    return round(d_ctx, 2), round(d_facts, 2)


def satire_reel(clip: str, start: float, clip_dur: float, claim: list[str], context: list[str],
                facts: list[dict], punchline: str, highlight: list[str], out_mp4: str, opener: str = "",
                opener_still: Image.Image | None = None, opener_video: tuple[str, float] | None = None
                ) -> list[Image.Image]:
    """1) the dig over a reaction clip (meme layout), 2) KYA HUA? plain context, 3) REALITY CHECK: recent
    facts, each with its own source, 4) the punchline on yellow, 5) 'send this'. Each part stays long enough
    to read; a clip shorter than clip_dur freezes on its last frame. Returns frames to proofread:
    [hook, context, facts, punchline]."""
    highlights = {norm(w) for phrase in highlight for w in phrase.split()}
    clip_w, clip_h = RW, int(RW * 9 / 16)
    clip_y, text_bottom = 1000, 960
    c_lines, c_fnt, c_lh = fit_lines("\n".join(claim), RIGHT - LEFT, 520, sizes=range(84, 44, -4))
    c_imgs = line_images(c_lines, c_fnt, c_lh, "#111111", RED, highlights)
    k_lines, k_fnt, k_lh = fit_lines("\n".join(context), RIGHT - LEFT, 760, sizes=range(64, 38, -2))
    k_imgs = line_images(k_lines, k_fnt, k_lh, "#FFFFFF", YELLOW, highlights)
    blocks = []
    for f in facts:
        lines, fnt, lh = fit_lines(f["fact"], RIGHT - LEFT - 70, 300, sizes=range(54, 34, -2))
        nums = {norm(w) for line in lines for w in line if any(ch.isdigit() for ch in w)}
        blocks.append((line_images(lines, fnt, lh, "#FFFFFF", YELLOW, highlights | nums), lh, printable(f["source"])[:60]))
    p_lines, p_fnt, p_lh = fit_lines(punchline, RIGHT - LEFT, 700, sizes=range(96, 48, -4))
    p_imgs = line_images(p_lines, p_fnt, p_lh, "#111111", RED, highlights)
    kya = pill("KYA HUA?", font("Anton-Regular.ttf", 84), "#FFFFFF", "#111111", pad=(36, 12))
    head = pill("REALITY CHECK", font("Anton-Regular.ttf", 92), YELLOW, "#111111", pad=(40, 14))
    cta = pill("SEND THIS TO THAT ONE FRIEND", font("Poppins-Bold.ttf", 40), RED, "#FFFFFF", pad=(30, 16), arrow=True)
    src_f = font("Poppins-SemiBold.ttf", 26)
    logo = logo_img()

    t_ctx = clip_dur
    d_ctx, d_facts = satire_part_secs(context, facts)
    t_facts = t_ctx + d_ctx
    t_punch = t_facts + d_facts
    d_punch = SATIRE_PUNCH_SECS
    total = t_punch + d_punch
    enc, checks, frame = Encoder(out_mp4), [], None
    opened = None
    if opener and (opener_still is not None or opener_video):   # 🪝 like the news: the leader full-size + a big line
        layer = hook_layer(opener, highlights)
        stills = video_stills(opener_video[0], opener_video[1], SATIRE_OPEN_SECS) if opener_video else None
        still = opener_still
        for i in range(int(SATIRE_OPEN_SECS * FPS)):
            still = next(stills, still) if stills else still
            if still is None:
                break
            f = hook_frame(still, layer, i / FPS) if opener_video else \
                cover_hook_frame(still, opener, "ROAST", "", i / FPS, dur=SATIRE_OPEN_SECS)   # ⭐ standard cover
            if i == int(0.8 * FPS):
                opened = f.convert("RGB")
            enc.add(f)
    marks = [t_ctx - 0.2, t_facts - 0.2, t_punch - 0.2, t_punch + d_punch - 0.2]
    shots, shot = clip_frames(clip, start, clip_dur, clip_w, clip_h), None
    for i in range(int(total * FPS)):
        t = i / FPS
        if t < t_ctx:                                   # 1) the dig over the clip
            shot = next(shots, shot)                    # a short clip freezes on its last frame
            frame = Image.new("RGBA", (RW, RH), "#FFFFFF")
            if logo:
                frame.alpha_composite(logo, ((RW - logo.width) // 2, 150))
            y0 = text_bottom - len(c_imgs) * c_lh
            for j, img in enumerate(c_imgs):   # 🪝 the whole dig from the first frame
                frame.alpha_composite(img, (LEFT, y0 + j * c_lh))
            if shot is not None:
                frame.paste(shot, (0, clip_y))
            handle_tag(frame, clip_y + clip_h + 70, fill="#111111", stroke=0)
        elif t < t_facts:                               # 2) KYA HUA?
            lt = t - t_ctx
            frame = Image.new("RGBA", (RW, RH), "#1b1b1b")
            if logo:
                frame.alpha_composite(logo, (60, 150))
            frame.alpha_composite(kya, ((RW - kya.width) // 2, 330))
            y0 = 330 + kya.height + 80
            for j, img in enumerate(k_imgs):
                frame.alpha_composite(img, (LEFT, y0 + j * k_lh))
            handle_tag(frame, 1730)
        elif t < t_punch:                               # 3) REALITY CHECK, all facts at once
            lt = t - t_facts
            frame = Image.new("RGBA", (RW, RH), "#111111")
            if logo:
                frame.alpha_composite(logo, (60, 150))
            frame.alpha_composite(head, ((RW - head.width) // 2, 300))
            y = 300 + head.height + 70
            d = ImageDraw.Draw(frame)
            for imgs, lh, src in blocks:   # every fact at once
                d.ellipse((LEFT, y + lh // 2 - 14, LEFT + 28, y + lh // 2 + 14), fill=YELLOW)
                for n, img in enumerate(imgs):
                    frame.alpha_composite(img, (LEFT + 60, y + n * lh))
                y += len(imgs) * lh + 8
                d.text((LEFT + 60, y), f"Source: {src}", font=src_f, fill="#9a9a9a")
                y += 70
            handle_tag(frame, 1730)
        else:                                           # 4) punchline, 5) send this
            lt = t - t_punch
            frame = Image.new("RGBA", (RW, RH), YELLOW)
            if logo:
                frame.alpha_composite(logo, ((RW - logo.width) // 2, 150))
            y0 = (RH - len(p_imgs) * p_lh) // 2 - 120
            for j, img in enumerate(p_imgs):
                frame.alpha_composite(img, (LEFT, y0 + j * p_lh))
            frame.alpha_composite(cta, ((RW - cta.width) // 2, int(y0 + len(p_imgs) * p_lh + 90)))
            handle_tag(frame, 1730, fill="#111111", stroke=0)
        if len(checks) < len(marks) and t >= marks[len(checks)]:
            checks.append(frame.convert("RGB"))
        enc.add(frame)
    add_follow(enc, frame, SATIRE_FOLLOW_SECS)
    enc.close()
    while len(checks) < 4:
        checks.append(frame.convert("RGB"))
    return checks + ([opened] if opened else [])


POLITICS_TARGETS = ("Congress, AAP, TMC, the Left parties (CPI, CPI-M), the Cockroach Janta Party (CJP, founder "
                    "Abhijeet Dipke) and left-leaning activists/celebrities campaigning with them (e.g. Prakash Raj)")
POLITICS_SEARCHES = ["Congress party", "Rahul Gandhi", "AAP Kejriwal", "TMC Mamata Banerjee", "Abhishek Banerjee TMC",
                     "CPI(M)", "Cockroach Janta Party", "Dipke CJP"]
SATIRE_FORMATS = ["POV", "Nobody: / Party:", "Then vs Now (U-turn)", "Expectation vs Reality", "Job ad / rate card",
                  "Rules for being a ___ leader", "Breaking news parody", "WhatsApp chat"]

POLITICS_OPTION = {"type": "object", "properties": {
    "based_on": {"type": "string", "description": "The party and its action this joke is about"},
    "issue": {"type": "string", "description": "The issue in 2-4 words, e.g. 'Election Commission protests', "
              "'free electricity promise', 'Bihar seat sharing'"},
    "party": {"type": "string"},
    "format": {"type": "string", "enum": SATIRE_FORMATS, "description": "The meme format of the hook; the 3 options "
               "use 3 different formats, none used in the last 7 Reels"},
    "claim": {"type": "array", "items": {"type": "string"}, "description":
              "The hook over the clip in the chosen format: 1-3 short lines (max 18 words total), a funny dig at what "
              "they did, e.g. 'POV: ...', 'Nobody: / <party>: ...', '<year>: ... / Ab: ...', "
              "'Expectation: ... / Reality: ...'. No emojis."},
    "context": {"type": "array", "items": {"type": "string"}, "description":
                "'KYA HUA?': 2 short lines (max 28 words total) so a stranger understands: who (full name + role on "
                "first mention, e.g. 'Uttarakhand CM Pushkar Singh Dhami'), what they did, when ('is hafte', 'kal'), "
                "and explain every term in brackets (e.g. 'SIR (voter list ki jaanch)'). Natural Hinglish: "
                "'X ne kaha', 'X ke mutabik', never 'X ke as per'. No emojis."},
    "facts": {"type": "array", "description": "REALITY CHECK: 2 recent facts (2023-2026, with month/year) from THIS "
              "story's articles that PROVE them wrong by CONTRAST, so the hypocrisy is obvious: their promise vs what "
              "happened, what they said before vs now, their own record where they rule (official data). No "
              "election results unless the story is itself about an election result. Settled facts only (never "
              "allegations, FIRs, charges or probes). NEVER repeat their own statements, slogans or accusations as "
              "facts, and never a fact a recent Reel used.",
              "items": {"type": "object", "properties": {
                  "fact": {"type": "string", "description": "Max 14 words, Hinglish, with month/year, e.g. "
                           "'<Mon YYYY> <place>: <who> ne <what, with the number>'"},
                  "source": {"type": "string", "description": "Where it is reported: the article's site, or e.g. "
                             "'Election Commission' for official results"}},
                  "required": ["fact", "source"], "additionalProperties": False}},
    "punchline": {"type": "string", "description": "The big funny closing line, max 14 words, NEW (not a common "
                  "slogan, not like a recent Reel's). No emojis."},
    "versus": {"type": "boolean", "description": "true only if the people in 'people' are OPPONENTS in this story (e.g. "
               "Rahul Gandhi vs Bhagwant Mann); false for a leader and his own party or allies"},
    "hook": {"type": "string", "description": "4-8 words shown BIG over the leader's photo/video for the first 2 s, "
             "like a news hook: who + what in plain words, e.g. 'Rahul ne Mann se jawab maanga, AAP chup'. No emojis."},
    "people": {"type": "array", "description": "1-3 real people (or parties) this joke is about, main one first; their "
               "photos are shown instead of a stock clip, e.g. Rahul Gandhi + Bhagwant Mann",
               "items": {"type": "object", "properties": {
                   "name": {"type": "string", "description": "Short name shown under the photo, e.g. 'Rahul', 'Mann'"},
                   "wiki": {"type": "string", "description": "Exact English Wikipedia page title, e.g. 'Bhagwant Mann'"},
                   "kind": {"type": "string", "enum": ["person", "party"]}},
                   "required": ["name", "wiki", "kind"], "additionalProperties": False}},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-3 words copied exactly from the texts"},
    "clip_search": {"type": "array", "items": {"type": "string"},
                    "description": "3 searches for a REACTION stock clip (laughing, facepalm, shocked, eye roll...)"},
    "clip_wanted": {"type": "string"},
    "caption": {"type": "string"},
    "pin_comment": {"type": "string", "description": "Max 20 words, no hashtags: a funny line + a share nudge, e.g. "
                    "'Ye us dost ko bhejo jo har baat pe INDIA bloc ko defend karta hai 👇'"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5, include #reelsindia"},
    "songs": SONGS},
    "required": ["based_on", "issue", "party", "format", "versus", "hook", "claim", "context", "facts", "punchline", "people", "highlight", "clip_search",
                 "clip_wanted", "caption", "pin_comment", "hashtags", "songs"], "additionalProperties": False}

POLITICS_SYSTEM = """You write the daily political satire Reel for {page}, an Indian Hinglish page for 18-34s \
(English letters only, never Devanagari). The page is pro right-wing (BJP, NDA) and mocks the opposition: \
{targets}. Structure: a sharp funny dig (hook) -> KYA HUA? (plain context so ANYONE gets it without reading the \
news) -> REALITY CHECK (recent facts with month/year and a source, taken from THIS story's articles, that prove \
them wrong by CONTRAST: their promise vs what happened, what they said before vs now, their own record where they \
rule; never their own lines) -> a big funny punchline. NO election results in REALITY CHECK (owner, 14 Oct 2026: \
they came up every single day) unless the MAIN story is itself about an election result, and never when a recent \
Reel's facts were election results. \
Keep it tight: every word is read on a phone (hook max 18 words, KYA HUA? max 28, each fact max 14, punchline \
max 14). The hook must make sense on its own and KYA HUA? must tell a stranger who did what, in plain words. \
Make it something people SEND to friends.
Every day is a NEW joke: a new angle from THIS story, a meme format the recent Reels didn't use, a punchline \
nobody has heard. Never reuse a recent Reel's issue, angle, format, hook, punchline or facts, and no tired lines (e.g. \
"X ka formula: jeeto toh..., haaro toh...", "EVM mahaan / EVM shaitaan").
Make it instantly clear: name people with their role, explain every term, say when it happened. Tie it to a \
running joke young Indians already know (free-everything promises, "Sheesh Mahal", U-turns, foreign trips \
before results, blaming EVMs after losing...) only if it fits THIS story, and use RECENT examples (2024-2026).
Hard rules: real facts only, from the articles given (or official data they cite) with the right source; quotes only if an article has them; attribute claims ("X ne kaha", \
"X ke mutabik"). Mock their politics and actions only: never religion, caste, region, community, family, looks \
or health; a death, illness or grief is never the joke; no slurs, no abuse, no calls to violence; nothing that \
embarrasses the BJP. REALITY CHECK facts must be settled (official data, results, court verdicts, their own dated \
promises, statements and actions on record), never allegations, FIRs, charges or ongoing probes.
Caption: 2-4 short Hinglish lines, the first with the searchable keywords (leader/party/issue names), the context in \
one line, 1-3 emojis, last line a question that makes people take a side ('Kaun sahi? Comment karo 👇'). \
Prefer the opposition's best-known faces (Rahul Gandhi, Kejriwal, Mamata Banerjee...) when the story allows: big \
names get the most views and shares."""

# Not given to the satire writer since 14 Oct 2026 (owner: election results came up every day); kept for reference.
# Official results (Election Commission of India) for REALITY CHECK contrasts: the opposition also wins under the
# same EC/EVMs. Update when new big results come in.
ELECTION_RECORD = """Recent official results (Election Commission of India):
- Lok Sabha, Jun 2024: NDA 293 (BJP 240); INDIA bloc about 234 (Congress 99). Opposition did not question the EC/EVMs then.
- Karnataka, May 2023: Congress 135/224 (won).  - Telangana, Dec 2023: Congress 64/119 (won).
- Himachal Pradesh, Dec 2022: Congress 40/68 (won).  - Punjab, Mar 2022: AAP 92/117 (won).
- Jharkhand, Nov 2024: JMM-Congress alliance 56/81 (won).  - Jammu & Kashmir, Oct 2024: NC-Congress 48/90 (won).
- Haryana, Oct 2024: BJP 48/90, Congress 37.  - Maharashtra, Nov 2024: BJP-led Mahayuti 230/288.
- Delhi, Feb 2025: BJP 48/70, AAP 22; Arvind Kejriwal lost his New Delhi seat."""

SATIRE_JUDGE = ("You are a 22-year-old Indian on Instagram who did NOT read today's news. Rate each political satire "
                "Reel 1-10 for: understandable (from the Reel alone you know who did what and why it's funny, and the "
                "reality check really proves them wrong), funny (would you send it to your group), fresh (a NEW joke: "
                "not a tired old line, not the issue, angle, format, punchline or facts of a recent Reel below; "
                "election results as REALITY CHECK again = fresh 2). Be strict.")


def satire_recent(state: dict) -> str:
    """The last 15 satire Reels (state 'satire_recent'), so the next one is new."""
    rows = state.get("satire_recent", [])[-15:]
    return "\n".join(f"- {r['date']}: {r['party']} | issue: {r.get('issue') or r['topic']} | {r['format']} | "
                     f"hook: {r['hook']} | punchline: {r['punchline']}"
                     + (f" | facts: {'; '.join(r['facts'])}" if r.get("facts") else "") for r in rows) or "(none yet)"


def too_long(o: dict) -> list[str]:
    """Parts of a satire Reel over their word limits (every line must be readable; owner, 12 Oct 2026)."""
    def words(x) -> int:
        return len((" ".join(x) if isinstance(x, list) else x).split())
    out = [k for k, n in (("claim", 18), ("context", 28), ("punchline", 14)) if words(o[k]) > n]
    return out + ["facts"] * any(words(f["fact"]) > 14 for f in o["facts"][:2])


# ---------------------------------------------------------------- 🎯 roast card formats (owner, 19 Oct 2026)

ROAST_FORMATS = ["scoreboard", "quote_trap", "report_card", "then_now", "counter"]   # new ones; 'classic' = the old Reel
ROAST_HELP = {
    "scoreboard": "A SCOREBOARD of promise vs record: columns like ['Kaha', 'Hua'], 3-4 rows, each a dated, checkable item "
                  "from the articles (a number, a deadline, an own-record figure); the contrast is the joke.",
    "quote_trap": "A QUOTE TRAP: the leader's EXACT words from the articles (quote_text, in quotes), who said it "
                  "(quote_speaker) and where/when (quote_when). The hook mocks the quote itself ('Reporter: Court "
                  "jaoge? Leader: ...'). No rows.",
    "report_card": "A REPORT CARD of the leader's/party's promises: columns ['Wada', 'Hua kya', 'Marks'], 3-4 rows, "
                   "marks like '2/10' (a joke grade, but the 'hua kya' must be a documented fact).",
    "then_now": "THEN vs NOW: columns ['Tab', 'Ab'], 3 rows contrasting the SAME leader/party's earlier stand with "
                "today's, each dated, from the articles.",
    "counter": "A DAY COUNTER: counter_number = days since a dated promise/claim in the articles ('31'), counter_label "
               "= what it was ('Din se 'sabut jald saamne aayenge''). Compute the days from the dates in the articles. No rows.",
}
ROAST_OPTION = {"type": "object", "properties": {
    "feasible": {"type": "boolean", "description": "false if the articles do NOT contain what this format needs "
                 "(dated checkable items, an exact quote, a dated promise): then the other fields can be empty"},
    "based_on": {"type": "string"}, "issue": {"type": "string", "description": "2-4 words"}, "party": {"type": "string"},
    "hook": {"type": "string", "description": "4-9 words, huge on the leader's photo: the leader/party first, then the "
             "roast ('Rahul bole: Mera kaam loktantra bachana thodi hai'). Hinglish, no emojis, a fact"},
    "title": {"type": "string", "description": "Max 6 words, the card's title in a red bar, no emojis"},
    "columns": {"type": "array", "items": {"type": "string"}, "description": "2-3 column heads (empty if no table)"},
    "rows": {"type": "array", "description": "3-4 table rows (empty if no table)", "items": {"type": "object", "properties": {
        "cells": {"type": "array", "items": {"type": "string"}, "description": "one text per column, max 9 words each"},
        "source": {"type": "string", "description": "the article's site for this row"}},
        "required": ["cells", "source"], "additionalProperties": False}},
    "quote_text": {"type": "string"}, "quote_speaker": {"type": "string"}, "quote_when": {"type": "string"},
    "counter_number": {"type": "string"}, "counter_label": {"type": "string"},
    "verdict": {"type": "string", "description": "A stamp of max 3 words over the card, e.g. 'PROMISE FAIL', 'GRADE: F', "
                "'SAB RECORD PE'. Roasts the record, never the person's looks or family"},
    "punchline": {"type": "string", "description": "Max 14 words, the big funny closing line. No emojis"},
    "people": {"type": "array", "description": "1-2 real people (or the party) shown on the cover, main first", "items": {
        "type": "object", "properties": {"name": {"type": "string"}, "wiki": {"type": "string"},
                                         "kind": {"type": "string", "enum": ["person", "party"]}},
        "required": ["name", "wiki", "kind"], "additionalProperties": False}},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-3 words copied from the hook"},
    "caption": {"type": "string", "description": "2-4 short Hinglish lines, the first with the searchable names, last line "
                "a side-taking question ('Kaun sahi? Comment karo 👇'). 1-3 emojis"},
    "pin_comment": {"type": "string", "description": "Max 20 words, no hashtags, a funny line + a share nudge"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5, include #reelsindia"},
    "songs": SONGS},
    "required": ["feasible", "based_on", "issue", "party", "hook", "title", "columns", "rows", "quote_text", "quote_speaker",
                 "quote_when", "counter_number", "counter_label", "verdict", "punchline", "people", "highlight", "caption",
                 "pin_comment", "hashtags", "songs"], "additionalProperties": False}


ROAST_JUDGE = ("You are a 22-year-old Indian on Instagram who did NOT read today's news. Rate each political roast CARD "
               "Reel (a scoreboard, quote, report card, then-vs-now or day counter with a big hook and a punchline) 1-10 "
               "for: understandable (from the card alone you see who did what and the facts prove the point), funny "
               "(a sharp, shareable roast: the contrast, the quote or the punchline makes you smirk; a clean, factual "
               "burn counts), fresh (not the issue, angle, hook or punchline of a recent Reel below). Be fair, not harsh: "
               "a well-sourced roast that lands is a 7.")


def squash(text: str) -> str:
    """Lower-case letters and digits only, for checking that a quote is word for word in an article."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def named_in(names: list[str], text: str) -> bool:
    """True if a person/thing is actually named in the text (the last word of its name, 4+ letters, or the whole name)."""
    low = text.lower()
    for n in names:
        parts = [w for w in re.findall(r"[A-Za-z]+", n) if len(w) >= 4]
        if n.lower() in low or (parts and parts[-1].lower() in low):
            return True
    return False


def sense_check(kind: str, texts: str, shown: str = "") -> tuple[bool, str]:
    """Does this Reel make sense? (owner, 19 Oct 2026: a satire showed Mann + Kejriwal photos for a Kejriwal-vs-Centre
    story and mixed in another incident; a comedy had Aamir Khan and Thackeray photos for an everyday joke): ONE clear
    story or joke, hook / text / punchline fit together, no unrelated names or incidents, and the people or photos
    shown are the ones it is about. On any doubt the Reel is rewritten or the next story tried."""
    import fun
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}, "problem": {"type": "string"}},
              "required": ["ok", "problem"], "additionalProperties": False}
    try:
        res = fun.claude_json(
            "You are a 22-year-old Indian who sees a short Instagram Reel for the first time and did not read the news. "
            "Does it make sense? ok = true only if: it tells ONE clear story or joke; the hook, the text and the "
            "punchline belong together; no unrelated person, film or incident is mixed in (e.g. a different news event "
            "dropped into the hook); and the people/photos shown are the ones the text is about. Be strict.",
            f"Reel kind: {kind}\n\nText on the Reel:\n{texts}\n\nPeople/photos shown: {shown or '(none specific)'}",
            schema, CONFIG.get("membership_model", "sonnet"), purpose="sense check")
        if not res["ok"]:
            print(f"  sense check FAILED: {res['problem'][:160]}")
        return res["ok"], res["problem"]
    except Exception as e:
        print(f"  ! sense check failed to run ({str(e)[:80]}): accepted")
        return True, ""


def roast_format_today(today: str) -> str:
    """Even days a NEW card format in turn, odd days the classic Reel (starts Tue 20 Oct 2026 with the scoreboard)."""
    from datetime import date
    d = date.fromisoformat(today).toordinal() - date(2026, 10, 20).toordinal()
    return "classic" if d % 2 else ROAST_FORMATS[(d // 2) % len(ROAST_FORMATS)]


def roast_card(fmt: str, post: dict, photo: Image.Image | None, out_mp4: str, hook: str, credit: str) -> list[Image.Image]:
    """🧾 The new satire Reel: 1) the standard cover (the leader full-screen + the roast hook, 2.2 s), 2) the card of
    this format (scoreboard / then-vs-now / report card table, quote card, day counter) with the verdict stamp, 3) the
    punchline + SEND THIS (5 s), then Follow (1 s). Returns [cover, card, punchline] to proofread."""
    highlights = {norm(w) for phrase in post.get("highlight", []) for w in phrase.split()}
    BG, PANEL = "#0E0E12", "#1B1B24"
    logo = logo_img()
    card = Image.new("RGBA", (RW, RH), BG)
    if logo:
        card.alpha_composite(logo, (60, 110))
    cd = ImageDraw.Draw(card)
    title = printable(post["title"]).upper()
    tsize = 66
    tf = font("Anton-Regular.ttf", tsize)
    while tf.getlength(title) > RW - 2 * LEFT - 60 and tsize > 40:
        tsize -= 4
        tf = font("Anton-Regular.ttf", tsize)
    tbar = pill(title, tf, RED, "#FFFFFF", pad=(28, 12))
    card.alpha_composite(tbar, ((RW - tbar.width) // 2, 250))
    y = 250 + tbar.height + 50
    words = 0
    sources = ""
    if fmt in ("scoreboard", "then_now", "report_card"):
        cols = [printable(c) for c in post["columns"]][:3]
        n = len(cols)
        widths = {2: [490, 490], 3: [360, 400, 220]}.get(n, [980])
        x0 = 50
        hf = font("Poppins-Bold.ttf", 40)
        xs = [x0 + sum(widths[:i]) for i in range(n)]
        for c, cx in zip(cols, xs):
            cd.text((cx + 20, y), c.upper(), font=hf, fill=YELLOW)
        y += 78
        for row in post["rows"][:4]:
            cells = [printable(c) for c in row["cells"]][:n]
            laid = []
            for c, w in zip(cells, widths):
                ls, cf, clh = fit_lines(c, w - 44, 330, sizes=range(54, 34, -2))
                laid.append((ls, cf, clh))
                words += len(c.split())
            h = max(len(ls) * clh for ls, cf, clh in laid) + 66
            cd.rounded_rectangle((x0, y, x0 + sum(widths), y + h), radius=22, fill=PANEL)
            for (ls, cf, clh), cx, ci in zip(laid, xs, range(n)):
                last = (fmt == "report_card" and ci == n - 1)
                for j, wds in enumerate(ls):
                    cd.text((cx + 22, y + 33 + j * clh), " ".join(wds), font=cf,
                            fill=COVER_PINK if last else "#FFFFFF")
            y += h + 24
        sources = "Source: " + ", ".join(dict.fromkeys(printable(r["source"]) for r in post["rows"][:4] if r["source"]))
    elif fmt == "quote_trap":
        if photo is not None:
            win = ImageOps.fit(photo.convert("RGB"), (920, 300), Image.LANCZOS, centering=(0.5, 0.3))
            m = Image.new("L", win.size, 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0, win.width - 1, win.height - 1), radius=26, fill=255)
            card.paste(win, (80, y), m)
            y += 335
        q = "“" + printable(post["quote_text"]).strip("\"“” ") + "”"
        ls, qf, qlh = fit_lines(q, RW - 2 * LEFT - 20, 400, sizes=range(66, 34, -2))
        for j, wds in enumerate(ls):
            cd.text((LEFT + 10, y + j * qlh), " ".join(wds), font=qf, fill="#FFFFFF")
        y += len(ls) * qlh + 36
        sl, sf2, slh = fit_lines("- " + printable(post["quote_speaker"]), RW - 2 * LEFT - 20, 110, sizes=range(50, 30, -2))
        for j, wds in enumerate(sl):   # wrapped, never cut off at the edge
            cd.text((LEFT + 10, y + j * slh), " ".join(wds), font=sf2, fill=YELLOW)
        y += len(sl) * slh + 8
        wl, wf, wlh = fit_lines(printable(post["quote_when"]), RW - 2 * LEFT - 20, 110, sizes=range(34, 22, -2))
        for j, wds in enumerate(wl):
            cd.text((LEFT + 10, y + j * wlh), " ".join(wds), font=wf, fill="#9A9AA5")
        y += len(wl) * wlh + 90
        words = len(q.split()) + 6
        sources = ""
    else:   # counter
        num = printable(post["counter_number"])
        nf = font("Anton-Regular.ttf", 420)
        cd.text((RW // 2, y + 250), num, font=nf, fill=COVER_PINK, anchor="mm", stroke_width=6, stroke_fill="#000000")
        y += 520
        ls, lf, llh = fit_lines(printable(post["counter_label"]), RW - 2 * LEFT, 400, sizes=range(70, 40, -4))
        for j, wds in enumerate(ls):
            cd.text((RW // 2, y + j * llh), " ".join(wds), font=lf, fill="#FFFFFF", anchor="mt")
        words = len(post["counter_label"].split()) + 4
    if post.get("verdict"):   # the stamp: red outline, tilted
        sf = font("Anton-Regular.ttf", 110)
        txt = printable(post["verdict"]).upper()
        w = int(sf.getlength(txt)) + 80
        stamp = Image.new("RGBA", (w, 190), (0, 0, 0, 0))
        sd = ImageDraw.Draw(stamp)
        sd.rounded_rectangle((4, 4, w - 5, 185), radius=18, outline=RED, width=10)
        sd.text((w // 2, 95), txt, font=sf, fill=RED, anchor="mm")
        stamp = stamp.rotate(8, expand=True, resample=Image.BICUBIC)
        card.alpha_composite(stamp, ((RW - stamp.width) // 2, max(y + 150, 260) if y + 150 + stamp.height < 1640
                                      else 1640 - stamp.height))
    if sources:
        cd.text((50, 1700), sources[:90], font=font("Poppins-SemiBold.ttf", 26), fill="#888890")
    handle_tag(card, 1790)
    # punchline screen
    pl = Image.new("RGBA", (RW, RH), YELLOW)
    if logo:
        pl.alpha_composite(logo, ((RW - logo.width) // 2, 150))
    p_lines, p_fnt, p_lh = fit_lines(printable(post["punchline"]), RIGHT - LEFT, 700, sizes=range(96, 48, -4))
    p_imgs = line_images(p_lines, p_fnt, p_lh, "#111111", RED, highlights)
    py = (RH - len(p_imgs) * p_lh) // 2 - 120
    for j, img in enumerate(p_imgs):
        pl.alpha_composite(img, (LEFT, py + j * p_lh))
    cta = pill("SEND THIS TO THAT ONE FRIEND", font("Poppins-Bold.ttf", 40), RED, "#FFFFFF", pad=(30, 16), arrow=True)
    pl.alpha_composite(cta, ((RW - cta.width) // 2, int(py + len(p_imgs) * p_lh + 90)))
    handle_tag(pl, 1730, fill="#111111", stroke=0)
    d_cover, d_card, d_punch = COVER_SECS, min(11.0, max(6.5, 4.0 + 0.25 * words)), SATIRE_PUNCH_SECS
    enc, c1, last = Encoder(out_mp4), None, None
    base = photo if photo is not None else brand_background()
    for i in range(int(d_cover * FPS)):
        last = cover_hook_frame(base, hook, "ROAST", credit, i / FPS, dur=d_cover)
        if i == int(1.0 * FPS):
            c1 = last.convert("RGB")
        enc.add(last)
    for i in range(int(d_card * FPS)):
        enc.add(card)
    for i in range(int(d_punch * FPS)):
        enc.add(pl)
    add_follow(enc, pl, SATIRE_FOLLOW_SECS)
    enc.close()
    return [c1 or last.convert("RGB"), card.convert("RGB"), pl.convert("RGB")]


def make_roast_card_reel(state: dict, feed: list, today: str, tmp: str, stories: list, picks: list, recent: str,
                         fmt: str) -> bool:
    """🧾 6 PM on its new-format days (owner, 19 Oct 2026: 'explore new ideas to roast the left'): one story, one card
    format, every row/quote/number from the articles, the standard cover. False (reason printed) if nothing fits,
    then the classic Reel is made instead."""
    import carousel
    import create_post
    import fun
    import generate as g
    import proofread
    import status
    model = CONFIG.get("membership_model", "sonnet")
    songs = trending_songs(state)
    used = set(state.get("reel_politics_used", []))
    best = story = sources = None
    for n in picks:
        story = stories[n - 1]
        article = carousel.story_text(story["url"], story["title"]) or story["title"]
        related = [a for a in create_post.bing_articles(story["title"][:90], limit=6) if a["url"] != story["url"]][:3]
        background = []
        for a in related:
            t = carousel.article_text(carousel.page_html(a["url"]), limit=2500)
            background.append(f"({a['site']}) {a['title']}\n{t}" if t else f"({a['site']}) {a['title']}")
        sources = f"MAIN ({story['site']}): {story['title']}\n{article[:5000]}\n\nRELATED:\n" + "\n\n".join(background)
        status.step(f"Writing the {fmt.replace('_', ' ')} card: {story['title'][:50]}", 3)
        try:
            options = fun.claude_json(
                POLITICS_SYSTEM.format(page=CONFIG["page_name"], targets=POLITICS_TARGETS) + "\n\nTHIS Reel is a CARD "
                "Reel, not the usual KYA HUA?/REALITY CHECK one: " + ROAST_HELP[fmt] + " Every row, quote, number and date "
                "must be in the articles below (right source); if they are not, set feasible=false. Roast actions, "
                "promises and own words, never looks, family or religion. The hook is huge on the leader's photo.",
                f"{fun.today_context()}\n\nArticles:\n{sources}\n\nRecent satire Reels (new angle, hook, punchline):\n"
                f"{recent}\n\nWrite exactly 2 different {fmt} cards about the MAIN story, format = {fmt}."
                f"{viral_memory()}{songs_prompt(songs)}",
                options_schema(ROAST_OPTION, "Exactly 2 cards"), model, purpose="roast card writing")["options"][:2]
        except Exception as e:
            print(f"  ! roast card writing failed: {str(e)[:100]}")
            continue
        options = [o for o in options if o.get("feasible") and o.get("punchline") and (
            o["rows"] if fmt in ("scoreboard", "then_now", "report_card") else
            # a quote card shows ONLY words that are word for word in the articles (19 Oct 2026 test: a minister's
            # word was put in the leader's mouth)
            (o["quote_text"] and len(squash(o["quote_text"])) > 12 and squash(o["quote_text"]) in squash(sources))
            if fmt == "quote_trap" else o["counter_number"])]
        if not options:
            print(f"  roast card: the articles don't fit the {fmt} format: next story")
            continue
        try:
            best = fun.judge_best(options, lambda o: f"[{fmt}] {o['hook']} | {o['title']} | " + " / ".join(
                " + ".join(r["cells"]) for r in o["rows"]) + f" | {o['quote_text']} {o['counter_number']} | {o['punchline']}",
                model, judge_system=ROAST_JUDGE + f"\n\nRecent Reels:\n{recent}", min_fresh=5, min_clear=6)
            blob = " ".join([best["hook"], best["title"], best["quote_text"], best["punchline"], best["counter_label"]]
                            + [c for r in best["rows"] for c in r["cells"]])
            best["people"] = [x for x in best.get("people", []) if named_in([x["name"], x["wiki"]], blob + " " + best["quote_speaker"])]
            ok, why = sense_check("political roast card (" + fmt + ")", blob, ", ".join(x["name"] for x in best["people"]))
            if not ok:
                print("  roast card doesn't make sense: next story")
                continue
            break
        except fun.NoGoodJoke:
            print("  roast card below the judge's bar: next story")
    if best is None:
        return False
    roman_only(best, ["caption", "pin_comment", "punchline", "hook", "title"])
    picked_songs = checked_songs(best.pop("songs", []), songs)
    people = [{"name": x["name"], "kind": x["kind"], "lookup": x["wiki"]} for x in best.get("people", [])
              if x["kind"] == "person"][:2]   # a party's logo or flag is never the cover (19 Oct 2026 test)
    photos, credits = named_photos(people, fill_missing=True, vs=False) if people else ([], [])
    real = [x for x in (photos[1:] if len(photos) > 1 else photos) if min(x.size) >= 400]
    photo = real[pick_cover_photo(real, story["title"], tmp)] if real else None   # 📸 photo contest
    best["hook"] = pick_hook(story["title"], sources, best["hook"], "A roast of the left/opposition: punchy, fair.",
                             card_text=" ".join([best["title"], best["quote_text"], best["quote_speaker"], best["punchline"],
                                                 best["counter_label"]] + [c for r in best["rows"] for c in r["cells"]]))
    item = {"link": story["url"], "source": story["site"], "title": story["title"]}
    out = os.path.join(tmp, "reel.mp4")

    def caption_of(d):
        d = d["post"]
        return g.full_caption({**d, "caption": d["caption"].strip() + (
            "\n\nPhotos (Wikimedia Commons): " + "; ".join(credits) if credits else "")}, item)
    status.step("Making the card + fact-checking", 5)
    frames, data, proof = proofread.run(
        lambda d: roast_card(fmt, d["post"], photo, out, d["post"]["hook"], credits[0].split(":")[-1].strip()[:50] if credits else ""),
        {"post": best}, caption_of, sources, strict=True)
    post = data["post"]
    if fmt == "quote_trap" and squash(post["quote_text"]) not in squash(sources):   # the proofreader changed the words
        raise proofread.Unverified("the quote on the card is not word for word in the articles")
    state["reel_politics_used"] = (list(used) + [story["url"]])[-60:]
    state["satire_recent"] = (state.get("satire_recent", []) + [{
        "date": today, "party": post["party"], "format": fmt, "topic": post["based_on"], "hook": printable(post["hook"]),
        "punchline": post["punchline"], "issue": post.get("issue", ""),
        "facts": [" + ".join(r["cells"]) for r in post["rows"]][:3] or [post["quote_text"] or post["counter_label"]]}])[-15:]
    save_reel(feed, state, frames[0], out, f"reel_politics-{today}", {
        "headline": printable(post["hook"]), "tag": f"🎬 REEL · ROAST · {fmt.replace('_', ' ').upper()}",
        "caption": g.limit_hashtags(caption_of(data)), "source": story["site"], "source_url": story["url"],
        "pin_comment": post.get("pin_comment", ""), "songs": picked_songs, "proof": proof})
    return True


def make_politics_reel(state: dict, feed: list, today: str, tmp: str) -> None:
    """🎬 6 PM: a dig at the opposition's latest action, the context, a REALITY CHECK with recent sourced facts
    and a punchline."""
    from datetime import datetime, timedelta, timezone
    import carousel
    import create_post
    import fun
    import generate as g
    import proofread
    import status
    status.step("Finding the opposition's most viral news", 2)
    model = CONFIG.get("membership_model", "sonnet")
    now = datetime.now(timezone.utc)
    stories = [{"title": p["headline"], "url": p["source_url"], "site": p["source"], "date": p["created_at"]}
               for p in feed if p.get("kind") == "news" and p.get("source_url") and "POLITIC" in p.get("tag", "").upper()
               and datetime.fromisoformat(p["created_at"]) >= now - timedelta(hours=24)]
    for q in POLITICS_SEARCHES:   # newest first: today's stories, then backward (owner, 12 Oct 2026)
        stories += [a for a in create_post.bing_articles(q, limit=6, newest=True) if a.get("title")]
    # Most viral first (owner, 12 Oct 2026): X trends name people, not parties (e.g. "Dipke"), so the AI turns
    # the trends about the opposition into news searches.
    xt = fun.x_trends()
    hours = dict(xt)
    if xt:
        heads = "\n".join(f"- {s['title']}" for s in stories[:50])
        found = fun.claude_json(
            f"You find which of today's X (Twitter) trends in India are about {POLITICS_TARGETS}.",
            "X trends in India, last 24 h (hours trended):\n" + "\n".join(f"{n}. {t} ({h} h)"
                                                                        for n, (t, h) in enumerate(xt[:150], 1)) +
            f"\n\nToday's headlines about these parties (to connect names to parties):\n{heads}\n\n"
            "List up to 6 trends about these parties (their leaders, protests, statements, controversies), most hours "
            "first: the trend's number, and an English news search query for what happened. Empty if none.",
            {"type": "object", "properties": {"trends": {"type": "array", "items": {"type": "object", "properties": {
                "n": {"type": "integer"}, "query": {"type": "string"}},
                "required": ["n", "query"], "additionalProperties": False}}},
             "required": ["trends"], "additionalProperties": False}, model, purpose="satire trends")["trends"]
        for t in found[:6]:
            if not 1 <= t["n"] <= min(150, len(xt)):
                continue
            term = xt[t["n"] - 1][0]
            print(f"  X trend: {term} ({hours[term]} h) -> {t['query']}")
            for q in dict.fromkeys([t["query"], term.lstrip("#")]):   # the AI's query + the trend word itself
                stories += [dict(a, trend=term) for a in create_post.bing_articles(q, limit=4, newest=True)
                            if a.get("title")]
    for s in stories:   # a headline naming a trend (e.g. "Dipke") counts as that trend
        if not s.get("trend"):
            s["trend"] = max((t for t in hours if len(t) >= 4 and t.lower().lstrip("#") in s["title"].lower()),
                             key=lambda t: hours[t], default="")
    used = set(state.get("reel_politics_used", []))

    def age_h(s: dict) -> float:
        try:
            d = datetime.fromisoformat(s.get("date", ""))
        except ValueError:
            return 9999.0
        return (now - (d if d.tzinfo else d.replace(tzinfo=timezone.utc))).total_seconds() / 3600
    stories = sorted((s for s in {s["url"]: s for s in stories}.values() if s["url"] not in used),
                     key=lambda s: (-hours.get(s.get("trend", ""), 0), age_h(s)))[:45]   # most viral, then newest
    if not stories:
        raise RuntimeError("no opposition news found today")
    listing = "\n".join(f"{n}. ({'date unknown' if age_h(s) >= 9999 else f'{age_h(s):.0f} h ago' if age_h(s) < 48 else f'{age_h(s) / 24:.0f} days ago'}) "
                        + (f"[trending on X: {s['trend']}, {hours.get(s['trend'], 0)} h] " if s.get("trend") else "")
                        + f"({s['site']}) {s['title']}" for n, s in enumerate(stories, 1))
    recent = satire_recent(state)
    picks = fun.claude_json(POLITICS_SYSTEM.format(page=CONFIG["page_name"], targets=POLITICS_TARGETS),
                            f"{fun.today_context()}\n\nRecent satire Reels (newest last):\n{recent}\n\n"
                            f"Stories (most viral first, then newest):\n{listing}\n\n"
                            f"Rank the 3 best stories about {POLITICS_TARGETS} for a funny reality-check Reel. Take "
                            f"the MOST VIRAL story that makes them look bad first (a claim exposed, e.g. by a video, a "
                            f"U-turn, hypocrisy, an own goal): trending on X the longest, many headlines about it. Their "
                            f"own campaign or protest trending is not by itself a story against them. Go from "
                            f"the newest backward: TODAY's stories first, yesterday's only if today has nothing good, "
                            f"older only if nothing newer works. Prefer a NEW topic (not the topic or angle of a recent "
                            f"Reel) and a party that isn't in the last 2 Reels when it has a good story. NEVER a story about the same "
                            f"issue as any of the last 3 Reels (owner, 14 Oct 2026: five days of Election Commission "
                            f"protests in a row): pick a different issue even if that one still trends. A story "
                            f"involving a death is fine when the joke is about a leader's claim or action (e.g. a claim "
                            f"a video exposed), never the death itself. Skip stories "
                            f"mainly about a leader's relatives, and stories that are only allegations (FIR, ED/CBI "
                            f"case, chargesheet, no verdict). Empty if none.",
                            {"type": "object", "properties": {"picks": {"type": "array", "items": {"type": "integer"}}},
                             "required": ["picks"], "additionalProperties": False},
                            CONFIG.get("membership_model", "sonnet"), purpose="satire pick")["picks"]
    picks = [n for n in picks if 1 <= n <= len(stories)][:3]
    if not picks:
        raise RuntimeError("no story about the opposition today")
    fmt = roast_format_today(today)
    if fmt != "classic":   # 🧾 a new card format today (then the next one in the cycle); else the classic Reel below
        order = [fmt, ROAST_FORMATS[(ROAST_FORMATS.index(fmt) + 1) % len(ROAST_FORMATS)]]
        for f in order:
            try:
                if make_roast_card_reel(state, feed, today, tmp, stories, picks, recent, f):
                    return
            except Exception as e:   # never lose the 6 PM slot over a new format
                print(f"  ! roast card {f} failed ({type(e).__name__}: {str(e)[:120]})")
            status.step(f"The {f.replace('_', ' ')} card didn't fit today", 3)
        status.step("No card format fit today: the classic Reel", 3)
    songs = trending_songs(state)
    best = None
    def write(sources: str, extra: str = "") -> list[dict]:
        options = fun.claude_json(POLITICS_SYSTEM.format(page=CONFIG["page_name"], targets=POLITICS_TARGETS),
                                  f"{fun.today_context()}\n\nArticles:\n{sources}\n\nRecent satire "
                                  f"Reels (yours must differ in angle, format, hook and punchline; no format from the "
                                  f"last 7):\n{recent}\n\nWrite exactly 3 different Reels about the MAIN story, each "
                                  "in a different format." + extra + viral_memory() + songs_prompt(songs),
                                  options_schema(POLITICS_OPTION, "Exactly 3 different Reels"),
                                  model, purpose="satire writing")["options"][:3]
        for o in options:
            o["caption"] = unescape(o["caption"])
        return options

    def judge(options: list[dict]) -> dict:
        best = fun.judge_best(options, lambda o: f"[{o['format']}] " + " ".join(o["claim"]) + " | KYA HUA: "
                              + " ".join(o["context"]) + " | REALITY CHECK: " + " / ".join(f["fact"] for f in o["facts"])
                              + " | " + o["punchline"], model,
                              judge_system=f"{SATIRE_JUDGE}\n\nRecent Reels:\n{recent}", min_fresh=6,
                              min_clear=6)   # one point lower than jokes (owner, 14 Oct 2026: post more reliably)
        blob = " ".join(best["claim"] + best["context"] + [f["fact"] for f in best["facts"]] + [best["punchline"], best.get("hook", "")])
        best["people"] = [x for x in best.get("people", []) if named_in([x["name"], x["wiki"]], blob)]   # only who is named
        ok, why = sense_check("political satire", f"Hook: {best.get('hook', '')}\nDig: {' '.join(best['claim'])}\n"
                              f"KYA HUA: {' '.join(best['context'])}\nFacts: " + " / ".join(f["fact"] for f in best["facts"])
                              + f"\nPunchline: {best['punchline']}", ", ".join(x["name"] for x in best["people"]))
        if not ok:
            err = fun.NoGoodJoke("the Reel does not make sense: " + why)
            err.scores = [{"option": 1, "understandable": 4, "funny": 5, "fresh": 5, "why": why}]
            raise err
        return best

    for i, n in enumerate(picks):   # the Reel must pass the judge; else try the next story
        story = stories[n - 1]
        article = carousel.story_text(story["url"], story["title"]) or story["title"]
        # Background: related recent coverage, so the Reel has real context and recent examples.
        related = [a for a in create_post.bing_articles(story["title"][:90], limit=6) if a["url"] != story["url"]][:3]
        background = []
        for a in related:
            text = carousel.article_text(carousel.page_html(a["url"]), limit=2500)
            background.append(f"({a['site']}) {a['title']}\n{text}" if text else f"({a['site']}) {a['title']}")
        sources = f"MAIN ({story['site']}): {story['title']}\n{article[:5000]}\n\nRELATED:\n" + "\n\n".join(background)
        status.step(f"Writing 3 Reels: {story['title'][:60]}", 3)
        try:
            best = judge(write(sources))
            break
        except fun.NoGoodJoke as e:
            if i == 0:   # the most viral story gets one rewrite with the judge's reasons (owner, 12 Oct 2026)
                print(f"  Reels for '{story['title'][:50]}' weren't clear/funny/fresh enough: rewriting once")
                status.step("Rewriting with the judge's notes", 3)
                notes = "\n".join(f"- Option {s['option']}: understandable {s['understandable']}, funny {s['funny']}, "
                                  f"fresh {s['fresh']}: {s['why']}" for s in e.scores)
                try:
                    best = judge(write(sources, "\n\nA strict judge (a 22-year-old who did NOT read the news) "
                                       f"rejected your first 3 Reels:\n{notes}\nWrite 3 new Reels that fix this: who, "
                                       "what and why instantly clear (KYA HUA? explains it), and a joke that lands."))
                    break
                except fun.NoGoodJoke:
                    pass
            print(f"  Reels for '{story['title'][:50]}' weren't clear/funny/fresh enough: trying the next story")
    if best is None:
        raise RuntimeError("the Reels for today's opposition stories weren't clear, funny or fresh enough")
    if too_long(best):   # one rewrite to the word limits, so every line can be read
        status.step("Shortening the text", 3)
        best = fun.claude_json(POLITICS_SYSTEM.format(page=CONFIG["page_name"], targets=POLITICS_TARGETS),
                               f"Articles:\n{sources}\n\nThis Reel has too many words in: {', '.join(too_long(best))}. "
                               "Shorten those parts to the limits (hook max 18 words, KYA HUA? max 28 words in total, "
                               "each fact max 14, punchline max 14): same joke, same facts and sources, who/what/when "
                               f"still clear. Change nothing else.\n\n{json.dumps(best, ensure_ascii=False)}",
                               POLITICS_OPTION, model, purpose="satire shorten")
    roman_only(best, ["caption", "pin_comment", "punchline"])
    picked_songs = checked_songs(best.pop("songs", []), songs)
    best["facts"] = best["facts"][:2]   # (a 1-fact, shorter version failed the judge on every story, 14 Oct 2026)
    art_note, real, cid = "", False, None
    # The people the joke is about, not a random stock face (owner, 15 Oct 2026: Rahul Gandhi + Bhagwant Mann were
    # the story, the Reel showed a stranger): 'RAHUL VS MANN' card + their free Wikipedia photos (credited).
    people = [{"name": x["name"], "kind": x["kind"], "lookup": x["wiki"]} for x in best.get("people", [])][:3]
    # 🎥 Their OWN video of the moment first (owner, 16 Oct 2026), playing in the window (no sound, the song is added)
    qv = quote_video(story["title"], sources[:1500], tmp)
    video_start, video_credit = None, ""
    photos, credits = ([], []) if qv and qv["own"] else (
        named_photos(people, fill_missing=True, vs=best.get("versus", False)) if people else ([], []))
    if qv and qv["own"]:
        clip, secs, video_start = qv["path"], video_info(qv["path"])[2], qv["start"]
        video_credit = f"Video: @{qv['handle']} on X"
        status.step(f"Their own video: @{qv['handle']}", 4)
    elif credits:   # at least one real photo
        status.step(f"Photos: {', '.join(x['name'] for x in people)}", 4)
        # As long as the hook window, so every photo gets the same time (owner, 15 Oct 2026: Mann had 1.3 s)
        secs = satire_hook_secs(best["claim"])
        clip, real = os.path.join(tmp, "people.mp4"), True
        photo_clip(photos[:3], RW, 608, secs, clip)
        print(f"  people photos: {len(photos[:3])} of {[x['lookup'] for x in people]}")
    else:
        credits = []
        try:
            cid = pick_clip({**best, "text": " ".join(best["claim"]), "punch": best["punchline"]}, state)
            clip, secs = download_clip(cid, tmp)
        except RuntimeError as e:   # no fitting reaction clip: photos -> 🎨 AI picture -> branded background
            print(f"  ! {e}")
            clip, secs, art_note = fallback_visual({**best, "photo_query": best.get("clip_wanted", "")}, (RW, 608), tmp)
    out = os.path.join(tmp, "reel.mp4")
    item = {"link": story["url"], "source": story["site"], "title": story["title"]}

    def caption_of(p):   # Wikipedia photos need their photographer + licence (owner, 15 Oct 2026)
        return g.full_caption({**p, "caption": p["caption"].strip() + (
            "\n\nPhotos (Wikimedia Commons): " + "; ".join(credits) if credits else "") +
            (f"\n\n{video_credit}" if video_credit else "")}, item)

    # 🪝 Opening like the news (owner, 16 Oct 2026: 73% skipped the meme-text opening): the leader full-size —
    # their own video, else their photo (not the VS card), else the clip's first frame — under the big `hook` line
    opener_video = (clip, video_start) if video_start is not None else None
    real_photos = photos[1:] if len(photos) > 1 else photos   # photos[0] is the 'A VS B' card
    opener_still = None if opener_video else (
        real_photos[pick_cover_photo(real_photos, story["title"], tmp)] if real_photos else
        next(video_stills(clip, 0.0, 0.1, 1280), None))   # 📸 photo contest
    if best.get("hook"):   # 🪝 hook contest
        best["hook"] = pick_hook(story["title"], sources, best["hook"], "A roast of the left/opposition: punchy, fair.",
                                 card_text=" ".join(best["claim"] + best["context"] + [f["fact"] for f in best["facts"]]
                                                    + [best["punchline"]]))

    def render(d):   # the dig over the clip stays until it's read (satire_hook_secs); later parts time themselves
        clip_dur = satire_hook_secs(d["post"]["claim"])
        start = video_start + SATIRE_OPEN_SECS if video_start is not None else \
            0.0 if real else max(0.0, secs - clip_dur - 0.2)   # a short clip freezes on its last frame
        return satire_reel(clip, start, clip_dur, d["post"]["claim"], d["post"]["context"], d["post"]["facts"],
                           d["post"]["punchline"], d["post"]["highlight"], out, opener=d["post"].get("hook", ""),
                           opener_still=opener_still, opener_video=opener_video)
    status.step("Making the video + fact-checking", 5)
    frames, data, proof = proofread.run(
        render,
        {"post": best}, lambda d: caption_of(d["post"]),
        sources, strict=True)
    post = data["post"]
    if cid:
        state["reel_clips"] = (state.get("reel_clips", []) + [cid])[-80:]
    if art_note.startswith("AI"):
        proof = {**proof, "state": "check", "issues": proof.get("issues", []) + [
            f"🎨 The picture is an {art_note}: turn on Instagram's 'AI info' label when posting."]}
    state["reel_politics_used"] = (list(used) + [story["url"]])[-60:]
    state["satire_recent"] = (state.get("satire_recent", []) + [{
        "date": today, "party": post["party"], "format": post["format"], "topic": post["based_on"],
        "hook": printable(" ".join(post["claim"])), "punchline": post["punchline"], "issue": post.get("issue", ""),
        "facts": [f["fact"] for f in post["facts"]]}])[-15:]
    save_reel(feed, state, frames[0], out, f"reel_politics-{today}", {
        "headline": printable(" ".join(post["claim"]))[:140], "tag": "🎬 REEL · REALITY CHECK",
        "caption": g.limit_hashtags(caption_of(post)), "source": story["site"], "source_url": story["url"],
        "pin_comment": post.get("pin_comment", ""), "songs": picked_songs, "proof": proof})


# ---------------------------------------------------------------- 5. 🙏 Bhagavad Gita Reel (📦 Ready, every night)

SAFFRON = "#FF9933"
GITA_SEARCHES = ["Krishna Arjuna chariot painting", "Gita Upadesh painting", "Krishna Arjuna miniature painting",
                 "Bhagavad Gita illustration Krishna Arjuna"]
PAINTING_WORDS = ("painting", "miniature", "upadesh", "illustration", "school", "museum", "gita", "kurukshetra")
FREE_LICENCES = ("public domain", "cc0", "no restrictions", "cc by")   # "cc by" also covers CC BY-SA (credit given)


def commons_images(queries: list[str]) -> list[dict]:
    """Free-licence images from Wikimedia Commons: [{url, title, credit, w, h}] (1080 px wide copies)."""
    import re
    import requests
    ua = {"User-Agent": "KhabarBawaalStudio/1.0 (github.com/vikashlal12345)"}
    out, seen = [], set()
    for q in queries:
        try:
            r = requests.get("https://commons.wikimedia.org/w/api.php", headers=ua, timeout=25, params={
                "action": "query", "format": "json", "generator": "search", "gsrsearch": q + " filetype:bitmap",
                "gsrnamespace": 6, "gsrlimit": 15, "prop": "imageinfo", "iiprop": "url|size|extmetadata",
                "iiurlwidth": 1080}).json()
        except Exception as e:
            print(f"  ! Commons search '{q}' failed: {e}")
            continue
        for page in (r.get("query", {}).get("pages") or {}).values():
            ii = page["imageinfo"][0]
            meta = ii.get("extmetadata", {})
            lic = meta.get("LicenseShortName", {}).get("value", "")
            if (page["title"] in seen or not lic.lower().startswith(FREE_LICENCES) or min(ii["width"], ii["height"]) < 700
                    or ii["width"] > 2.1 * ii["height"] or "3d" in page["title"].lower()):   # no stereo/panorama pairs
                continue
            seen.add(page["title"])
            artist = re.sub(r"<[^>]+>|\s+", " ", meta.get("Artist", {}).get("value", "")).strip()
            artist = "" if "unknown" in artist.lower() else artist[:40]
            out.append({"url": ii.get("thumburl") or ii["url"], "title": page["title"][5:],
                        "credit": ", ".join(x for x in (artist, lic, "Wikimedia Commons") if x),
                        "w": ii["width"], "h": ii["height"]})
    return out


# 🎨 God pictures for the devotional and Gita Reels (owner, 11 Oct 2026): painted by free public image demos on
# Hugging Face (no account, no billing) in a glowing storybook style, newest model first; one that fails is skipped
# for the rest of the run. If all are busy or nothing looks right, the free Wikimedia Commons paintings below are
# used as before. Without an account the demos allow only ~4-5 pictures a day per internet address (shared; each
# GitHub run gets a fresh one), hence 2 tries per scene. Gods are drawn in a simple two-armed form (owner's choice:
# the models get multi-armed forms wrong).
IMAGE_MODELS = [("Z-Image", "https://tongyi-mai-z-image-turbo.hf.space/gradio_api/call/generate"),
                ("FLUX.2", "https://black-forest-labs-flux-2-klein-9b.hf.space/gradio_api/call/generate"),
                ("FLUX", "https://black-forest-labs-flux-1-schnell.hf.space/gradio_api/call/infer")]
Z_SIZES = {(864, 1536): "1152x2048 ( 9:16 )", (1088, 1360): "1344x1728 ( 7:9 )"}   # Z-Image: fixed sizes, sharpest
_MODELS_DOWN: set[str] = set()
FLUX_STYLE = ("Highly detailed hand-drawn storybook illustration with fine ink linework and rich painterly colour, "
              "cinematic warm golden glow against deep blue-black shadows (night, dusk or dawn as described), glowing "
              "light sources such as a halo, diyas or moonlight, emotional, reverent Indian devotional art, sharp and "
              "clean. Vertical composition: the figures large in the upper-middle of the picture, their heads well "
              "below the top edge, plain dark sky in the top fifth and calm dark ground in the lower third, both "
              "kept empty. ")
FLUX_AVOID = " No text, no captions, no letters, no writing, no signature, no watermark."
SCENE_HELP = ("English picture description for an AI painter, max 50 words. Keep it simple, the painter gets "
              "confused by busy scenes: the deity and at most one person, one setting. The painter adds extra arms to gods, "
              "so show only ONE of the deity's hands (raised in blessing, or resting on the person's head or shoulder) "
              "with the other arm hidden in the saree or shawl, or show the deity from behind or from a distance; never "
              "describe several arms or hands. Only the 1-2 most recognisable items, each named once (e.g. Krishna: blue "
              "skin, peacock feather crown, flute; Hanuman: monkey face, golden mace on his shoulder; Maa Durga: red saree, "
              "golden crown, her lion beside her; Maa Shailputri: white bull beside her, lotus in her one visible hand). "
              "The deity must look clearly divine, never like an ordinary person: golden crown (or Hanuman's and "
              "Ganesh's own look) and a soft golden halo or glow around them. Then the light and the mood. Fully "
              "clothed, reverent, nothing scary.")


def first_url(x) -> str:
    """The first file URL in a demo's answer (a picture, or a gallery of them)."""
    if isinstance(x, dict):
        return x.get("url") or first_url(x.get("image")) if x.get("url") or x.get("image") else ""
    if isinstance(x, list):
        return next((u for u in map(first_url, x) if u), "")
    return ""


def demo_image(url: str, data: list) -> Image.Image:
    """One picture from a Hugging Face demo's API. Raises if it's busy (queue full, GPU limit, timeout)."""
    import io
    import json
    import requests
    r = requests.post(url, json={"data": data}, timeout=60)
    r.raise_for_status()
    text = requests.get(f"{url}/{r.json()['event_id']}", timeout=240).text
    event = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:") and event == "complete" and first_url(json.loads(line[5:])):
            img = Image.open(io.BytesIO(requests.get(first_url(json.loads(line[5:])), timeout=60).content))
            img.load()
            return img.convert("RGB")
    raise RuntimeError(f"busy ({event or 'no answer'}: {text.strip()[-120:]})")


def flux_image(prompt: str, seed: int, w: int = 864, h: int = 1536) -> tuple[Image.Image, str]:
    """One picture (default 9:16) from the best free model that works: (picture, model name)."""
    for name, url in IMAGE_MODELS:
        if name in _MODELS_DOWN:
            continue
        data = {"Z-Image": [prompt, Z_SIZES.get((w, h), "864x1536 ( 9:16 )"), seed, 8, 3.0, False, []],
                "FLUX.2": [prompt, [], "Distilled (4 steps)", seed, False, w, h, 4, 1.0, False],
                "FLUX": [prompt, seed, False, w, h, 4]}[name]
        try:
            return demo_image(url, data), name
        except Exception as e:
            print(f"  ! {name}: {str(e)[:160]}")
            _MODELS_DOWN.add(name)
    raise RuntimeError("free image AI busy")


def flux_images(scenes: list[str], subject: str, tmp: str, tries: int = 2, style: str = FLUX_STYLE,
                size: tuple[int, int] = (864, 1536)) -> list[tuple[Image.Image, str, float]]:
    """Up to 2 pictures (one per scene), each painted `tries` times; the AI looks at them and keeps the best
    correct, respectful one per scene. Returns [(image, credit, focus_x)]; raises if the AI is busy or none fits."""
    import random
    import fun
    folder = os.path.join(tmp, "flux")
    os.makedirs(folder, exist_ok=True)
    made, fails = [], 0
    # Scenes take turns (1, 2, 1, 2): only ~3 pictures a run are allowed, so each scene gets a try.
    for k in range(1, tries + 1):
        for n, scene in enumerate(scenes[:2], 1):
            try:
                img, model = flux_image(style + scene.strip() + FLUX_AVOID, random.randint(1, 999999), *size)
            except Exception:
                fails += 1
                if not made and fails >= 2:
                    raise RuntimeError("free image AI busy")
                continue
            name = f"scene{n}_{k}.jpg"
            thumb = img.copy()
            thumb.thumbnail((576, 1024))
            thumb.save(os.path.join(folder, name), quality=85)
            made.append((name, img, model))
    if not made:
        raise RuntimeError("free image AI busy")
    schema = {"type": "object", "properties": {"picks": {"type": "array", "description": "At most one per scene, scene 1 first",
              "items": {"type": "object", "properties": {"file": {"type": "string"}, "focus": {"type": "number",
              "description": "Horizontal position (0 = left, 1 = right) of the main figure"}, "face_y": {"type": "number",
              "description": "Vertical position (0 = top, 1 = bottom) of the main face(s)"}},
              "required": ["file", "focus", "face_y"], "additionalProperties": False}},
              "reason": {"type": "string", "description": "One short line: what you rejected and why"}},
              "required": ["picks", "reason"], "additionalProperties": False}
    listing = "\n".join(f"Scene {n}: {sc.strip()}" for n, sc in enumerate(scenes[:2], 1))
    result = fun.claude_json(
        "You check AI-painted pictures for a respectful Hindu devotional Instagram Reel.",
        f"The pictures should show {subject}.\n{listing}\nFiles: {', '.join(n for n, _, _ in made)} (sceneN_... = scene N). "
        "Open each with the Read tool. For each scene pick the most beautiful picture that is fit to post, or none: "
        "the best is a detailed, sharp, emotional storybook illustration with a warm golden glow. "
        "Gods are drawn on purpose in a simple form with one or two arms visible, even those who traditionally have "
        "more: that is correct. Reject only real problems: the deity doesn't look like their well-known form (wrong "
        "skin colour, wrong animal) or looks like an ordinary person (no crown, halo or divine glow), more than two arms or extra hands, an item duplicated (two tridents, two flutes), the deity "
        "merged with an animal or person, disrespectful, scary or revealing, broken faces, eyes or hands, any text, "
        "letters, signature or watermark, a messy or ugly picture. Small differences from the description are fine "
        "(the person's clothes, a small item like a crescent moon not visible, a different pose or setting).",
        schema, CONFIG.get("membership_model", "sonnet"), folder=folder, purpose="flux pick")
    print(f"  picture check: {result['reason'][:200]}")
    picks = result["picks"]
    clamp = lambda v: min(1.0, max(0.0, float(v)))
    chosen = [(img, f"AI illustration ({model})", (clamp(p["focus"]), clamp(p["face_y"])))
              for p in picks for name, img, model in made if name == p["file"]][:2]
    if not chosen:
        raise RuntimeError("none of the AI pictures looked right")
    print(f"  AI pictures: {len(made)} painted ({', '.join(sorted({m for _, _, m in made}))}), kept "
          + ", ".join(p["file"] for p in picks[:2]))
    return chosen


def pick_gita_images(state: dict, tmp: str) -> list[tuple[Image.Image, str, float]]:
    return pick_paintings(GITA_SEARCHES, "Lord Krishna with Arjuna (chariot, Kurukshetra battlefield, Gita Upadesh)",
                          "gita_images_used", state, tmp)


def pick_paintings(queries: list[str], subject: str, used_key: str, state: dict, tmp: str) -> list[tuple[Image.Image, str, float]]:
    """The AI looks at free Wikimedia Commons paintings of the subject and picks the 2 most beautiful,
    on-topic ones (not used recently). Returns [(image, credit, focus_x)]."""
    import generate as g
    import fun
    used = state.get(used_key, [])
    found = commons_images(queries)
    cands = [c for c in found if c["title"] not in used] or found
    # paintings first (photos of statues/buildings rarely look good); only a few downloads (Wikimedia rate limit)
    cands.sort(key=lambda c: -sum(w in c["title"].lower() for w in PAINTING_WORDS))
    folder = os.path.join(tmp, "gita")
    os.makedirs(folder, exist_ok=True)
    import io
    import time
    import requests
    ua = {"User-Agent": "KhabarBawaalStudio/1.0 (github.com/vikashlal12345)"}   # Wikimedia blocks anonymous bursts
    loaded = []
    for c in cands[:9]:
        try:
            time.sleep(2.0)
            r = requests.get(c["url"], headers=ua, timeout=30)
            if r.status_code == 429:   # Wikimedia: too many requests, wait and try once more
                time.sleep(6)
                r = requests.get(c["url"], headers=ua, timeout=30)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content))
            img.load()
        except Exception as e:
            print(f"  ! Commons image skipped: {str(e)[:80]}")
            continue
        thumb = img.copy()
        thumb.thumbnail((512, 512))
        name = f"img{len(loaded) + 1}.jpg"
        thumb.convert("RGB").save(os.path.join(folder, name), quality=80)
        loaded.append((name, img, c))
    if not loaded:
        raise RuntimeError(f"no free images of {subject[:40]} found on Wikimedia Commons")
    schema = {"type": "object", "properties": {"best": {"type": "array", "description": "Best first (max 2)", "items": {
              "type": "object", "properties": {"file": {"type": "string"}, "focus": {"type": "number",
              "description": "Horizontal position (0 = left, 1 = right) of the main figure(s), to centre a tall crop"}},
              "required": ["file", "focus"], "additionalProperties": False}}}, "required": ["best"], "additionalProperties": False}
    best = fun.claude_json("You choose background images for a respectful devotional Instagram Reel.",
                           f"Candidate images: {', '.join(n for n, _, _ in loaded)}. Open each with the Read tool. "
                           f"Pick the 2 most beautiful, striking images that clearly show {subject}. "
                           "ONLY paintings or illustrations: reject all "
                           "photographs (statues, temples, buildings, people, events), book covers, manuscript pages with "
                           "writing, text-heavy, blurry, side-by-side duplicated (stereo) or collage images. Colourful "
                           "paintings with large figures are best. Empty list if none fits.",
                           schema, CONFIG.get("membership_model", "sonnet"), folder=folder, purpose="painting pick")["best"]
    chosen = [(img, c, min(1.0, max(0.0, float(b["focus"])))) for b in best for name, img, c in loaded
              if name == b["file"]][:2]
    if not chosen:
        raise RuntimeError(f"no suitable image of {subject[:40]} among the free ones")
    state[used_key] = (used + [c["title"] for _, c, _ in chosen])[-30:]
    print("  paintings: " + " | ".join(f"{c['title'][:45]} (focus {f:.2f})" for _, c, f in chosen))
    return [(img.convert("RGB"), c["credit"], f) for img, c, f in chosen]


GOLD, CREAM = "#F5C26B", "#FFF4DD"


def vfont(name: str, size: int, weight: str = "") -> ImageFont.FreeTypeFont:
    """Variable Google fonts (Playfair Display, Cinzel) at a named weight."""
    from generate import FONTS
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight:
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass
    return f


def glow_line(text: str, fnt, fill: str, glow: int = 16) -> Image.Image:
    """One centred text line with a soft dark glow behind it (no hard outline)."""
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    l, t, r, b = probe.textbbox((0, 0), text, font=fnt)
    pad = glow * 3
    img = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((pad - l, pad - t), text, font=fnt, fill=(0, 0, 0, 235))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(glow)))
    ImageDraw.Draw(img).text((pad - l, pad - t), text, font=fnt, fill=fill)
    return img


def wrap_centre(text: str, fnt, max_w: int) -> list[str]:
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines, cur = [], ""
    for word in printable(text).split():
        trial = (cur + " " + word).strip()
        if cur and probe.textlength(trial, font=fnt) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    if len(lines) > 1 and len(lines[-1].split()) == 1:   # no lone last word
        prev = lines[-2].split()
        lines[-2], lines[-1] = " ".join(prev[:-1]), prev[-1] + " " + lines[-1]
    return lines


def read_time(text: str) -> float:
    """Seconds to read a line comfortably twice-ish (Hinglish ~3 words/s) + a calm hold."""
    return max(2.4, len(text.split()) / 3.2 + 1.2)


def faded(img: Image.Image, a: float) -> Image.Image:
    if a >= 1:
        return img
    out = img.copy()
    out.putalpha(out.getchannel("A").point(lambda v: int(v * max(0.0, a))))
    return out


def step_parts(steps: list[dict], switch_step: int, lines_of, big, body, label_f, target: float = 45.0):
    """🙏 Gita series layout: one step at a time ({label, text, source}), centred in the dark lower part, each
    held long enough to read (0.6 s + 0.25 s a word after its lines appear), then stretched or squeezed so the
    Reel lasts about 45 s (owner, 11 Oct 2026). The last step stays while the 'send this' line comes in.
    Returns (parts, divider, switch time, cta time, total) for gita_reel."""
    gap, fade = 0.3, 0.5
    blocks = []
    for k, st in enumerate(steps):
        fnt = big if k == 0 else body
        blocks.append((lines_of(st["text"], fnt, CREAM), len(st["text"].split()), int(fnt.size * 1.32)))
    fixed = 0.5 + sum(0.3 + len(ln) * gap for ln, _, _ in blocks) + fade * (len(steps) - 1) + 2.4
    holds = [0.6 + 0.25 * w for _, w, _ in blocks]
    k = max(0.75, min(1.5, (target - fixed) / sum(holds)))
    parts, t, switch = [], 0.5, 0.0
    for n, (st, (lines, _, lh), hold) in enumerate(zip(steps, blocks, holds)):
        if n == switch_step:
            switch = t - 0.2                                  # 2nd picture cross-fades in here
        # Rows as (kind, image, height of its slot); images are placed by their centre in their slot.
        rows = ([("label", glow_line(st["label"], label_f, GOLD), 64),
                 ("rule", Image.new("RGBA", (180, 2), GOLD), 44)] if st.get("label") else []) + \
               [("line", im, lh) for im in lines] + \
               ([("source", glow_line(st["source"], vfont("Cinzel-Variable.ttf", 34, "SemiBold"), GOLD), 70)]
                if st.get("source") else [])
        y = 1230 - sum(r[2] for r in rows) // 2
        items, li = [], 0
        for kind, im, slot in rows:
            at = t   # the whole section at once
            li += kind == "line"
            items.append((y + slot // 2, im, at))
            y += slot
        end = t + 0.3 + len(lines) * gap + hold * k
        parts.append((items, end))
        t = end + fade
    cta_at = parts[-1][1] - 0.6
    total = cta_at + 2.6
    parts[-1] = (parts[-1][0], total)                         # the question stays until the end
    return parts, (0, -9.0, -9.0), switch or 99.0, cta_at, total


def gita_reel(images: list[tuple[Image.Image, str, float]], verse: str, hook: str, lesson: str,
              real_life: str, highlight: list[str], out_mp4: str, labels: dict | None = None,
              steps: list[dict] | None = None, switch_step: int = 3, handle_text: str = "") -> list[Image.Image]:
    """🙏 Devotional Reel: the painting full screen (warm golden tone, soft dark edges, slow zoom, floating
    golden dust), elegant centred serif text that fades in line by line and stays long enough to read:
    hook → Shri Krishna ka sandesh + lesson + verse → (2nd painting) Aaj ki seekh → send this. (No Sanskrit.)
    images = [(painting, credit, focus_x 0-1 where Krishna/Arjuna are)]. Returns frames to proofread.
    labels: other wording for the 🙏 6 AM devotional Reel: head, middle, source (line under the middle text,
    '' for none), seekh, cta."""
    lab = {"head": "GITA  GYAAN", "middle": "SHRI KRISHNA KA SANDESH", "source": f"—  Bhagavad Gita {verse}",
           "seekh": "AAJ KI SEEKH", "cta": "Send this to someone who needs it", **(labels or {})}
    import random

    def graded(img: Image.Image, focus) -> Image.Image:
        """focus = x (0-1) of the figures, or (x, y) for AI pictures: then the faces are placed just below the
        heading (screen y ~620), whatever the picture's layout; uncovered space blends into dark."""
        focus, face_y = focus if isinstance(focus, (tuple, list)) else (focus, None)
        zmax = 1.12
        cw, ch = int(RW * zmax), int(RH * zmax)
        k = max(RW / img.width, RH / img.height) * zmax
        big = img.resize((int(img.width * k) + 2, int(img.height * k) + 2), Image.LANCZOS)
        x0 = int(min(max(0, big.width * focus - cw / 2), big.width - cw))
        big = big.crop((x0, (big.height - ch) // 2, x0 + cw, (big.height - ch) // 2 + ch))
        if img.width > img.height * 1.15:
            # Wide painting: a full-screen crop would cut the figures off. Show more of it in the upper part
            # of the screen, on a blurred, darkened copy of itself.
            bh = int(1080 * zmax)
            fg = img.resize((int(img.width * bh / img.height), bh), Image.LANCZOS)
            fx = int(min(max(0, fg.width * focus - cw / 2), max(0, fg.width - cw)))
            fg = fg.crop((fx, 0, fx + min(cw, fg.width), bh))
            back = Image.blend(big.resize((cw // 12, ch // 12)).filter(ImageFilter.GaussianBlur(2)).resize((cw, ch)),
                               Image.new("RGB", (cw, ch), "#000000"), 0.45)
            back.paste(fg, ((cw - fg.width) // 2, int(70 * zmax)))
            big = back
        elif face_y is not None:
            shift = int(min(0.25 * ch, max(-0.35 * ch, 620 * zmax - face_y * ch)))
            moved = Image.new("RGB", big.size, "#05070c")
            moved.paste(big, (0, shift))
            big = moved
        return Image.blend(big, Image.new("RGB", big.size, "#3a2000"), 0.18)    # warm golden grade

    bases = [graded(img, focus) for img, _, focus in images]
    credits = [c for _, c, _ in images]
    # Overlay: soft dark edges + dark lower part where the text sits.
    shade = Image.new("L", (RW, RH), 0)
    ImageDraw.Draw(shade).ellipse((-RW * 0.35, -RH * 0.15, RW * 1.35, RH * 1.05), fill=255)
    shade = shade.filter(ImageFilter.GaussianBlur(160)).point(lambda v: 255 - v)
    grad = Image.new("L", (1, RH))
    dark_from, dark_span = (0.42, 0.18) if steps else (0.33, 0.27)   # Gita series: more of the picture shows
    for y in range(RH):
        lower = 248 * min(1.0, max(0.0, (y - RH * dark_from) / (RH * dark_span)))   # text area: nearly black
        top = 175 * max(0.0, 1 - y / 430)                                 # behind the logo + "GITA GYAAN"
        grad.putpixel((0, y), int(max(lower, top)))
    alpha = Image.composite(Image.new("L", (RW, RH), 255), shade, grad.resize((RW, RH)))
    overlay = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    overlay.putalpha(alpha)

    head_f = vfont("Cinzel-Variable.ttf", 58, "Bold")
    while head_f.size > 34 and ImageDraw.Draw(Image.new("RGBA", (1, 1))).textlength(lab["head"], font=head_f) > RW - 560:
        head_f = vfont("Cinzel-Variable.ttf", head_f.size - 4, "Bold")
    head = glow_line(lab["head"], head_f, GOLD)
    logo = logo_img(400, 96)   # KhabarBawaal logo, centred above the heading
    body = vfont("PlayfairDisplay-Variable.ttf", 70, "Bold")
    lesson_f = vfont("PlayfairDisplay-Variable.ttf", 56, "SemiBold")
    small_gold = vfont("Cinzel-Variable.ttf", 40, "SemiBold")
    hl = {norm(w) for phrase in highlight for w in phrase.split()}

    def lines_of(text, fnt, color):
        out = []
        for ln in wrap_centre(text, fnt, RW - 200):
            gold = any(norm(w) in hl for w in ln.split())
            out.append(glow_line(ln, fnt, GOLD if gold else color))
        return out

    # Parts: (start, list of (y, image, appear_at)), plus fade-out time.
    gap, pause = 0.45, 0.35
    parts, t = [], 0.6
    cta_y, handle_y = 1380, 1470
    if steps:
        parts, divider, switch, cta_at, total = step_parts(steps, switch_step, lines_of, body, lesson_f, small_gold)
        cta_y, handle_y = 1580, 1660
    else:
        hook_l = lines_of(hook, body, CREAM)
        y0 = 1330 - len(hook_l) * 50
        items = [(y0 + i * int(body.size * 1.3), im, -1.0) for i, im in enumerate(hook_l)]   # 🪝 visible at 0 s
        end = t + len(hook_l) * gap + read_time(hook)
        parts.append((items, end))
        t = end + 0.5 + pause
        items, y = [], 1060
        items.append((y, glow_line(lab["middle"], small_gold, GOLD), t)); y += 85
        t2 = t + 0.8
        y += 10
        divider_y = y
        y += 30 + lesson_f.size // 2 + 20     # lesson lines are placed by their centre: start below the divider
        les = lines_of(lesson, lesson_f, CREAM)
        for i, im in enumerate(les):
            items.append((y, im, t)); y += int(lesson_f.size * 1.32)   # the whole section at once
        if lab["source"]:
            items.append((y + 20, glow_line(lab["source"], vfont("Cinzel-Variable.ttf", 36, "SemiBold"), GOLD),
                          t))
        end = t2 + len(les) * gap + read_time(lesson)
        parts.append((items, end))
        divider = (divider_y, t2 - 0.2, end)
        t = end + 0.5 + pause
        switch = t - 0.2                                     # 2nd painting cross-fades in here
        items, y = [], 1200
        items.append((y, glow_line(lab["seekh"], vfont("Cinzel-Variable.ttf", 46, "Bold"), GOLD), t)); y += 110
        take_f = vfont("PlayfairDisplay-Variable.ttf", 64, "Bold")
        for i, im in enumerate(lines_of(real_life, take_f, CREAM)):
            items.append((y, im, t)); y += int(take_f.size * 1.3)
        end = t + 0.8 + len(items) * gap + read_time(real_life)
        parts.append((items, end))
        cta_at = end + 0.3
        total = cta_at + 3.0
    cta = glow_line(lab["cta"], vfont("PlayfairDisplay-Variable.ttf", 48, "SemiBold"), GOLD)
    handle = glow_line(handle_text or CONFIG["handle"], vfont("PlayfairDisplay-Variable.ttf", 36, "SemiBold"), "#E8D9B8")

    random.seed(7)
    dust = [(random.uniform(0, RW), random.uniform(300, 1700), random.uniform(-14, -40), random.choice((2, 3, 4)),
             random.randint(70, 200)) for _ in range(46)]
    enc, checks, frame = Encoder(out_mp4), [], None
    check_at = [min(end, total) - 0.2 for _, end in parts] + [total - 0.1]
    for i in range(int(total * FPS)):
        t = i / FPS
        n = 1 if len(bases) > 1 and t >= switch else 0
        z = 1.0 + 0.10 * t / total                       # slow zoom in
        b = bases[n]
        cw, ch = int(RW * 1.12 / z), int(RH * 1.12 / z)
        x0, y0 = (b.width - cw) // 2, (b.height - ch) // 2
        bg = b.crop((x0, y0, x0 + cw, y0 + ch)).resize((RW, RH), Image.BILINEAR)
        if n == 1 and t < switch + 1.0:                  # slow cross-fade
            p = bases[0]
            prev = p.crop((x0, y0, x0 + cw, y0 + ch)).resize((RW, RH), Image.BILINEAR)
            bg = Image.blend(prev, bg, (t - switch) / 1.0)
        frame = bg.convert("RGBA")
        frame.alpha_composite(overlay)
        d = ImageDraw.Draw(frame)
        for x, y, v, r, a in dust:                       # golden dust drifting up
            yy = (y + v * t) % 1500 + 250
            d.ellipse((x - r, yy - r, x + r, yy + r), fill=(245, 194, 107, a))
        if logo:
            frame.alpha_composite(logo, ((RW - logo.width) // 2, 120))
        frame.alpha_composite(head, ((RW - head.width) // 2, 285 - head.height // 2))
        hy, hx = 285, (RW - head.width) // 2 + 6            # side lines just outside the heading text
        d.line((max(60, hx - 150), hy, hx - 4, hy), fill=GOLD, width=2)
        d.line((RW - hx + 4, hy, min(RW - 60, RW - hx + 150), hy), fill=GOLD, width=2)
        start = 0.0
        for items, end in parts:
            if start - 0.1 <= t <= end + 0.5:
                out_a = 1.0 if t <= end else 1 - (t - end) / 0.5
                for y, im, at in items:
                    a = min(1.0, max(0.0, (t - at) / 0.3)) * out_a
                    if a > 0:
                        frame.alpha_composite(faded(im, a), ((RW - im.width) // 2, int(y - im.height / 2)))
            start = end + 0.5
        dy, d0, d1 = divider
        if d0 <= t <= d1 + 0.5:
            a = min(1.0, (t - d0) / 0.6) * (1.0 if t <= d1 else 1 - (t - d1) / 0.5)
            d.line((RW // 2 - 90, dy, RW // 2 + 90, dy), fill=(245, 194, 107, int(255 * a)), width=2)
        if t >= cta_at:
            a = min(1.0, (t - cta_at) / 0.7)
            frame.alpha_composite(faded(cta, a), ((RW - cta.width) // 2, cta_y - cta.height // 2))
            frame.alpha_composite(faded(handle, a), ((RW - handle.width) // 2, handle_y - handle.height // 2))
        ImageDraw.Draw(frame).text((RW - 40, RH - 40), f"Image: {credits[n]}"[:80], font=font("Poppins-SemiBold.ttf", 20),
                                   fill="#9a8f7a", anchor="rd")
        if len(checks) < len(check_at) and t >= check_at[len(checks)]:
            checks.append(frame.convert("RGB"))
        enc.add(frame)
    add_follow(enc, frame)
    enc.close()
    while len(checks) < 4:
        checks.append(frame.convert("RGB"))
    return checks


# 🙏 "Gita Gyaan: roz ek shlok" (owner, 11 Oct 2026): one shlok a day in order from 1.1, ~30 s, explained step by step
# like the owner's own Hindi series (gita-shorts): hook → kya ho raha hai → who says what → matlab → aaj ki seekh →
# aaj ka sawaal. assets/gita_ref.json = that series' meaning/bhavarth for 1.1-2.30 (numbering as there: 1.21-22
# together, chapter 1 has 46); from 2.31 the free open library vedicscriptures.github.io (same numbering for chapters
# 2-18): Gita Press / Swami Tejomayananda / Swami Sivananda. Library down → the AI's own knowledge + proofreader.
GITA_COUNTS = {2: 72, 3: 43, 4: 42, 5: 29, 6: 47, 7: 30, 8: 28, 9: 34, 10: 42, 11: 55, 12: 20, 13: 35, 14: 27, 15: 20,
               16: 24, 17: 28, 18: 78}


def gita_ref() -> dict:
    return json.loads((Path(__file__).parent / "assets" / "gita_ref.json").read_text())


def gita_library(verse: str) -> str:
    """The shlok from the free open Bhagavad Gita library (no key): Sanskrit (shows who speaks), Hindi meanings
    (Swami Tejomayananda; Swami Ramsukhdas, Gita Press, + the start of his explanation), English (Swami Sivananda).
    '' if it can't be read."""
    import re
    import requests
    ch, v = verse.split("-")[0].split(".")
    try:
        d = requests.get(f"https://vedicscriptures.github.io/slok/{ch}/{v}/", timeout=20).json()
    except Exception as e:
        print(f"  ! Gita library: {e}")
        return ""
    clean = lambda t: re.sub(r"\s+", " ", re.sub(r"।।\s*[\d.]+\s*।।", "", t or "")).strip()
    parts = [("Sanskrit", d.get("slok")),
             ("Hindi meaning (Swami Tejomayananda)", (d.get("tej") or {}).get("ht")),
             ("Hindi meaning (Swami Ramsukhdas, Gita Press)", (d.get("rams") or {}).get("ht")),
             ("Explanation (Swami Ramsukhdas), start", ((d.get("rams") or {}).get("hc") or "")[:700]),
             ("English (Swami Sivananda)", (d.get("siva") or {}).get("et"))]
    return "\n".join(f"{k}: {clean(t)}" for k, t in parts if clean(t))


def gita_sequence() -> list[str]:
    """All shloks in order: the reference series' own list, then the rest of chapter 2 and chapters 3-18."""
    seq = list(gita_ref())
    last_ch, last_v = (int(x) for x in seq[-1].split("-")[0].split("."))
    for ch in range(last_ch, 19):
        seq += [f"{ch}.{v}" for v in range(last_v + 1 if ch == last_ch else 1, GITA_COUNTS[ch] + 1)]
    return seq


GITA_OPTION = {"type": "object", "properties": {
    "based_on": {"type": "string", "description": "One line: what happens in this shlok and today's lesson"},
    "hook": {"type": "string", "description": "Curiosity hook, max 8 words, with a natural pause '...', e.g. "
             "'Ek andha raja... aur ek sawaal'"},
    "context": {"type": "string", "description": "'KYA HO RAHA HAI': max 18 words: where we are, who speaks to whom, "
                "what just happened, e.g. 'Kurukshetra mein yudh shuru hone wala hai. Mahal mein Dhritarashtra poochte "
                "hain:'"},
    "speaker": {"type": "string", "description": "Who says this shlok, max 3 words, e.g. 'Dhritarashtra ne poocha', "
                "'Sanjay ne bataya', 'Duryodhan ne kaha', 'Arjun ne kaha', 'Shri Krishna ne kaha'"},
    "verse_text": {"type": "string", "description": "What the shlok says, in simple Hinglish, true to its meaning "
                   "(not word-for-word), max 24 words"},
    "matlab": {"type": "string", "description": "'MATLAB': what it really means, like a bhavarth: 2-3 short "
               "sentences, max 20 words"},
    "seekh": {"type": "string", "description": "'AAJ KI SEEKH' for life today, one line people want to save, max 12 words"},
    "sawaal": {"type": "string", "description": "'AAJ KA SAWAAL': a question to the viewer, max 10 words, e.g. "
               "'Aapke jeevan mein mere-paraye kaun hain?'"},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-3 words copied exactly from the texts"},
    "caption": {"type": "string", "description": "2-4 short Hinglish lines about this shlok, 1-2 emojis (🙏 fits), "
                "last line asks people to comment or send it"},
    "pin_comment": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5, include #bhagavadgita and #reelsindia"},
    "songs": SONGS,
    "scene_gita": {"type": "string", "description": "Picture 1 (behind the hook, context, shlok and matlab): the exact "
                   "moment of this shlok in the Mahabharata, who speaks to whom and where (e.g. blind king Dhritarashtra "
                   "on his throne in a dark palace, asking Sanjaya). " + SCENE_HELP},
    "scene_life": {"type": "string", "description": "Picture 2 (behind aaj ki seekh and aaj ka sawaal): a young Indian of "
                   "today living this lesson, Krishna gently beside them. " + SCENE_HELP}},
    "required": ["based_on", "hook", "context", "speaker", "verse_text", "matlab", "seekh", "sawaal", "highlight",
                 "caption", "pin_comment", "hashtags", "songs", "scene_gita", "scene_life"], "additionalProperties": False}

GITA_SYSTEM = """You write today's Reel in the daily "Gita Gyaan: roz ek shlok" series for {page}, an Indian Instagram page for 18-34s (Hinglish in English letters only, never Devanagari). The series goes through the Bhagavad Gita in order, one shlok a day, so someone who missed the earlier days must still understand it: say where we are, who speaks to whom and what is happening. Simple, warm, spoken Hinglish, like explaining to a friend; short lines, natural pauses ("..."). The shlok text must keep the shlok's true meaning (not a word-for-word translation). The whole Reel is read in about 45 seconds, so keep every part within its word limit. Reverent: never joke about Krishna or the Gita, no politics, nothing against any religion or community."""

GITA_JUDGE = ("You are a 22-year-old Indian who loves Gita Reels on Instagram. Rate each Reel 1-10 for: "
              "understandable (the lesson is clear from the Reel alone), funny (here: how beautiful and moving the "
              "lines are, would you save it or send it to someone), fresh (not a tired motivational line). Be strict.")


def build_gita_reel(state: dict, tmp: str) -> tuple[Image.Image, str, dict]:
    """🙏 Today's shlok of the "roz ek shlok" series for the 📦 Ready tab. Returns (cover, mp4 path, feed fields)."""
    import fun
    import generate as g
    import proofread
    import status
    seq = gita_sequence()
    i = state.get("gita_next", 0) % len(seq)
    verse, nxt = seq[i], seq[(i + 1) % len(seq)]
    ref = gita_ref().get(verse)
    ref_text = "\n".join(f"{k}: {v}" for k, v in ref.items() if v) if ref else gita_library(verse)
    source = ("the owner's own Hindi series" if ref else
              "the open Bhagavad Gita library (Gita Press / Swami Tejomayananda / Swami Sivananda)")
    status.step(f"Writing shlok {verse}", 2)
    songs = trending_songs(state)
    options = fun.claude_json(GITA_SYSTEM.format(page=CONFIG["page_name"]),
                              f"Today's shlok: Bhagavad Gita {verse}. Tomorrow: {nxt}.\n"
                              + (f"Reference from {source} (follow its meaning and context; write it in simple "
                                 f"Hinglish, English letters only):\n{ref_text}\n" if ref_text else
                                 "Use this shlok's well-known meaning; who speaks it and the situation must be right.\n")
                              + "\nWrite exactly 3 different Reels for this same shlok." + songs_prompt(songs),
                              options_schema(GITA_OPTION, "Exactly 3 different Reels"),
                              CONFIG.get("membership_model", "sonnet"), purpose="gita writing")["options"][:3]
    for o in options:
        o["caption"] = unescape(o["caption"])
    texts = ("hook", "context", "speaker", "verse_text", "matlab", "seekh", "sawaal")
    best = fun.judge_best(options, lambda o: " | ".join(o[k] for k in texts),
                          CONFIG.get("membership_model", "sonnet"), judge_system=GITA_JUDGE)
    roman_only(best, ["caption", "pin_comment", *texts])
    for k in texts:
        best[k] = plain_letters(best[k])
    picked_songs = checked_songs(best.pop("songs", []), songs)
    scenes = [best.pop("scene_gita"), best.pop("scene_life")]   # kept out of the proofreader's text
    status.step("Painting the scenes with free AI", 3)
    try:
        images = flux_images(scenes, f"the Bhagavad Gita {verse} scene (Mahabharata characters; Krishna where shown)", tmp)
    except Exception as e:
        print(f"  {e}: using free paintings instead")
        status.step("AI pictures not usable: finding Krishna-Arjuna paintings (free licence)", 3)
        images = pick_gita_images(state, tmp)
    out = os.path.join(tmp, "reel.mp4")
    status.step("Making the video + proofreading", 5)
    labels = {"head": f"GITA GYAAN  {verse}", "cta": "Send this to someone who needs it"}

    def steps_of(p):
        return [{"text": p["hook"]},
                {"label": "KYA HO RAHA HAI", "text": p["context"]},
                {"label": screen_label(p["speaker"]).upper(), "text": p["verse_text"],
                 "source": f"—  Bhagavad Gita {verse}"},
                {"label": "MATLAB", "text": p["matlab"]},
                {"label": "AAJ KI SEEKH", "text": p["seekh"]},
                {"label": "AAJ KA SAWAAL", "text": p["sawaal"]}]

    def caption_of(d):
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in d["post"]["hashtags"])
        return (f"{d['post']['caption'].strip()}\n\n📖 Roz ek shlok: Bhagavad Gita {verse} · Kal: shlok {nxt}\n"
                f"Follow {CONFIG['handle']} for daily Gita Gyaan 🙏\n\n{tags}")
    frames, data, proof = proofread.run(
        lambda d: gita_reel(images, verse, "", "", "", d["post"]["highlight"], out, labels=labels,
                            steps=steps_of(d["post"]), switch_step=4,   # today's picture from AAJ KI SEEKH
                            handle_text=f"{CONFIG['handle']}  ·  Kal: shlok {nxt}"),
        {"post": best}, caption_of,
        f"Bhagavad Gita {verse}. Check that the speaker and the situation are right and the shlok text keeps the "
        "shlok's meaning; simple Hinglish wording (not word-for-word) is correct here, don't replace it with a literal "
        "translation." + (f"\nReference ({source}):\n{ref_text}" if ref_text else ""))
    post = data["post"]
    state["gita_next"] = i + 1
    state["recent_gita"] = (state.get("recent_gita", []) + [f"Gita {verse}: {post['based_on']}"])[-60:]
    return frames[2], out, {
        "headline": printable(f"Gita {verse}: {post['hook']}")[:140], "tag": "🙏 GITA GYAAN",
        "caption": g.limit_hashtags(caption_of(data)), "source": "Bhagavad Gita", "source_url": "",
        "pin_comment": post.get("pin_comment", ""), "songs": picked_songs, "proof": proof}

# ---------------------------------------------------------------- 6. 🙏 6 AM devotional Reel (festival / god of the day)

FESTIVAL_ICS = "https://calendar.google.com/calendar/ical/en.indian%23holiday%40group.v.calendar.google.com/public/basic.ics"
NAVRATRI_GODDESSES = ["Maa Shailputri", "Maa Brahmacharini", "Maa Chandraghanta", "Maa Kushmanda", "Maa Skandamata",
                      "Maa Katyayani", "Maa Kalaratri", "Maa Mahagauri", "Maa Siddhidatri"]
# God of the day when there's no festival (common tradition), Monday first.
WEEKDAY_GODS = ["Shiv ji", "Hanuman ji", "Ganesh ji", "Vishnu ji", "Maa Lakshmi", "Shani Dev", "Surya Dev"]


_FESTIVALS: dict = {}


def festivals_on(day) -> list[str]:
    """Indian festivals on a date (Google's free public 'Holidays in India' calendar, downloaded once per run)."""
    import re
    import requests
    if not _FESTIVALS:
        try:
            ics = requests.get(FESTIVAL_ICS, timeout=25).text
        except Exception as e:
            print(f"  ! festival calendar failed: {e}")
            return []
        for d, name in re.findall(r"DTSTART;VALUE=DATE:(\d{8}).*?SUMMARY:([^\r\n]+)", ics, re.S):
            _FESTIVALS.setdefault(d, []).append(name.strip())
    return _FESTIVALS.get(day.strftime("%Y%m%d"), [])


def navratri_day(day) -> int:
    """1-9 during Sharad/Chaitra Navratri, else 0. Counted from the first day in the calendar, but anchored on
    Maha Saptami (7), Ashtami (8) and Navami (9) when listed: a doubled tithi can't push a day past 6 before Saptami."""
    from datetime import timedelta
    today = [f.lower() for f in festivals_on(day)]
    for word, n in (("navami", 9), ("ashtami", 8), ("saptami", 7)):
        if any(word in f for f in today):
            return n
    for back in range(10):
        if any("first day of" in f.lower() and "navratri" in f.lower() for f in festivals_on(day - timedelta(days=back))):
            count = back + 1
            ahead = [f.lower() for k in range(1, 4) for f in festivals_on(day + timedelta(days=k))]
            if any("saptami" in f for f in ahead) or count < 7:
                return min(count, 6)
            return count if count <= 9 else 0
    return 0


def devotion_subject(day) -> dict:
    """What today's 6 AM Reel is about: Navratri goddess, another festival, or the god of the day."""
    from datetime import timedelta
    n = navratri_day(day)
    if n:
        goddess = NAVRATRI_GODDESSES[n - 1]
        tomorrow = [f.lower() for f in festivals_on(day + timedelta(days=1))]
        if n == 8 and any("dussehra" in f or "dasara" in f for f in tomorrow):   # Navami falls on Ashtami this year
            goddess, n = "Maa Mahagauri aur Maa Siddhidatri", "8-9"
        return {"kind": "navratri", "name": f"Navratri Din {n}: {goddess}", "deity": goddess, "festivals": festivals_on(day)}
    fests = [f for f in festivals_on(day) if not any(w in f.lower() for w in ("gandhi", "republic", "independence",
                                                                             "christmas", "good friday", "eid", "muharram"))]
    if fests:
        return {"kind": "festival", "name": fests[0], "deity": "", "festivals": fests}
    return {"kind": "weekday", "name": f"{day:%A}: {WEEKDAY_GODS[day.weekday()]}", "deity": WEEKDAY_GODS[day.weekday()],
            "festivals": []}


def devotional_songs(queries: list[str]) -> list[dict]:
    """Most-played devotional songs on JioSaavn for these searches (free search, real songs only)."""
    import html
    import requests
    found = {}
    for q in queries:
        try:
            r = requests.get("https://www.jiosaavn.com/api.php", headers=UA, timeout=20, params={
                "__call": "search.getResults", "q": q, "_format": "json", "_marker": "0", "api_version": "4",
                "ctx": "web6dot0", "n": "10", "p": "1"})
            results = r.json().get("results", [])
        except Exception as e:
            print(f"  ! song search '{q}' failed: {e}")
            continue
        for x in results:
            title = html.unescape(x.get("title", "")).strip()
            artists = ", ".join(a["name"] for a in x.get("more_info", {}).get("artistMap", {}).get("primary_artists", [])[:2])
            plays = int(x.get("play_count") or 0)
            if title and title.lower() not in found:
                found[title.lower()] = {"title": title, "artist": artists or html.unescape(x.get("subtitle", "")).split(" - ")[0],
                                        "lang": "hindi", "mood": "devotional", "plays": plays}
    return sorted(found.values(), key=lambda s: -s["plays"])


DEVOTION_PLAN = {"type": "object", "properties": {
    "deity": {"type": "string", "description": "The god/goddess this Reel is about (Hinglish name, e.g. 'Maa Shailputri')"},
    "image_searches": {"type": "array", "items": {"type": "string"}, "description":
                       "3 English Wikimedia Commons searches for classical paintings of this deity, e.g. "
                       "'Shailaputri painting', 'Durga Raja Ravi Varma', 'Goddess Durga miniature painting'"},
    "image_subject": {"type": "string", "description": "What the painting must clearly show, in English"},
    "family": {"type": "string", "description": "English name of the main deity for a wider painting search if this exact "
               "form has none, e.g. 'Durga' for any Navratri goddess, 'Shiva', 'Hanuman', 'Lakshmi', 'Rama'"},
    "song_searches": {"type": "array", "items": {"type": "string"}, "description":
                      "2-3 JioSaavn searches for popular devotional songs, e.g. 'Navratri bhajan', 'Durga aarti'"},
    "head": {"type": "string", "description": "Top heading, max 16 letters, e.g. 'SHUBH NAVRATRI', 'JAI HANUMAN', 'HAR HAR MAHADEV'"},
    "middle": {"type": "string", "description": "Label over the deity's meaning, e.g. 'MAA SHAILPUTRI', 'BAJRANGBALI KA BAL'"},
    "seekh": {"type": "string", "description": "Label over the blessing, e.g. 'AAJ KA ASHIRWAAD', 'AAJ KI PRARTHANA'"},
    "cta": {"type": "string", "description": "Ending line, e.g. 'Jai Mata Di 🙏 Apni family ko bhejo' (max 7 words, no emoji)"},
    "scene_darshan": {"type": "string", "description": "Picture 1 (behind the greeting and who the deity is): the deity "
                      "alone, clearly divine, with her/his vahana or one main item (e.g. Maa Shailputri: a white bull "
                      "beside her, lotus in her one visible hand), in a divine setting at sunrise. " + SCENE_HELP},
    "scene_life": {"type": "string", "description": "Picture 2 (behind the blessing): the same deity, same look, gently "
                   "blessing or walking beside a young Indian of today in a relatable moment (exam morning, first job, "
                   "going home, family puja). " + SCENE_HELP}},
    "required": ["deity", "image_searches", "image_subject", "family", "song_searches", "head", "middle", "seekh", "cta",
                 "scene_darshan", "scene_life"],
    "additionalProperties": False}

DEVOTION_OPTION = {"type": "object", "properties": {
    "based_on": {"type": "string"},
    "hook": {"type": "string", "description": "Morning greeting with feeling, max 11 words, natural pause '...', e.g. "
             "'Navratri ka pehla din... Maa Shailputri ka aashirwaad aapke ghar aaye'"},
    "lesson": {"type": "string", "description": "Who the deity is / what today means, simple warm Hinglish, max 22 words, "
               "only well-known traditional facts"},
    "real_life": {"type": "string", "description": "A blessing or life lesson for today, max 18 words, people want to save it"},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-3 words copied exactly from the texts"},
    "caption": {"type": "string"},
    "pin_comment": {"type": "string", "description": "Max 15 words: a share line to family, e.g. 'Apni maa ko bhejo 🙏'"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5 (e.g. #navratri #jaimatadi), include #reelsindia"}},
    "required": ["based_on", "hook", "lesson", "real_life", "highlight", "caption", "pin_comment", "hashtags"],
    "additionalProperties": False}

DEVOTION_SYSTEM = """You write the 6 AM devotional Reel for {page}, an Indian Hinglish page for 18-34s (English \
letters only, never Devanagari). Warm, reverent, uplifting, like a blessing from family: festival or god of the day, \
who the deity is in simple words, and a blessing or life lesson for today. Only well-known traditional facts; never \
joke about any god, no politics, nothing against any religion or community. Write it like poetry: short lines, \
natural pauses ("..."). Caption: 2-4 short Hinglish lines, the first naming the god/festival (searchable), 1-2 emojis \
(🙏 fits), last line asks people to comment 'Jai ...' (e.g. 'Jai Mata Di likho 🙏'). Pinned comment: a share line to \
family, e.g. 'Apni maa ko bhejo, unka din ban jayega 🙏'."""


def plain_letters(text: str) -> str:
    """Drop accent marks the fonts can't draw well (e.g. 'beṭi' -> 'beti')."""
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def screen_label(text: str) -> str:
    """On-screen label: emojis (the fonts can't draw them) become a neat ' · ' separator."""
    import re
    text = re.sub(r"\s*[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]+\s*", " · ", plain_letters(text))
    return re.sub(r"(\s*·\s*)+$", "", re.sub(r"^(\s*·\s*)+", "", text)).strip()


def make_devotion_reel(state: dict, feed: list, today: str, tmp: str) -> None:
    """🙏 6 AM: festival of the day (Navratri goddess of the day) or the god of the day, in the Gita-Reel style,
    with popular devotional songs for that god."""
    from datetime import date
    import fun
    import generate as g
    import proofread
    import status
    day = date.fromisoformat(today)
    status.step("Checking today's festival", 2)
    subj = devotion_subject(day)
    print(f"  devotion: {subj['name']} | festivals: {subj['festivals']}")
    plan = fun.claude_json(DEVOTION_SYSTEM.format(page=CONFIG["page_name"]),
                           f"Today: {day:%A, %d %B %Y}. Reel subject: {subj['name']}. Festivals today: "
                           f"{', '.join(subj['festivals']) or 'none'}. Plan the Reel.", DEVOTION_PLAN,
                           CONFIG.get("membership_model", "sonnet"), purpose="devotion plan")
    status.step(f"Painting {plan['deity']} with free AI", 3)
    exact = True
    try:
        images = flux_images([plan["scene_darshan"], plan["scene_life"]], plan["image_subject"], tmp)
    except Exception as e:
        print(f"  {e}: using free paintings instead")
        status.step(f"AI pictures not usable: finding paintings of {plan['deity']} (free licence)", 3)
        images = None
    try:
        images = images or pick_paintings(plan["image_searches"], plan["image_subject"], "devotion_images_used", state, tmp)
    except RuntimeError as e:   # few paintings of this exact form: use the main deity's paintings
        exact = False
        fam = plan["family"].strip() or "Durga"
        print(f"  {e}: trying paintings of {fam}")
        images = pick_paintings([f"{fam} painting", f"{fam} Raja Ravi Varma", f"Goddess {fam} miniature painting"
                                 if subj["kind"] == "navratri" else f"{fam} miniature painting"],
                                f"{'Goddess ' if subj['kind'] == 'navratri' else ''}{fam} (any classical depiction)",
                                "devotion_images_used", state, tmp)
    songs = devotional_songs(plan["song_searches"])[:4]
    status.step(f"Writing the {plan['deity']} Reel", 4)
    options = fun.claude_json(DEVOTION_SYSTEM.format(page=CONFIG["page_name"]),
                              f"Today: {day:%A, %d %B %Y}. Reel subject: {subj['name']} (deity: {plan['deity']}). "
                              f"Festivals today: {', '.join(subj['festivals']) or 'none'}.\n"
                              f"On screen the parts are labelled '{plan['middle']}' (lesson) and '{plan['seekh']}' (real_life), "
                              "so don't repeat those words.\n"
                              + ("" if exact else f"The paintings show {plan['family']} in general, not this exact form: "
                                 "don't describe her/his vahana, weapons or looks; write about the meaning and blessing.\n")
                              + viral_memory("for tone and shareability only: today's Reel stays about the deity")
                              + "\nWrite exactly 3 different Reels.",
                              options_schema(DEVOTION_OPTION, "Exactly 3 different Reels"),
                              CONFIG.get("membership_model", "sonnet"), purpose="devotion writing")["options"][:3]
    for o in options:
        o["caption"] = unescape(o["caption"])
    best = fun.judge_best(options, lambda o: f"{o['hook']} -> {o['lesson']} -> {o['real_life']}",
                          CONFIG.get("membership_model", "sonnet"), judge_system=GITA_JUDGE.replace("Gita", "devotional"))
    roman_only(best, ["caption", "pin_comment", "hook", "lesson", "real_life"])
    for k in ("hook", "lesson", "real_life"):
        best[k] = plain_letters(best[k])
    labels = {k: screen_label(plan[k]).upper() if k != "cta" else screen_label(plan[k]) for k in ("head", "middle", "seekh", "cta")}
    labels["source"] = f"—  {printable(subj['name'].split(':')[0])}" if subj["kind"] == "navratri" else ""
    out = os.path.join(tmp, "reel.mp4")
    status.step("Making the video + proofreading", 5)

    def caption_of(d):
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in d["post"]["hashtags"])
        return f"{d['post']['caption'].strip()}\n\nFollow {CONFIG['handle']} for daily darshan 🙏\n\n{tags}"
    frames, data, proof = proofread.run(
        lambda d: gita_reel(images, "", d["post"]["hook"], d["post"]["lesson"], d["post"]["real_life"],
                            d["post"]["highlight"], out, labels=labels),
        {"post": best}, caption_of,
        f"Devotional Reel for {subj['name']} ({plan['deity']}). Check the facts about the deity/festival are the "
        "well-known traditional ones." + ("" if exact else f" The paintings intentionally show {plan['family']} in general "
        "(no free painting of this exact form exists), and the text doesn't describe the form's looks: don't flag that."))
    post = data["post"]
    save_reel(feed, state, frames[1], out, f"reel_devotion-{today}", {
        "headline": printable(f"{subj['name']}: {post['hook']}")[:140], "tag": "🎬 REEL · 🙏 " + labels["head"],
        "caption": g.limit_hashtags(caption_of(data)), "source": "KhabarBawaal Original", "source_url": "",
        "pin_comment": post.get("pin_comment", ""),
        "songs": [{k: s_[k] for k in ("title", "artist", "lang", "mood")} for s_ in songs], "proof": proof})

# ---------------------------------------------------------------- 🗳️ "CHOOSE YOUR ___" poll Reel (4 PM)
# Owner, 15 Oct 2026, after a creator's "CHOOSE YOUR FUTURE" Reel got 16K likes + 8K comments in a day: 4 options,
# each tied to an Instagram button (❤️ like, 💬 comment, 🔁 repost, ✈️ share), so people vote by pressing them.

POLL_OPTION = {"type": "object", "properties": {
    "name": {"type": "string", "description": "Short label under the picture, max 14 characters"},
    "image_kind": {"type": "string", "enum": ["wiki", "app", "photo"], "description": "wiki = a person, party, team, "
                   "film or place with a Wikipedia page; app = a phone app; photo = food, objects, everyday things"},
    "image_query": {"type": "string", "description": "The exact English Wikipedia title / App Store app name / short "
                    "English stock-photo search"}},
    "required": ["name", "image_kind", "image_query"], "additionalProperties": False}
POLL_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string", "description": "Big title, 2-4 words, starts with CHOOSE YOUR, e.g. 'CHOOSE YOUR FUTURE', "
              "'CHOOSE YOUR CAPTAIN', 'CHOOSE YOUR BIRYANI'"},
    "topic": {"type": "string", "description": "What today's poll is about, one short line"},
    "category": {"type": "string", "enum": ["politics", "cricket", "bollywood", "food", "tech", "apps", "daily life",
                                            "festival", "other"]},
    "options": {"type": "array", "items": POLL_OPTION, "description": "Exactly 4, in button order: like, comment, "
                "repost, share"},
    "caption": {"type": "string", "description": "1-2 short Hinglish lines (hook + 'button dabao, vote karo' idea, first "
                "line with the searchable names), 1-2 emojis. The button list is added automatically."},
    "pin_comment": {"type": "string", "description": "Pinned first comment, max 15 words, asks people to explain their vote"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5: the names first, then high-reach"},
    "clip_search": {"type": "array", "items": {"type": "string"}, "description": "3 short English stock-video searches "
                    "for a bright background clip that fits the topic (e.g. 'cricket fans cheering', 'people eating "
                    "biryani', 'friends watching movie'), most specific first"},
    "clip_wanted": {"type": "string", "description": "One line: the ideal background clip"},
    "songs": SONGS},
    "required": ["title", "topic", "category", "options", "caption", "pin_comment", "hashtags", "clip_search",
                 "clip_wanted", "songs"],
    "additionalProperties": False}
POLL_BUTTONS = [("heart", "LIKE", "❤️ Like"), ("comment", "COMMENT", "💬 Comment"), ("repost", "REPOST", "🔁 Repost"),
                ("share", "SHARE", "✈️ Share")]


def html_unescape(text: str) -> str:
    import html as _html
    return _html.unescape(_html.unescape(text))   # Wikimedia credits come double-escaped ('&amp;amp;')


WIKI_UA = {"User-Agent": "KhabarBawaalStudio/1.0 (github.com/vikashlal12345)"}   # Wikimedia asks for a contact
WIKI_WIDTHS = (330, 500, 960, 1280)   # Wikimedia only serves standard thumbnail widths quickly; others get 429


def wiki_get(url: str, params: dict | None = None, tries: int = 3):
    """A Wikimedia request with a polite User-Agent, retried after a pause (it answers bursts with 429)."""
    import time
    import requests
    for k in range(tries):
        try:
            r = requests.get(url, headers=WIKI_UA, timeout=20, params=params)
            if r.status_code == 200:
                return r
        except Exception:
            pass
        time.sleep(2 * (k + 1))
    return None


def wiki_thumb(title: str, size: int = 600) -> tuple[Image.Image | None, str]:
    """The main picture of an English Wikipedia page, else its Wikidata 'image' (owner, 15 Oct 2026: Rahul Gandhi's
    page had none), as a PNG/JPG at the nearest standard width (logos are SVG originals), and its credit, e.g.
    'Bollywood Hungama (CC BY 3.0)' (owner, 15 Oct 2026: most Wikipedia photos need the photographer and licence
    credited). Non-free pictures (fair-use logos, posters) are skipped: (None, '')."""
    import io
    import re
    api = "https://en.wikipedia.org/w/api.php"
    try:
        r = wiki_get(api, {"action": "query", "format": "json", "redirects": 1, "prop": "pageimages|pageprops",
                           "piprop": "name", "ppprop": "wikibase_item", "titles": title})
        page = next(iter(r.json()["query"]["pages"].values())) if r else {}
        name, qid = page.get("pageimage", ""), page.get("pageprops", {}).get("wikibase_item", "")
        if not name and qid:
            r = wiki_get("https://www.wikidata.org/w/api.php", {"action": "wbgetclaims", "format": "json",
                                                                 "entity": qid, "property": "P18"})
            claims = r.json().get("claims", {}).get("P18", []) if r else []
            name = claims[0]["mainsnak"]["datavalue"]["value"].replace(" ", "_") if claims else ""
        if not name:
            print(f"  ! Wikipedia picture '{title}': none found")
            return None, ""
        width = min(WIKI_WIDTHS, key=lambda w: abs(w - size))
        r = wiki_get(api, {"action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|extmetadata",
                           "iiurlwidth": width, "titles": f"File:{name}"})
        info = (next(iter(r.json()["query"]["pages"].values())).get("imageinfo") or [{}])[0] if r else {}
        meta = info.get("extmetadata", {})
        value = lambda k: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", (meta.get(k) or {}).get("value", ""))).strip()
        lic = value("LicenseShortName")
        if value("NonFree") or not re.match(r"(?i)(cc|public domain|pd|gfdl|godl|ogl|open government|attribution)", lic):
            print(f"  ! Wikipedia picture '{title}' isn't free to reuse ({lic or 'no licence'}): skipped")
            return None, ""
        r = wiki_get(info.get("thumburl") or info.get("url", ""))
        if r is None:
            print(f"  ! Wikipedia picture '{title}': download refused")
            return None, ""
        img = Image.open(io.BytesIO(r.content)).convert("RGBA")
        artist = html_unescape(value("Artist"))[:60]
        return img, (f"{artist} ({lic})" if artist else lic)
    except Exception as e:
        print(f"  ! Wikipedia picture '{title}': {str(e)[:80]}")
    return None, ""


def app_icon(name: str) -> Image.Image | None:
    """An app's official icon from the free App Store search (India)."""
    import requests
    import generate as g
    try:
        res = requests.get("https://itunes.apple.com/search", timeout=15, params={
            "term": name, "entity": "software", "country": "in", "limit": 5}).json().get("results", [])
    except Exception:
        return None
    app = next((x for x in res if name.lower() in x.get("trackName", "").lower()), res[0] if res else None)
    img = app and g.download_image(app.get("artworkUrl512", ""))
    return img.convert("RGBA") if img else None


def poll_picture(opt: dict, tmp: str) -> tuple[Image.Image, str]:
    """The picture for one poll option and its credit ('' when none is needed): Wikipedia (credited) / App Store /
    free stock photo, then news photo, then an AI picture, last a coloured card with the name's first letter."""
    img, credit = wiki_thumb(opt["image_query"]) if opt["image_kind"] == "wiki" else (None, "")
    if img is None and opt["image_kind"] == "app":
        img = app_icon(opt["image_query"])
    if img is None and opt["image_kind"] == "wiki":   # a person/party/team: never a random photo (could be someone else)
        img = Image.new("RGB", (400, 400), RED)
        ImageDraw.Draw(img).text((200, 200), printable(opt["name"])[:1].upper(), font=font("Anton-Regular.ttf", 220),
                                 fill="#FFFFFF", anchor="mm")
    if img is None:
        photos = stock_photos(opt["image_query"], want=1) or topic_photos(opt["image_query"], want=1)
        img = photos[0] if photos else None
    if img is None:
        art = ai_picture(opt["image_query"], tmp, landscape=False)
        img = art[0] if art else None
    if img is None:
        img = Image.new("RGB", (400, 400), RED)
        ImageDraw.Draw(img).text((200, 200), printable(opt["name"])[:1].upper(), font=font("Anton-Regular.ttf", 220),
                                 fill="#FFFFFF", anchor="mm")
    return img.convert("RGBA"), credit


def button_icon(kind: str, size: int = 92, color: str = "#111111") -> Image.Image:
    """Instagram-style action icons drawn by hand (emoji can't be drawn): heart, comment, repost, share."""
    s, w = size * 4, size // 3   # drawn 4x big, then shrunk smooth
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if kind == "heart":
        r = s // 4
        d.ellipse((s // 2 - 2 * r, s // 4, s // 2, s // 4 + 2 * r), fill=color)
        d.ellipse((s // 2, s // 4, s // 2 + 2 * r, s // 4 + 2 * r), fill=color)
        d.polygon([(s // 2 - 2 * r + 6, s // 4 + r + 30), (s // 2 + 2 * r - 6, s // 4 + r + 30), (s // 2, s - s // 10)], fill=color)
    elif kind == "comment":
        d.ellipse((s // 8, s // 8, s - s // 8, s - s // 8), outline=color, width=w)
        d.polygon([(s // 6, s - s // 8), (s // 3, s - s // 4 - 10), (s // 6 + 40, s // 2 + 60)], fill=color)
    elif kind == "repost":
        m = s // 6
        d.line((m, s // 3, s - m - 40, s // 3), fill=color, width=w)
        d.polygon([(s - m - 80, s // 3 - 70), (s - m + 20, s // 3), (s - m - 80, s // 3 + 70)], fill=color)
        d.line((m + 40, 2 * s // 3, s - m, 2 * s // 3), fill=color, width=w)
        d.polygon([(m + 80, 2 * s // 3 - 70), (m - 20, 2 * s // 3), (m + 80, 2 * s // 3 + 70)], fill=color)
        d.line((m, s // 3 - w // 2, m, s // 2), fill=color, width=w)
        d.line((s - m, s // 2, s - m, 2 * s // 3 + w // 2), fill=color, width=w)
    else:   # share: a paper plane
        d.polygon([(s // 8, s // 5), (s - s // 8, s // 5), (s // 2, s - s // 8)], outline=color, width=w)
        d.line((s // 2, s - s // 8, s * 7 // 16, s // 2 - 10), fill=color, width=w)
        d.line((s * 7 // 16, s // 2 - 10, s - s // 8, s // 5), fill=color, width=w)
    return im.resize((size, size), Image.LANCZOS)


def poll_reel(title: str, options: list[tuple[Image.Image, str]], out_mp4: str, secs: float = 7.0,
              background: str = "blur", bg_clip: str | None = None,
              bg_image: Image.Image | None = None) -> list[Image.Image]:
    """🗳️ 1080x1920: big 'CHOOSE YOUR ___' title, 4 option cards (picture + name) and an Instagram button bar
    under them (❤️ 💬 🔁 ✈️), everything on screen from the first frame; behind it a blurred, slowly zooming mix of
    the 4 pictures; then the follow card. Returns [cover]."""
    tiles = [ImageOps.fit(img.convert("RGB"), (540, 960)) for img, _ in options]
    collage = Image.new("RGB", (1080, 1920))
    for i, t in enumerate(tiles):
        collage.paste(t, ((i % 2) * 540, (i // 2) * 960))
    bg = (Image.blend(ImageOps.fit(bg_image.convert("RGB"), (RW, RH)), Image.new("RGB", (RW, RH), "#000000"), 0.25)
          if bg_image is not None else
          Image.blend(collage, Image.new("RGB", (RW, RH), "#000000"), 0.35) if background == "grid" else
          Image.blend(collage.filter(ImageFilter.GaussianBlur(28)), Image.new("RGB", (RW, RH), "#000000"), 0.45))
    shots = clip_frames(bg_clip, 0.0, secs, RW, RH, hold=secs) if bg_clip else None   # a video behind the poll
    dim = Image.new("RGBA", (RW, RH), (0, 0, 0, 80))
    over = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    tf = font("Poppins-Bold.ttf", 76)
    while d.textlength(printable(title).upper(), font=tf) > RW - 220 and tf.size > 44:
        tf = font("Poppins-Bold.ttf", tf.size - 4)
    tw = d.textlength(printable(title).upper(), font=tf)
    d.rounded_rectangle(((RW - tw) // 2 - 40, 330, (RW + tw) // 2 + 40, 330 + tf.size + 60), radius=26,
                        fill=(255, 255, 255, 235))
    d.text((RW // 2, 330 + (tf.size + 60) // 2), printable(title).upper(), font=tf, fill="#111111", anchor="mm")
    card, gap = 228, 20
    x0, y0 = (RW - 4 * card - 3 * gap) // 2, 820
    nf = font("Poppins-Bold.ttf", 34)
    for i, (img, name) in enumerate(options):
        x = x0 + i * (card + gap)
        pic = ImageOps.fit(img.convert("RGBA"), (card, card))
        mask = Image.new("L", (card, card), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, card - 1, card - 1), radius=36, fill=255)
        d.rounded_rectangle((x - 6, y0 - 6, x + card + 6, y0 + card + 6), radius=40, fill=(255, 255, 255, 255))
        over.paste(pic, (x, y0), mask)
        label = printable(name)
        while d.textlength(label, font=nf) > card + 10 and nf.size > 22:
            nf = font("Poppins-Bold.ttf", nf.size - 2)
        d.text((x + card // 2, y0 + card + 46), label, font=nf, fill="#FFFFFF", anchor="mm", stroke_width=4,
               stroke_fill="#000000")
    by = y0 + card + 110
    d.rounded_rectangle((x0 - 20, by, x0 + 4 * card + 3 * gap + 20, by + 200), radius=40, fill=(255, 255, 255, 235))
    lf = font("Poppins-Bold.ttf", 26)
    for i, (icon, label, _) in enumerate(POLL_BUTTONS):
        cx = x0 + i * (card + gap) + card // 2
        ic = button_icon(icon, 92, RED if icon == "heart" else "#111111")
        over.alpha_composite(ic, (cx - 46, by + 30))
        d.text((cx, by + 158), label, font=lf, fill="#111111", anchor="mm")
    sub = "Button dabao, vote karo"
    d.text((RW // 2, by + 290), sub, font=font("Poppins-Bold.ttf", 46), fill="#FFFFFF", anchor="mm", stroke_width=5,
           stroke_fill="#000000")
    logo = logo_img()
    if logo:
        over.alpha_composite(logo, ((RW - logo.width) // 2, 150))
    handle_tag(over, 1730)
    enc, frame = Encoder(out_mp4), None
    shot = None
    for i in range(int(secs * FPS)):
        if shots is not None:
            shot = next(shots, shot)
            frame = shot.convert("RGBA")
            frame.alpha_composite(dim)
        else:
            z = 1.0 + 0.05 * i / (secs * FPS)
            big = bg.resize((int(RW * z), int(RH * z)), Image.BICUBIC)
            frame = big.crop(((big.width - RW) // 2, (big.height - RH) // 2, (big.width + RW) // 2, (big.height + RH) // 2)).convert("RGBA")
        frame.alpha_composite(over)
        enc.add(frame)
    cover = frame.convert("RGB")
    add_follow(enc, frame)
    enc.close()
    return [cover]


def make_poll_reel(state: dict, feed: list, today: str, tmp: str) -> None:
    """🗳️ 4 PM: today's 'CHOOSE YOUR ___' poll on a talking point (politics in the page's line: BJP on ✈️ Share,
    the button that spreads furthest), 4 real pictures, the button list in the caption."""
    import fun
    import generate as g
    import proofread
    import status
    status.step("Choosing today's poll topic", 2)
    topics = buzz_topics(state)
    recent = state.get("poll_recent", [])[-10:]
    songs = trending_songs(state)
    poll = fun.claude_json(
        f"You make the daily 'CHOOSE YOUR ___' poll Reel for {CONFIG['page_name']}, an Indian Hinglish page for 18-34s "
        "(English letters only). 4 options that young Indians will fight about in the comments, each tied to an "
        "Instagram button: 1 = like, 2 = comment, 3 = repost, 4 = share. Topic: one of today's talking points if it "
        "makes a fun 4-way choice, else an evergreen desi debate (biryani styles, chai vs coffee, phone brands, "
        "cricket captains, Bollywood Khans, quick-commerce apps, cities...). Star power wins when it fits (CHOOSE YOUR "
        "KHAN, best 90s jodi, captains): the biggest names get the most votes. Vary the category: not the category of "
        "the last 2 polls. Politics only when it is the day's big talk, in the page's line: pro BJP/NDA, BJP or Modi "
        "as option 4 (share); respectful: no religion, caste, region or community choices, no insults. Real, "
        "well-known options only; names short.",
        f"{fun.today_context()}\n\nToday's talking points:\n" + ("\n".join(f"- {t['topic']}: {t['why']}" for t in topics)
                                                                 or "(none big)") +
        "\n\nRecent polls (don't repeat the topic or category of the last 2):\n" + ("\n".join(f"- {p}" for p in recent) or "(none)")
        + viral_memory() + songs_prompt(songs), POLL_SCHEMA, CONFIG.get("membership_model", "sonnet"), purpose="poll writing")
    poll["options"] = poll["options"][:4]
    if len(poll["options"]) < 4:
        raise RuntimeError("the poll needs 4 options")
    roman_only(poll, ["caption", "pin_comment", "title"])
    status.step("Finding the 4 pictures + a background video", 3)
    found = [poll_picture(o, tmp) for o in poll["options"]]
    pics = [img for img, _ in found]
    credits = [f"{printable(o['name'])}: {c}" for o, (_, c) in zip(poll["options"], found) if c]
    # Background (owner, 15 Oct 2026: chose 'a video picked for the topic'): a fitting free clip, else the sharp grid.
    clip, cid = None, None
    try:
        cid = pick_clip({"clip_search": poll["clip_search"], "clip_wanted": poll["clip_wanted"], "text": poll["topic"]}, state)
        clip, _ = download_clip(cid, tmp)
    except RuntimeError as e:
        print(f"  ! no background video ({e}): sharp photo grid instead")
        cid = None
    out = os.path.join(tmp, "reel.mp4")

    def caption_of(d):
        buttons = "\n".join(f"{b[2]} = {printable(o['name'])}" for b, o in zip(POLL_BUTTONS, d["options"]))
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in d["hashtags"])
        photo_credits = ("\n\nPhotos (Wikimedia Commons): " + "; ".join(credits)) if credits else ""
        return (f"{d['caption'].strip()}\n\n{buttons}\n\nFollow {CONFIG['handle']} for daily bawaal{photo_credits}"
                f"\n\n{tags}")

    status.step("Making the video + proofreading", 5)
    picked_songs = checked_songs(poll.pop("songs", []), songs)
    frames, poll, proof = proofread.run(lambda d: poll_reel(d["title"], [(p, o["name"]) for p, o in zip(pics, d["options"])],
                                                          out, background="grid", bg_clip=clip),
                                        poll, caption_of, f"Today's poll topic (the page's own fun poll): {poll['topic']}",
                                        strict=True)
    state["poll_recent"] = (state.get("poll_recent", []) + [f"{poll['category']}: {poll['topic']}"])[-20:]
    if cid:
        state["reel_clips"] = (state.get("reel_clips", []) + [cid])[-80:]
    save_reel(feed, state, frames[0], out, f"reel_poll-{today}", {
        "headline": f"{printable(poll['title'])}: " + " / ".join(printable(o["name"]) for o in poll["options"]),
        "tag": "🎬 REEL · 🗳️ CHOOSE YOUR", "caption": g.limit_hashtags(caption_of(poll)), "source": "KhabarBawaal Original",
        "source_url": "", "pin_comment": poll.get("pin_comment", ""), "songs": picked_songs, "proof": proof})


def make_backup(kind: str, state: dict, feed: list) -> bool:
    """Level 2 of 'never fail' (owner, 15 Oct 2026): the slot's Reel failed every try, so a different, reliable Reel
    with the same checks: a news slideshow for a joke/satire/devotional slot, a Tag that friend joke for a news or
    Top 5 slot. Saved under the slot's id. False (with the reason in ⏳ Activity) if it fails too."""
    import tempfile
    import schedule
    import status
    global SERIES_KIND
    today = schedule.ist_now().date().isoformat()
    news_like = kind in NEWS_KINDS or kind in TOP5_KINDS
    SERIES_KIND = "reel_clip" if news_like else "reel_news"   # the backup's own series line, not the slot's
    status.start(kind, today, f"🎬 Backup Reel ({'Tag that friend' if news_like else 'news'})", 6,
                 "The planned Reel failed every try: making a backup Reel")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if news_like:
                cover, out, entry = build_joke_reel("reel_clip", state, tmp)
                save_reel(feed, state, cover, out, f"{kind}-{today}", entry)
            else:
                make_news_reel(kind, state, feed, today, tmp)
        return True
    except Exception as e:
        print(f"  ! backup Reel not made: {str(e)[:300]}")
        status.fail(f"Backup Reel not made: {str(e)[:200]}. Trying a ready-made Reel from 📦 Ready.", True)
        return False


def make(kind: str, state: dict, feed: list, attempt: int = 1, tries: int = 1) -> bool:
    """Make today's Reel for this slot (try `attempt` of `tries`); False (with the reason printed) if it
    couldn't be made."""
    import tempfile
    import schedule
    import status
    global SERIES_KIND
    SERIES_KIND = kind
    now = schedule.ist_now()
    today = now.date().isoformat()
    h, m = schedule.SPECIALS.get(kind, (now.hour, now.minute))
    name = ("🔥 Viral news" if kind in NEWS_KINDS else "🙏 Devotional" if kind == "reel_devotion" else
            "🎯 Satire" if kind == "reel_politics" else "😂 Meme" if kind == "reel_meme" else
            "😂 Tag that friend" if kind in CLIP_KINDS else "🎬 Top 5" if kind in TOP5_KINDS else
            "🗳️ Choose your" if kind == "reel_poll" else "🧒 Bachpan" if kind == "reel_bachpan" else
            "🎥🔊 Official video" if kind == "reel_official" else "⚡ Breaking" if kind in BREAK_KINDS else "🎭 Rotating" if kind == "reel_rotate" else "🎬 Reel")
    status.start(kind, today, f"{name} · {(h - 1) % 12 + 1}{':%02d' % m if m else ''} {'AM' if h < 12 else 'PM'}", 6,
                 "Started" if attempt == 1 else f"Trying again ({attempt} of {tries})")
    then = f"Trying again ({attempt + 1} of {tries})." if attempt < tries else "Making a backup Reel instead."
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI")):
        print("  Reels need the AI: skipped")
        status.fail(f"Reel not made: the AI isn't available (membership token?). {then}", attempt < tries)
        return False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if kind in NEWS_KINDS:
                make_news_reel(kind, state, feed, today, tmp)
            elif kind in TOP5_KINDS:
                make_top5_reel(kind, state, feed, today, tmp)
            elif kind == "reel_poll":
                make_poll_reel(state, feed, today, tmp)
            elif kind == "reel_bachpan":
                make_bachpan_reel(state, feed, today, tmp)
            elif kind == "reel_official":
                make_official_reel(state, feed, today, tmp)
            elif kind in BREAK_KINDS:
                make_breaking_reel(kind, state, feed, today, tmp)
            elif kind == "reel_rotate":
                make_rotate_reel(state, feed, today, tmp)
            elif kind == "reel_politics":
                make_politics_reel(state, feed, today, tmp)
            elif kind == "reel_devotion":
                make_devotion_reel(state, feed, today, tmp)
            else:
                make_joke_reel(kind, state, feed, today, tmp)
        return True
    except Exception as e:
        print(f"  ! Reel not made (try {attempt} of {tries}): {str(e)[:300]}")
        status.fail(f"Reel not made: {str(e)[:200]}. {then}", attempt < tries)
        return False

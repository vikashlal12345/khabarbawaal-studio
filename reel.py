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

from generate import CONFIG, LOGO_FILE, font, norm, printable, wrap_words

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


HOOK_SECS, FOLLOW_SECS = 1.8, 1.5


def hook_layer(text: str, highlight: set[str]) -> Image.Image:
    """🪝 The opening line (owner, 14 Oct 2026: viewers decide in under a second; our Reels used to open on an
    empty text area and the logo): 4-8 words, big and bold, white with a thick outline, yellow highlights."""
    lines, fnt, lh = fit_lines(printable(text), RIGHT - LEFT + 60, 560, sizes=range(118, 64, -4), name="Anton-Regular.ttf")
    imgs = line_images(lines, fnt, lh, "#FFFFFF", YELLOW, highlight, stroke=9)
    layer = Image.new("RGBA", (RW, len(imgs) * lh + 80), (0, 0, 0, 0))
    for j, img in enumerate(imgs):
        layer.alpha_composite(img, ((RW - img.width) // 2, 40 + j * lh))
    return layer


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


def follow_frame(last: Image.Image) -> Image.Image:
    """End card on every Reel (owner, 14 Oct 2026: ~200 people see a Reel, almost none follow): the last frame
    dimmed, 'Follow @khabarbawaal' + 'roz ki khabar + memes'."""
    frame = last.convert("RGBA")
    dim = Image.new("RGBA", (RW, RH), (12, 12, 12, 225))
    frame.alpha_composite(dim)
    head = pill(f"Follow {CONFIG['handle']}", font("Anton-Regular.ttf", 96), YELLOW, "#111111", pad=(44, 18))
    sub = pill("roz ki khabar + memes", font("Poppins-Bold.ttf", 52), RED, "#FFFFFF", pad=(36, 14))
    y = (RH - head.height - sub.height - 40) // 2
    frame.alpha_composite(head, ((RW - head.width) // 2, y))
    frame.alpha_composite(sub, ((RW - sub.width) // 2, y + head.height + 40))
    return frame


def add_follow(enc, last: Image.Image) -> None:
    """Writes the follow end card (FOLLOW_SECS) after a Reel's last frame."""
    card = follow_frame(last)
    for _ in range(int(FOLLOW_SECS * FPS)):
        enc.add(card)


# ---------------------------------------------------------------- 1. clip + meme text

def clip_reel(clip: str, start: float, dur: float, hook: str, text: str, highlight: list[str],
              out_mp4: str, crop_x: float = 0.5, text_top: int = 960, hold: float = 0.0) -> list[Image.Image]:
    """The last `hold` seconds of dur freeze the clip's last frame (clip shorter than the reading time).
    Returns the frames to proofread: [cover] (all text shown)."""
    highlights = {norm(w) for phrase in highlight for w in phrase.split()}
    lines, fnt, line_h = fit_lines(text, RIGHT - LEFT, 470, sizes=range(80, 40, -4))
    imgs = line_images(lines, fnt, line_h, "#FFFFFF", YELLOW, highlights, stroke=7)
    hook_img = pill(hook, font("Anton-Regular.ttf", 84), RED, "#FFFFFF", pad=(38, 12))
    foot = pill("TAG KARO USKO", font("Poppins-Bold.ttf", 46), YELLOW, "#111111", pad=(34, 16), arrow=True)
    logo = logo_img()
    # Soft dark fade behind the text so it reads on any footage.
    shade = Image.new("RGBA", (RW, 900), (0, 0, 0, 0))
    ImageDraw.Draw(shade).rectangle((0, 120, RW, 780), fill=(0, 0, 0, 120))
    shade = shade.filter(ImageFilter.GaussianBlur(60))
    enc, cover, frame = Encoder(out_mp4), None, None
    for i, frame in enumerate(clip_frames(clip, start, dur - hold, RW, RH, crop_x, hold)):
        t = i / FPS
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
    return [cover or frame.convert("RGB")]


# ---------------------------------------------------------------- 2. classic meme layout

def meme_reel(clip: str, start: float, dur: float, setup: list[str], punch: str, punch_at: float,
              highlight: list[str], out_mp4: str, hold: float = 0.0) -> list[Image.Image]:
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
    enc, before, cover, frame = Encoder(out_mp4), None, None, None
    for i, shot in enumerate(clip_frames(clip, start, dur - hold, clip_w, clip_h, hold=hold)):
        t = i / FPS
        frame = Image.new("RGBA", (RW, RH), "#FFFFFF")
        if logo:
            frame.alpha_composite(logo, ((RW - logo.width) // 2, 150))
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
    return [(before or frame).convert("RGB"), cover or frame.convert("RGB")]


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
              out_mp4: str, end: float = 3.0, hook: str = "") -> list[Image.Image]:
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
    follow = pill(f"Follow {CONFIG['handle']}: roz ki khabar + memes", font("Poppins-Bold.ttf", 40), "#111111", YELLOW,
                  pad=(30, 16))
    logo = logo_img()
    total = int((starts[-1] + end) * FPS)
    enc, checks, frame, hook_check = Encoder(out_mp4), [], None, []
    if hook:   # 🪝 the hook screen first (owner, 14 Oct 2026): first photo + a big line from the first frame
        layer = hook_layer(hook, highlights)
        for i in range(int(HOOK_SECS * FPS)):
            frame = hook_frame(slides[0][0], layer, i / FPS)
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
CLIP_KINDS = ("reel_clip", "reel_clip_am")   # "Tag that friend" clip Reels (1 PM and 8 AM)

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
    "based_on": {"type": "string", "description": "The trend/topic or everyday moment this joke is about"},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-3 words copied exactly from the text to colour"},
    "clip_search": {"type": "array", "items": {"type": "string"}},
    "clip_wanted": {"type": "string"},
    "apps": {"type": "array", "items": {"type": "string"}, "description": "Real phone apps the joke names, as the App "
             "Store calls them (e.g. ['Hopr', 'Tinder']); empty if none"},
    "photo_query": {"type": "string", "description": "If the joke names a real app, brand, person, film or event: a "
                    "short English news search for real photos of it (e.g. 'Hopr dating app Bengaluru Tinder'); "
                    "empty for an everyday moment"},
    "caption": {"type": "string"},
    "pin_comment": {"type": "string"},
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
             "saaf.' or 'Is case ne poore desh ko hila diya'. A fact from the story, not clickbait."},
    "tag": {"type": "string", "enum": NEWS_TAGS},
    "end_text": {"type": "string", "description": "Last card, max 14 words, no emojis: a relatable Hinglish reaction "
                 "(\"Aur hum? Uber mein baithke bhi nervous ho jaate hain\") or, for serious news, the punchy takeaway."},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-2 words copied exactly from end_text"},
    "caption": {"type": "string", "description": "Hinglish: hook line with the searchable keywords (names, film/event, "
                "e.g. 'Drishyam 3 box office collection'), 2-3 short fact lines, a comment prompt. 1-3 emojis."},
    "pin_comment": {"type": "string", "description": "Pinned first comment, max 20 words, no hashtags"},
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
        "in a group chat. Counts: big Bollywood/OTT/celebrity moments, viral internet moments, festivals and their "
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
    style = ("Style: TAG THAT FRIEND: the text describes that one friend everyone has."
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
            "\n\nRecent Reels (don't repeat ideas):\n" + ("\n".join(f"- {r}" for r in state.get("recent_reels", [])[-20:]) or "(none)"))
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


def pick_clip(post: dict, state: dict) -> str:
    """The best-fitting free stock clip for a joke (owner, 15 Oct 2026: 'best Reels, never fail'): Pixabay videos,
    then Mixkit (only its Free licence: Restricted forbids business social media). The AI ranks the clips by title;
    a clip must fit 5/10 or better. Raises RuntimeError if none does (the caller falls back to real photos, then a
    branded background). Returns the clip id (Pixabay 'pb…', Mixkit plain). (Pexels: no new API keys, 15 Oct 2026.)"""
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
    for r in ranked[:12]:   # Mixkit: many good clips are Restricted, keep going until a Free one fits
        if r["id"] not in found or r["fits"] < 5:
            continue
        lic = "Free" if r["id"].startswith("pb") else clip_license(r["id"], known)
        if lic == "Free":
            print(f"  clip {r['id']}: {found[r['id']]} (fits {r['fits']}/10)")
            return r["id"]
        print(f"  skipped clip {r['id']} ({found[r['id']]}): licence '{lic or 'unknown'}'")
    raise RuntimeError("no stock clip fits this joke well enough")


def download_clip(cid: str, folder: str) -> tuple[str, float]:
    """A stock clip -> (path, seconds): Pixabay by the link found while searching, Mixkit 720p (else 1080p/360p)."""
    import re
    import requests
    path = os.path.join(folder, f"{cid}.mp4")
    links = [CLIP_URLS[cid]] if cid in CLIP_URLS else \
        [f"https://assets.mixkit.co/videos/{cid}/{cid}-{res}.mp4" for res in (720, 1080, 360)]
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
    photo_clip(photos, *size, 14.0, clip)
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


def app_photos(names: list[str]) -> list[Image.Image]:
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
    card = Image.new("RGB", (1200, 675), "#111111")
    d = ImageDraw.Draw(card)
    for y in range(675):
        d.line((0, y, 1200, y), fill=(17 + y // 30, 17, 17 + y // 25))
    size, gap = 300, 160
    x = (1200 - len(found) * size - (len(found) - 1) * gap) // 2
    for i, (name, icon, _) in enumerate(found):
        ic = icon.resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=66, fill=255)
        card.paste(ic, (x, 150), mask)
        d.text((x + size // 2, 150 + size + 50), name, font=font("Poppins-Bold.ttf", 56), fill="#FFFFFF", anchor="mm")
        if i < len(found) - 1:
            d.text((x + size + gap // 2, 150 + size // 2), "VS", font=font("Anton-Regular.ttf", 110), fill=YELLOW, anchor="mm")
        x += size + gap
    return [card] + [shot.convert("RGB") for _, _, shot in found if shot]


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


def photo_clip(photos: list[Image.Image], w: int, h: int, secs: float, out_mp4: str) -> None:
    """A 'clip' made of real photos, one after another with a slow zoom, for the joke layouts: the meme window
    (w x h landscape: photo fills it) or full screen (whole photo on a blurred copy of itself)."""
    enc, per = Encoder(out_mp4, size=(w, h)), secs / len(photos)
    for i in range(int(secs * FPS)):
        t = i / FPS
        n = min(len(photos) - 1, int(t / per))
        img, z = photos[n], 1.0 + 0.06 * (t - n * per) / per
        if h > w:
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
    notes, apps = "", []
    if topic:   # owner, 14 Oct 2026: Hopr is a carpool app; the writer thought it was a dating app
        apps = named_apps(topic)
        search = re.sub(r"(?i)^\s*(please\s+)?(make|create|bana\w*)\s+(a|an|ek)?\s*(funny\s+)?(reel|post|meme)\s+"
                        r"(of|on|about|for|ki|ka|pe|par)?\s*", "", topic)
        news = topic_articles(search)
        notes = (("Apps named in the idea (App Store): " + "; ".join(f"{a['name']} = {a['title']}: {a['about']}"
                                                                     for a in apps)) if apps else "") + \
                (f"\n\nLatest news about it (use these facts, invent none):\n{news[:3000]}" if news else "")
    post = write_joke(kind, state, evergreen, topic, notes)
    status.step(f"Judge picked: {post['based_on'][:80]}", 3)
    # ➕ Create (owner, 14 Oct 2026: "no photos or logo of Hopr and Tinder"): real photos of what the idea names
    # (from news articles, often the app screens/logos) instead of a stock clip; stock clip if none are found.
    art_note = ""
    photos = app_photos(list(dict.fromkeys([a["name"] for a in apps] + post.get("apps", [])))) if topic else []
    if topic and len(photos) < 3 and post.get("photo_query", "").strip():
        photos += topic_photos(post["photo_query"], want=3 - len(photos) + 1)
    real = len(photos) >= 2
    if real:
        status.step(f"Real photos: {', '.join(post.get('apps', [])) or post.get('photo_query', '')[:60]}", 4)
        clip, secs, cid = os.path.join(tmp, "photos.mp4"), 14.0, None
        photo_clip(photos, *((RW, RH) if kind in CLIP_KINDS else (RW, 608)), secs, clip)
        print(f"  real photos: {len(photos)} (apps {post.get('apps', [])}, search '{post.get('photo_query', '')}')")
    else:
        try:
            cid = pick_clip(post, state)
            clip, secs = download_clip(cid, tmp)
        except RuntimeError as e:   # no fitting clip: photos -> 🎨 AI picture -> branded background
            print(f"  ! {e}")
            clip, secs, art_note = fallback_visual(post, (RW, RH) if kind in CLIP_KINDS else (RW, 608), tmp)
            real, cid = True, None
    out = os.path.join(tmp, "reel.mp4")
    songs = post.pop("songs", [])   # kept out of the proofreader's text
    # Long enough to read the (proofread) text in full; a short stock clip caps Tag that friend (memes freeze).
    if kind in CLIP_KINDS:
        def render(d):   # + 1 s for the TAG THAT FRIEND banner and the lines popping in, + 2 s to read (12 Oct)
            dur = min(max(CLIP_SECONDS, read_secs(d["text"], 4.5)), CLIP_MAX)
            start = 0.0 if real else max(0.0, min(1.0, secs - dur - 0.2))
            hold = max(0.0, dur - (secs - 0.2 - start))   # a short clip freezes on its last frame
            return clip_reel(clip, start, dur, "TAG THAT FRIEND", d["text"], d["highlight"], out, hold=hold)
    else:
        def render(d):   # build-up, then the punchline: each 2.5 s + 0.25 s a word (owner, 12 Oct 2026: +1 s)
            setup_t = min(8.5, max(MEME_SECONDS * 0.55, read_secs(" ".join(d["setup"]), 2.5)))
            punch_t = min(8.5, max(MEME_SECONDS * 0.45, read_secs(d["punch"], 2.5)))
            dur = setup_t + punch_t
            start = 0.0 if real else max(0.0, secs - dur - 0.2)      # reactions usually come late in a clip
            hold = max(0.0, dur - (secs - 0.2))     # a short clip freezes on its last frame, the text keeps its time
            return meme_reel(clip, start, dur, d["setup"], d["punch"], round(setup_t, 2), d["highlight"], out, hold)
    status.step("Making the video + proofreading", 5)
    if topic:   # the owner's idea is a joke premise, not a news claim: the proofreader keeps it (14 Oct 2026)
        check = (f"THE PAGE OWNER'S OWN JOKE IDEA (a premise, not a news claim: keep its premise and the names in it, "
                 f"including in hashtags; check only spellings and any other stated facts): {topic}\n\n{notes}")
    else:
        check = "" if evergreen else topic_articles(post["based_on"])
    frames, post, proof = proofread.run(render, post, g.fun_caption, check, strict=True)
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
        "tag": "🎬 REEL · " + ("TAG THAT FRIEND" if kind in CLIP_KINDS else "MEME"),
        "caption": g.limit_hashtags(g.fun_caption(post)), "source": "KhabarBawaal Original", "source_url": "",
        "pin_comment": post.get("pin_comment", ""), "songs": songs, "proof": proof}


def make_joke_reel(kind: str, state: dict, feed: list, today: str, tmp: str) -> None:
    cover, mp4, entry = build_joke_reel(kind, state, tmp)
    save_reel(feed, state, cover, mp4, f"{kind}-{today}", entry)


NEWS_SYSTEM = """You make a VIRAL NEWS REEL for {page}, an Indian Instagram page for 18-34s (Hinglish, \
English letters only, never Devanagari): ONE single story people are talking about right now, told over its real \
photos (owner, 15 Oct 2026: 'like the Bittu Tabahi one': one man cleaned a river for 8 months). From the latest \
stories pick the 3 MOST VIRAL / TRENDING, best first: trending on X or Google in India, covered by many sites, \
viral human-interest moments (shocking, inspiring, heart-warming, unbelievable), big Bollywood/OTT/cricket moments, \
with strong visuals. A story everyone will want to watch to the end and share.
Only stories most people across India care about (owner, 14 Oct 2026: Indian stories got ~227 views, foreign or niche \
celebrity news ~15): Indian news, Bollywood/OTT, Team India and IPL, viral Indian moments, big national stories. Skip \
foreign news unless it is about India or Indians, foreign sports and niche or foreign celebrity news.
Rules: real facts only, no invented quotes. Never make jokes about deaths, accidents, crimes or disasters. \
Politics: supportive of the right wing (BJP, NDA), critical of the left and opposition; never pick a story \
that embarrasses the BJP. No hate against any community."""


NEWS_KINDS = ("reel_news_early", "reel_news_am", "reel_news_noon", "reel_news_aft", "reel_news_pm", "reel_news",
              "reel_news_night")     # 🔥 viral news Reels: 7, 10, 11:30 AM, 3, 5, 8, 9:15 PM (owner, 15 Oct 2026)
TOP5_KINDS = {"reel_top5_am": "night_roundup", "reel_top5": "top10_day"}   # Top 5 Reel -> its specials.TOP10 settings


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
                              f"\n\nStories:\n{listing}\n\nGive your top 3 picks, the most viral first."
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
    cover, out, entry = finish_news_reel(item, opt, photos, songs, tmp)
    state["reel_news_used"] = (used + [story["link"]])[-80:]
    if story["id"]:
        state["seen"] = state.get("seen", []) + [story["id"]]   # the other news Reels skip it
    save_reel(feed, state, cover, out, f"{kind}-{today}", {**entry, "headline": story["title"]})


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


def finish_news_reel(item: dict, opt: dict, photos: list[tuple], songs: dict, tmp: str,
                     article: str | None = None) -> tuple[Image.Image, str, dict]:
    """Render + proofread a news Reel. Returns (cover, mp4 path, feed fields)."""
    import carousel
    import generate as g
    import proofread
    import status
    status.step("Making the video + proofreading", 5)
    opt["caption"] = unescape(opt["caption"])
    roman_only(opt, ["caption", "pin_comment", "end_text"])
    picked_songs = checked_songs(opt.pop("songs", []), songs)   # kept out of the proofreader's text
    photos = photos[:3]   # shorter Reels (owner, 14 Oct 2026): 3 photos max
    roman_only(opt, ["hook"])
    images = [(img, whole) for img, _, _, whole in photos]
    data = {"post": opt, "slides": [{"n": i, "credit": c, "text": t} for i, (_, c, t, _) in enumerate(photos)]}
    out = os.path.join(tmp, "reel.mp4")

    def render(d):
        return news_reel([(images[s["n"]][0], s["credit"], s["text"], images[s["n"]][1]) for s in d["slides"]],
                         d["post"]["end_text"], d["post"]["highlight"], d["post"]["tag"], out,
                         hook=d["post"].get("hook", ""))

    def drop(d, numbers):   # proofreader flagged weak photos; keep at least 2
        keep = [s for i, s in enumerate(d["slides"], 1) if i not in numbers]
        return {**d, "slides": keep} if 2 <= len(keep) < len(d["slides"]) else None

    if article is None:
        article = carousel.article_text(carousel.page_html(item["link"]))
    caption_of = (lambda d: g.full_caption(d["post"], item)) if item["link"] else \
        (lambda d: f"{d['post']['caption'].strip()}\n\nFollow {CONFIG['handle']} for daily updates.\n\n"
                   + " ".join(t if t.startswith("#") else f"#{t}" for t in d["post"]["hashtags"]))
    frames, data, proof = proofread.run(render, data, caption_of, article, drop=drop, strict=True)
    post = data["post"]
    return frames[0], out, {
        "headline": item["title"], "tag": "🎬 REEL · " + post["tag"],
        "caption": g.limit_hashtags(caption_of(data)), "source": item["source"], "source_url": item["link"],
        "pin_comment": post.get("pin_comment", ""), "songs": picked_songs, "proof": proof}


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
    story = ((f"The owner asked: {text}\n" if text else "") +
             (f"Headline: {article['title']}\nSite: {article['site']}\n{article.get('description', '')}"
              if article else ""))
    opt = fun.claude_json(NEWS_SYSTEM.split("From today's stories")[0] +
                          "The owner sent ONE story: write the Reel for it.\n" +
                          "Rules:" + NEWS_SYSTEM.split("Rules:")[1],
                          f"{fun.today_context()}\n\n{story}" + songs_prompt(songs), schema,
                          CONFIG.get("membership_model", "sonnet"), purpose="reel news script")
    item = {"link": article["url"] if article else "", "source": article["site"] if article else "Your pick",
            "title": (article or {}).get("title") or text[:120]}
    status.step("Collecting photos", 4)
    photos = news_photos(item, opt)
    if len(photos) < 2:
        raise RuntimeError(f"only {len(photos)} usable photo(s) found for this story")
    return finish_news_reel(item, opt, photos, songs, tmp, article=None if article else text)


# ---------------------------------------------------------------- 4. political satire + reality check (6 PM)

def satire_reel(clip: str, start: float, clip_dur: float, claim: list[str], context: list[str],
                facts: list[dict], punchline: str, highlight: list[str], out_mp4: str) -> list[Image.Image]:
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

    def secs(text: str) -> float:          # time to read comfortably (~3.5 words/s + a hold; owner 12 Oct 2026: +1 s)
        return max(3.0, len(text.split()) / 3.5 + 1.9)
    t_ctx = clip_dur
    d_ctx = 0.5 + secs(" ".join(context))
    t_facts = t_ctx + d_ctx
    per = [0.4 + secs(f["fact"]) for f in facts]
    t_punch = t_facts + 0.4 + sum(per)
    d_punch = 0.4 + secs(punchline)
    total = t_punch + d_punch + 2.0
    enc, checks, frame = Encoder(out_mp4), [], None
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
    add_follow(enc, frame)
    enc.close()
    while len(checks) < 4:
        checks.append(frame.convert("RGB"))
    return checks


POLITICS_TARGETS = ("Congress, AAP, TMC, the Left parties (CPI, CPI-M) and the Cockroach Janta Party (CJP, founder "
                    "Abhijeet Dipke)")
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
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-3 words copied exactly from the texts"},
    "clip_search": {"type": "array", "items": {"type": "string"},
                    "description": "3 searches for a REACTION stock clip (laughing, facepalm, shocked, eye roll...)"},
    "clip_wanted": {"type": "string"},
    "caption": {"type": "string"}, "pin_comment": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5, include #reelsindia"},
    "songs": SONGS},
    "required": ["based_on", "issue", "party", "format", "claim", "context", "facts", "punchline", "highlight", "clip_search",
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
one line, 1-3 emojis, last line makes people comment or send it."""

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
    songs = trending_songs(state)
    best = None
    def write(sources: str, extra: str = "") -> list[dict]:
        options = fun.claude_json(POLITICS_SYSTEM.format(page=CONFIG["page_name"], targets=POLITICS_TARGETS),
                                  f"{fun.today_context()}\n\nArticles:\n{sources}\n\nRecent satire "
                                  f"Reels (yours must differ in angle, format, hook and punchline; no format from the "
                                  f"last 7):\n{recent}\n\nWrite exactly 3 different Reels about the MAIN story, each "
                                  "in a different format." + extra + songs_prompt(songs),
                                  options_schema(POLITICS_OPTION, "Exactly 3 different Reels"),
                                  model, purpose="satire writing")["options"][:3]
        for o in options:
            o["caption"] = unescape(o["caption"])
        return options

    def judge(options: list[dict]) -> dict:
        return fun.judge_best(options, lambda o: f"[{o['format']}] " + " ".join(o["claim"]) + " | KYA HUA: "
                              + " ".join(o["context"]) + " | REALITY CHECK: " + " / ".join(f["fact"] for f in o["facts"])
                              + " | " + o["punchline"], model,
                              judge_system=f"{SATIRE_JUDGE}\n\nRecent Reels:\n{recent}", min_fresh=6,
                              min_clear=6)   # one point lower than jokes (owner, 14 Oct 2026: post more reliably)

    for i, n in enumerate(picks):   # the Reel must pass the judge; else try the next story
        story = stories[n - 1]
        article = carousel.article_text(carousel.page_html(story["url"])) or story["title"]
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
    art_note = ""
    try:
        cid = pick_clip({**best, "text": " ".join(best["claim"]), "punch": best["punchline"]}, state)
        clip, secs = download_clip(cid, tmp)
    except RuntimeError as e:   # no fitting reaction clip: photos -> 🎨 AI picture -> branded background
        print(f"  ! {e}")
        cid = None
        clip, secs, art_note = fallback_visual({**best, "photo_query": best.get("clip_wanted", "")}, (RW, 608), tmp)
    out = os.path.join(tmp, "reel.mp4")
    item = {"link": story["url"], "source": story["site"], "title": story["title"]}

    def render(d):   # the dig over the clip stays until it's read (2.5 s + 0.25 s a word, 5.5-7 s); later parts time themselves
        clip_dur = min(max(5.5, read_secs(" ".join(d["post"]["claim"]), 2.5)), 7.0)
        start = max(0.0, secs - clip_dur - 0.2)   # a short clip freezes on its last frame
        return satire_reel(clip, start, clip_dur, d["post"]["claim"], d["post"]["context"], d["post"]["facts"],
                           d["post"]["punchline"], d["post"]["highlight"], out)
    status.step("Making the video + fact-checking", 5)
    frames, data, proof = proofread.run(
        render,
        {"post": best}, lambda d: g.full_caption(d["post"], item),
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
        "caption": g.limit_hashtags(g.full_caption(post, item)), "source": story["site"], "source_url": story["url"],
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
    "caption": {"type": "string"}, "pin_comment": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5 (e.g. #navratri #jaimatadi), include #reelsindia"}},
    "required": ["based_on", "hook", "lesson", "real_life", "highlight", "caption", "pin_comment", "hashtags"],
    "additionalProperties": False}

DEVOTION_SYSTEM = """You write the 6 AM devotional Reel for {page}, an Indian Hinglish page for 18-34s (English \
letters only, never Devanagari). Warm, reverent, uplifting, like a blessing from family: festival or god of the day, \
who the deity is in simple words, and a blessing or life lesson for today. Only well-known traditional facts; never \
joke about any god, no politics, nothing against any religion or community. Write it like poetry: short lines, \
natural pauses ("..."). Caption: 2-4 short Hinglish lines, 1-2 emojis (🙏 fits), last line asks people to share \
with family or comment 'Jai ...'."""


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


def wiki_thumb(title: str) -> tuple[Image.Image | None, str]:
    """The main picture of an English Wikipedia page (a PNG/JPG render: logos are SVG originals) and its credit,
    e.g. 'Bollywood Hungama (CC BY 3.0)' (owner, 15 Oct 2026: most Wikipedia photos need the photographer and licence
    credited). Non-free pictures (fair-use logos, posters) are skipped: (None, '')."""
    import io
    import re
    import requests
    api, head = "https://en.wikipedia.org/w/api.php", {"User-Agent": "KhabarBawaalStudio/1.0"}
    try:
        page = next(iter(requests.get(api, timeout=15, headers=head, params={
            "action": "query", "format": "json", "redirects": 1, "prop": "pageimages", "piprop": "thumbnail|name",
            "pithumbsize": 600, "titles": title}).json()["query"]["pages"].values()))
        src, name = page.get("thumbnail", {}).get("source", ""), page.get("pageimage", "")
        if not src or not name:
            return None, ""
        info = next(iter(requests.get(api, timeout=15, headers=head, params={
            "action": "query", "format": "json", "prop": "imageinfo", "iiprop": "extmetadata",
            "titles": f"File:{name}"}).json()["query"]["pages"].values()))
        meta = (info.get("imageinfo") or [{}])[0].get("extmetadata", {})
        value = lambda k: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", (meta.get(k) or {}).get("value", ""))).strip()
        lic = value("LicenseShortName")
        if value("NonFree") or not re.match(r"(?i)(cc|public domain|pd|gfdl|godl|ogl|open government|attribution)", lic):
            print(f"  ! Wikipedia picture '{title}' isn't free to reuse ({lic or 'no licence'}): skipped")
            return None, ""
        img = Image.open(io.BytesIO(requests.get(src, timeout=20, headers=head).content)).convert("RGBA")
        artist = value("Artist")[:60]
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
        "cricket captains, Bollywood Khans, quick-commerce apps, cities...). Vary the category: not the category of "
        "the last 2 polls. Politics only when it is the day's big talk, in the page's line: pro BJP/NDA, BJP or Modi "
        "as option 4 (share); respectful: no religion, caste, region or community choices, no insults. Real, "
        "well-known options only; names short.",
        f"{fun.today_context()}\n\nToday's talking points:\n" + ("\n".join(f"- {t['topic']}: {t['why']}" for t in topics)
                                                                 or "(none big)") +
        "\n\nRecent polls (don't repeat the topic or category of the last 2):\n" + ("\n".join(f"- {p}" for p in recent) or "(none)")
        + songs_prompt(songs), POLL_SCHEMA, CONFIG.get("membership_model", "sonnet"), purpose="poll writing")
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
    today = schedule.ist_now().date().isoformat()
    news_like = kind in NEWS_KINDS or kind in TOP5_KINDS
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
    now = schedule.ist_now()
    today = now.date().isoformat()
    h, m = schedule.SPECIALS.get(kind, (now.hour, now.minute))
    name = ("🔥 Viral news" if kind in NEWS_KINDS else "🙏 Devotional" if kind == "reel_devotion" else
            "🎯 Satire" if kind == "reel_politics" else "😂 Meme" if kind == "reel_meme" else
            "😂 Tag that friend" if kind in CLIP_KINDS else "🎬 Top 5" if kind in TOP5_KINDS else
            "🗳️ Choose your" if kind == "reel_poll" else "🎬 Reel")
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

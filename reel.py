"""Reels: 1080x1920 videos, no music (the owner adds a trending song in Instagram).

    make(kind, state, feed)   the robot's entry: reel_clip_am (8 AM) and reel_clip (1 PM) "Tag that friend",
                              reel_meme (7 PM), reel_news (8 PM)

Three styles (each writes the MP4 and returns still frames for the proofreader):
    clip_reel(clip, start, dur, hook, text, highlight, out_mp4)
        real video clip filling the screen, outlined meme text popping in on top
    meme_reel(clip, start, dur, setup, punch, punch_at, highlight, out_mp4)
        classic meme layout: white screen, text above the clip; setup text counts up,
        punchline swaps in at punch_at seconds
    news_reel(slides, end_text, highlight, tag, out_mp4)
        news photos zooming/sliding with story lines, ending on a punchline card
        slides = [(photo, credit, text), ...]

Clips come from Mixkit (no sign-up); only clips under its Free licence are used. Text stays inside Instagram's safe
zone (Reels buttons cover the bottom and the right edge).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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

    def __init__(self, out_mp4: str, audio_from: str | None = None, audio_start: float = 0.0):
        sound = (["-ss", str(audio_start), "-i", audio_from] if audio_from and has_audio(audio_from)
                 else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])   # some apps reject video-only files
        self.p = subprocess.Popen(
            [ffmpeg_exe(), "-y", "-loglevel", "error",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{RW}x{RH}", "-r", str(FPS), "-i", "-",
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


def clip_frames(path: str, start: float, dur: float, w: int, h: int, crop_x: float = 0.5):
    """Frames of a clip, scaled to cover w x h (crop centred at crop_x of the width), 30 fps."""
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
          f"crop={w}:{h}:(iw-{w})*{crop_x}:(ih-{h})/2,fps={FPS}")
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


# ---------------------------------------------------------------- 1. clip + meme text

def clip_reel(clip: str, start: float, dur: float, hook: str, text: str, highlight: list[str],
              out_mp4: str, crop_x: float = 0.5, text_top: int = 960) -> list[Image.Image]:
    """Returns the frames to proofread: [cover] (all text shown)."""
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
    step = min(0.45, 2.0 / max(len(lines), 1))
    enc, cover, frame = Encoder(out_mp4), None, None
    for i, frame in enumerate(clip_frames(clip, start, dur, RW, RH, crop_x)):
        t = i / FPS
        frame = frame.convert("RGBA")
        frame.alpha_composite(shade, (0, text_top - 300))
        if logo:
            frame.alpha_composite(logo, (60, 150))
        k = ease_out_back(t / 0.45)
        frame.alpha_composite(hook_img, ((RW - hook_img.width) // 2, int(text_top - 150 - 300 * (1 - k))))
        for j, img in enumerate(imgs):
            paste_pop(frame, img, LEFT, text_top + j * line_h, (t - 0.4 - j * step) / 0.3)
        k = ease_out((t - (dur - 1.6)) / 0.4)
        if k > 0:
            fy = int(text_top + len(lines) * line_h + 40 + 120 * (1 - k))
            frame.alpha_composite(foot, ((RW - foot.width) // 2, fy))
        handle_tag(frame, 1730)
        if t >= dur - 1.0 and cover is None:
            cover = frame.convert("RGB")
        enc.add(frame)
    enc.close()
    return [cover or frame.convert("RGB")]


# ---------------------------------------------------------------- 2. classic meme layout

def meme_reel(clip: str, start: float, dur: float, setup: list[str], punch: str, punch_at: float,
              highlight: list[str], out_mp4: str) -> list[Image.Image]:
    """White screen, text on top, clip in the middle. setup = short items shown one by one
    (e.g. alarm times), punch replaces them at punch_at seconds.
    Returns the frames to proofread: [setup complete, cover (punchline)]."""
    highlights = {norm(w) for phrase in highlight for w in phrase.split()}
    clip_w, clip_h = RW, int(RW * 9 / 16)
    clip_y, text_bottom = 1000, 960
    s_lines, s_fnt, s_lh = fit_lines(" ".join(setup), RIGHT - LEFT, 480, sizes=range(76, 40, -4))
    p_lines, p_fnt, p_lh = fit_lines(punch, RIGHT - LEFT, 480, sizes=range(96, 44, -4))
    p_imgs = line_images(p_lines, p_fnt, p_lh, "#111111", RED, highlights)
    logo = logo_img()
    every = min(0.75, (punch_at - 0.3) / max(len(setup), 1))
    enc, before, cover, frame = Encoder(out_mp4), None, None, None
    for i, shot in enumerate(clip_frames(clip, start, dur, clip_w, clip_h)):
        t = i / FPS
        frame = Image.new("RGBA", (RW, RH), "#FFFFFF")
        if logo:
            frame.alpha_composite(logo, ((RW - logo.width) // 2, 150))
        if t < punch_at:
            shown = setup[: 1 + int(t / every)]
            ls, fnt, lh = fit_lines(" ".join(shown), RIGHT - LEFT, 480, sizes=[s_fnt.size])
            y0 = text_bottom - len(s_lines) * s_lh
            for j, img in enumerate(line_images(ls, fnt, lh, "#111111", RED, highlights)):
                frame.alpha_composite(img, (LEFT, y0 + j * lh))
        else:
            y0 = text_bottom - len(p_lines) * p_lh
            for j, img in enumerate(p_imgs):
                paste_pop(frame, img, LEFT, y0 + j * p_lh, (t - punch_at - j * 0.12) / 0.3)
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
            paste_pop(frame, img, LEFT, text_top + j * line_h, (t - 0.2 - j * 0.25) / 0.3)
        handle_tag(frame, 1730)
        if t >= min(1.8, dur * 0.4) and cover is None:
            cover = frame.convert("RGB")
        enc.add(frame)
    enc.close()
    return [cover or frame.convert("RGB")]


# ---------------------------------------------------------------- 3. news photo slideshow

def news_reel(slides: list[tuple[Image.Image, str, str, bool]], end_text: str, highlight: list[str], tag: str,
              out_mp4: str, end: float = 2.5) -> list[Image.Image]:
    """slides = [(photo, credit, text, whole)]; whole=True (X post screenshots) shows the picture
    uncropped instead of zooming. Each photo stays long enough to read its line (2.6-4.5 s).
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
        per = min(4.5, max(2.6, 1.0 + 0.16 * len(text.split())))
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
    follow = pill("FOLLOW FOR DAILY BAWAAL", font("Poppins-Bold.ttf", 44), "#111111", YELLOW, pad=(34, 16))
    logo = logo_img()
    total = int((starts[-1] + end) * FPS)
    enc, checks, frame = Encoder(out_mp4), [], None
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
                paste_pop(frame, img, LEFT, photo_y + photo_h + 60 + j * lh, (lt - 0.35 - j * 0.15) / 0.3)
            if len(checks) == n and lt >= per - 0.3:
                checks.append(frame.convert("RGB"))
        else:
            lt = t - starts[-1]
            frame = Image.new("RGBA", (RW, RH), YELLOW)
            y0 = (RH - len(e_imgs) * e_lh - 200) // 2
            for j, img in enumerate(e_imgs):
                paste_pop(frame, img, LEFT, y0 + j * e_lh, (lt - j * 0.15) / 0.3)
            k = ease_out((lt - 0.8) / 0.4)
            if k > 0:
                frame.alpha_composite(follow, ((RW - follow.width) // 2,
                                               int(y0 + len(e_imgs) * e_lh + 80 + 100 * (1 - k))))
            handle_tag(frame, 1600, fill="#111111", stroke=0)
        enc.add(frame)
    enc.close()
    return checks + [frame.convert("RGB")]


# ---------------------------------------------------------------- the robot
# 8 AM reel_clip_am + 1 PM reel_clip, 7 PM reel_meme, 8 PM reel_news (schedule.py). Jokes: 3 options, the
# 'young Indian' judge picks (like fun posts), the AI picks a matching Mixkit clip by title.
# News: the AI picks today's most visual story from our own news posts; photos are collected
# and checked like carousel photos. Every Reel is proofread (still frames). If anything fails,
# make() returns False and the slot gets a regular post.

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
CLIP_SECONDS, MEME_SECONDS = 6.5, 8.0
CLIP_KINDS = ("reel_clip", "reel_clip_am")   # "Tag that friend" clip Reels (1 PM and 8 AM)

REEL_RULES = """This joke becomes a short Instagram REEL: a free stock video clip (Mixkit) with our text on top.
The clip must be something a stock-video site has: everyday people doing everyday things (lying in bed \
with a phone, eating, laughing, shocked face, running late, working on a laptop, dancing, sleeping, cooking, \
studying, waiting...). No celebrities, brands or specific Indian places in the clip.
clip_search: 3 different searches for the stock site, 1-3 plain English words each, most specific first (e.g. ["texting in bed", "man phone bed", "lazy"]). Simple words find more clips. Prefer jokes whose moment a stock clip can actually show.
clip_wanted: one line describing the ideal clip.
caption, pin_comment and hashtags follow the same rules as fun posts (exactly 5 hashtags, include #reelsindia)."""

SONGS = {"type": "array", "description": "2 Hindi + 2 English songs that suit this Reel's mood, copied exactly "
         "from today's trending lists",
         "items": {"type": "object", "properties": {
             "title": {"type": "string"}, "artist": {"type": "string"},
             "lang": {"type": "string", "enum": ["hindi", "english"]},
             "mood": {"type": "string", "description": "2-4 words, e.g. 'funny, upbeat drop'"}},
             "required": ["title", "artist", "lang", "mood"], "additionalProperties": False}}

_COMMON = {
    "songs": SONGS,
    "based_on": {"type": "string", "description": "The trend/topic or everyday moment this joke is about"},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-3 words copied exactly from the text to colour"},
    "clip_search": {"type": "array", "items": {"type": "string"}},
    "clip_wanted": {"type": "string"},
    "caption": {"type": "string"},
    "pin_comment": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}},
}
CLIP_OPTION = {"type": "object", "properties": {
    **_COMMON,
    "text": {"type": "string", "description": "Text shown under a big 'TAG THAT FRIEND' banner, describing that "
             "friend, e.g. \"Wo dost jo bed se text karta hai:\\n'Bhai nikal gaya, 5 min mein pahunch raha hoon'\". "
             "Max 22 words, no emojis, \\n line breaks allowed."}},
    "required": list(_COMMON) + ["text"], "additionalProperties": False}
MEME_OPTION = {"type": "object", "properties": {
    **_COMMON,
    "setup": {"type": "array", "items": {"type": "string"}, "description":
              "2-7 short pieces (max 16 words in total) that appear one after another while the clip plays, "
              "building up, e.g. [\"Alarm:\", \"6:00,\", \"6:05,\", \"6:10,\", \"6:15...\"]. No emojis."},
    "punch": {"type": "string", "description": "Punchline (max 14 words, no emojis) that replaces the setup at the "
              "clip's reaction moment, e.g. \"Me at 9:45 jab office 10 baje hai\". highlight words come from here."}},
    "required": list(_COMMON) + ["setup", "punch"], "additionalProperties": False}

NEWS_TAGS = ["VIRAL", "BREAKING", "BOLLYWOOD", "CRICKET", "SPORTS", "TECH", "INDIA", "WORLD", "POLITICS", "MONEY"]
NEWS_OPTION = {"type": "object", "properties": {
    "pick": {"type": "integer", "description": "Story number from the list"},
    "tag": {"type": "string", "enum": NEWS_TAGS},
    "end_text": {"type": "string", "description": "Last card, max 14 words, no emojis: a relatable Hinglish reaction "
                 "(\"Aur hum? Uber mein baithke bhi nervous ho jaate hain\") or, for serious news, the punchy takeaway."},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-2 words copied exactly from end_text"},
    "caption": {"type": "string", "description": "Hinglish: hook line, 2-3 short fact lines, a comment prompt. 1-3 emojis."},
    "pin_comment": {"type": "string", "description": "Pinned first comment, max 20 words, no hashtags"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5, include #reelsindia"},
    "photo_query": {"type": "string", "description": "Short English news-search query that finds photos of this exact story"},
    "wiki_title": {"type": "string", "description": "English Wikipedia title of the main person/team/place, or empty"},
    "songs": SONGS},
    "required": ["pick", "tag", "end_text", "highlight", "caption", "pin_comment", "hashtags", "photo_query", "wiki_title",
                 "songs"],
    "additionalProperties": False}


def options_schema(option: dict, n: str) -> dict:
    return {"type": "object", "properties": {"options": {"type": "array", "items": option, "description": n}},
            "required": ["options"], "additionalProperties": False}


def unescape(s: str) -> str:
    return s.replace("\\n", "\n").strip()


def write_joke(kind: str, state: dict, evergreen: bool = False, topic: str = "") -> dict:
    """evergreen=True: for 📦 Ready Reels, posted any day this week, so no trends, news or dates.
    topic: the owner's idea from ➕ Create (all 3 options are about it)."""
    import fun
    import generate as g
    system = fun.FUN_SYSTEM.format(page=CONFIG["page_name"]) + "\n\n" + fun.JOKE_RULES + "\n\n" + REEL_RULES
    style = ("Style: TAG THAT FRIEND: the text describes that one friend everyone has."
             if kind in CLIP_KINDS else
             "Style: classic meme Reel: a setup that builds up, then a punchline (\"Me at...\", \"Me after...\", "
             "\"POV: ...\") landing on a REACTION clip (shocked, panicking, laughing, crying, facepalm, dancing...).")
    songs = trending_songs()
    if topic:
        context = f"{fun.today_context()}\n\nThe page owner asked for a Reel about: {topic}"
        ask = "Write exactly 3 different jokes, all about the owner's idea (different angles)."
    elif evergreen:
        context = ("This Reel may be posted on ANY day in the next week: NO trends, news, festivals, dates, "
                   "weather or day names. Only timeless relatable desi-life moments.")
        ask = "Write exactly 3 different jokes, each a timeless relatable desi-life moment (different situations)."
    else:
        searches = fun.google_trends()
        context = (f"{fun.today_context()}\n\n"
                   "What India is searching on Google today:\n" + ("\n".join(f"- {t}" for t in searches) or "(unavailable)") +
                   "\n\nToday's big headlines (use only ones nearly everyone knows):\n" +
                   "\n".join(f"- {t}" for t in g.trending_titles()[:15]))
        ask = ("Write exactly 3 different jokes: option 1 on a widely-known trend, options 2 and 3 as "
               "relatable desi-life moments (different situations).")
    user = (f"{style}\n\n{context}" +
            "\n\nTopics already used recently (don't reuse):\n" +
            ("\n".join(f"- {t}" for t in (state.get("fun_topics", []) + state.get("reel_topics", []))[-30:]) or "(none)") +
            "\n\nRecent Reels (don't repeat ideas):\n" + ("\n".join(f"- {r}" for r in state.get("recent_reels", [])[-20:]) or "(none)") +
            f"\n\n{ask}" + songs_prompt(songs))
    model = CONFIG.get("membership_model", "sonnet")
    option = CLIP_OPTION if kind in CLIP_KINDS else MEME_OPTION
    options = fun.claude_json(system, user, options_schema(option, "Exactly 3 different jokes"), model,
                              purpose="reel writing")["options"][:3]
    for o in options:
        o["caption"] = unescape(o["caption"])
        if kind in CLIP_KINDS:
            o["text"] = unescape(o["text"])
        else:
            o["punch"] = unescape(o["punch"])
    if kind in CLIP_KINDS:
        text_of = lambda o: f"TAG THAT FRIEND: {o['text']}  [video: {o['clip_wanted']}]"
    else:
        text_of = lambda o: f"{' '.join(o['setup'])} -> {o['punch']}  [video: {o['clip_wanted']}]"
    best = fun.judge_best(options, text_of, model)
    print(f"  reel joke based on: {best['based_on']}")
    roman_only(best, ["caption", "pin_comment"])
    best["songs"] = checked_songs(best.get("songs", []), songs)
    return best


def trending_songs() -> dict[str, list[tuple[str, str]]]:
    """Today's trending Hindi and English songs on JioSaavn (free, no key): {lang: [(title, artist)]}."""
    import html
    import requests
    out = {}
    for lang in ("hindi", "english"):
        try:
            r = requests.get("https://www.jiosaavn.com/api.php", headers=UA, timeout=20, params={
                "__call": "content.getTrending", "entity_type": "song", "entity_language": lang,
                "_format": "json", "_marker": "0", "api_version": "4", "ctx": "web6dot0"})
            items = [s for s in r.json() if s.get("type") == "song"]
        except Exception as e:
            print(f"  ! trending {lang} songs failed: {e}")
            items = []
        out[lang] = [(html.unescape(s["title"]),
                      ", ".join(a["name"] for a in s.get("more_info", {}).get("artistMap", {}).get("primary_artists", [])[:2])
                      or html.unescape(s.get("subtitle", "")).split(" - ")[0])
                     for s in items][:25]
    return out


def songs_prompt(songs: dict) -> str:
    if not any(songs.values()):
        return ""
    lists = "\n".join(f"{lang.title()}:\n" + "\n".join(f"- {t} | {a}" for t, a in songs[lang]) for lang in songs if songs[lang])
    return ("\n\nsongs: the owner adds music in Instagram. Suggest 2 Hindi + 2 English songs from these trending "
            "lists that suit the mood (funny, dramatic, chill, hype...). Copy title and artist exactly; no other songs.\n"
            + lists)


def checked_songs(picked: list[dict], songs: dict) -> list[dict]:
    """Keep only songs that are really on today's lists (max 2 per language), with the list's spelling."""
    out, seen = [], set()
    for s in picked:
        lang = s.get("lang", "")
        match = next(((t, a) for t, a in songs.get(lang, []) if t.lower() == s.get("title", "").strip().lower()), None)
        if match and match[0] not in seen and sum(o["lang"] == lang for o in out) < 2:
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


def pick_clip(post: dict, state: dict) -> str:
    """Search Mixkit; the AI ranks the clips whose titles fit the joke; the first one under the
    Free licence is used (the Restricted licence forbids business social media). Returns the clip id."""
    import fun
    import status
    status.step("Finding a Free-licence clip", 4)
    used = state.get("reel_clips", [])
    known = state.setdefault("clip_licences", {})
    found: dict[str, str] = {}
    queries = list(dict.fromkeys(q.strip() for q in post["clip_search"] if q.strip()))
    if post.get("punch"):   # meme Reels land on a reaction: common reaction clips as backup
        queries += ["shocked", "surprised", "laughing", "panic", "facepalm"]
    for q in queries:
        for cid, title in mixkit_search(q):
            if cid not in used and known.get(cid, "Free") == "Free":
                found.setdefault(cid, title)
        if len(found) >= 60:
            break
    if not found:
        raise RuntimeError(f"no Mixkit clips for {post['clip_search']}")
    listing = "\n".join(f"{cid}: {title}" for cid, title in list(found.items())[:60])
    schema = {"type": "object", "properties": {"ranked": {"type": "array", "description": "Best 12 clips, best first",
              "items": {"type": "object", "properties": {"id": {"type": "string"}, "fits": {"type": "integer"}},
                        "required": ["id", "fits"], "additionalProperties": False}}},
              "required": ["ranked"], "additionalProperties": False}
    ranked = fun.claude_json("You pick stock video clips for funny Instagram Reels. Rank by title the 12 clips that "
                             "best show the wanted moment; fits = 1-10.",
                             f"Joke: {post.get('text') or ' '.join(post.get('setup', [])) + ' -> ' + post.get('punch', '')}\n"
                             f"Wanted clip: {post['clip_wanted']}\n\nClips (id: title):\n{listing}",
                             schema, CONFIG.get("membership_model", "sonnet"), purpose="clip pick")["ranked"]
    for r in ranked[:12]:   # many good clips are Restricted: keep going until a Free one fits
        if r["id"] not in found or r["fits"] < 5:
            continue
        lic = clip_license(r["id"], known)
        if lic == "Free":
            print(f"  clip {r['id']}: {found[r['id']]} (fits {r['fits']}/10, Free licence)")
            return r["id"]
        print(f"  skipped clip {r['id']} ({found[r['id']]}): licence '{lic or 'unknown'}'")
    raise RuntimeError("no fitting clip under the Free licence")


def download_clip(cid: str, folder: str) -> tuple[str, float]:
    """Mixkit clip (720p, else 1080p/360p) -> (path, seconds)."""
    import re
    import requests
    path = os.path.join(folder, f"{cid}.mp4")
    for res in (720, 1080, 360):
        r = requests.get(f"https://assets.mixkit.co/videos/{cid}/{cid}-{res}.mp4", headers=UA, timeout=90)
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
    g.save_post(feed, state, cover, post_id, {"kind": "reel", "media": "reel", "video": name, **entry})
    import status
    status.done(f"Ready: {entry['headline'][:90]}", post=post_id)


def build_joke_reel(kind: str, state: dict, tmp: str, evergreen: bool = False,
                    topic: str = "") -> tuple[Image.Image, str, dict]:
    """Write, judge, find a clip, render and proofread a joke Reel.
    Returns (cover, mp4 path in tmp, feed fields); also notes the clip/topic in state."""
    import generate as g
    import proofread
    import status
    status.step("Writing 3 jokes", 2)
    post = write_joke(kind, state, evergreen, topic)
    status.step(f"Judge picked: {post['based_on'][:80]}", 3)
    cid = pick_clip(post, state)
    clip, secs = download_clip(cid, tmp)
    out = os.path.join(tmp, "reel.mp4")
    songs = post.pop("songs", [])   # kept out of the proofreader's text
    if kind in CLIP_KINDS:
        dur = min(CLIP_SECONDS, secs - 0.2)
        start = max(0.0, min(1.0, secs - dur - 0.2))
        render = lambda d: clip_reel(clip, start, dur, "TAG THAT FRIEND", d["text"], d["highlight"], out)
    else:
        dur = min(MEME_SECONDS, secs - 0.2)
        start = max(0.0, secs - dur - 0.2)      # reactions usually come late in a clip
        render = lambda d: meme_reel(clip, start, dur, d["setup"], d["punch"], round(dur * 0.55, 2), d["highlight"], out)
    status.step("Making the video + proofreading", 5)
    frames, post, proof = proofread.run(render, post, g.fun_caption)
    text = post["text"] if kind in CLIP_KINDS else f"{' '.join(post['setup'])} {post['punch']}"
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


NEWS_SYSTEM = """You make the evening news REEL for {page}, an Indian Instagram page for 18-34s (Hinglish, \
English letters only, never Devanagari). From today's stories pick the 3 best for a short video made of news \
photos: strong visuals (people, sports moments, celebrities, viral scenes, big events) and a story everyone \
will want to watch and share. Rank them, best first.
Rules: real facts only, no invented quotes. Never make jokes about deaths, accidents, crimes or disasters. \
Politics: supportive of the right wing (BJP, NDA), critical of the left and opposition; never pick a story \
that embarrasses the BJP. No hate against any community."""


def make_news_reel(state: dict, feed: list, today: str, tmp: str) -> None:
    from datetime import datetime, timedelta, timezone
    import fun
    import status
    status.step("Picking today's most visual story", 2)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=14)
    used = state.get("reel_news_used", [])
    stories = [p for p in feed if p.get("kind") == "news" and p.get("source_url") and p["id"] not in used
               and datetime.fromisoformat(p["created_at"]) >= cutoff][:25]
    if not stories:
        raise RuntimeError("no news posts from today to make a Reel from")
    listing = "\n".join(f"{n}. [{p['tag']}] {p['headline']}" for n, p in enumerate(stories, 1))
    songs = trending_songs()
    options = fun.claude_json(NEWS_SYSTEM.format(page=CONFIG["page_name"]),
                              f"{fun.today_context()}\n\nToday's stories:\n{listing}\n\nGive your top 3 picks."
                              + songs_prompt(songs),
                              options_schema(NEWS_OPTION, "Top 3 picks, best first"),
                              CONFIG.get("membership_model", "sonnet"), purpose="reel news pick")["options"]
    for opt in options:
        if not 1 <= opt["pick"] <= len(stories):
            continue
        story = stories[opt["pick"] - 1]
        item = {"link": story["source_url"], "source": story["source"], "title": story["headline"]}
        print(f"  news reel: {story['headline']}")
        status.step(f"Collecting photos: {story['headline'][:70]}", 3)
        photos = news_photos(item, opt)
        if len(photos) >= 2:
            break
        print(f"  only {len(photos)} usable photo(s): trying the next story")
    else:
        raise RuntimeError("no story had 2+ good photos")
    cover, out, entry = finish_news_reel(item, opt, photos, songs, tmp)
    state["reel_news_used"] = (used + [story["id"]])[-60:]
    save_reel(feed, state, cover, out, f"reel_news-{today}", {**entry, "headline": story["headline"]})


def news_photos(item: dict, opt: dict) -> list[tuple]:
    """Story photos checked like carousel photos, each with a short story line: [(img, credit, text, whole)]."""
    import carousel
    picked, _ = carousel.collect(item, {"headline": item["title"], "photo_query": opt["photo_query"],
                                        "wiki_title": opt["wiki_title"]}, [], want=4, max_words=14)
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
    images = [(img, whole) for img, _, _, whole in photos]
    data = {"post": opt, "slides": [{"n": i, "credit": c, "text": t} for i, (_, c, t, _) in enumerate(photos)]}
    out = os.path.join(tmp, "reel.mp4")

    def render(d):
        return news_reel([(images[s["n"]][0], s["credit"], s["text"], images[s["n"]][1]) for s in d["slides"]],
                         d["post"]["end_text"], d["post"]["highlight"], d["post"]["tag"], out)

    def drop(d, numbers):   # proofreader flagged weak photos; keep at least 2
        keep = [s for i, s in enumerate(d["slides"], 1) if i not in numbers]
        return {**d, "slides": keep} if 2 <= len(keep) < len(d["slides"]) else None

    if article is None:
        article = carousel.article_text(carousel.page_html(item["link"]))
    caption_of = (lambda d: g.full_caption(d["post"], item)) if item["link"] else \
        (lambda d: f"{d['post']['caption'].strip()}\n\nFollow {CONFIG['handle']} for daily updates.\n\n"
                   + " ".join(t if t.startswith("#") else f"#{t}" for t in d["post"]["hashtags"]))
    frames, data, proof = proofread.run(render, data, caption_of, article, drop=drop)
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
    style = "news" if article else fun.claude_json(
        "Decide what kind of Instagram Reel fits the page owner's request.", f"Request: {about.strip()}",
        TOPIC_STYLE, CONFIG.get("membership_model", "sonnet"), purpose="reel style")["style"]
    print(f"  Reel style: {style}")
    status.step({"news": "News photo slideshow", "tag_friend": "Tag that friend clip",
                 "meme": "Meme"}[style] + " Reel", 3)
    if style != "news":
        return build_joke_reel("reel_clip" if style == "tag_friend" else "reel_meme", state, tmp, topic=about.strip())
    songs = trending_songs()
    schema = {**NEWS_OPTION, "properties": {k: v for k, v in NEWS_OPTION["properties"].items() if k != "pick"},
              "required": [k for k in NEWS_OPTION["required"] if k != "pick"]}
    story = (f"Headline: {article['title']}\nSite: {article['site']}\n{article.get('description', '')}"
             if article else f"The owner's news: {text}")
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

def satire_reel(clip: str, start: float, clip_dur: float, claim: list[str], facts: list[str], source: str,
                highlight: list[str], out_mp4: str) -> list[Image.Image]:
    """Meme part: the party's claim/action over a reaction clip; then a REALITY CHECK card where the
    facts pop in one by one; then a 'send this' ending. Returns frames to proofread: [claim, facts]."""
    highlights = {norm(w) for phrase in highlight for w in phrase.split()}
    clip_w, clip_h = RW, int(RW * 9 / 16)
    clip_y, text_bottom = 1000, 960
    c_lines, c_fnt, c_lh = fit_lines("\n".join(claim), RIGHT - LEFT, 520, sizes=range(84, 44, -4))
    c_imgs = line_images(c_lines, c_fnt, c_lh, "#111111", RED, highlights)
    fact_blocks = []
    for f in facts:
        lines, fnt, lh = fit_lines(f, RIGHT - LEFT - 70, 300, sizes=range(58, 36, -2))
        nums = {norm(w) for line in lines for w in line if any(ch.isdigit() for ch in w)}
        fact_blocks.append((line_images(lines, fnt, lh, "#FFFFFF", YELLOW, highlights | nums), lh))
    head = pill("REALITY CHECK", font("Anton-Regular.ttf", 96), YELLOW, "#111111", pad=(40, 14))
    cta = pill("SEND THIS TO THAT ONE FRIEND", font("Poppins-Bold.ttf", 40), RED, "#FFFFFF", pad=(30, 16), arrow=True)
    logo = logo_img()
    per_fact = 2.4
    total = clip_dur + per_fact * len(facts) + 1.8
    enc, checks, frame = Encoder(out_mp4), [], None
    shots = clip_frames(clip, start, clip_dur, clip_w, clip_h)
    for i in range(int(total * FPS)):
        t = i / FPS
        if t < clip_dur:
            shot = next(shots, None)
            frame = Image.new("RGBA", (RW, RH), "#FFFFFF")
            if logo:
                frame.alpha_composite(logo, ((RW - logo.width) // 2, 150))
            y0 = text_bottom - len(c_imgs) * c_lh
            for j, img in enumerate(c_imgs):
                paste_pop(frame, img, LEFT, y0 + j * c_lh, (t - 0.2 - j * 0.3) / 0.3)
            if shot is not None:
                frame.paste(shot, (0, clip_y))
            handle_tag(frame, clip_y + clip_h + 70, fill="#111111", stroke=0)
            if len(checks) == 0 and t >= clip_dur - 0.2:
                checks.append(frame.convert("RGB"))
        else:
            lt = t - clip_dur
            frame = Image.new("RGBA", (RW, RH), "#111111")
            if logo:
                frame.alpha_composite(logo, (60, 150))
            k = ease_out_back(lt / 0.4)
            frame.alpha_composite(head, ((RW - head.width) // 2, int(300 - 200 * (1 - k))))
            y = 300 + head.height + 70
            d = ImageDraw.Draw(frame)
            for j, (imgs, lh) in enumerate(fact_blocks):
                p = (lt - 0.4 - j * per_fact) / 0.3
                if p > 0:
                    d.ellipse((LEFT, y + lh // 2 - 14, LEFT + 28, y + lh // 2 + 14), fill=YELLOW)
                for n, img in enumerate(imgs):
                    paste_pop(frame, img, LEFT + 60, y + n * lh, p)
                y += len(imgs) * lh + 40
            d.text((LEFT, min(y + 10, 1500)), f"Source: {source}", font=font("Poppins-SemiBold.ttf", 28), fill="#9a9a9a")
            k = ease_out((lt - per_fact * len(facts)) / 0.4)
            if k > 0:
                frame.alpha_composite(cta, ((RW - cta.width) // 2, int(1560 + 120 * (1 - k))))
            handle_tag(frame, 1730)
        enc.add(frame)
    enc.close()
    checks.append(frame.convert("RGB"))
    return checks


POLITICS_TARGETS = "Congress, AAP, the Left parties (CPI, CPI-M) and the Cockroach Janta Party (CJP)"
POLITICS_SEARCHES = ["Congress party", "Rahul Gandhi", "AAP Kejriwal", "CPI(M)", "Cockroach Janta Party"]

POLITICS_OPTION = {"type": "object", "properties": {
    "based_on": {"type": "string", "description": "The party and its action this joke is about"},
    "party": {"type": "string"},
    "claim": {"type": "array", "items": {"type": "string"}, "description":
              "1-3 short lines (max 22 words total) shown over the clip: what the party said or did, with a "
              "funny dig, e.g. [\"Congress:\", \"'Hum hi asli garibon ki party hain'\"]. Quote only if the article "
              "quotes it, else paraphrase. No emojis."},
    "facts": {"type": "array", "items": {"type": "string"}, "description":
              "REALITY CHECK: 2-3 facts (max 16 words each, Hinglish) that show why they are wrong or hypocritical. "
              "From the article, or certain, widely reported public record (election results, official data). "
              "No invented numbers or quotes. No emojis."},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-3 words copied exactly from claim/facts"},
    "clip_search": {"type": "array", "items": {"type": "string"},
                    "description": "3 searches for a REACTION stock clip (laughing, facepalm, shocked, eye roll...)"},
    "clip_wanted": {"type": "string"},
    "caption": {"type": "string"}, "pin_comment": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5, include #reelsindia"},
    "songs": SONGS},
    "required": ["based_on", "party", "claim", "facts", "highlight", "clip_search", "clip_wanted", "caption",
                 "pin_comment", "hashtags", "songs"], "additionalProperties": False}

POLITICS_SYSTEM = """You write the daily political satire Reel for {page}, an Indian Hinglish page for 18-34s \
(English letters only, never Devanagari). The page is pro right-wing (BJP, NDA) and mocks the opposition: \
{targets}. Take the story about their LATEST action or statement, make a sharp, funny dig, then a REALITY CHECK \
with real facts that show why they are wrong or hypocritical. Make it something people SEND to friends.
Hard rules: real facts only (from the article, or certain and widely reported public record); quotes only if the \
article has them; attribute claims ("as per ..."). Mock their politics and actions only: never religion, caste, \
region, community, family, looks or health; no slurs, no abuse, no calls to violence; nothing that embarrasses the BJP.
Make it instantly clear: tie the latest action to a running joke about them that young Indians already know \
(e.g. blaming EVMs after losing, "Sheesh Mahal", free-everything promises, foreign trips before results, \
U-turns), so the Reel alone is enough to get it. Short and punchy beats clever and long.
Caption: 2-4 short Hinglish lines, 1-3 emojis, last line makes people comment or send it."""

SATIRE_JUDGE = ("You are a 22-year-old Indian who follows politics memes on Instagram. Rate each political satire "
                "Reel 1-10 for: understandable (you get the dig from the Reel alone, knowing the usual news), funny "
                "(would you send it to your group), fresh (not a tired old line). Be strict.")


def make_politics_reel(state: dict, feed: list, today: str, tmp: str) -> None:
    """🎬 6 PM: a dig at the opposition's latest action + a REALITY CHECK with real facts."""
    from datetime import datetime, timedelta, timezone
    import carousel
    import create_post
    import fun
    import generate as g
    import proofread
    import status
    status.step("Finding the opposition's latest news", 2)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    stories = [{"title": p["headline"], "url": p["source_url"], "site": p["source"]} for p in feed
               if p.get("kind") == "news" and p.get("source_url") and "POLITIC" in p.get("tag", "").upper()
               and datetime.fromisoformat(p["created_at"]) >= cutoff]
    for q in POLITICS_SEARCHES:
        stories += [a for a in create_post.bing_articles(q, limit=5) if a.get("title")]
    used = set(state.get("reel_politics_used", []))
    stories = [s for s in {s["url"]: s for s in stories}.values() if s["url"] not in used][:40]
    if not stories:
        raise RuntimeError("no opposition news found today")
    listing = "\n".join(f"{n}. ({s['site']}) {s['title']}" for n, s in enumerate(stories, 1))
    picks = fun.claude_json(POLITICS_SYSTEM.format(page=CONFIG["page_name"], targets=POLITICS_TARGETS),
                            f"{fun.today_context()}\n\nStories:\n{listing}\n\nRank the 3 best stories about "
                            f"{POLITICS_TARGETS} for a funny reality-check Reel. Prefer BIG stories most young Indians "
                            f"already know (a stranger must get the joke without reading the article). Empty if none.",
                            {"type": "object", "properties": {"picks": {"type": "array", "items": {"type": "integer"}}},
                             "required": ["picks"], "additionalProperties": False},
                            CONFIG.get("membership_model", "sonnet"), purpose="satire pick")["picks"]
    picks = [n for n in picks if 1 <= n <= len(stories)][:3]
    if not picks:
        raise RuntimeError("no story about the opposition today")
    songs = trending_songs()
    best = None
    for n in picks:   # the jokes must pass the judge; else try the next story
        story = stories[n - 1]
        article = carousel.article_text(carousel.page_html(story["url"])) or story["title"]
        status.step(f"Writing 3 digs: {story['title'][:60]}", 3)
        options = fun.claude_json(POLITICS_SYSTEM.format(page=CONFIG["page_name"], targets=POLITICS_TARGETS),
                                  f"{fun.today_context()}\n\nStory ({story['site']}): {story['title']}\n\nArticle:\n{article[:5000]}"
                                  f"\n\nWrite exactly 3 different Reels about this story." + songs_prompt(songs),
                                  options_schema(POLITICS_OPTION, "Exactly 3 different Reels"),
                                  CONFIG.get("membership_model", "sonnet"), purpose="satire writing")["options"][:3]
        for o in options:
            o["caption"] = unescape(o["caption"])
        try:
            best = fun.judge_best(options, lambda o: " ".join(o["claim"]) + " -> REALITY CHECK: " + " | ".join(o["facts"]),
                                  CONFIG.get("membership_model", "sonnet"), judge_system=SATIRE_JUDGE)
            break
        except fun.NoGoodJoke:
            print(f"  jokes for '{story['title'][:50]}' weren't clear/funny enough: trying the next story")
    if best is None:
        raise RuntimeError("the jokes for today's opposition stories weren't clear or funny enough")
    roman_only(best, ["caption", "pin_comment"])
    picked_songs = checked_songs(best.pop("songs", []), songs)
    best["facts"] = best["facts"][:3]
    cid = pick_clip({**best, "text": " ".join(best["claim"]), "punch": " ".join(best["facts"])}, state)
    clip, secs = download_clip(cid, tmp)
    clip_dur = min(4.5, secs - 0.2)
    start = max(0.0, secs - clip_dur - 0.2)
    out = os.path.join(tmp, "reel.mp4")
    item = {"link": story["url"], "source": story["site"], "title": story["title"]}
    status.step("Making the video + fact-checking", 5)
    frames, data, proof = proofread.run(
        lambda d: satire_reel(clip, start, clip_dur, d["post"]["claim"], d["post"]["facts"], story["site"],
                              d["post"]["highlight"], out),
        {"post": best}, lambda d: g.full_caption(d["post"], item), article)
    post = data["post"]
    state["reel_clips"] = (state.get("reel_clips", []) + [cid])[-80:]
    state["reel_politics_used"] = (list(used) + [story["url"]])[-60:]
    save_reel(feed, state, frames[0], out, f"reel_politics-{today}", {
        "headline": printable(" ".join(post["claim"]))[:140], "tag": "🎬 REEL · REALITY CHECK",
        "caption": g.limit_hashtags(g.full_caption(post, item)), "source": story["site"], "source_url": story["url"],
        "pin_comment": post.get("pin_comment", ""), "songs": picked_songs, "proof": proof})


# ---------------------------------------------------------------- 5. 🙏 Bhagavad Gita Reel (📦 Ready, every night)

SAFFRON = "#FF9933"
GITA_SEARCHES = ["Krishna Arjuna chariot painting", "Bhagavad Gita Krishna Arjuna", "Gita Upadesh painting",
                 "Kurukshetra Krishna Arjuna"]
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


def pick_gita_images(state: dict, tmp: str) -> list[tuple[Image.Image, str, float]]:
    """The AI looks at the free Krishna-Arjuna images and picks the 2 most beautiful, on-topic ones
    (not used recently). Returns [(image, credit)]."""
    import generate as g
    import fun
    used = state.get("gita_images_used", [])
    cands = [c for c in commons_images(GITA_SEARCHES) if c["title"] not in used] or commons_images(GITA_SEARCHES)
    folder = os.path.join(tmp, "gita")
    os.makedirs(folder, exist_ok=True)
    import io
    import time
    import requests
    ua = {"User-Agent": "KhabarBawaalStudio/1.0 (github.com/vikashlal12345)"}   # Wikimedia blocks anonymous bursts
    loaded = []
    for c in cands[:14]:
        try:
            time.sleep(1.2)
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
        raise RuntimeError("no free Krishna-Arjuna images found on Wikimedia Commons")
    schema = {"type": "object", "properties": {"best": {"type": "array", "description": "Best first (max 2)", "items": {
              "type": "object", "properties": {"file": {"type": "string"}, "focus": {"type": "number",
              "description": "Horizontal position (0 = left, 1 = right) of Krishna and Arjuna, to centre a tall crop"}},
              "required": ["file", "focus"], "additionalProperties": False}}}, "required": ["best"], "additionalProperties": False}
    best = fun.claude_json("You choose background images for a respectful Bhagavad Gita Instagram Reel.",
                           f"Candidate images: {', '.join(n for n, _, _ in loaded)}. Open each with the Read tool. "
                           "Pick the 2 most beautiful, striking images that clearly show Lord Krishna with Arjuna "
                           "(chariot, Kurukshetra battlefield, Gita Upadesh). Reject photos of people/actors/events, "
                           "plain buildings, book covers, manuscript pages with writing, text-heavy, blurry, side-by-side "
                           "duplicated (stereo) or collage images. Colourful paintings with large figures are best. "
                           "Empty list if none fits.",
                           schema, CONFIG.get("membership_model", "sonnet"), folder=folder, purpose="gita image pick")["best"]
    chosen = [(img, c, min(1.0, max(0.0, float(b["focus"])))) for b in best for name, img, c in loaded
              if name == b["file"]][:2]
    if not chosen:
        raise RuntimeError("no suitable Krishna-Arjuna image among the free ones")
    state["gita_images_used"] = (used + [c["title"] for _, c, _ in chosen])[-20:]
    print("  Gita images: " + " | ".join(f"{c['title'][:45]} (focus {f:.2f})" for _, c, f in chosen))
    return [(img.convert("RGB"), c["credit"], f) for img, c, f in chosen]


GOLD, CREAM = "#F5C26B", "#FFF4DD"


def vfont(name: str, size: int, weight: str = "") -> ImageFont.FreeTypeFont:
    """Variable Google fonts (Playfair Display, Cinzel, Lora) at a named weight."""
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


def gita_reel(images: list[tuple[Image.Image, str, float]], verse: str, shloka: str, hook: str, lesson: str,
              real_life: str, highlight: list[str], out_mp4: str) -> list[Image.Image]:
    """🙏 Devotional Reel: the painting full screen (warm golden tone, soft dark edges, slow zoom, floating
    golden dust), elegant centred serif text that fades in line by line and stays long enough to read:
    hook → Shri Krishna kehte hain + shloka → lesson + verse → (2nd painting) Aaj ki seekh → send this.
    images = [(painting, credit, focus_x 0-1 where Krishna/Arjuna are)]. Returns frames to proofread."""
    import random

    def graded(img: Image.Image, focus: float) -> Image.Image:
        zmax = 1.12
        k = max(RW / img.width, RH / img.height) * zmax
        big = img.resize((int(img.width * k) + 2, int(img.height * k) + 2), Image.LANCZOS)
        cw, ch = int(RW * zmax), int(RH * zmax)
        x0 = int(min(max(0, big.width * focus - cw / 2), big.width - cw))
        big = big.crop((x0, (big.height - ch) // 2, x0 + cw, (big.height - ch) // 2 + ch))
        return Image.blend(big, Image.new("RGB", big.size, "#3a2000"), 0.18)    # warm golden grade

    bases = [graded(img, focus) for img, _, focus in images]
    credits = [c for _, c, _ in images]
    # Overlay: soft dark edges + dark lower part where the text sits.
    shade = Image.new("L", (RW, RH), 0)
    ImageDraw.Draw(shade).ellipse((-RW * 0.35, -RH * 0.15, RW * 1.35, RH * 1.05), fill=255)
    shade = shade.filter(ImageFilter.GaussianBlur(160)).point(lambda v: 255 - v)
    grad = Image.new("L", (1, RH))
    for y in range(RH):
        lower = 248 * min(1.0, max(0.0, (y - RH * 0.33) / (RH * 0.27)))   # text area: nearly black
        top = 170 * max(0.0, 1 - y / 360)                                 # behind "GITA GYAAN"
        grad.putpixel((0, y), int(max(lower, top)))
    alpha = Image.composite(Image.new("L", (RW, RH), 255), shade, grad.resize((RW, RH)))
    overlay = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    overlay.putalpha(alpha)

    head = glow_line("GITA  GYAAN", vfont("Cinzel-Variable.ttf", 58, "Bold"), GOLD)
    body = vfont("PlayfairDisplay-Variable.ttf", 70, "Bold")
    lesson_f = vfont("PlayfairDisplay-Variable.ttf", 56, "SemiBold")
    small_gold = vfont("Cinzel-Variable.ttf", 40, "SemiBold")
    shloka_f = vfont("Lora-Variable.ttf", 46, "Medium")
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
    hook_l = lines_of(hook, body, CREAM)
    y0 = 1330 - len(hook_l) * 50
    items = [(y0 + i * int(body.size * 1.3), im, t + i * gap) for i, im in enumerate(hook_l)]
    end = t + len(hook_l) * gap + read_time(hook)
    parts.append((items, end))
    t = end + 0.5 + pause
    items, y = [], 1060
    items.append((y, glow_line("SHRI KRISHNA KEHTE HAIN", small_gold, GOLD), t)); y += 85
    sh_lines = [x.strip() for x in printable(shloka).replace("|", "\n").split("\n") if x.strip()][:2]
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    for i, ln in enumerate(sh_lines):
        size = 46
        while size > 26 and probe.textlength(ln, font=vfont("Lora-Variable.ttf", size, "Medium")) > RW - 140:
            size -= 2
        items.append((y, glow_line(ln, vfont("Lora-Variable.ttf", size, "Medium"), "#FFE3A8"), t + 0.5 + i * gap)); y += 62
    shloka_hold = 1.8 if sh_lines else 0.5
    t2 = t + 0.5 + len(sh_lines) * gap + shloka_hold
    y += 10
    divider_y = y
    y += 30 + lesson_f.size // 2 + 20     # lesson lines are placed by their centre: start below the divider
    les = lines_of(lesson, lesson_f, CREAM)
    for i, im in enumerate(les):
        items.append((y, im, t2 + i * gap)); y += int(lesson_f.size * 1.32)
    items.append((y + 20, glow_line(f"—  Bhagavad Gita {verse}", vfont("Cinzel-Variable.ttf", 36, "SemiBold"), GOLD),
                  t2 + len(les) * gap))
    end = t2 + len(les) * gap + read_time(lesson)
    parts.append((items, end))
    divider = (divider_y, t2 - 0.2, end)
    t = end + 0.5 + pause
    switch = t - 0.2                                     # 2nd painting cross-fades in here
    items, y = [], 1200
    items.append((y, glow_line("AAJ KI SEEKH", vfont("Cinzel-Variable.ttf", 46, "Bold"), GOLD), t + 0.4)); y += 110
    take_f = vfont("PlayfairDisplay-Variable.ttf", 64, "Bold")
    for i, im in enumerate(lines_of(real_life, take_f, CREAM)):
        items.append((y, im, t + 0.8 + i * gap)); y += int(take_f.size * 1.3)
    end = t + 0.8 + len(items) * gap + read_time(real_life)
    parts.append((items, end))
    cta_at = end + 0.3
    cta = glow_line("Send this to someone who needs it", vfont("PlayfairDisplay-Variable.ttf", 48, "SemiBold"), GOLD)
    handle = glow_line(CONFIG["handle"], vfont("PlayfairDisplay-Variable.ttf", 36, "SemiBold"), "#E8D9B8")
    total = cta_at + 3.0

    random.seed(7)
    dust = [(random.uniform(0, RW), random.uniform(300, 1700), random.uniform(-14, -40), random.choice((2, 3, 4)),
             random.randint(70, 200)) for _ in range(46)]
    enc, checks, frame = Encoder(out_mp4), [], None
    check_at = [parts[0][1] - 0.2, parts[1][1] - 0.2, parts[2][1] - 0.2, total - 0.1]
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
        frame.alpha_composite(head, ((RW - head.width) // 2, 210 - head.height // 2))
        hy = 210
        d.line((250, hy, 395, hy), fill=GOLD, width=2)
        d.line((RW - 395, hy, RW - 250, hy), fill=GOLD, width=2)
        start = 0.0
        for items, end in parts:
            if start - 0.1 <= t <= end + 0.5:
                out_a = 1.0 if t <= end else 1 - (t - end) / 0.5
                for y, im, at in items:
                    a = min(1.0, max(0.0, (t - at) / 0.6)) * out_a
                    if a > 0:
                        frame.alpha_composite(faded(im, a), ((RW - im.width) // 2, int(y - im.height / 2)))
            start = end + 0.5
        dy, d0, d1 = divider
        if d0 <= t <= d1 + 0.5:
            a = min(1.0, (t - d0) / 0.6) * (1.0 if t <= d1 else 1 - (t - d1) / 0.5)
            d.line((RW // 2 - 90, dy, RW // 2 + 90, dy), fill=(245, 194, 107, int(255 * a)), width=2)
        if t >= cta_at:
            a = min(1.0, (t - cta_at) / 0.7)
            frame.alpha_composite(faded(cta, a), ((RW - cta.width) // 2, 1380 - cta.height // 2))
            frame.alpha_composite(faded(handle, a), ((RW - handle.width) // 2, 1470 - handle.height // 2))
        ImageDraw.Draw(frame).text((RW - 40, RH - 40), f"Image: {credits[n]}"[:80], font=font("Poppins-SemiBold.ttf", 20),
                                   fill="#9a8f7a", anchor="rd")
        if len(checks) < len(check_at) and t >= check_at[len(checks)]:
            checks.append(frame.convert("RGB"))
        enc.add(frame)
    enc.close()
    while len(checks) < 4:
        checks.append(frame.convert("RGB"))
    return checks


GITA_OPTION = {"type": "object", "properties": {
    "based_on": {"type": "string", "description": "The life situation or trending topic this lesson is about"},
    "verse": {"type": "string", "description": "Chapter.verse of the Bhagavad Gita, e.g. 2.47"},
    "shloka": {"type": "string", "description": "The shloka's first two half-lines in Roman letters, separated by ' | ' "
               "(max 12 words), or empty if unsure of the exact words"},
    "hook": {"type": "string", "description": "A feeling the viewer knows, max 11 words, with a natural pause '...', e.g. "
             "'Gusse mein kuch keh diya... aur baad mein pachtaye?'"},
    "lesson": {"type": "string", "description": "What Krishna says in this verse, simple poetic Hinglish, max 20 words. "
               "It is shown under 'SHRI KRISHNA KEHTE HAIN', so don't start with 'Krishna kehte hain'"},
    "real_life": {"type": "string", "description": "'Aaj ki seekh': one line people want to save, max 18 words, e.g. "
                  "'Jawab kal bhi diya ja sakta hai, shabd wapas nahi aate.'"},
    "highlight": {"type": "array", "items": {"type": "string"}, "description": "1-3 words copied exactly from the texts"},
    "caption": {"type": "string"}, "pin_comment": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5, include #bhagavadgita and #reelsindia"},
    "songs": SONGS},
    "required": ["based_on", "verse", "shloka", "hook", "lesson", "real_life", "highlight", "caption", "pin_comment",
                 "hashtags", "songs"], "additionalProperties": False}

GITA_SYSTEM = """You write a daily Bhagavad Gita Reel for {page}, an Indian Instagram page for 18-34s \
(Hinglish in English letters only, never Devanagari). Each Reel: a real-life situation young Indians face \
(exams, jobs, breakups, money, family pressure, failure, comparison, anger, overthinking) or a widely-known \
trending topic of the day, then ONE lesson Krishna gives Arjuna, then what it means in practice today.
Write it like poetry, not a lecture: short lines, warm words, natural pauses ("..."). \
Rules: the lesson must truly match the chapter.verse you cite (use well-known verses you are sure of; leave \
shloka empty if unsure of the exact words). Reverent and warm tone: never joke about Krishna or the Gita, no \
politics, no tragedies, nothing against any religion or community. Make it something people send to a friend \
who needs it. Caption: 2-4 short Hinglish lines, 1-2 emojis (🙏 fits), last line invites a comment or a send."""

GITA_JUDGE = ("You are a 22-year-old Indian who loves Gita Reels on Instagram. Rate each Reel 1-10 for: "
              "understandable (the lesson is clear from the Reel alone), funny (here: how beautiful and moving the "
              "lines are, would you save it or send it to someone), fresh (not a tired motivational line). Be strict.")


def build_gita_reel(state: dict, tmp: str) -> tuple[Image.Image, str, dict]:
    """🙏 Bhagavad Gita Reel for the 📦 Ready tab. Returns (cover, mp4 path, feed fields)."""
    import fun
    import generate as g
    import proofread
    import status
    status.step("Finding Krishna-Arjuna paintings (free licence)", 2)
    images = pick_gita_images(state, tmp)
    status.step("Writing 3 Gita lessons", 3)
    songs = trending_songs()
    trends = fun.google_trends()
    options = fun.claude_json(GITA_SYSTEM.format(page=CONFIG["page_name"]),
                              f"{fun.today_context()}\n\nWhat India is searching today (use one only if it fits respectfully):\n"
                              + "\n".join(f"- {t}" for t in trends[:15]) +
                              "\n\nLessons used recently (pick other verses/situations):\n"
                              + ("\n".join(f"- {r}" for r in state.get("recent_gita", [])[-20:]) or "(none)") +
                              "\n\nWrite exactly 3 different Reels." + songs_prompt(songs),
                              options_schema(GITA_OPTION, "Exactly 3 different Reels"),
                              CONFIG.get("membership_model", "sonnet"), purpose="gita writing")["options"][:3]
    for o in options:
        o["caption"] = unescape(o["caption"])
    best = fun.judge_best(options, lambda o: f"{o['hook']} -> Krishna: {o['lesson']} (Gita {o['verse']}) -> {o['real_life']}",
                          CONFIG.get("membership_model", "sonnet"), judge_system=GITA_JUDGE)
    roman_only(best, ["caption", "pin_comment", "hook", "lesson", "real_life", "shloka"])
    picked_songs = checked_songs(best.pop("songs", []), songs)
    out = os.path.join(tmp, "reel.mp4")
    status.step("Making the video + proofreading", 5)

    def caption_of(d):
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in d["post"]["hashtags"])
        return f"{d['post']['caption'].strip()}\n\nFollow {CONFIG['handle']} for daily Gita Gyaan 🙏\n\n{tags}"
    frames, data, proof = proofread.run(
        lambda d: gita_reel(images, d["post"]["verse"], d["post"]["shloka"], d["post"]["hook"], d["post"]["lesson"],
                            d["post"]["real_life"], d["post"]["highlight"], out),
        {"post": best}, caption_of,
        f"Bhagavad Gita {best['verse']}: check that the lesson and the shloka line truly match this verse.")
    post = data["post"]
    state["recent_gita"] = (state.get("recent_gita", []) + [f"Gita {post['verse']}: {post['based_on']}"])[-60:]
    return frames[1], out, {
        "headline": printable(f"{post['hook']} Gita {post['verse']}")[:140], "tag": "🙏 GITA GYAAN",
        "caption": g.limit_hashtags(caption_of(data)), "source": "Bhagavad Gita", "source_url": "",
        "pin_comment": post.get("pin_comment", ""), "songs": picked_songs, "proof": proof}

def make(kind: str, state: dict, feed: list) -> bool:
    """Make today's Reel for this slot; False (with the reason printed) if it couldn't be made."""
    import tempfile
    import schedule
    import status
    today = schedule.ist_now().date().isoformat()
    h, m = schedule.SPECIALS[kind]
    status.start(kind, today, f"🎬 {(h - 1) % 12 + 1}{':%02d' % m if m else ''} {'AM' if h < 12 else 'PM'} Reel", 6)
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI")):
        print("  Reels need the AI: skipped")
        status.fail("Reel not made: the AI isn't available (membership token?). A normal post goes out instead.", True)
        return False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if kind == "reel_news":
                make_news_reel(state, feed, today, tmp)
            elif kind == "reel_politics":
                make_politics_reel(state, feed, today, tmp)
            else:
                make_joke_reel(kind, state, feed, today, tmp)
        return True
    except Exception as e:
        print(f"  ! Reel not made: {str(e)[:300]}")
        status.fail(f"Reel not made: {str(e)[:200]}. A normal post goes out in this slot instead.", True)
        return False

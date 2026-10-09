"""Reels: 1080x1920 videos, no music (the owner adds a trending song in Instagram).

    make(kind, state, feed)   the robot's entry: reel_clip (7 AM), reel_meme (7 PM), reel_news (8 PM)

Three styles (each writes the MP4 and returns still frames for the proofreader):
    clip_reel(clip, start, dur, hook, text, highlight, out_mp4)
        real video clip filling the screen, outlined meme text popping in on top
    meme_reel(clip, start, dur, setup, punch, punch_at, highlight, out_mp4)
        classic meme layout: white screen, text above the clip; setup text counts up,
        punchline swaps in at punch_at seconds
    news_reel(slides, end_text, highlight, tag, out_mp4)
        news photos zooming/sliding with story lines, ending on a punchline card
        slides = [(photo, credit, text), ...]

Clips come from Mixkit (free licence, no sign-up). Text stays inside Instagram's safe
zone (Reels buttons cover the bottom and the right edge).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFilter

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

class Encoder:
    """Feed PIL frames in, get an Instagram-friendly MP4 (H.264 + silent audio track)."""

    def __init__(self, out_mp4: str):
        self.p = subprocess.Popen(
            [ffmpeg_exe(), "-y", "-loglevel", "error",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{RW}x{RH}", "-r", str(FPS), "-i", "-",
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",   # some apps reject video-only files
             "-shortest", "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
             "-profile:v", "high", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", out_mp4],
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
# 7 AM reel_clip, 7 PM reel_meme, 8 PM reel_news (schedule.py). Jokes: 3 options, the
# 'young Indian' judge picks (like fun posts), the AI picks a matching Mixkit clip by title.
# News: the AI picks today's most visual story from our own news posts; photos are collected
# and checked like carousel photos. Every Reel is proofread (still frames). If anything fails,
# make() returns False and the slot gets a regular post.

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
CLIP_SECONDS, MEME_SECONDS = 6.5, 8.0

REEL_RULES = """This joke becomes a short Instagram REEL: a free stock video clip (Mixkit) with our text on top.
The clip must be something a stock-video site has: everyday people doing everyday things (lying in bed \
with a phone, eating, laughing, shocked face, running late, working on a laptop, dancing, sleeping, cooking, \
studying, waiting...). No celebrities, brands or specific Indian places in the clip.
clip_search: 3 different searches for the stock site, 1-3 plain English words each, most specific first (e.g. ["texting in bed", "man phone bed", "lazy"]). Simple words find more clips. Prefer jokes whose moment a stock clip can actually show.
clip_wanted: one line describing the ideal clip.
caption, pin_comment and hashtags follow the same rules as fun posts (exactly 5 hashtags, include #reelsindia)."""

_COMMON = {
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
    "wiki_title": {"type": "string", "description": "English Wikipedia title of the main person/team/place, or empty"}},
    "required": ["pick", "tag", "end_text", "highlight", "caption", "pin_comment", "hashtags", "photo_query", "wiki_title"],
    "additionalProperties": False}


def options_schema(option: dict, n: str) -> dict:
    return {"type": "object", "properties": {"options": {"type": "array", "items": option, "description": n}},
            "required": ["options"], "additionalProperties": False}


def unescape(s: str) -> str:
    return s.replace("\\n", "\n").strip()


def write_joke(kind: str, state: dict) -> dict:
    import fun
    import generate as g
    system = fun.FUN_SYSTEM.format(page=CONFIG["page_name"]) + "\n\n" + fun.JOKE_RULES + "\n\n" + REEL_RULES
    style = ("Style: TAG THAT FRIEND: the text describes that one friend everyone has."
             if kind == "reel_clip" else
             "Style: classic meme Reel: a setup that builds up, then a punchline (\"Me at...\", \"Me after...\", "
             "\"POV: ...\") landing on a REACTION clip (shocked, panicking, laughing, crying, facepalm, dancing...).")
    searches = fun.google_trends()
    user = (f"{fun.today_context()}\n{style}\n\n"
            "What India is searching on Google today:\n" + ("\n".join(f"- {t}" for t in searches) or "(unavailable)") +
            "\n\nToday's big headlines (use only ones nearly everyone knows):\n" +
            "\n".join(f"- {t}" for t in g.trending_titles()[:15]) +
            "\n\nTopics already used recently (don't reuse):\n" +
            ("\n".join(f"- {t}" for t in (state.get("fun_topics", []) + state.get("reel_topics", []))[-30:]) or "(none)") +
            "\n\nRecent Reels (don't repeat ideas):\n" + ("\n".join(f"- {r}" for r in state.get("recent_reels", [])[-20:]) or "(none)") +
            "\n\nWrite exactly 3 different jokes: option 1 on a widely-known trend, options 2 and 3 as "
            "relatable desi-life moments (different situations).")
    model = CONFIG.get("membership_model", "sonnet")
    option = CLIP_OPTION if kind == "reel_clip" else MEME_OPTION
    options = fun.claude_json(system, user, options_schema(option, "Exactly 3 different jokes"), model,
                              purpose="reel writing")["options"][:3]
    for o in options:
        o["caption"] = unescape(o["caption"])
        if kind == "reel_clip":
            o["text"] = unescape(o["text"])
        else:
            o["punch"] = unescape(o["punch"])
    if kind == "reel_clip":
        text_of = lambda o: f"TAG THAT FRIEND: {o['text']}  [video: {o['clip_wanted']}]"
    else:
        text_of = lambda o: f"{' '.join(o['setup'])} -> {o['punch']}  [video: {o['clip_wanted']}]"
    best = fun.judge_best(options, text_of, model)
    print(f"  reel joke based on: {best['based_on']}")
    roman_only(best, ["caption", "pin_comment"])
    return best


def roman_only(post: dict, fields: list[str]) -> None:
    """Captions must be English (Roman) letters only: if Hindi script slipped in, the AI rewrites those fields."""
    import re
    import fun
    bad = [f for f in fields if re.search(r"[\u0900-\u097F]", post.get(f, ""))]
    if not bad:
        return
    schema = {"type": "object", "properties": {f: {"type": "string"} for f in bad},
              "required": bad, "additionalProperties": False}
    fixed = fun.claude_json("Rewrite each text exactly, but write every Hindi (Devanagari) word in English (Roman) "
                            "letters, Hinglish style. Keep emojis, line breaks and everything else unchanged.",
                            json.dumps({f: post[f] for f in bad}, ensure_ascii=False), schema,
                            CONFIG.get("membership_model", "sonnet"), purpose="roman letters")
    for f in bad:
        if not re.search(r"[\u0900-\u097F]", fixed.get(f, "")):
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
    try:
        page = requests.get(f"https://mixkit.co/free-stock-video/{slug}/", headers=UA, timeout=25).text
    except Exception as e:
        print(f"  ! Mixkit search '{query}' failed: {e}")
        return []
    out = []
    for m in re.finditer(r'<div class="item-grid-video-player.*?</div>\s*</div>', page, re.S):
        v = re.search(r"assets\.mixkit\.co/videos/(\d+)/", m.group(0))
        t = re.search(r'title="([^"]+)"|alt="([^"]+)"', m.group(0))
        if v and t:
            out.append((v.group(1), html.unescape(t.group(1) or t.group(2))))
    return out


def pick_clip(post: dict, used: list[str]) -> str:
    """Search Mixkit and let the AI pick the clip whose title fits the joke best. Returns the clip id."""
    import fun
    found: dict[str, str] = {}
    queries = list(dict.fromkeys(q.strip() for q in post["clip_search"] if q.strip()))
    if post.get("punch"):   # meme Reels land on a reaction: common reaction clips as backup
        queries += ["shocked", "surprised", "laughing", "panic", "facepalm"]
    for q in queries:
        for cid, title in mixkit_search(q):
            if cid not in used:
                found.setdefault(cid, title)
        if len(found) >= 60:
            break
    if not found:
        raise RuntimeError(f"no Mixkit clips for {post['clip_search']}")
    listing = "\n".join(f"{cid}: {title}" for cid, title in list(found.items())[:60])
    schema = {"type": "object", "properties": {"id": {"type": "string"}, "fits": {"type": "integer"}},
              "required": ["id", "fits"], "additionalProperties": False}
    pick = fun.claude_json("You pick stock video clips for funny Instagram Reels. Choose by title the clip that "
                           "best shows the wanted moment; fits = 1-10.",
                           f"Joke: {post.get('text') or ' '.join(post.get('setup', [])) + ' -> ' + post.get('punch', '')}\n"
                           f"Wanted clip: {post['clip_wanted']}\n\nClips (id: title):\n{listing}",
                           schema, CONFIG.get("membership_model", "sonnet"), purpose="clip pick")
    if pick["id"] not in found or pick["fits"] < 5:
        raise RuntimeError(f"no fitting clip (best: {found.get(pick['id'], '?')}, fits {pick['fits']})")
    print(f"  clip {pick['id']}: {found[pick['id']]} (fits {pick['fits']}/10)")
    return pick["id"]


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


def make_joke_reel(kind: str, state: dict, feed: list, today: str, tmp: str) -> None:
    import generate as g
    import proofread
    post = write_joke(kind, state)
    cid = pick_clip(post, state.get("reel_clips", []))
    clip, secs = download_clip(cid, tmp)
    out = os.path.join(tmp, "reel.mp4")
    if kind == "reel_clip":
        dur = min(CLIP_SECONDS, secs - 0.2)
        start = max(0.0, min(1.0, secs - dur - 0.2))
        render = lambda d: clip_reel(clip, start, dur, "TAG THAT FRIEND", d["text"], d["highlight"], out)
    else:
        dur = min(MEME_SECONDS, secs - 0.2)
        start = max(0.0, secs - dur - 0.2)      # reactions usually come late in a clip
        render = lambda d: meme_reel(clip, start, dur, d["setup"], d["punch"], round(dur * 0.55, 2), d["highlight"], out)
    frames, post, proof = proofread.run(render, post, g.fun_caption)
    text = post["text"] if kind == "reel_clip" else f"{' '.join(post['setup'])} {post['punch']}"
    state["reel_clips"] = (state.get("reel_clips", []) + [cid])[-80:]
    state["reel_topics"] = (state.get("reel_topics", []) + [post["based_on"]])[-60:]
    state["recent_reels"] = (state.get("recent_reels", []) + [printable(text).replace("\n", " ")])[-60:]
    save_reel(feed, state, frames[-1], out, f"{kind}-{today}", {
        "headline": printable(text).replace("\n", " "),
        "tag": "🎬 REEL · " + ("TAG THAT FRIEND" if kind == "reel_clip" else "MEME"),
        "caption": g.fun_caption(post), "source": "KhabarBawaal Original", "source_url": "",
        "pin_comment": post.get("pin_comment", ""), "proof": proof})


NEWS_SYSTEM = """You make the evening news REEL for {page}, an Indian Instagram page for 18-34s (Hinglish, \
English letters only, never Devanagari). From today's stories pick the 3 best for a short video made of news \
photos: strong visuals (people, sports moments, celebrities, viral scenes, big events) and a story everyone \
will want to watch and share. Rank them, best first.
Rules: real facts only, no invented quotes. Never make jokes about deaths, accidents, crimes or disasters. \
Politics: supportive of the right wing (BJP, NDA), critical of the left and opposition; never pick a story \
that embarrasses the BJP. No hate against any community."""


def make_news_reel(state: dict, feed: list, today: str, tmp: str) -> None:
    from datetime import datetime, timedelta, timezone
    import carousel
    import fun
    import generate as g
    import proofread
    cutoff = datetime.now(timezone.utc) - timedelta(hours=14)
    used = state.get("reel_news_used", [])
    stories = [p for p in feed if p.get("kind") == "news" and p.get("source_url") and p["id"] not in used
               and datetime.fromisoformat(p["created_at"]) >= cutoff][:25]
    if not stories:
        raise RuntimeError("no news posts from today to make a Reel from")
    listing = "\n".join(f"{n}. [{p['tag']}] {p['headline']}" for n, p in enumerate(stories, 1))
    options = fun.claude_json(NEWS_SYSTEM.format(page=CONFIG["page_name"]),
                              f"{fun.today_context()}\n\nToday's stories:\n{listing}\n\nGive your top 3 picks.",
                              options_schema(NEWS_OPTION, "Top 3 picks, best first"),
                              CONFIG.get("membership_model", "sonnet"), purpose="reel news pick")["options"]
    for opt in options:
        if not 1 <= opt["pick"] <= len(stories):
            continue
        story = stories[opt["pick"] - 1]
        item = {"link": story["source_url"], "source": story["source"], "title": story["headline"]}
        print(f"  news reel: {story['headline']}")
        picked, _ = carousel.collect(item, {"headline": story["headline"], "photo_query": opt["photo_query"],
                                            "wiki_title": opt["wiki_title"]}, [], want=4, max_words=14)
        photos = [(img, credit, text.strip(), kind == "tweet") for (kind, img, credit, _), text in picked if text.strip()]
        if len(photos) >= 2:
            break
        print(f"  only {len(photos)} usable photo(s): trying the next story")
    else:
        raise RuntimeError("no story had 2+ good photos")
    opt["caption"] = unescape(opt["caption"])
    roman_only(opt, ["caption", "pin_comment", "end_text"])
    images = [(img, whole) for img, _, _, whole in photos]
    data = {"post": opt, "slides": [{"n": i, "credit": c, "text": t} for i, (_, c, t, _) in enumerate(photos)]}
    out = os.path.join(tmp, "reel.mp4")

    def render(d):
        return news_reel([(images[s["n"]][0], s["credit"], s["text"], images[s["n"]][1]) for s in d["slides"]],
                         d["post"]["end_text"], d["post"]["highlight"], d["post"]["tag"], out)

    def drop(d, numbers):   # proofreader flagged weak photos; keep at least 2
        keep = [s for i, s in enumerate(d["slides"], 1) if i not in numbers]
        return {**d, "slides": keep} if 2 <= len(keep) < len(d["slides"]) else None

    article = carousel.article_text(carousel.page_html(item["link"]))
    frames, data, proof = proofread.run(render, data, lambda d: g.full_caption(d["post"], item), article, drop=drop)
    post = data["post"]
    state["reel_news_used"] = (used + [story["id"]])[-60:]
    save_reel(feed, state, frames[0], out, f"reel_news-{today}", {
        "headline": story["headline"], "tag": "🎬 REEL · " + post["tag"],
        "caption": g.full_caption(post, item), "source": story["source"], "source_url": story["source_url"],
        "pin_comment": post.get("pin_comment", ""), "proof": proof})


def make(kind: str, state: dict, feed: list) -> bool:
    """Make today's Reel for this slot; False (with the reason printed) if it couldn't be made."""
    import tempfile
    import schedule
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI")):
        print("  Reels need the AI: skipped")
        return False
    today = schedule.ist_now().date().isoformat()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if kind == "reel_news":
                make_news_reel(state, feed, today, tmp)
            else:
                make_joke_reel(kind, state, feed, today, tmp)
        return True
    except Exception as e:
        print(f"  ! Reel not made: {str(e)[:300]}")
        return False

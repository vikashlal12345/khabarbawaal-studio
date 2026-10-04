"""Multi-photo (carousel) slides for news posts.

Photos are collected from the article itself, X posts embedded in it (screenshot via
X's public embed + the post's photos; deleted posts are skipped), other news sites
covering the story (Bing News) and Wikipedia. Slides: photos and X screenshots on a
branded frame, a "key facts" slide when photos are scarce, and a closing slide.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

import generate as g

MAX_SLIDES = 6
SKIP_IMG = re.compile(r"logo|icon|avatar|sprite|static-content|placeholder|1x1|badge|banner-ad|\.svg|\.gif", re.I)


# ---------------------------------------------------------------- collecting

def page_html(url: str) -> str:
    try:
        return requests.get(url, headers={"User-Agent": g.UA}, timeout=20).text
    except requests.RequestException:
        return ""


def article_image_urls(url: str, page: str, limit: int = 8) -> list[str]:
    body = page
    m = re.search(r"<article\b.*?</article>", page, re.S | re.I)
    if m:
        body = m.group(0)
    found = []
    for tag in re.findall(r"<img\b[^>]*>", body, re.I):
        srcset = re.search(r'(?:data-srcset|srcset)=["\']([^"\']+)', tag)
        if srcset:  # biggest candidate in a srcset
            parts = [p.strip().split(" ") for p in srcset.group(1).split(",") if p.strip()]
            src = parts[-1][0]
        else:
            m2 = re.search(r'(?:data-src|data-original|src)=["\']([^"\']+)', tag)
            src = m2.group(1) if m2 else ""
        src = html.unescape(src)
        if src.startswith("//"):
            src = "https:" + src
        if src and not src.startswith("data:") and not SKIP_IMG.search(src):
            found.append(urljoin(url, src))
    return list(dict.fromkeys(found))[:limit]


def embedded_tweet_ids(page: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"(?:twitter|x)\.com/\w+/status/(\d+)", page)))[:2]


def tweet_data(tweet_id: str) -> dict | None:
    """Public embed data; None if the post was deleted or is unavailable."""
    try:
        j = requests.get(f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&lang=en&token=4",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15).json()
    except Exception:
        return None
    return j if "user" in j and j.get("__typename") != "TweetTombstone" else None


def chrome_binary() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return mac if os.path.exists(mac) else None


def tweet_screenshot(tweet_id: str) -> Image.Image | None:
    chrome = chrome_binary()
    if not chrome:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "t.png"
        cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
               "--force-device-scale-factor=2", "--window-size=550,1400", "--virtual-time-budget=15000",
               f"--user-data-dir={tmp}/profile", f"--screenshot={out}",
               f"https://platform.twitter.com/embed/Tweet.html?id={tweet_id}&theme=light&hideThread=true"]
        # Chrome can keep running after writing the screenshot: stop it once the file is there.
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(90):
            if out.exists() and out.stat().st_size > 0:
                time.sleep(1)
                break
            time.sleep(0.5)
        proc.kill()
        proc.wait()
        if not out.exists():
            return None
        im = Image.open(out).convert("RGB")
    box = ImageChops.difference(im, Image.new("RGB", im.size, im.getpixel((0, 0)))).getbbox()
    if not box or box[3] - box[1] < 200:  # nothing rendered
        return None
    return im.crop(box)


def good_photo(img: Image.Image | None) -> bool:
    return (img is not None and img.width >= 500 and
            ImageStat.Stat(img.convert("L").resize((64, 64))).stddev[0] >= 38)


def fingerprint(img: Image.Image) -> bytes:
    return img.convert("L").resize((8, 8)).tobytes()


class Collector:
    """Unique, decent photos (with credit) in the order they were added."""

    def __init__(self, used: list[Image.Image]):
        self.items: list[tuple[str, Image.Image, str, str]] = []  # (kind, image, credit, origin)
        self.prints = [fingerprint(u) for u in used]

    def add_photo(self, url: str, credit: str, origin: str = "news") -> None:
        if not url:
            return
        img = g.download_image(url)
        if not good_photo(img):
            return
        fp = fingerprint(img)
        if any(sum(abs(a - b) for a, b in zip(fp, s)) < 400 for s in self.prints):
            return
        self.prints.append(fp)
        self.items.append(("photo", img, credit, origin))

    def add_tweet(self, shot: Image.Image, credit: str) -> None:
        self.items.append(("tweet", shot, credit, "x"))

    def photos(self) -> int:
        return len(self.items)


def article_text(page: str, limit: int = 6000) -> str:
    """Main story text: the article's paragraphs, joined."""
    body = page
    m = re.search(r"<article\b.*?</article>", page, re.S | re.I)
    if m:
        body = m.group(0)
    paras = [html.unescape(re.sub(r"<[^>]+>", "", p)).strip() for p in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.S | re.I)]
    text = "\n".join(p for p in paras if len(p) > 60)
    return re.sub(r"\s+\n", "\n", text)[:limit]


def ai_curate(items: list, post: dict, story_text: str) -> tuple[list, list[str]] | None:
    """Claude looks at every candidate photo and the full article, keeps only real,
    on-story, non-duplicate photos, puts them in story order and writes the line shown
    on each slide, so swiping tells the whole story. Returns (slides, extra_lines) where
    slides are (item, text). None if the AI isn't available."""
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI")):
        return None
    schema = {"type": "object", "properties": {
        "slides": {"type": "array", "items": {"type": "object", "properties": {
            "file": {"type": "string"}, "text": {"type": "string"}},
            "required": ["file", "text"], "additionalProperties": False}},
        "more": {"type": "array", "items": {"type": "string"}}},
        "required": ["slides", "more"], "additionalProperties": False}
    with tempfile.TemporaryDirectory() as tmp:
        names = []
        for n, (kind, img, credit, origin) in enumerate(items, 1):
            thumb = img.copy()
            thumb.thumbnail((512, 512))
            thumb.convert("RGB").save(Path(tmp) / f"img{n}.jpg", quality=80)
            names.append(f"img{n}.jpg")
        prompt = (f'Headline: "{post["headline"]}"\n\nFull article:\n{story_text or "(not available)"}\n\n'
                  f"Candidate photos: {', '.join(names)}. Look at each with the Read tool.\n\n"
                  f"Build the swipe slides that come after the headline card:\n"
                  f"1. Keep only real photos or social-post screenshots that clearly belong to this story. Drop logos, "
                  f"placeholders, ads, unrelated people or places, photos with a big agency watermark across them "
                  f"(Getty, Reuters, AP, PTI, ANI), and near-duplicates of a photo already kept.\n"
                  f"2. Put the kept photos in the order that tells the story best.\n"
                  f"3. For each kept photo write `text`: the next part of the story in Hinglish, max 26 words, "
                  f"matching what that photo shows. Together the slides must tell the WHOLE story from the article: "
                  f"what happened, who said what (include the key quote or clarification), and the latest update. "
                  f"Facts only from the article; attribute claims.\n"
                  f"4. `more`: 0-3 further Hinglish lines (max 22 words each) for important parts of the story that "
                  f"no photo covers.")
        cmd = [os.environ.get("CLAUDE_BIN", "claude"), "-p", "--output-format", "json",
               "--model", g.CONFIG.get("membership_model", "sonnet"), "--tools", "Read", "--allowedTools", "Read",
               "--system-prompt", "You build Instagram carousels for KhabarBawaal, an Indian news page.",
               "--json-schema", json.dumps(schema)]
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=400, cwd=tmp)
            out = json.loads(proc.stdout)["structured_output"]
        except Exception as e:
            print(f"  ! photo check failed: {str(e)[:120]}")
            return None
    by_name = dict(zip(names, items))
    slides = [(by_name[sl["file"]], sl["text"].strip()) for sl in out["slides"] if sl["file"] in by_name]
    print(f"  AI curation: {len(slides)} of {len(items)} photos kept, {len(out['more'])} extra line(s)")
    return slides, [m.strip() for m in out["more"] if m.strip()]


def collect(item: dict, post: dict, used: list[Image.Image], want: int = 5) -> tuple[list, list[str]]:
    import create_post  # Bing / Wikipedia helpers

    pool = want + 4  # gather extra candidates; the AI check drops the weak ones
    c = Collector(used)
    page = page_html(item["link"])
    site = item["source"]

    # 1. X posts embedded in the article: screenshot + their photos.
    for tid in embedded_tweet_ids(page):
        data = tweet_data(tid)
        if not data:
            print(f"  X post {tid}: deleted/unavailable, skipped")
            continue
        handle = "@" + data["user"]["screen_name"]
        shot = tweet_screenshot(tid)
        if shot is not None:
            c.add_tweet(shot, handle)
            print(f"  X post {handle}: screenshot")
        for m in data.get("mediaDetails", [])[:3]:
            if m.get("type") == "photo":
                c.add_photo(m.get("media_url_https", "") + "?name=large", f"{handle} on X")

    # 2. Photos inside the article.
    for url in article_image_urls(item["link"], page):
        if c.photos() >= pool:
            break
        c.add_photo(url, site, origin="article")

    # 3. Other news sites on the same story.
    skip = urlsplit(item["link"]).netloc
    for a in create_post.bing_articles(post.get("photo_query") or item["title"], limit=8):
        if c.photos() >= pool:
            break
        if urlsplit(a["url"]).netloc != skip:
            c.add_photo(g.og_image(a["url"]), a["site"])

    # 4. Wikipedia photo of the main person/team/place.
    if c.photos() < pool and post.get("wiki_title"):
        c.add_photo(create_post.wikipedia_photo(post["wiki_title"]), "Wikimedia Commons")

    print(f"  carousel: {c.photos()} candidate(s)")
    curated = ai_curate(c.items, post, article_text(page))
    if curated is None:  # no AI: skip in-page article images (the riskiest source), no slide text
        return [(it, "") for it in c.items if it[3] != "article"][:want], []
    slides, more = curated
    return slides[:want], more


# ---------------------------------------------------------------- slides

def frame(title: str, n: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (g.W, g.H), "#0d0d0d")
    d = ImageDraw.Draw(img)
    if g.LOGO_FILE.exists():
        logo = Image.open(g.LOGO_FILE).convert("RGBA")
        logo.thumbnail((360, 82))
        img.paste(logo, (44, 44), logo)
    if title:
        d.text((g.W - 50, 86), title, font=g.font("Poppins-Bold.ttf", 28), fill=g.CONFIG["accent_color"], anchor="rm")
    d.rectangle((0, g.H - 12, g.W, g.H), fill=g.CONFIG["accent_color"])
    return img, d


def footer(d: ImageDraw.ImageDraw, credit: str, n: int, total: int) -> None:
    f = g.font("Poppins-SemiBold.ttf", 26)
    d.text((50, g.H - 58), f"{n}/{total}  ·  {g.CONFIG['handle']}", font=f, fill="white", anchor="lm")
    if credit:
        d.text((g.W - 50, g.H - 58), f"Photo: {credit}"[:48], font=g.font("Poppins-SemiBold.ttf", 22),
               fill=(190, 190, 190), anchor="rm")


def story_strip(slide: Image.Image, d: ImageDraw.ImageDraw, text: str, bottom: int) -> int:
    """Story line in a dark band above `bottom`; returns the band's top y."""
    text = g.printable(text)
    if not text:
        return bottom
    for size in (46, 42, 38, 34):
        f = g.font("Poppins-Bold.ttf", size)
        lines = g.wrap_words(text.split(), f, g.W - 130, d)
        if len(lines) <= 5:
            break
    line_h = int(size * 1.32)
    top = bottom - len(lines) * line_h - 60
    d.rectangle((0, top, g.W, bottom), fill="#0d0d0d")
    d.rectangle((50, top + 30, 58, bottom - 30), fill=g.CONFIG["accent_color"])
    y = top + 30
    for line in lines:
        d.text((80, y), " ".join(line), font=f, fill="white")
        y += line_h
    return top


def photo_slide(img: Image.Image, credit: str, text: str, n: int, total: int) -> Image.Image:
    """Whole photo, uncropped, on a blurred copy of itself, with the story line below."""
    slide, d = frame("", n)
    bottom = g.H - 100
    strip_top = story_strip(slide, d, text, bottom) if text else bottom
    area = (0, 150, g.W, strip_top)
    aw, ah = area[2] - area[0], area[3] - area[1]
    blur = g.cover(img, aw, ah).filter(ImageFilter.GaussianBlur(30))
    blur = Image.blend(blur, Image.new("RGB", blur.size, "#000000"), 0.45)
    slide.paste(blur, area[:2])
    fit = img.copy()
    fit.thumbnail((aw, ah), Image.LANCZOS)
    slide.paste(fit, (area[0] + (aw - fit.width) // 2, area[1] + (ah - fit.height) // 2))
    footer(d, credit, n, total)
    return slide


def tweet_slide(shot: Image.Image, handle: str, text: str, n: int, total: int) -> Image.Image:
    slide, d = frame("WHAT THEY POSTED ON X", n)
    bottom = g.H - 100
    strip_top = story_strip(slide, d, text, bottom) if text else bottom
    maxw, maxh = g.W - 100, strip_top - 200
    shot = shot.copy()
    shot.thumbnail((maxw, maxh), Image.LANCZOS)
    x, y = (g.W - shot.width) // 2, 165 + (maxh - shot.height) // 2
    d.rounded_rectangle((x - 8, y - 8, x + shot.width + 8, y + shot.height + 8), radius=26, fill="#ffffff")
    slide.paste(shot, (x, y))
    footer(d, handle + " on X", n, total)
    return slide


def lines_slide(title_top: str, title_bottom: str, lines: list[str], n: int, total: int) -> Image.Image:
    slide, d = frame("", n)
    d.text((60, 260), title_top, font=g.font("Anton-Regular.ttf", 96), fill="white", anchor="lm")
    d.text((60, 370), title_bottom, font=g.font("Anton-Regular.ttf", 96), fill=g.CONFIG["accent_color"], anchor="lm")
    f = g.font("Poppins-Bold.ttf", 40)
    y = 500
    for i, line in enumerate(lines[:3], 1):
        d.rounded_rectangle((60, y, 130, y + 70), radius=14, fill=g.CONFIG["tag_color"])
        d.text((95, y + 35), str(i), font=g.font("Anton-Regular.ttf", 48), fill="white", anchor="mm")
        for row in g.wrap_words(g.printable(line).split(), f, g.W - 230, d)[:4]:
            d.text((160, y + 8), " ".join(row), font=f, fill="white")
            y += 54
        y += 50
    footer(d, "", n, total)
    return slide


def closing_slide(n: int, total: int) -> Image.Image:
    slide, d = frame("", n)
    d.text((g.W / 2, 520), "Aap kya", font=g.font("Anton-Regular.ttf", 150), fill="white", anchor="mm")
    d.text((g.W / 2, 690), "sochte ho?", font=g.font("Anton-Regular.ttf", 150), fill=g.CONFIG["accent_color"], anchor="mm")
    d.rounded_rectangle((g.W / 2 - 300, 820, g.W / 2 + 300, 910), radius=45, fill=g.CONFIG["tag_color"])
    d.text((g.W / 2, 865), "COMMENT KARO", font=g.font("Poppins-Bold.ttf", 40), fill="white", anchor="mm")
    d.text((g.W / 2, 1010), f"Follow {g.CONFIG['handle']} for daily bawaal", font=g.font("Poppins-SemiBold.ttf", 34),
           fill="white", anchor="mm")
    footer(d, "", n, total)
    return slide


def mark_cover(card: Image.Image) -> Image.Image:
    """SWIPE hint on the headline card of a carousel, top right next to the logo."""
    card = card.copy()
    d = ImageDraw.Draw(card)
    f = g.font("Poppins-Bold.ttf", 30)
    label = "SWIPE  >>"
    w = d.textlength(label, font=f)
    x1, y1 = g.W - 50, 64
    d.rounded_rectangle((x1 - w - 44, y1, x1, y1 + 56), radius=28, fill=g.CONFIG["accent_color"])
    d.text((x1 - 22, y1 + 28), label, font=f, fill="#0d0d0d", anchor="rm")
    return card


def plan(item: dict, post: dict, cover_photo: Image.Image | list | None) -> list[dict]:
    """Decide slides 2..N (photos found and checked, story lines written). Done once per post;
    render() can then draw it again cheaply, e.g. after a proofreading fix.
    cover_photo: the cover's photo, or a list of photos already used, so they aren't repeated."""
    used = cover_photo if isinstance(cover_photo, list) else [cover_photo] if cover_photo is not None else []
    picked, more = collect(item, post, used, want=MAX_SLIDES - 2)
    extra = more if len(more) >= 2 else []
    if not extra and len(picked) < 2:
        extra = [f for f in post.get("key_facts", []) if f.strip()][:3]
    titles = ["Aur kya", "hua?"] if more and extra == more else ["3 cheezein jo", "jaanni chahiye"]
    specs = [{"type": kind, "img": img, "credit": credit, "text": text}
             for (kind, img, credit, _), text in picked]
    if len(extra) >= 2:
        specs.append({"type": "lines", "titles": titles, "lines": extra})
    specs.append({"type": "closing"})
    return specs


def render(specs: list[dict]) -> list[Image.Image]:
    total = 1 + len(specs)
    slides = []
    for n, sp in enumerate(specs, 2):
        if sp["type"] == "tweet":
            slides.append(tweet_slide(sp["img"], sp["credit"], sp["text"], n, total))
        elif sp["type"] == "photo":
            slides.append(photo_slide(sp["img"], sp["credit"], sp["text"], n, total))
        elif sp["type"] == "lines":
            slides.append(lines_slide(sp["titles"][0], sp["titles"][1], sp["lines"], n, total))
        else:
            slides.append(closing_slide(n, total))
    return slides


def build(item: dict, post: dict, cover_photo: Image.Image | list | None) -> list[Image.Image]:
    """Slides 2..N for a news post (slide 1 is the headline card; pass it through mark_cover)."""
    return render(plan(item, post, cover_photo))

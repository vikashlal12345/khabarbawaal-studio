"""Daily special posts: Top 5 Viral (6:15 AM), Thought of the Day (9:15 AM),
Top 5 News of the Day (9:15 PM), 5 AM overnight roundup, all in IST. Written by Claude on the membership,
with a no-AI fallback so the slot is never missed.
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFilter

import fun
import generate as g

IST = timedelta(hours=5, minutes=30)

TOP10 = {
    # Top 5 (cover + 5 stories + closing = 7 slides): Instagram's share sheet takes at most 10 photos.
    "top10_viral": {"title": ("TOP 5", "VIRAL"), "sub": "Aaj ki 5 sabse viral khabrein",
                    "tag": "🔥 TOP 5 VIRAL", "count": 5,
                    "cats": {"viral", "funny/offbeat", "entertainment", "cricket", "sports", "tech"},
                    "brief": "the 5 stories that went most VIRAL in India in the last 24 hours: internet "
                             "moments, shocking or funny stories, celebrity buzz, big sports moments"},
    "top10_day": {"title": ("TOP 5", "NEWS"), "sub": "Aaj ki 5 sabse badi khabrein",
                  "tag": "📰 TOP 5 NEWS", "count": 5,
                  "cats": None,
                  "brief": "the 5 BIGGEST news stories of today in India across politics, India news, "
                           "cricket, Bollywood, tech and viral"},
    "night_roundup": {"title": ("TOP 5", "RAAT KI KHABAR"), "sub": "Jab aap so rahe the...",
                      "tag": "🌅 RAAT KI 5 KHABREIN", "cats": None, "count": 5, "hours": 9,
                      "brief": "the 5 BIGGEST stories that happened overnight (since about 9 PM IST yesterday): "
                               "India news, world news, sports results, politics, viral"},
}

TOP10_SCHEMA = {
    "type": "object",
    "properties": {
        "stories": {"type": "array", "items": {"type": "object", "properties": {
            "pick": {"type": "integer"},
            "title": {"type": "string", "description": "Max 9 words, English, punchy"},
            "line": {"type": "string", "description": "Max 22 words, Hinglish: what happened"}},
            "required": ["pick", "title", "line"], "additionalProperties": False},
            "description": "Exactly the requested number of stories, #1 (biggest) first"},
        "caption_hook": {"type": "string", "description": "1-2 Hinglish lines opening the caption"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["stories", "caption_hook", "hashtags"],
    "additionalProperties": False,
}

THOUGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string", "description": "Max 24 words, no emojis"},
        "author": {"type": "string", "description": "Real person if it's their famous quote, else empty"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["thought", "author", "caption", "hashtags"],
    "additionalProperties": False,
}

FALLBACK_THOUGHTS = [
    ("Sapne woh nahi jo hum sote hue dekhte hain, sapne woh hain jo hume sone nahi dete.", "A.P.J. Abdul Kalam"),
    ("Utho, jaago aur tab tak mat ruko jab tak lakshya prapt na ho jaaye.", "Swami Vivekananda"),
    ("Woh badlaav bano jo tum duniya mein dekhna chahte ho.", "Mahatma Gandhi"),
    ("Agar tum suraj ki tarah chamakna chahte ho, toh pehle suraj ki tarah jalna seekho.", "A.P.J. Abdul Kalam"),
    ("Khud ko kamzor samajhna sabse bada paap hai.", "Swami Vivekananda"),
    ("Kal ki chinta chhodo, aaj ki mehnat hi kal ki kahani likhegi.", ""),
    ("Comparison band karo. Har insaan ki race alag hai aur finish line bhi.", ""),
    ("Chhote kadam bhi aage hi le jaate hain. Bas rukna mat.", ""),
    ("Jo log tumhe neeche kheenchte hain, woh already tumse neeche hain.", ""),
    ("Mehnat itni khamoshi se karo ki safalta shor macha de.", ""),
]


def ist_now() -> datetime:
    return datetime.now(timezone.utc) + IST


# ---------------------------------------------------------------- top 10

def gather(kind: str) -> list[dict]:
    cfg = TOP10[kind]
    saved = g.CONFIG["lookback_hours"], g.CONFIG["per_feed"]
    g.CONFIG["lookback_hours"], g.CONFIG["per_feed"] = cfg.get("hours", 24), 8
    try:
        items = g.fetch_candidates(set())
    finally:
        g.CONFIG["lookback_hours"], g.CONFIG["per_feed"] = saved
    if cfg["cats"]:
        items = [i for i in items if i["category"] in cfg["cats"]]
    return items[:60]


def pick_top10(kind: str, items: list[dict]) -> dict:
    lines = [f"[{n}] ({i['category']} | {i['source']}) {i['title']}\n    {i['summary'][:200]}"
             for n, i in enumerate(items, 1)]
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI"):
        try:
            count = TOP10[kind].get("count", 10)
            system = (f"You make the Top {count} carousel for {g.CONFIG['page_name']}, an Indian Instagram "
                      f"news page for Gen Z. Pick exactly {count}: {TOP10[kind]['brief']}. One slide per story, so no two picks "
                      f"about the same story. Facts only from the candidates; attribute claims. Political stories: the page's editorial line is pro right-wing (BJP, NDA, nationalist) and critical of the left and opposition (Congress, Left parties and other opposition parties). Choose political stories that show the right wing or the government positively, or the left/opposition negatively; never choose a story that embarrasses or criticises the BJP or the right wing. Tone: supportive and positive for the right wing, sharp and critical for the left/opposition. Facts only from the story, allegations attributed, no invented quotes; no hate or slurs against any religion, caste or community, and no calls to violence. Hinglish in English letters only, never Devanagari. "
                      f"Skip sad stories about deaths unless they are the day's biggest news. "
                      f"Hashtags: 8-10, include #KhabarBawaal.")
            out = fun.claude_json(system, "Candidates:\n\n" + "\n\n".join(lines), TOP10_SCHEMA,
                                  g.CONFIG.get("membership_model", "sonnet"))
            stories, used = [], set()
            for s in out["stories"]:
                if 1 <= s["pick"] <= len(items) and s["pick"] not in used:
                    used.add(s["pick"])
                    stories.append({**items[s["pick"] - 1], "slide_title": s["title"], "slide_line": s["line"]})
            if len(stories) >= min(5, count):
                print(f"Mode: {kind} (membership), {len(stories)} stories")
                return {"stories": stories[:count], "hook": out["caption_hook"], "hashtags": out["hashtags"]}
        except Exception as e:
            print(f"  ! AI failed, free mode: {e}")
    print(f"Mode: {kind} (free)")
    stories = [{**i, "slide_title": g.short_headline(i["title"]), "slide_line": i["summary"][:140]}
               for i in [x for x in items if x["image"] and x["category"] not in g.POLITICAL][:TOP10[kind].get("count", 10)]]
    return {"stories": stories, "hook": TOP10[kind]["sub"] + " 👇",
            "hashtags": ["#Top10", "#TrendingNews", "#India", "#ViralNews", "#KhabarBawaal"]}


def cover_slide(kind: str, photos: list[Image.Image | None]) -> Image.Image:
    W, H = g.W, g.H
    img = Image.new("RGB", (W, H), "#0d0d0d")
    tiles = [p for p in photos if p is not None][:6]
    for i, p in enumerate(tiles):  # photo mosaic behind the title
        x, y = (i % 2) * (W // 2), (i // 2) * (H // 3)
        img.paste(g.cover(p, W // 2, H // 3), (x, y))
    img = Image.blend(img, Image.new("RGB", (W, H), "#000000"), 0.62)
    d = ImageDraw.Draw(img)
    if g.LOGO_FILE.exists():
        logo = Image.open(g.LOGO_FILE).convert("RGBA")
        logo.thumbnail((420, 96))
        img.paste(logo, (44, 44), logo)
    big, word = TOP10[kind]["title"]
    d.text((W / 2, 520), big, font=g.font("Anton-Regular.ttf", 300), fill=g.CONFIG["accent_color"], anchor="mm")
    wf = g.font("Anton-Regular.ttf", 96)
    half = max(330, d.textlength(word, font=wf) / 2 + 40)
    d.rectangle((W / 2 - half, 690, W / 2 + half, 800), fill=g.CONFIG["tag_color"])
    d.text((W / 2, 745), word, font=wf, fill="white", anchor="mm")
    d.text((W / 2, 880), TOP10[kind]["sub"], font=g.font("Poppins-Bold.ttf", 48), fill="white", anchor="mm")
    d.text((W / 2, 950), ist_now().strftime("%d %B %Y"), font=g.font("Poppins-SemiBold.ttf", 36),
           fill=(210, 210, 210), anchor="mm")
    f = g.font("Poppins-Bold.ttf", 32)
    label = "SWIPE  >>"
    w = d.textlength(label, font=f)
    d.rounded_rectangle((W / 2 - w / 2 - 26, 1040, W / 2 + w / 2 + 26, 1100), radius=30, fill=g.CONFIG["accent_color"])
    d.text((W / 2, 1070), label, font=f, fill="#0d0d0d", anchor="mm")
    d.text((W / 2, H - 70), g.CONFIG["handle"], font=g.font("Poppins-SemiBold.ttf", 32), fill="white", anchor="mm")
    d.rectangle((0, H - 12, W, H), fill=g.CONFIG["accent_color"])
    return img


def rank_slide(rank: int, story: dict, photo: Image.Image | None, n: int, total: int) -> Image.Image:
    W, H = g.W, g.H
    img = Image.new("RGB", (W, H), "#0d0d0d")
    ph_h = 760
    if photo is not None:
        img.paste(g.cover(photo, W, ph_h), (0, 0))
    else:
        img.paste(g.branded_background(story.get("category", "").split("/")[0]).crop((0, 0, W, ph_h)), (0, 0))
    img = img.convert("RGBA")
    img.alpha_composite(g.vertical_gradient(W, 300, 0, 255), (0, ph_h - 300))
    d = ImageDraw.Draw(img)
    if g.LOGO_FILE.exists():
        logo = Image.open(g.LOGO_FILE).convert("RGBA")
        logo.thumbnail((330, 76))
        img.alpha_composite(logo, (40, 40))
    # Rank: big yellow number in a dark square.
    d.rounded_rectangle((40, ph_h - 230, 270, ph_h - 20), radius=24, fill=(13, 13, 13, 235))
    d.text((155, ph_h - 125), f"#{rank}", font=g.font("Anton-Regular.ttf", 150), fill=g.CONFIG["accent_color"], anchor="mm")
    y = ph_h + 30
    title_font = g.font("Anton-Regular.ttf", 72)
    for row in g.wrap_words(g.printable(story["slide_title"]).upper().split(), title_font, W - 100, d)[:3]:
        d.text((50, y), " ".join(row), font=title_font, fill="white")
        y += 84
    y += 16
    line_font = g.font("Poppins-SemiBold.ttf", 38)
    for row in g.wrap_words(g.printable(story["slide_line"]).split(), line_font, W - 100, d)[:4]:
        d.text((50, y), " ".join(row), font=line_font, fill=(225, 225, 225))
        y += 52
    f = g.font("Poppins-SemiBold.ttf", 24)
    d.text((50, H - 58), f"{n}/{total}  ·  {g.CONFIG['handle']}", font=f, fill="white", anchor="lm")
    d.text((W - 50, H - 58), f"Source: {story['source']}", font=g.font("Poppins-SemiBold.ttf", 22),
           fill=(180, 180, 180), anchor="rm")
    d.rectangle((0, H - 12, W, H), fill=g.CONFIG["accent_color"])
    return img.convert("RGB")


def closing_slide(kind: str, n: int, total: int) -> Image.Image:
    import carousel
    slide = carousel.closing_slide(n, total)
    d = ImageDraw.Draw(slide)
    d.rectangle((0, 330, g.W, 440), fill="#0d0d0d")
    d.text((g.W / 2, 385), "Kaunsi khabar sabse badi lagi?", font=g.font("Poppins-Bold.ttf", 46),
           fill="white", anchor="mm")
    return slide


def render_top10(kind: str, data: dict) -> list[Image.Image]:
    stories, photos = data["stories"], data["photos"]
    count = len(stories)
    total = 1 + count + 1
    slides = [cover_slide(kind, photos)]
    # Countdown: slide 2 is the lowest rank, the last story slide is #1.
    for n, idx in enumerate(range(count - 1, -1, -1), 2):
        slides.append(rank_slide(idx + 1, stories[idx], photos[idx], n, total))
    slides.append(closing_slide(kind, total, total))
    return slides


def top10_caption(data: dict) -> str:
    listing = "\n".join(f"{r + 1}. {g.printable(s['slide_title'])}" for r, s in enumerate(data["stories"]))
    # (same caption shape for the Top 10s and the 5 AM roundup)
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in data["hashtags"])
    return (f"{data['hook'].strip()}\n\n{listing}\n\nKaunsi khabar sabse badi lagi? Comment karo 👇\n"
            f"Follow {g.CONFIG['handle']} for daily updates.\n\n{tags}")


def make_top10(kind: str) -> dict | None:
    import proofread

    items = gather(kind)
    if len(items) < 5:
        print("Not enough stories for a Top 5.")
        return None
    pick = pick_top10(kind, items)
    data = {"stories": pick["stories"], "photos": [g.best_photo(s) for s in pick["stories"]],
            "hook": pick["hook"], "hashtags": pick["hashtags"]}
    article = "\n".join(f"- {s['title']}: {s['summary'][:200]}" for s in data["stories"])
    slides, data, proof = proofread.run(lambda d: render_top10(kind, d), data, top10_caption, article)
    return {"slides": slides, "caption": top10_caption(data), "tag": TOP10[kind]["tag"], "proof": proof,
            "headline": f"{TOP10[kind]['title'][0]} {TOP10[kind]['title'][1]}: {ist_now():%d %b}",
            "source": ", ".join(dict.fromkeys(s["source"] for s in data["stories"]))[:80]}


# ---------------------------------------------------------------- thought of the day

def write_thought(recent: list[str]) -> dict:
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI"):
        try:
            system = (f"You write the daily 'Thought of the Day' for {g.CONFIG['page_name']}, an Indian Instagram "
                      f"page for 18-35 year olds. Short, powerful, positive, in simple Hinglish (English letters only, never Devanagari). Either an original "
                      f"line, or a genuine famous quote by a real person (Kalam, Vivekananda, Gandhi, Tagore, Ratan "
                      f"Tata, Bhagat Singh, etc.) translated faithfully, with their name in author. Never invent a "
                      f"quote and attribute it to a real person. No emojis in thought. Caption: 2-3 Hinglish lines "
                      f"with 1-2 emojis asking people to share it. Hashtags: 6-8, include #KhabarBawaal and "
                      f"#ThoughtOfTheDay.")
            user = (f"{fun.today_context()}\nPick a different theme from these recent ones:\n"
                    + ("\n".join(f"- {r}" for r in recent) or "(none)"))
            out = fun.claude_json(system, user, THOUGHT_SCHEMA, g.CONFIG.get("membership_model", "sonnet"))
            print("Mode: thought (membership)")
            return out
        except Exception as e:
            print(f"  ! AI failed, using a classic quote: {e}")
    fresh = [q for q in FALLBACK_THOUGHTS if q[0] not in recent] or FALLBACK_THOUGHTS
    text, author = random.choice(fresh)
    print("Mode: thought (classic)")
    return {"thought": text, "author": author,
            "caption": "Aaj ki soch 💭 Kisi ek dost ko bhejo jise aaj iski zaroorat hai.",
            "hashtags": ["#ThoughtOfTheDay", "#Motivation", "#HindiQuotes", "#KhabarBawaal"]}


def thought_card(thought: str, author: str) -> Image.Image:
    W, H = g.W, g.H
    img = Image.new("RGB", (W, H), "#0d0d0d")
    d = ImageDraw.Draw(img)
    for y in range(H):  # soft dark-to-deep-brown gradient
        t = y / H
        d.line((0, y, W, y), fill=(int(13 + 30 * t), int(13 + 18 * t), int(13 + 4 * t)))
    glow = Image.new("RGB", (W, H), "#000000")
    ImageDraw.Draw(glow).ellipse((W / 2 - 420, 260, W / 2 + 420, 1080), fill="#3a2c00")
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(160)), 0.55)
    d = ImageDraw.Draw(img)
    if g.LOGO_FILE.exists():
        logo = Image.open(g.LOGO_FILE).convert("RGBA")
        logo.thumbnail((400, 92))
        img.paste(logo, (int(W / 2 - logo.width / 2), 60), logo)
    d.text((W / 2, 230), "THOUGHT OF THE DAY", font=g.font("Poppins-Bold.ttf", 40),
           fill=g.CONFIG["accent_color"], anchor="mm")
    d.rectangle((W / 2 - 80, 275, W / 2 + 80, 281), fill=g.CONFIG["tag_color"])
    d.text((W / 2, 400), "“", font=g.font("Anton-Regular.ttf", 260), fill=g.CONFIG["accent_color"], anchor="mm")
    words = g.printable(thought).split()
    for size in range(72, 40, -4):
        f = g.font("Poppins-Bold.ttf", size)
        rows = g.wrap_words(words, f, W - 180, d)
        line_h = int(size * 1.35)
        if len(rows) * line_h <= 520:
            break
    y = 760 - len(rows) * line_h / 2
    for row in rows:
        d.text((W / 2, y), " ".join(row), font=f, fill="white", anchor="ma")
        y += line_h
    if author:
        d.text((W / 2, y + 50), f"— {g.printable(author)}", font=g.font("Poppins-SemiBold.ttf", 40),
               fill=g.CONFIG["accent_color"], anchor="ma")
    d.text((W / 2, H - 120), ist_now().strftime("%A, %d %B %Y"), font=g.font("Poppins-SemiBold.ttf", 30),
           fill=(200, 200, 200), anchor="mm")
    d.text((W / 2, H - 70), g.CONFIG["handle"], font=g.font("Poppins-SemiBold.ttf", 32), fill="white", anchor="mm")
    d.rectangle((0, H - 12, W, H), fill=g.CONFIG["accent_color"])
    return img


def thought_caption(out: dict) -> str:
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in out["hashtags"])
    return f"{out['caption'].strip()}\n\nFollow {g.CONFIG['handle']} for daily updates.\n\n{tags}"


def make_thought(state: dict) -> dict:
    import proofread

    out = write_thought(state.get("recent_thoughts", [])[-30:])
    slides, out, proof = proofread.run(lambda d: [thought_card(d["thought"], d["author"])], out, thought_caption)
    state["recent_thoughts"] = (state.get("recent_thoughts", []) + [out["thought"]])[-60:]
    return {"slides": slides, "caption": thought_caption(out), "proof": proof,
            "tag": "💭 THOUGHT OF THE DAY", "headline": g.printable(out["thought"]),
            "source": out["author"] or g.CONFIG["page_name"]}

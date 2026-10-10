"""🙏 Bhagwan ka saath: a 📦 Ready picture post made every night at 3 AM (owner, 11 Oct 2026; style like
@radhe.krishna.prem_): a god beside a young Indian in a relatable moment, painted by the free FLUX demo
(reel.flux_images: 3 tries, Claude checks them), with one heartfelt Hinglish line in handwriting (Kalam) in the
sky at the top (bottom only if the top is very bright). FLUX busy or no picture fits -> no post that night.
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import fun
import generate as g
import reel

W, H = 1080, 1350
CREAM, GOLD = (255, 241, 214), (245, 194, 107)
STYLE = ("Detailed hand-drawn storybook illustration with fine ink linework, cinematic warm golden glow, night or "
         "dusk, rich dark shadows, reverent and emotional Indian devotional art. Large calm dark empty sky in the "
         "upper third (text goes there), the figures in the lower two-thirds. ")

OPTION = {"type": "object", "properties": {
    "based_on": {"type": "string", "description": "The relatable moment, e.g. 'walking home alone after a job rejection'"},
    "deity": {"type": "string", "description": "e.g. Krishna, Mahadev, Hanuman ji, Ganesh ji, Maa Durga, Ram ji"},
    "address": {"type": "string", "description": "How the line starts, talking to the god, 1-2 words with a comma, "
                "e.g. 'Kanha,' 'Mahadev,' 'Bappa,' 'Maa,' 'Bajrangbali,'"},
    "line": {"type": "string", "description": "The rest of the line, first person, max 22 words, simple words from "
             "the heart, like a quiet prayer, e.g. 'pata nahi aap mujhe kahan le ja rahe ho, par mujhe aap par poora "
             "bharosa hai.'"},
    "scene": {"type": "string", "description": "The picture: the deity gently beside a young Indian of today in this "
              "moment (e.g. walking ahead on a dark forest path, the boy holding the end of his yellow shawl, glowing "
              "footprints). " + reel.SCENE_HELP},
    "caption": {"type": "string", "description": "2-3 short Hinglish lines, 1-2 emojis (🙏 fits), ends with "
                "'Comment karo Jai ...' or 'Send karo usko jise aaj ye sunna hai'"},
    "pin_comment": {"type": "string", "description": "Pinned first comment, max 15 words"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "Exactly 5, high reach (e.g. #radhekrishna "
                 "#mahadev #bhakti #explorepage #reelsindia)"}},
    "required": ["based_on", "deity", "address", "line", "scene", "caption", "pin_comment", "hashtags"],
    "additionalProperties": False}

SYSTEM = """You make a daily devotional picture post for {page}, an Indian Instagram page for 18-34s (Hinglish \
in English letters only, never Devanagari). Each post: a god (Krishna, Mahadev, Hanuman ji, Ganesh ji, Maa Durga, \
Ram ji...) quietly beside a young Indian of today in a moment they know (exam night, job rejection, walking home \
alone, first salary, hospital corridor, missing home, heartbreak, a new city), and ONE line the young person says to \
the god, simple and from the heart, the kind people save and send. Reverent and warm: never joke about any god, no \
politics, nothing against any religion or community, nothing scary or sad without hope."""

JUDGE = ("You are a 22-year-old Indian who saves devotional posts on Instagram. Rate each post 1-10 for: "
         "understandable (the moment and the line are instantly clear), funny (here: how moving it is, would you "
         "save it or send it to someone), fresh (not a tired forward). Be strict.")


def fnt(size: int) -> ImageFont.FreeTypeFont:
    return g.font("Kalam-Regular.ttf", size)


def wrap(text: str, f, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        test = f"{cur} {word}".strip()
        if cur and f.getlength(test) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = test
    return lines + [cur] if cur else lines


def card(img: Image.Image, address: str, line: str) -> Image.Image:
    """The picture with the line in handwriting on its darker part (top or bottom), soft shadow behind it."""
    base = img.convert("RGB").resize((W, H), Image.LANCZOS)
    grey = base.convert("L")
    top = sum(grey.crop((0, 60, W, 480)).getdata()) / (W * 420)
    bottom = sum(grey.crop((0, 860, W, 1280)).getdata()) / (W * 420)
    big, body = fnt(70), fnt(48)
    rows = [(address, big, GOLD)] + [(t, body, CREAM) for t in wrap(line, body, 900)]
    block_h = sum(f.size + 22 for _, f, _ in rows)
    # In the sky at the top (FLUX is asked to leave it empty); at the bottom only if the top is very bright.
    y = 120 if top <= 100 or top <= bottom else H - 110 - block_h
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d, sd = ImageDraw.Draw(layer), ImageDraw.Draw(shadow)
    for text, f, color in rows:
        x = (W - f.getlength(text)) / 2
        sd.text((x, y), text, font=f, fill=(0, 0, 0, 235))
        d.text((x, y), text, font=f, fill=color)
        y += f.size + 22
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    out = base.convert("RGBA")
    out.alpha_composite(shadow)
    out.alpha_composite(shadow)
    out.alpha_composite(layer)
    d = ImageDraw.Draw(out)
    d.text((W / 2, H - 42), g.CONFIG["handle"], font=g.font("Poppins-SemiBold.ttf", 26), fill=(*CREAM, 210), anchor="mm")
    d.text((W - 24, H - 18), "AI illustration", font=g.font("Poppins-SemiBold.ttf", 16), fill=(200, 190, 170, 150),
           anchor="rd")
    return out.convert("RGB")


def build(state: dict, tmp: str) -> tuple[Image.Image, dict]:
    """Write 3 posts, judge, paint the best one, proofread. Returns (picture, feed fields)."""
    import proofread
    import schedule
    import status
    model = g.CONFIG.get("membership_model", "sonnet")
    status.step("Writing 3 moments + lines", 2)
    subj = reel.devotion_subject(schedule.ist_now().date())
    options = fun.claude_json(SYSTEM.format(page=g.CONFIG["page_name"]),
                              f"{fun.today_context()}\nToday's festival / god of the day: {subj['name']} (use it if it's "
                              "a big festival or Navratri, else pick any god people love in this style).\n"
                              "Posts made recently (pick a different god and moment):\n"
                              + ("\n".join(f"- {r}" for r in state.get("recent_bhakti", [])[-14:]) or "(none)")
                              + "\n\nWrite exactly 3 different posts.",
                              reel.options_schema(OPTION, "Exactly 3 different posts"), model,
                              purpose="bhakti writing")["options"][:3]
    best = fun.judge_best(options, lambda o: f"[{o['deity']}] {o['address']} {o['line']}", model, judge_system=JUDGE)
    reel.roman_only(best, ["caption", "pin_comment", "address", "line"])
    for k in ("address", "line"):
        best[k] = reel.plain_letters(g.printable(best[k]))
    scene = best.pop("scene")   # kept out of the proofreader's text
    status.step(f"Painting {best['deity']} with AI (FLUX)", 3)
    img = reel.flux_images([scene], f"{best['deity']} with a young Indian of today", tmp, tries=3, style=STYLE,
                           size=(1088, 1360))[0][0]
    status.step("Writing the line + proofreading", 4)

    def caption_of(d):
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in d["post"]["hashtags"])
        return f"{d['post']['caption'].strip()}\n\nFollow {g.CONFIG['handle']} 🙏\n\n{tags}"
    pics, data, proof = proofread.run(lambda d: [card(img, d["post"]["address"], d["post"]["line"])],
                                      {"post": best}, caption_of)
    post = data["post"]
    state["recent_bhakti"] = (state.get("recent_bhakti", []) + [f"{post['deity']}: {post['based_on']}"])[-30:]
    return pics[0], {"headline": g.printable(f"{post['address']} {post['line']}")[:140],
                     "tag": "🙏 BHAGWAN KA SAATH", "caption": g.limit_hashtags(caption_of(data)),
                     "pin_comment": post.get("pin_comment", ""), "source": "KhabarBawaal Original", "source_url": "",
                     "proof": proof}

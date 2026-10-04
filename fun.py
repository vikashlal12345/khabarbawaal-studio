"""Fun posts: original, family-friendly Hinglish humour made to be forwarded.

Written by Claude on the membership (same CLI as news posts), riffing on today's
trending headlines. When that is unavailable, a fresh (under fun_bank_max_age_days)
unused post from assets/fun_bank.json is used; if none is fresh, a news post is made.
    python fun.py --bank 25   -> drop expired bank posts, add 25 new ones (uses the membership)
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
BANK_FILE = ROOT / "assets" / "fun_bank.json"

FUN_TAGS = ["TAG KARO", "RELATABLE", "DESI LIFE", "MOOD", "EXPECTATION VS REALITY",
            "THIS OR THAT", "FAMILY GROUP", "DOSTI", "OFFICE LIFE", "STUDENT LIFE"]

FUN_POST = {
    "type": "object",
    "properties": {
        "tag": {"type": "string", "enum": FUN_TAGS},
        "card_text": {"type": "string",
                      "description": "Text printed on the image. Max 30 words, no emojis, "
                                     "use \\n line breaks for structure (e.g. Expectation:/Reality:)."},
        "highlight": {"type": "array", "items": {"type": "string"},
                      "description": "1-3 words copied exactly from card_text to colour"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tag", "card_text", "highlight", "caption", "hashtags"],
    "additionalProperties": False,
}

FUN_SYSTEM = """You write original funny posts for {page}, an Indian Instagram page \
for 18-35 year olds. Each post must make people laugh and want to forward it to friends, \
siblings, cousins, office groups and family WhatsApp groups.

Style: Hinglish (Hindi in English letters mixed with simple English), short, punchy, \
instantly relatable everyday Indian life — moms, dads, siblings, dosti, padosi, \
relatives, office, college, exams, traffic, weddings, food, cricket, Monday, salary day, \
online shopping, festivals, weather. Mix the formats: "tag that friend who...", \
relatable observations, expectation vs reality, this-or-that questions, mood posts.

Freshness is everything: every post must feel new today. Never use classic or \
recycled jokes — no Santa-Banta, Pappu, husband-wife or mother-in-law jokes, no old \
WhatsApp forwards, no joke formats people have seen a hundred times.

Rules (the page must stay safe to share with family):
- Clean humour only. No jokes about religion, caste, region, skin colour, body shape, \
gender stereotypes, disabilities, politicians or real private people. No adult jokes.
- Trending topics: joke about the situation everyone is talking about, never insult a \
real person, and never joke about deaths, accidents, crimes or disasters.
- Original: do not copy famous jokes or memes word for word.
- card_text: max 30 words, NO emojis (the image font can't show them).
- caption: 2-3 short Hinglish lines with 1-3 emojis, ending with a call to tag or share \
("Tag karo us dost ko...", "Family group mein bhejo...").
- hashtags: 6-8, each starting with #, always include #KhabarBawaal."""


def claude_json(system: str, user: str, schema: dict, model: str, timeout: int = 300) -> dict:
    cmd = [os.environ.get("CLAUDE_BIN", "claude"), "-p", "--output-format", "json", "--tools", "",
           "--model", model, "--system-prompt", system, "--json-schema", json.dumps(schema)]
    proc = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=timeout)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude CLI failed: {(proc.stderr or proc.stdout)[:300]}")
    if result.get("is_error") or not result.get("structured_output"):
        raise RuntimeError(f"Claude CLI error: {str(result.get('result'))[:300]}")
    return result["structured_output"]


def normalize(post: dict) -> dict:
    """The model sometimes writes line breaks as a literal backslash-n; make them real."""
    post["card_text"] = post["card_text"].replace("\\n", "\n").strip()
    post["caption"] = post["caption"].replace("\\n", "\n").strip()
    return post


def today_context() -> str:
    ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    return f"Today in India: {ist.strftime('%A, %d %B %Y')}, around {ist.strftime('%I %p')}."


TRENDING_POST = {**FUN_POST,
                 "properties": {"based_on": {"type": "string", "description":
                                             "The exact trending headline this joke is about"},
                                **FUN_POST["properties"]},
                 "required": ["based_on"] + FUN_POST["required"]}


def write_fun_post(config: dict, recent: list[str], trending: list[str]) -> dict:
    system = FUN_SYSTEM.format(page=config["page_name"])
    tag = random.choice(FUN_TAGS)
    topics = "\n".join(f"- {t}" for t in trending) or "(none available)"
    user = (f"{today_context()}\n\n"
            f"What India is talking about right now (today's trending and viral headlines):\n{topics}\n\n"
            f"Pick the trending headline above with the most comic potential (skip anything sad or "
            f"serious) and write one post that is clearly about it: people should recognise today's "
            f"buzz the moment they read it. Put that headline in based_on. "
            f"Preferred format: {tag} (switch format if another fits the topic better).\n\n"
            f"Recent fun posts (don't repeat these ideas):\n" + ("\n".join(f"- {r}" for r in recent) or "(none)"))
    schema = TRENDING_POST if trending else FUN_POST
    post = normalize(claude_json(system, user, schema, config.get("membership_model", "sonnet")))
    if post.get("based_on"):
        print(f"  joke based on: {post['based_on']}")
    return post


def post_key(post: dict) -> str:
    import hashlib
    return hashlib.sha1(post["card_text"].strip().encode()).hexdigest()[:12]


def bank_post(used: set[str], max_age_days: int) -> dict | None:
    """An unused bank post written within max_age_days, or None (then post news instead)."""
    bank = json.loads(BANK_FILE.read_text()) if BANK_FILE.exists() else []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).date().isoformat()
    fresh = [p for p in bank if p.get("added", "") >= cutoff and post_key(p) not in used]
    return normalize(dict(random.choice(fresh))) if fresh else None


def grow_bank(count: int, config: dict) -> None:
    bank = json.loads(BANK_FILE.read_text()) if BANK_FILE.exists() else []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.get("fun_bank_max_age_days", 14))).date().isoformat()
    bank = [p for p in bank if p.get("added", "") >= cutoff]  # expired jokes go
    schema = {"type": "object", "properties": {"posts": {"type": "array", "items": FUN_POST}},
              "required": ["posts"], "additionalProperties": False}
    system = FUN_SYSTEM.format(page=config["page_name"])
    while count > 0:
        n = min(count, 25)
        existing = "\n".join(f"- {p['card_text']}" for p in bank[-60:]) or "(none)"
        user = (f"Write {n} different posts, spread across all formats ({', '.join(FUN_TAGS)}). "
                f"Avoid seasonal or date-specific jokes: these are kept in a bank and used any day.\n\n"
                f"Already in the bank (don't repeat):\n{existing}")
        posts = claude_json(system, user, schema, config.get("membership_model", "sonnet"), timeout=900)["posts"]
        today = datetime.now(timezone.utc).date().isoformat()
        bank += [dict(normalize(p), added=today) for p in posts]
        count -= len(posts)
        BANK_FILE.write_text(json.dumps(bank, ensure_ascii=False, indent=1))
        print(f"bank: {len(bank)} posts")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--bank":
        grow_bank(int(sys.argv[2]), json.loads((ROOT / "config.json").read_text()))
    else:
        print(__doc__)

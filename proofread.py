"""AI proofreader: looks at the finished slides (as images) and the caption like an editor.

Spelling/wording mistakes in our own text come back as exact find/replace fixes, which
are applied to the post's data before the slides are drawn again; anything that can't
be fixed that way (text over a face, a doubtful fact) is reported so the app can show
"⚠️ Check before posting".

    slides, data, proof = proofread.run(render, data, texts_of, article)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

import generate as g

SCHEMA = {
    "type": "object",
    "properties": {
        "fixes": {"type": "array", "items": {"type": "object", "properties": {
            "wrong": {"type": "string"}, "right": {"type": "string"}, "why": {"type": "string"},
            "kind": {"type": "string", "enum": ["spelling", "fact"]}},
            "required": ["wrong", "right", "why", "kind"], "additionalProperties": False}},
        "issues": {"type": "array", "items": {"type": "object", "properties": {
            "slide": {"type": "integer"}, "problem": {"type": "string"}},
            "required": ["slide", "problem"], "additionalProperties": False}},
    },
    "required": ["fixes", "issues"],
    "additionalProperties": False,
}

PROMPT = """You are the final proofreader for {page}, an Indian Instagram news page that writes in \
Hinglish (Hindi in English letters) and English. Look at every slide image ({files}) with the Read \
tool, then the caption and the texts below.

Check:
1. Spelling and grammar in English and Hinglish, and people's/places' names spelled correctly.
2. Broken or strange-looking letters, cut-off or overlapping text, text unreadable on its background, \
text covering someone's face.
3. Facts on the slides and caption match the article (names, numbers, quotes); allegations are \
attributed, not stated as fact.
4. Nothing offensive or defamatory.

Return:
- fixes: corrections for mistakes in OUR texts. kind = "spelling" for spelling/grammar/names, \
"fact" when you change what the text claims (numbers, who said what, what happened). `wrong` must be an EXACT substring (same case) of one \
of the texts listed below; `right` is the replacement. Only real mistakes, not style preferences. \
Hinglish spellings vary (nahi/nahin, hai/hain): don't "fix" those.
- issues: only problems a text fix can't solve (layout, photo problems, doubtful facts), with the \
slide number (1 = first image, 0 = caption). Empty if the post is fine.

Article (for fact checks):
{article}

Caption:
{caption}

Texts used on the slides:
{texts}"""


def ai_available() -> bool:
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI"))


def check(slides: list[Image.Image], caption: str, texts: list[str], article: str = "") -> dict | None:
    if not ai_available():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        names = []
        for n, slide in enumerate(slides, 1):
            im = slide.copy()
            im.thumbnail((800, 1000))
            im.convert("RGB").save(Path(tmp) / f"slide{n}.jpg", quality=85)
            names.append(f"slide{n}.jpg")
        prompt = PROMPT.format(page=g.CONFIG["page_name"], files=", ".join(names),
                               article=(article or "(not available)")[:4000], caption=caption,
                               texts="\n".join(f"- {t}" for t in texts if t and t.strip()))
        cmd = [os.environ.get("CLAUDE_BIN", "claude"), "-p", "--output-format", "json",
               "--model", g.CONFIG.get("membership_model", "sonnet"), "--tools", "Read", "--allowedTools", "Read",
               "--system-prompt", "You are a careful news proofreader.", "--json-schema", json.dumps(SCHEMA)]
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=400, cwd=tmp)
            return json.loads(proc.stdout)["structured_output"]
        except Exception as e:
            print(f"  ! proofread failed: {str(e)[:120]}")
            return None


SKIP_KEYS = {"type", "id", "link", "image", "category", "pick"}  # internal fields, not shown text


def strings(obj) -> list[str]:
    """Every text inside the post data (images and numbers ignored)."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() if k not in SKIP_KEYS for s in strings(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in strings(v)]
    return []


def fix_pattern(fixes: list[dict]):
    """One regex for all fixes, longest first, matching whole words only, so a fix is
    never applied twice or inside a longer word (Gil -> Gill must not turn Gill into Gilll)."""
    def piece(w: str) -> str:
        p = re.escape(w)
        if w[:1].isalnum():
            p = r"(?<!\w)" + p
        if w[-1:].isalnum():
            p = p + r"(?!\w)"
        return p
    ordered = sorted({f["wrong"]: f["right"] for f in fixes}.items(), key=lambda kv: -len(kv[0]))
    return re.compile("|".join(piece(w) for w, _ in ordered)), dict(ordered)


def apply_fixes(obj, fixes: list[dict]):
    if not fixes:
        return obj
    if not isinstance(fixes, tuple):
        fixes = fix_pattern(fixes)
    pattern, table = fixes
    if isinstance(obj, str):
        return pattern.sub(lambda m: table[m.group(0)], obj)
    if isinstance(obj, dict):
        return {k: (v if k in SKIP_KEYS else apply_fixes(v, fixes)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [apply_fixes(v, fixes) for v in obj]
    if isinstance(obj, tuple):
        return tuple(apply_fixes(v, fixes) for v in obj)
    return obj


def run(render, data, caption_of, article: str = "", rounds: int = 2):
    """render(data) -> slides; caption_of(data) -> caption text.
    Returns (slides, data, proof) where proof = {"state": ok|check|none, "issues": [...], "fixed": [...]}."""
    slides = render(data)
    fixed: list[str] = []
    fact_notes: list[str] = []
    for _ in range(rounds):
        result = check(slides, caption_of(data), strings(data), article)
        if result is None:
            return slides, data, {"state": "none", "issues": [], "fixed": fixed}
        texts = strings(data)
        fixes = [f for f in result["fixes"]
                 if f["wrong"] and f["wrong"] != f["right"] and any(f["wrong"] in t for t in texts)]
        if not fixes:
            issues = fact_notes + [f"Slide {i['slide']}: {i['problem']}" if i["slide"] else f"Caption: {i['problem']}"
                                   for i in result["issues"]]
            print(f"  proofread: {len(fixed)} fix(es), {len(issues)} issue(s) left")
            return slides, data, {"state": "check" if issues else "ok", "issues": issues, "fixed": fixed}
        for f in fixes:
            print(f"  proofread fix: '{f['wrong']}' -> '{f['right']}' ({f['why']})")
            fixed.append(f"{f['wrong']} → {f['right']}")
            if f.get("kind") == "fact":
                fact_notes.append(f"Fact corrected to match the article: {f['why']}")
        data = apply_fixes(data, fixes)
        slides = render(data)
    # Still fixing after the last round: show what was done, ask for a look.
    return slides, data, {"state": "check",
                          "issues": fact_notes + ["Many corrections were needed: please give it a quick read."],
                          "fixed": fixed}

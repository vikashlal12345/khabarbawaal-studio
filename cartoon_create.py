"""🎨 Cartoon Reel from ➕ Create (owner 18 Nov 2026: 'I will give the frames; if I give only context you write the lines').

The request starts with the word CARTOON. Format the owner uses:
    cartoon
    Topic: <what the Reel is about>
    Frame 1 (0-5 seconds)
    <what the picture shows>   [optional:  Line: "<exact words on the picture>"]
    Frame 2 (6-11 seconds)
    ...
    [optional  Hook: ...   Last card: ...   Follow line: ...]
Only context (no Frame lines) = the AI invents 3 frames. His `Line:` words are used word for word; frames without a line get one short Hinglish line from the AI.
The pictures are AI-painted cartoons (free painters, one person per picture, no text in the picture); the Reel is labelled AI cartoon.
"""
from __future__ import annotations

import os
import re

import generate as g

CARTOON_RE = re.compile(r"(?i)^\s*cartoon\b")


def is_cartoon(raw: str) -> bool:
    return bool(CARTOON_RE.match(raw or ""))


def parse(raw: str) -> dict:
    """{'topic', 'frames': [{'scene', 'line', 'secs'}], 'hook', 'last', 'follow', 'context'}; frames may be empty (context only)."""
    text = CARTOON_RE.sub("", raw, count=1).strip(" \n:-")
    out = {"topic": "", "frames": [], "hook": "", "last": "", "follow": "", "context": ""}
    m = re.search(r"(?im)^\s*topic\s*[:\-]\s*(.+)$", text)
    if m:
        out["topic"] = m.group(1).strip()
    for key, pat in (("hook", r"hook"), ("last", r"(?:last card|punch ?line|ending)"), ("follow", r"follow line")):
        m = re.search(rf"(?im)^\s*{pat}\s*[:\-]\s*[\"“]?(.+?)[\"”]?\s*$", text)
        if m:
            out[key] = m.group(1).strip()
    # simpler and safer: walk the lines
    frames, cur = [], None
    for ln in text.splitlines():
        h = re.match(r"(?i)^\s*frame\s*(\d+)\s*(?:\(([^)]*)\))?\s*[:\-]?\s*(.*)$", ln)
        if h:
            cur = {"scene": h.group(3).strip(), "line": "", "secs": 0}
            sm = re.search(r"(\d+)\s*[-–—]\s*(\d+)", h.group(2) or "")
            if sm:
                cur["secs"] = max(3, min(9, int(sm.group(2)) - int(sm.group(1)) + 1))
            frames.append(cur)
            continue
        if cur is None:
            continue
        lm = re.match(r"(?i)^\s*line\s*[:\-]\s*[\"“]?(.+?)[\"”]?\s*$", ln)
        if lm:
            cur["line"] = lm.group(1).strip()
        elif re.match(r"(?i)^\s*(hook|last card|punch ?line|ending|follow line|topic)\s*[:\-]", ln):
            cur = None
        elif ln.strip():
            cur["scene"] = (cur["scene"] + " " + ln.strip()).strip()
    out["frames"] = [f for f in frames if f["scene"]][:4]
    if not out["frames"]:
        out["context"] = text
    return out


SCHEMA = {"type": "object", "properties": {
    "hook": {"type": "string", "description": "4-8 words, Hinglish, the first line over picture 1"},
    "frames": {"type": "array", "items": {"type": "object", "properties": {
        "paint": {"type": "string", "description": "A painter prompt for ONE person in ONE scene, fully self-contained"},
        "line": {"type": "string", "description": "One short funny Hinglish line (max 14 words) shown on this picture"}},
        "required": ["paint", "line"], "additionalProperties": False}},
    "punchline": {"type": "string", "description": "The last card: one funny Hinglish line, max 14 words"},
    "caption": {"type": "string", "description": "Instagram caption in Hinglish (English letters only): 2-3 short lines and a question that makes people comment, no hashtags"},
    "pin_comment": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}, "description": "3-5 hashtags"}},
    "required": ["hook", "frames", "punchline", "caption", "pin_comment", "hashtags"], "additionalProperties": False}

SYSTEM = """You write a funny AI-cartoon Instagram Reel for young Indians (Hinglish in ENGLISH letters only, never Devanagari). The pictures are painted by an AI painter: ONE person per picture, a plain background, NO readable text, signs, screens with words or logos in any picture.
You get the owner's topic and either his FRAMES (what each picture shows) or only context. Rules:
- If frames are given, return exactly those frames in the same order, with the SAME scene. Rewrite each scene as a fully self-contained painter prompt for ONE person: repeat the same look for a recurring character in every frame (age, hair, clothes, build), keep his props and mood, and replace anything that needs readable words (a screen, notification, email, sign) by a plain picture of the same feeling. Never add people. If a scene has two people, paint the main one only.
- If only context is given, invent exactly 3 frames (setup, escalation, twist) with one person each.
- `line` = what is written on the picture: max 14 words, funny, natural Hinglish, from the scene; where the owner gave a line, copy it EXACTLY.
- hook: the first line over picture 1; punchline: the closing card, a clear funny last line (not a vague phrase).
- Harmless humour only: no politics, no religion, no hate, no real person's name or face (a trend like a game release is fine).
- caption: 2-3 short lines + one question people answer in the comments; hashtags 3-5, high-reach."""

TOON_STYLE_FUN = ("3D animated cartoon in the style of a funny Pixar-like Indian comedy: big-head friendly characters with very expressive faces, glossy render, bright colours, "
                  "vertical composition with the character large in the middle and a calm uncluttered background. No text, no letters, no writing, no signs with words, no watermark, no logos. ")


def build(raw: str, tmp: str, status_step=lambda t, n=0: None):
    """Returns (cover, mp4, entry) for a cartoon request. Raises RuntimeError with a plain reason when the pictures can't be made."""
    import fun
    import reel
    spec = parse(raw)
    owner_lines = [f["line"] for f in spec["frames"]]
    desc = (f"Topic: {spec['topic']}\n" if spec["topic"] else "") + (
        "FRAMES (owner's, in order):\n" + "\n".join(f"{i}. scene: {f['scene']}" + (f" | OWNER'S LINE (copy exactly): {f['line']}" if f["line"] else "") for i, f in enumerate(spec["frames"], 1))
        if spec["frames"] else f"Context only (invent 3 frames):\n{spec['context'] or spec['topic']}")
    if spec["hook"]:
        desc += f"\nOwner's hook (use exactly): {spec['hook']}"
    if spec["last"]:
        desc += f"\nOwner's last card (use exactly): {spec['last']}"
    status_step("Writing the cartoon frames and lines", 3)
    res = fun.claude_json(SYSTEM, desc, SCHEMA, g.CONFIG.get("membership_model", "sonnet"), purpose="cartoon create")
    n = len(spec["frames"]) or 3
    frames = res["frames"][:n]
    if len(frames) < 2:
        raise RuntimeError("the AI returned fewer than 2 frames")
    lines = []
    for i, fr in enumerate(frames):
        lines.append(owner_lines[i] if i < len(owner_lines) and owner_lines[i] else fr["line"])
    hook = spec["hook"] or res["hook"]
    punch = spec["last"] or res["punchline"]
    best = {"characters": [], "scenes": [{"paint": f["paint"], "line": lines[i]} for i, f in enumerate(frames)], "max_scenes": 4, "need": len(frames)}
    old_style = reel.TOON_STYLE
    reel.TOON_STYLE = TOON_STYLE_FUN
    try:
        status_step("Painting the cartoon pictures (free painters can take 10-20 minutes)", 4)
        scenes = reel.paint_toon_scenes(best, tmp)
    finally:
        reel.TOON_STYLE = old_style
    keep = list(reel.LAST_TOON_GOOD)
    durs = [spec["frames"][i]["secs"] if spec["frames"] and i < len(spec["frames"]) else 0 for i in keep]
    models = ", ".join(sorted({m for _, _, m in scenes}))
    status_step("Making the video", 5)
    out = os.path.join(tmp, "cartoon.mp4")
    import functools
    orig_frame = reel.toon_frame
    reel.toon_frame = functools.partial(orig_frame, tag="AI COMEDY · CARTOON")   # not 'SATIRE': this is the owner's own comedy cartoon
    try:
        shots = reel.toon_reel([(img, lines[i]) for (img, _, _), i in zip(scenes, keep)], hook, punch, out, None, durs)
    finally:
        reel.toon_frame = orig_frame
    handle = g.CONFIG["handle"]
    cap = res["caption"].strip() + "\n\n🎨 AI cartoon (fiction), pictures: AI illustration (" + models + ")\n\nFollow " + handle + ": roz ki khabar + memes 🔥\n\n" + " ".join(
        ("#" + h.lstrip("#").replace(" ", "")) for h in res["hashtags"][:5])
    entry = {"headline": reel.printable(hook), "tag": "🎬 REEL · 🎨 AI CARTOON", "caption": cap,
             "source": "KhabarBawaal Original", "pin_comment": res["pin_comment"], "songs": [],
             "proof": {"state": "check", "issues": ["🎨 AI cartoon (fiction): the pictures are AI-painted. Turn on Instagram's 'AI info' label when posting."], "fixed": []}}
    reel.roman_only(entry, ["caption", "pin_comment"])
    entry["caption"] = g.limit_hashtags(entry["caption"])
    return shots[0], out, entry

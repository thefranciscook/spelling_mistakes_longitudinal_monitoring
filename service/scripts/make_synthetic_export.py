"""Generate a synthetic ChatGPT export (conversations.json) for demos and end-to-end tests.

The synthetic writer makes mechanical-like slips at a constant rate and phonologically
plausible errors at a rate that increases in the second half of the period, plus realistic
noise: Hungarian messages, code, pasted text, quoted assistant replies, dictated messages,
edited (re-submitted) messages and duplicated export entries.

Usage: python scripts/make_synthetic_export.py OUT.json [--seed 1] [--days 730]
"""

from __future__ import annotations

import argparse
import json
import random
import uuid
from datetime import datetime, timedelta, timezone

# (sentence with {w}, intended word, phonological misspelling, mechanical misspelling)
TARGETS = [
    ("the last {w} was full of surprises for everyone", "century", "sentury", "centruy"),
    ("can you keep these two lists {w} in the output", "separate", "seperate", None),
    ("please check the {w} of this paragraph before i send it", "grammar", "grammer", None),
    ("the {w} of the bug is clear now that we have logs", "existence", "existance", None),
    ("it was a real {w} to work with that team last year", "privilege", "privelege", None),
    ("draw a {w} around the important part of the diagram", "circle", "sircle", None),
    ("this is a {w} problem and we should fix it soon", "serious", "sirious", None),
    ("which {w} would you recommend for a small backend", "language", "langwage", None),
    ("my {w} with this tool has been mostly positive", "experience", "experiance", None),
    ("i have a strong {w} for simple solutions here", "preference", "preferance", None),
    ("it failed {w} the config file was missing", "because", None, "becuse"),
    ("this is {w} the best option we have right now", "probably", None, "probaly"),
    ("the new {w} looks promising but expensive", "technology", None, "tecnology"),
    ("what is the maximum {w} of a message in this field", "length", None, "lenght"),
    ("add a {w} to the original paper in the footnote", "reference", None, "refrence"),
    ("i did not {w} the package they promised to send", "receive", None, None),
]
FILLER = [
    "thanks, that makes sense",
    "could you explain this in more detail",
    "i think we should try a different approach",
    "what are the main differences between these options",
    "write a short summary of the article for me",
    "how long would it take to finish this project",
    "is there a simpler way to do this",
    "let me know if anything is unclear",
    "i want to understand why this happens",
    "this looks good but the tone is too formal",
]
HUNGARIAN = ["szia, holnap megnezem es irok", "ez nagyon jo otlet, koszi szepen", "nem tudom pontosan mit jelent"]
CODE = "here is my code\n\n```python\ndef f(x):\n    return x * 2\n```"
PASTE = "Can you improve this email?\n\n“Dear team, I’m writing to confirm the schedule…”"
ASSISTANT = [
    "Sure. The key idea is that small changes compound over time, so it helps to measure them carefully.",
    "Here is a summary of the main points you asked about in your previous message.",
]


def _msg(role: str, text: str, t: float, meta: dict | None = None, ctype: str = "text") -> dict:
    return {"id": str(uuid.UUID(int=random.getrandbits(128))), "author": {"role": role}, "create_time": t,
            "content": {"content_type": ctype, "parts": [text]}, "metadata": meta or {}}


def make_export(seed: int = 1, days: int = 730, start: datetime | None = None) -> list[dict]:
    random.seed(seed)
    start = start or datetime(2024, 1, 1, 9, tzinfo=timezone.utc)
    convs = []
    for d in range(days):
        if random.random() < 0.15:
            continue
        progress = d / days
        p_b = 0.04 if progress < 0.5 else 0.04 + 0.12 * (progress - 0.5) * 2   # phonological drift
        p_a = 0.06                                                              # stable slips
        t = (start + timedelta(days=d)).timestamp()
        nodes = []
        for k in range(random.randint(3, 8)):
            t += random.randint(30, 600)
            sentences = []
            for _ in range(random.randint(1, 3)):
                tmpl, word, phon, mech = random.choice(TARGETS)
                r_phon, r_mech = random.random(), random.random()
                if phon and r_phon < p_b:
                    w = phon
                elif mech and r_mech < p_a:
                    w = mech
                else:
                    w = word
                sentences.append(tmpl.format(w=w))
                if random.random() < 0.5:
                    sentences.append(random.choice(FILLER))
            nodes.append(_msg("user", ". ".join(sentences), t))
            t += 20
            nodes.append(_msg("assistant", random.choice(ASSISTANT), t))
        roll = random.random()
        t += 60
        if roll < 0.10:
            nodes.append(_msg("user", random.choice(HUNGARIAN), t))
        elif roll < 0.15:
            nodes.append(_msg("user", CODE, t))
        elif roll < 0.20:
            nodes.append(_msg("user", PASTE, t))
        elif roll < 0.23:
            nodes.append(_msg("user", "you said: " + ASSISTANT[0].lower(), t))
        elif roll < 0.25:
            nodes.append(_msg("user", "the sentury was long", t, meta={"voice_mode_message": True}))
        elif roll < 0.27:
            # Edited message: re-submission repeating most of the previous user text.
            prev = next(n for n in reversed(nodes) if n["author"]["role"] == "user")
            nodes.append(_msg("user", prev["content"]["parts"][0] + " please", t))
        mapping = {n["id"]: {"id": n["id"], "message": n, "parent": None, "children": []} for n in nodes}
        convs.append({"title": f"Conversation {d}", "create_time": nodes[0]["create_time"],
                      "conversation_id": str(uuid.UUID(int=random.getrandbits(128))), "mapping": mapping})
    convs.extend(random.sample(convs, k=min(5, len(convs))))   # duplicated export entries
    return convs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--days", type=int, default=730)
    a = ap.parse_args()
    with open(a.out, "w") as fh:
        json.dump(make_export(a.seed, a.days), fh)
    print(f"Wrote {a.out}")

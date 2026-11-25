"""Text segmentation, structural filtering and tokenisation.

The goal is precision: anything that may not be ordinary, keyboard-typed English prose is
removed *before* spelling analysis, and every removal is counted by reason so that data
quality can be reported. Nothing here looks at whether a word is spelled correctly, so
error-free and error-containing text are filtered identically.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
MD_LINK_RE = re.compile(r"\[([^\]\n]*)\]\([^)\s]*\)")
URL_RE = re.compile(r"(?:https?://|ftp://|www\.)\S+", re.I)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
HANDLE_RE = re.compile(r"(?<!\w)[@#][\w-]+")
PATH_RE = re.compile(r"(?<!\w)(?:~|\.{1,2})?(?:/[\w.@-]+){2,}/?|\b[A-Za-z]:\\\S+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
TOKEN_STRIP = "\"'“”‘’()[]{}<>.,;:!?*_~^|=+&%$€£•…–—«»„"

# Characters that are hard to produce by ordinary typing in a browser text field on macOS
# (Chrome does not apply smart quotes) and are typical of text pasted from documents or AI
# replies. One occurrence marks the paragraph as a suspected paste.
PASTE_TYPOGRAPHY = set("“”‘’…•▪●◦→⇒✓✔✗   ")

CODE_LINE_PATTERNS = re.compile(
    r"(^\s{4,}\S)|(^\t)|[;{}]\s*$|=>|==|!=|::|</?\w+[^>]*>|^\s*(def|class|import|from|return|const|let|var|"
    r"function|public|private|SELECT|INSERT|UPDATE|#include|\$|>>>|pip |npm |git |sudo |cd )\b|\w\(.*\)\s*$|"
    r"^\s*[\w.-]+\s*[:=]\s*[\"'\[{\d]|^\s*[{\[]\s*$|^\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:",
)


@dataclass
class Token:
    text: str               # as typed (punctuation stripped)
    sentence: int           # sentence index within the paragraph
    initial: bool           # first word of its sentence

    @property
    def lower(self) -> str:
        return self.text.lower()


@dataclass
class Sentence:
    text: str
    tokens: list[Token]
    n_words: int            # word-like chunks including excluded ones (for language weighting)


@dataclass
class Paragraph:
    text: str
    sentences: list[Sentence] = field(default_factory=list)


@dataclass
class Segmented:
    paragraphs: list[Paragraph]
    # Counts of excluded *words* (approximate, whitespace-delimited) by reason.
    excluded: Counter


def _approx_words(text: str) -> int:
    return len(text.split())


def looks_like_code_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if CODE_LINE_PATTERNS.search(line):
        return True
    symbols = sum(1 for c in stripped if not (c.isalnum() or c.isspace() or c in ".,!?'\"-()"))
    return len(stripped) >= 8 and symbols / len(stripped) > 0.15


def line_exclusion_reason(line: str) -> str | None:
    s = line.strip()
    if not s:
        return None
    if s.startswith(">"):
        return "quoted"
    if s.count("|") >= 2:
        return "structured"
    if re.match(r"^#{1,6}\s", s):
        return "suspected_paste"
    if re.match(r"^(from|to|subject|cc|date|sent):\s", s, re.I):
        return "suspected_paste"
    if looks_like_code_line(line):
        return "code"
    return None


def strip_inline(text: str) -> tuple[str, Counter]:
    """Remove inline machine-readable spans, counting what was removed."""
    removed: Counter = Counter()

    def sub(pattern: re.Pattern, reason: str, s: str, repl=" ") -> str:
        def r(m):
            removed[reason] += max(1, _approx_words(m.group(0)))
            return repl if not callable(repl) else repl(m)
        return pattern.sub(r, s)

    text = sub(INLINE_CODE_RE, "code", text)
    text = MD_LINK_RE.sub(lambda m: m.group(1), text)
    text = sub(URL_RE, "url", text)
    text = sub(EMAIL_RE, "url", text)
    text = sub(PATH_RE, "identifier", text)
    text = sub(HANDLE_RE, "identifier", text)
    return text, removed


def classify_chunk(chunk: str) -> tuple[str | None, list[str]]:
    """Return (exclusion_reason, word_parts) for one whitespace-delimited chunk."""
    word = chunk.replace("’", "'").replace("‘", "'").strip(TOKEN_STRIP + "'")
    if not word:
        return "punctuation", []
    if any(c.isdigit() for c in word):
        return "identifier", []
    parts = [p for p in word.split("-") if p] if "-" in word else [word]
    out = []
    for p in parts:
        p = p.strip("'")
        if not p:
            continue
        if not all(c.isalpha() or c == "'" for c in p):
            return "identifier", []
        if len(p) >= 2 and p.isupper():
            return "acronym", []
        if any(c.isupper() for c in p[1:]):
            return "identifier", []
        if re.search(r"(.)\1\1", p.lower()):
            return "elongation", []
        if p.endswith("'s") or p.endswith("'S"):
            p = p[:-2]
        out.append(p)
    if not out:
        return "punctuation", []
    return None, out


def tokenize_sentence(text: str, excluded: Counter) -> Sentence:
    tokens: list[Token] = []
    chunks = text.split()
    for chunk in chunks:
        reason, parts = classify_chunk(chunk)
        if reason:
            if reason != "punctuation":
                excluded[reason] += 1
            continue
        for p in parts:
            tokens.append(Token(text=p, sentence=0, initial=not tokens))
    return Sentence(text=text, tokens=tokens, n_words=len(chunks))


def segment(text: str) -> Segmented:
    excluded: Counter = Counter()

    def fence(m):
        excluded["code"] += max(1, _approx_words(m.group(0)))
        return "\n\n"

    text = FENCE_RE.sub(fence, text.replace("\r\n", "\n"))
    paragraphs: list[Paragraph] = []
    for raw_para in re.split(r"\n\s*\n", text):
        if not raw_para.strip():
            continue
        if any(c in PASTE_TYPOGRAPHY for c in raw_para):
            excluded["suspected_paste"] += _approx_words(raw_para)
            continue
        kept_lines = []
        for line in raw_para.split("\n"):
            reason = line_exclusion_reason(line)
            if reason:
                excluded[reason] += _approx_words(line)
            else:
                kept_lines.append(line)
        if not kept_lines:
            continue
        para_text, removed = strip_inline("\n".join(kept_lines))
        excluded.update(removed)
        para = Paragraph(text=para_text)
        for s_text in SENTENCE_SPLIT_RE.split(para_text):
            if not s_text.strip():
                continue
            sent = tokenize_sentence(s_text.strip(), excluded)
            idx = len(para.sentences)
            for t in sent.tokens:
                t.sentence = idx
            para.sentences.append(sent)
        if para.sentences:
            paragraphs.append(para)
    return Segmented(paragraphs=paragraphs, excluded=excluded)


def word_ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z']+", text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}

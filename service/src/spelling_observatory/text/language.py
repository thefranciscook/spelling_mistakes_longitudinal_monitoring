"""Offline language identification at sentence and paragraph level.

Two signals are combined, and a sentence counts as English only if both agree:

1. lingua (offline n-gram models) restricted to English vs Hungarian. Restricting to the
   user's two languages makes short, informal sentences far more reliable than an
   open-set detector (which, e.g., labels "ok thx" as French).
2. A lexical vote over the sentence's tokens: the share that are English words (including
   shorthand and technical vocabulary) versus Hungarian words. This catches other
   languages that the binary detector would force into "English".

The operating-system keyboard layout is deliberately not used: the user types Hungarian on
an English layout. Neither signal depends much on a single misspelling, which keeps the
exclusion of error-containing sentences (a potential bias) small.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from lingua import Language, LanguageDetectorBuilder

from ..config import LanguageConfig
from .segment import Paragraph, Token


@lru_cache(maxsize=4)
def _detector(languages: tuple[str, ...]):
    langs = [getattr(Language, name) for name in languages]
    return LanguageDetectorBuilder.from_languages(*langs).with_preloaded_language_models().build()


@dataclass
class LanguageDecision:
    english_sentences: set[int]
    reason: str | None          # "non_english" | "mixed_language" | None
    excluded_words: int


class LanguageFilter:
    def __init__(self, cfg: LanguageConfig, token_language):
        """`token_language(token, initial) -> "en" | "hu" | "neutral" | "unknown"`."""
        self.cfg = cfg
        self.detector = _detector(tuple(cfg.languages))
        self.token_language = token_language

    def english_confidence(self, text: str) -> float:
        return float(self.detector.compute_language_confidence(text, Language.ENGLISH))

    def _is_english(self, text: str, tokens: list[Token]) -> bool:
        votes = [self.token_language(t.text, t.initial) for t in tokens]
        counted = [v for v in votes if v != "neutral"]
        if counted:
            en = sum(v == "en" for v in counted) / len(counted)
            hu = sum(v == "hu" for v in counted) / len(counted)
            if en < self.cfg.min_english_word_ratio or hu > self.cfg.max_hungarian_word_ratio:
                return False
        return self.english_confidence(text) >= self.cfg.min_english_confidence

    def decide(self, para: Paragraph) -> LanguageDecision:
        cfg = self.cfg
        total_words = sum(s.n_words for s in para.sentences)
        if total_words == 0:
            return LanguageDecision(set(), None, 0)
        all_tokens = [t for s in para.sentences for t in s.tokens]
        if not self._is_english(para.text, all_tokens):
            return LanguageDecision(set(), "non_english", total_words)
        english: set[int] = set()
        foreign_words = 0
        for i, sent in enumerate(para.sentences):
            if len(sent.tokens) >= cfg.sentence_min_words and not self._is_english(sent.text, sent.tokens):
                foreign_words += sent.n_words
                continue
            english.add(i)
        if foreign_words / total_words > cfg.max_foreign_fraction:
            return LanguageDecision(set(), "mixed_language", total_words)
        return LanguageDecision(english, "non_english" if foreign_words else None, foreign_words)

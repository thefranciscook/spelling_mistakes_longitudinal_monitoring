"""Word validity, frequency and exclusion decisions.

English validity is the union of SCOWL-derived Hunspell dictionaries for US, Canadian and
British English (so ``colour``/``color``, ``centre``/``center``, ``analyse``/``analyze`` are
all correct), plus the SCOWL/Google-Books frequency list shipped with SymSpell.
"""

from __future__ import annotations

import hashlib
import importlib.resources as ir
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from spylls.hunspell import Dictionary

from ..resources import ENGLISH_VARIANTS, hunspell_base, resources_present

DATA = ir.files("spelling_observatory.lexicon") / "data"


def _read_list(path) -> set[str]:
    out = set()
    for line in Path(str(path)).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line.lower())
    return out


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def symspell_dictionary_path() -> str:
    return str(ir.files("symspellpy") / "frequency_dictionary_en_82_765.txt")


@dataclass(frozen=True)
class Assessment:
    status: str        # "valid" | "excluded" | "nonword"
    reason: str = ""   # exclusion reason, when status == "excluded"
    word: str = ""     # normalised (lowercase) form
    maybe_foreign: bool = False


class Lexicon:
    def __init__(self, resources: Path, user_exclusions: set[str] | None = None, min_error_len: int = 3):
        if not resources_present(resources):
            raise RuntimeError(
                f"Linguistic resources not found in {resources}. Run `observatory setup` first."
            )
        self.english = [Dictionary.from_files(str(hunspell_base(resources, v))) for v in ENGLISH_VARIANTS]
        self.hungarian = Dictionary.from_files(str(hunspell_base(resources, "hu")))
        self.hu_stems = self._hungarian_stems(hunspell_base(resources, "hu").with_suffix(".dic"))
        self.counts: dict[str, int] = {}
        with open(symspell_dictionary_path(), encoding="utf-8") as fh:
            for line in fh:
                w, c = line.split()
                self.counts[w] = int(c)
        self.shorthand = _read_list(DATA / "shorthand.txt")
        self.informal = _read_list(DATA / "informal.txt")
        self.technical = _read_list(DATA / "technical.txt")
        self.common_proper = _read_list(DATA / "common_proper.txt")
        self.user = {w.lower() for w in (user_exclusions or set())}
        self.min_error_len = min_error_len
        self._valid_cache: dict[str, bool] = {}

    @staticmethod
    def _hungarian_stems(dic_path: Path) -> set[str]:
        stems = set()
        with open(dic_path, encoding="utf-8", errors="ignore") as fh:
            next(fh, None)
            for line in fh:
                w = line.split("/", 1)[0].strip().lower()
                if len(w) >= 3 and w.isalpha():
                    stems.add(strip_accents(w))
        return stems

    @staticmethod
    def builtin_lists_fingerprint() -> str:
        h = hashlib.sha256()
        for name in ("shorthand.txt", "informal.txt", "technical.txt", "common_proper.txt"):
            h.update((DATA / name).read_bytes())
        return h.hexdigest()[:12]

    # --- dictionary lookups -------------------------------------------------------------

    def _hunspell(self, form: str) -> bool:
        return any(d.lookup(form) for d in self.english)

    def is_common_word(self, w: str) -> bool:
        """Valid lowercase English word in any supported regional variety."""
        w = w.lower()
        cached = self._valid_cache.get(w)
        if cached is None:
            cached = w == "i" or w in self.common_proper or self._hunspell(w)
            self._valid_cache[w] = cached
        return cached

    def is_valid_candidate(self, w: str) -> bool:
        return self.is_common_word(w)

    def is_proper_name(self, w: str) -> bool:
        # Note: all-caps lookups are not used; Hunspell accepts odd compounds in ALL CAPS.
        return not self.is_common_word(w) and self._hunspell(w.capitalize())

    def count(self, w: str) -> int:
        return self.counts.get(w.lower(), 0)

    def is_hungarian(self, w: str) -> bool:
        """Exact Hungarian word form (Hunspell, with inflection)."""
        lw = w.lower()
        return bool(self.hungarian.lookup(lw) or self.hungarian.lookup(w))

    def maybe_unaccented_hungarian(self, w: str) -> bool:
        """Matches a Hungarian stem once accents are removed (e.g. "koszi" for "köszi").

        Weak evidence: used only to abstain when an English reading is not clear-cut.
        """
        return strip_accents(w.lower()) in self.hu_stems

    def is_excluded_word(self, lw: str) -> str | None:
        if lw in self.user:
            return "personal_vocabulary"
        if lw in self.shorthand:
            return "shorthand"
        if lw in self.informal:
            return "informal_contraction"
        if lw in self.technical:
            return "technical"
        return None

    def token_language(self, token: str, initial: bool) -> str:
        """Lexical language vote for one token: "en", "hu", "neutral" or "unknown"."""
        lw = token.lower()
        if self.is_excluded_word(lw) or self.is_common_word(lw) or lw in self.counts:
            return "en"
        if self.is_proper_name(lw) or (token[:1].isupper() and not initial):
            return "neutral"
        if not lw.isascii() or self.is_hungarian(lw) or self.maybe_unaccented_hungarian(lw):
            return "hu"
        return "unknown"

    # --- main decision -------------------------------------------------------------------

    @lru_cache(maxsize=200_000)
    def assess(self, token: str, initial: bool) -> Assessment:
        lw = token.lower()
        reason = self.is_excluded_word(lw)
        if reason:
            return Assessment("excluded", reason, lw)
        capitalised = token[:1].isupper()
        if self.is_common_word(lw):
            return Assessment("valid", word=lw)
        if self.is_proper_name(lw):
            return Assessment("excluded", "proper_name", lw)
        if capitalised and not initial:
            # Mid-sentence capitalised unknown word: almost always a name or product.
            return Assessment("excluded", "proper_name", lw)
        if lw in self.counts:
            # In the SCOWL/Google-Books frequency list but not in the Hunspell dictionaries
            # (e.g. "gonna"): treat as a valid, if informal, word.
            return Assessment("valid", word=lw)
        if not lw.isascii():
            return Assessment("excluded", "foreign_word", lw)
        if self.is_hungarian(lw):
            return Assessment("excluded", "foreign_word", lw)
        if len(lw) < self.min_error_len:
            return Assessment("excluded", "short_nonword", lw)
        if "'" in lw:
            # Odd apostrophe constructions are too ambiguous to judge.
            return Assessment("excluded", "ambiguous_token", lw)
        return Assessment("nonword", word=lw, maybe_foreign=self.maybe_unaccented_hungarian(lw))

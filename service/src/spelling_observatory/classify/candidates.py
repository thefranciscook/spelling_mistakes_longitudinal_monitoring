"""Intended-word candidate generation and deterministic resolution.

Candidates come from three independent channels:

* orthographic: SymSpell, Damerau-Levenshtein distance <= 2;
* phonological: dictionary words whose pronunciation equals the predicted pronunciation of
  the observed string (finds e.g. ``nite`` -> ``night`` at edit distance 3);
* segmentation: the token splits into two frequent words (``alot`` -> ``a lot``).

Ranking uses only edit distance and corpus frequency (a simple noisy-channel prior). Error
*type* features (keyboard adjacency, phonetic match) are deliberately not used to choose the
intended word, so the choice does not presuppose the category. Phonetic candidates are used
only to *add* candidates and to trigger abstention when the channels disagree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from symspellpy import SymSpell, Verbosity

from ..config import ClassifierConfig
from ..lexicon.lexicon import Lexicon, symspell_dictionary_path
from .orthography import dl_alignment
from .phonology import Phonology

SPLIT_MIN_COUNT = 5_000_000


@dataclass
class Candidate:
    word: str
    distance: int
    count: int
    sources: set[str] = field(default_factory=set)
    score: float = 0.0


@dataclass
class Resolution:
    intended: str | None
    method: str                      # "deterministic" | "pending_context" | "ambiguous_intended" | "segmentation"
    candidates: list[Candidate]


class CandidateGenerator:
    def __init__(self, lexicon: Lexicon, phonology: Phonology, cfg: ClassifierConfig):
        self.lexicon = lexicon
        self.phonology = phonology
        self.cfg = cfg
        self.symspell = SymSpell(max_dictionary_edit_distance=cfg.max_edit_distance)
        self.symspell.load_dictionary(symspell_dictionary_path(), 0, 1)

    def _ok(self, w: str) -> bool:
        return self.lexicon.is_valid_candidate(w) and self.lexicon.count(w) >= self.cfg.min_candidate_count

    def generate(self, observed: str) -> list[Candidate]:
        cands: dict[str, Candidate] = {}
        for s in self.symspell.lookup(observed, Verbosity.ALL, self.cfg.max_edit_distance):
            if s.term != observed and self._ok(s.term):
                cands[s.term] = Candidate(s.term, s.distance, self.lexicon.count(s.term), {"orth"})
        for w in self.phonology.homophones(observed)[:5]:
            if w == observed or not self._ok(w):
                continue
            if w in cands:
                cands[w].sources.add("phon")
            else:
                cands[w] = Candidate(w, len(dl_alignment(observed, w)), self.lexicon.count(w), {"phon"})
        for i in range(1, len(observed)):
            left, right = observed[:i], observed[i:]
            if (len(left) > 1 or left in ("a", "i")) and len(right) > 1 \
                    and self.lexicon.is_common_word(left) and self.lexicon.is_common_word(right) \
                    and min(self.lexicon.count(left), self.lexicon.count(right)) >= SPLIT_MIN_COUNT:
                w = f"{left} {right}"
                cands[w] = Candidate(w, 1, min(self.lexicon.count(left), self.lexicon.count(right)), {"split"})
        penalty = math.log(self.cfg.distance_penalty)
        for c in cands.values():
            c.score = math.log(c.count) - penalty * c.distance
        return sorted(cands.values(), key=lambda c: (-c.score, c.word))

    def resolve(self, observed: str) -> Resolution:
        cands = self.generate(observed)
        if not cands:
            return Resolution(None, "no_candidates", [])
        top = cands[0]
        dominant = len(cands) == 1 or (top.score - cands[1].score) >= math.log(self.cfg.dominance_ratio)
        # Channel disagreement: a frequent sound-alike word other than the orthographic winner.
        conflict = any(
            "phon" in c.sources and c.word != top.word and c.count * self.cfg.dominance_ratio >= top.count
            for c in cands
        )
        if dominant and not conflict:
            if "split" in top.sources:
                return Resolution(top.word, "segmentation", cands)
            return Resolution(top.word, "deterministic", cands)
        return Resolution(None, "pending_context", cands)

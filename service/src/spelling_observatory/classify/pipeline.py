"""End-to-end analysis of one piece of submitted or typed text.

text -> structural filtering -> language filtering -> tokenisation -> lexicon decision ->
candidate generation -> intended-word resolution (deterministic, else optional local LLM)
-> A/B/C categorisation.

Sentence context is held in memory only for the duration of a run (for LLM resolution) and
is never written to disk.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.metadata
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .. import RULES_VERSION, __version__
from ..config import Config
from ..lexicon.lexicon import Lexicon, symspell_dictionary_path
from ..resources import resources_fingerprint
from ..text.language import LanguageFilter
from ..text.segment import segment
from .candidates import CandidateGenerator
from .classifier import classify_error
from .llm import LLMUnavailable, OllamaClient, cache_key, mark_token
from .phonology import Phonology


@dataclass
class WordObs:
    observed: str
    intended: str | None
    is_error: bool
    category: str | None = None
    resolution: str = ""         # how the intended word was obtained
    reason: str = ""             # why the category was assigned
    edit_distance: int | None = None
    edit_ops: str = ""
    phon_match: bool | None = None
    candidates: list[str] = field(default_factory=list)


@dataclass
class Pending:
    word_index: int
    token: str
    context: str
    candidates: list[str]


@dataclass
class TextAnalysis:
    words: list[WordObs] = field(default_factory=list)
    excluded: Counter = field(default_factory=Counter)
    unknown: Counter = field(default_factory=Counter)
    pending: list[Pending] = field(default_factory=list)

    @property
    def n_eligible(self) -> int:
        return len(self.words)

    @property
    def n_errors(self) -> int:
        return sum(w.is_error for w in self.words)


class LLMCache(Protocol):
    def get(self, key: str) -> dict | None: ...
    def put(self, key: str, value: dict) -> None: ...


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:12]


class Analyzer:
    def __init__(self, cfg: Config, user_exclusions: set[str] | None = None, llm_client=None):
        self.cfg = cfg
        self.lexicon = Lexicon(cfg.resources_dir, user_exclusions, cfg.classifier.min_error_token_length)
        self.phonology = Phonology(self.lexicon.is_valid_candidate, self.lexicon.count)
        self.candidates = CandidateGenerator(self.lexicon, self.phonology, cfg.classifier)
        self.language = LanguageFilter(cfg.language, self.lexicon.token_language)
        self.llm = llm_client
        if self.llm is None and cfg.llm.enabled:
            self.llm = OllamaClient(cfg.llm)

    # --- versioning --------------------------------------------------------------------

    def components(self) -> dict:
        return {
            "rules_version": RULES_VERSION,
            "package_version": __version__,
            "builtin_lists": self.lexicon.builtin_lists_fingerprint(),
            "hunspell": resources_fingerprint(),
            "symspell_dictionary": _sha(symspell_dictionary_path()),
            "g2p_checkpoint": self.phonology.g2p.fingerprint,
            "cmudict": importlib.metadata.version("cmudict"),
            "lingua": importlib.metadata.version("lingua-language-detector"),
            "classifier_config": dataclasses.asdict(self.cfg.classifier),
            "language_config": dataclasses.asdict(self.cfg.language),
            "llm": self.llm.fingerprint if self.llm else "none",
        }

    def fingerprint(self) -> str:
        blob = json.dumps(self.components(), sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    # --- analysis ----------------------------------------------------------------------

    def analyze(self, text: str, skip_sentence: Callable[[str], str | None] | None = None) -> TextAnalysis:
        out = TextAnalysis()
        seg = segment(text)
        out.excluded.update(seg.excluded)
        for para in seg.paragraphs:
            decision = self.language.decide(para)
            if decision.reason:
                out.excluded[decision.reason] += decision.excluded_words
            for i, sent in enumerate(para.sentences):
                if i not in decision.english_sentences:
                    continue
                if skip_sentence:
                    reason = skip_sentence(sent.text)
                    if reason:
                        out.excluded[reason] += sent.n_words
                        continue
                for tok in sent.tokens:
                    self._analyze_token(tok.text, tok.initial, sent.text, out)
        return out

    def _analyze_token(self, token: str, initial: bool, context: str, out: TextAnalysis) -> None:
        a = self.lexicon.assess(token, initial)
        if a.status == "valid":
            out.words.append(WordObs(a.word, a.word, False))
            return
        if a.status == "excluded":
            out.excluded[a.reason] += 1
            return
        res = self.candidates.resolve(a.word)
        if a.maybe_foreign and res.method not in ("deterministic", "segmentation"):
            out.excluded["foreign_word"] += 1
            return
        if res.method == "no_candidates":
            out.excluded["unknown_token"] += 1
            out.unknown[a.word] += 1
            return
        # Top candidates by noisy-channel score, plus every sound-alike and segmentation
        # candidate: those have large edit distances and would otherwise be cut off.
        cand_words = [c.word for c in res.candidates[:8]]
        cand_words += [c.word for c in res.candidates[8:] if c.sources & {"phon", "split"}]
        obs = WordObs(a.word, None, True, candidates=cand_words[:5])
        out.words.append(obs)
        if res.method == "pending_context":
            out.pending.append(Pending(len(out.words) - 1, a.word, mark_token(context, token), cand_words))
            self._apply(obs, None, "ambiguous_intended")
        else:
            self._apply(obs, res.intended, res.method)

    def _apply(self, obs: WordObs, intended: str | None, resolution: str) -> None:
        cls = classify_error(obs.observed, intended, self.phonology, unresolved_reason=resolution)
        obs.intended = intended
        obs.category = cls.category
        obs.resolution = resolution
        obs.reason = cls.reason
        obs.edit_distance = cls.edit_distance
        obs.edit_ops = " ".join(op.code() for op in cls.ops)
        obs.phon_match = cls.phon_match

    def resolve_pending(self, analyses: list[TextAnalysis], cache: LLMCache | None = None, log=None) -> Counter:
        """Resolve ambiguous intended words with the local LLM, in a batch.

        Unresolved items keep category C / ``ambiguous_intended``. Items the model judges to
        be intentional (names, slang, ...) are removed from the eligible words.
        """
        stats: Counter = Counter()
        items = [(a, p) for a in analyses for p in a.pending]
        if not items:
            return stats
        if self.llm is None:
            stats["unresolved_no_llm"] += len(items)
            return stats

        def ask(item):
            _, p = item
            key = cache_key(self.llm.fingerprint, p.token, p.context, p.candidates)
            hit = cache.get(key) if cache is not None else None
            if hit is not None:
                return hit
            ans = self.llm.ask(p.token, p.context, p.candidates)
            value = {"intended": ans.intended, "intentional": ans.intentional}
            if cache is not None:
                cache.put(key, value)
            return value

        try:
            with ThreadPoolExecutor(max_workers=max(1, self.cfg.llm.workers)) as pool:
                answers = list(pool.map(ask, items))
        except LLMUnavailable as e:
            if log:
                log(f"Local LLM unavailable ({e}); ambiguous words stay unclassified.")
            stats["unresolved_llm_error"] += len(items)
            return stats

        drop: dict[int, set[int]] = {}
        for (analysis, p), ans in zip(items, answers):
            obs = analysis.words[p.word_index]
            if ans.get("intentional"):
                drop.setdefault(id(analysis), set()).add(p.word_index)
                analysis.excluded["llm_intentional"] += 1
                stats["llm_intentional"] += 1
            elif ans.get("intended"):
                self._apply(obs, ans["intended"], "llm_context")
                stats["llm_resolved"] += 1
            else:
                obs.resolution = "llm_abstained"
                obs.reason = "ambiguous_intended"
                stats["llm_abstained"] += 1
        for analysis in analyses:
            idxs = drop.get(id(analysis))
            if idxs:
                analysis.words = [w for i, w in enumerate(analysis.words) if i not in idxs]
            analysis.pending = []
        return stats

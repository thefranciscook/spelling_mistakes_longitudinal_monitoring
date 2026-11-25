"""Pronunciation lookup and comparison.

* Dictionary words: CMU Pronouncing Dictionary (all listed variants).
* Misspellings and unknown words: the pretrained grapheme-to-phoneme GRU from ``g2p_en``
  (Park & Kim, Apache-2.0). Only its checkpoint file is used; the ``g2p_en`` Python module
  is never imported because it downloads NLTK data at import time.

Pronunciations are compared after normalisation: lexical stress is removed and unstressed
reduced vowels (AH0, IH0, UH0, EH0) are merged into a single schwa class, because spelling
cannot be expected to encode reduced-vowel quality.
"""

from __future__ import annotations

import hashlib
import importlib.util
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import cmudict
import numpy as np

PHONEMES = ["<pad>", "<unk>", "<s>", "</s>"] + [
    "AA0", "AA1", "AA2", "AE0", "AE1", "AE2", "AH0", "AH1", "AH2", "AO0", "AO1", "AO2", "AW0", "AW1", "AW2",
    "AY0", "AY1", "AY2", "B", "CH", "D", "DH", "EH0", "EH1", "EH2", "ER0", "ER1", "ER2", "EY0", "EY1", "EY2",
    "F", "G", "HH", "IH0", "IH1", "IH2", "IY0", "IY1", "IY2", "JH", "K", "L", "M", "N", "NG", "OW0", "OW1",
    "OW2", "OY0", "OY1", "OY2", "P", "R", "S", "SH", "T", "TH", "UH0", "UH1", "UH2", "UW", "UW0", "UW1", "UW2",
    "V", "W", "Y", "Z", "ZH",
]
GRAPHEMES = ["<pad>", "<unk>", "</s>"] + list("abcdefghijklmnopqrstuvwxyz")
_G2IDX = {g: i for i, g in enumerate(GRAPHEMES)}
REDUCED = {"AH0", "IH0", "UH0", "EH0"}


def _checkpoint_path() -> Path:
    spec = importlib.util.find_spec("g2p_en")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("g2p_en is not installed (needed for its G2P checkpoint)")
    return Path(list(spec.submodule_search_locations)[0]) / "checkpoint20.npz"


class G2P:
    """NumPy re-implementation of g2p_en's GRU encoder-decoder inference."""

    def __init__(self):
        path = _checkpoint_path()
        self.fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        v = np.load(path)
        self.enc_emb, self.enc_w_ih, self.enc_w_hh = v["enc_emb"], v["enc_w_ih"], v["enc_w_hh"]
        self.enc_b_ih, self.enc_b_hh = v["enc_b_ih"], v["enc_b_hh"]
        self.dec_emb, self.dec_w_ih, self.dec_w_hh = v["dec_emb"], v["dec_w_ih"], v["dec_w_hh"]
        self.dec_b_ih, self.dec_b_hh = v["dec_b_ih"], v["dec_b_hh"]
        self.fc_w, self.fc_b = v["fc_w"], v["fc_b"]

    @staticmethod
    def _cell(x, h, w_ih, w_hh, b_ih, b_hh):
        rzn_ih = x @ w_ih.T + b_ih
        rzn_hh = h @ w_hh.T + b_hh
        k = rzn_ih.shape[-1] * 2 // 3
        rz = 1 / (1 + np.exp(-(rzn_ih[:, :k] + rzn_hh[:, :k])))
        r, z = np.split(rz, 2, -1)
        n = np.tanh(rzn_ih[:, k:] + r * rzn_hh[:, k:])
        return (1 - z) * n + z * h

    @lru_cache(maxsize=50_000)
    def predict(self, word: str) -> tuple[str, ...]:
        word = "".join(c for c in word.lower() if c.isalpha())
        idx = [_G2IDX.get(c, 1) for c in word] + [_G2IDX["</s>"]]
        x = np.take(self.enc_emb, idx, axis=0)[None, :, :]
        h = np.zeros((1, self.enc_w_hh.shape[-1]), np.float32)
        for t in range(x.shape[1]):
            h = self._cell(x[:, t, :], h, self.enc_w_ih, self.enc_w_hh, self.enc_b_ih, self.enc_b_hh)
        dec = np.take(self.dec_emb, [2], axis=0)
        out = []
        for _ in range(25):
            h = self._cell(dec, h, self.dec_w_ih, self.dec_w_hh, self.dec_b_ih, self.dec_b_hh)
            pred = int((h @ self.fc_w.T + self.fc_b).argmax())
            if pred == 3:
                break
            out.append(PHONEMES[pred])
            dec = np.take(self.dec_emb, [pred], axis=0)
        return tuple(out)


def normalise(phones) -> str:
    out = []
    for p in phones:
        if p in REDUCED:
            out.append("@")
        else:
            out.append(p.rstrip("012"))
    return " ".join(out)


class Phonology:
    def __init__(self, is_candidate_word, count):
        self.g2p = G2P()
        self.cmu = cmudict.dict()
        self.cmu_version = getattr(cmudict, "__version__", "unknown")
        self._index: dict[str, list[str]] = defaultdict(list)
        for word, prons in self.cmu.items():
            if not word.isalpha() or not is_candidate_word(word):
                continue
            for pron in prons:
                key = normalise(pron)
                if word not in self._index[key]:
                    self._index[key].append(word)
        self._count = count

    def keys_for_word(self, word: str) -> set[str]:
        """Normalised pronunciations of a dictionary word (CMU variants, else G2P)."""
        prons = self.cmu.get(word.lower())
        if prons:
            return {normalise(p) for p in prons}
        return {normalise(self.g2p.predict(word))}

    def key_for_spelling(self, spelling: str) -> str:
        """Predicted pronunciation of an arbitrary (possibly misspelled) letter string."""
        return normalise(self.g2p.predict(spelling))

    def homophones(self, spelling: str) -> list[str]:
        """Dictionary words whose pronunciation equals the predicted pronunciation of `spelling`."""
        return sorted(self._index.get(self.key_for_spelling(spelling), []), key=lambda w: -self._count(w))

    def sounds_like(self, observed: str, intended: str) -> bool:
        """Observed spelling's predicted pronunciation matches a pronunciation of the intended word.

        The intended word's pronunciations are its CMU variants plus the G2P model's own
        reading of the correct spelling, so systematic G2P-vs-CMU differences do not hide
        a genuine match.
        """
        key = self.key_for_spelling(observed)
        return key in self.keys_for_word(intended) or key == self.key_for_spelling(intended)

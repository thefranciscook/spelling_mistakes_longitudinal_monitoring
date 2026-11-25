"""Error categorisation given an observed misspelling and its inferred intended word.

Categories (descriptive, not mechanistic):

* ``A`` mechanical-like: one edit with the shape of a typing slip (adjacent transposition,
  substitution by a neighbouring key, extra neighbouring/doubled key, omitted key) whose
  result does *not* preserve the intended pronunciation.
* ``B`` phonologically plausible: the misspelling's predicted pronunciation matches the
  intended word's, and the difference is *not* a single keyboard-shaped slip.
* ``C`` other/unclassified: everything else, including cases where both explanations fit
  (``goverment``), doubled-letter changes (``ocasion``, ``untill``), vowel reorderings
  (``recieve``), word-segmentation errors (``alot``) and unresolved intended words.
"""

from __future__ import annotations

from dataclasses import dataclass

from .orthography import EditOp, dl_alignment, doubled_letter_change, is_mechanical_op, vowel_transposition
from .phonology import Phonology


@dataclass
class ErrorClassification:
    category: str               # "A" | "B" | "C"
    reason: str
    edit_distance: int | None
    ops: list[EditOp]
    phon_match: bool | None
    mech_shape: bool | None


def classify_error(observed: str, intended: str | None, phonology: Phonology,
                   unresolved_reason: str = "ambiguous_intended") -> ErrorClassification:
    if not intended:
        return ErrorClassification("C", unresolved_reason, None, [], None, None)
    if " " in intended:
        ops = dl_alignment(observed, intended)
        return ErrorClassification("C", "segmentation", len(ops), ops, None, None)
    ops = dl_alignment(observed, intended)
    d = len(ops)
    phon = phonology.sounds_like(observed, intended)
    mech = d == 1 and is_mechanical_op(ops[0], observed, intended)
    if d == 1 and doubled_letter_change(ops[0], intended, observed):
        return ErrorClassification("C", "doubled_letter", d, ops, phon, mech)
    if d == 1 and vowel_transposition(ops[0]):
        return ErrorClassification("C", "vowel_transposition", d, ops, phon, mech)
    if mech and phon:
        return ErrorClassification("C", "both_explanations", d, ops, phon, mech)
    if mech:
        subtype = {"trans": "transposition", "sub": "adjacent_key", "ins": "extra_key", "del": "omission"}[ops[0].kind]
        return ErrorClassification("A", subtype, d, ops, phon, mech)
    if phon:
        return ErrorClassification("B", "pronunciation_preserved", d, ops, phon, mech)
    return ErrorClassification("C", "no_reliable_explanation", d, ops, phon, mech)

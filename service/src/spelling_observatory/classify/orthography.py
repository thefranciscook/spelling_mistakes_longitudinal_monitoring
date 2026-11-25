"""Character-level comparison: Damerau-Levenshtein alignment and keyboard geometry."""

from __future__ import annotations

from dataclasses import dataclass

# US/Canadian QWERTY (the user's physical layout is assumed to be an English Mac keyboard).
_ROWS = ["1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"]
_OFFSETS = [0.0, 0.5, 0.75, 1.25]
_POS: dict[str, tuple[float, float]] = {}
for r, (row, off) in enumerate(zip(_ROWS, _OFFSETS)):
    for c, ch in enumerate(row):
        _POS[ch] = (r, c + off)


def keys_adjacent(a: str, b: str) -> bool:
    pa, pb = _POS.get(a), _POS.get(b)
    if not pa or not pb or a == b:
        return False
    dr, dc = abs(pa[0] - pb[0]), abs(pa[1] - pb[1])
    return dr <= 1 and dc <= 1.0 and (dr + dc) > 0


@dataclass(frozen=True)
class EditOp:
    kind: str            # "sub" | "ins" | "del" | "trans"
    pos: int             # position in the intended word
    observed: str = ""   # character(s) in the observed (typed) word
    intended: str = ""   # character(s) in the intended word

    def code(self) -> str:
        return f"{self.kind}:{self.intended or '-'}>{self.observed or '-'}@{self.pos}"


def dl_alignment(observed: str, intended: str) -> list[EditOp]:
    """Optimal-string-alignment Damerau-Levenshtein distance with an operation trace.

    Operations describe how the *observed* string differs from the *intended* one:
    ``ins`` = extra character typed, ``del`` = intended character omitted.
    """
    a, b = observed, intended
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    ops: list[EditOp] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1] and a[i - 1] != a[i - 2] \
                and d[i][j] == d[i - 2][j - 2] + 1:
            ops.append(EditOp("trans", j - 2, a[i - 2:i], b[j - 2:j]))
            i, j = i - 2, j - 2
        elif i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            if a[i - 1] != b[j - 1]:
                ops.append(EditOp("sub", j - 1, a[i - 1], b[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            ops.append(EditOp("ins", j, a[i - 1], ""))
            i -= 1
        else:
            ops.append(EditOp("del", j - 1, "", b[j - 1]))
            j -= 1
    ops.reverse()
    return ops


def is_mechanical_op(op: EditOp, observed: str, intended: str) -> bool:
    """Whether a single edit has the character-level shape of a typing slip."""
    if op.kind == "trans":
        return True
    if op.kind == "sub":
        return keys_adjacent(op.observed, op.intended)
    if op.kind == "ins":
        # Extra key: a doubled neighbour or a key adjacent to a neighbouring intended letter.
        neighbours = intended[max(0, op.pos - 1):op.pos + 1]
        return any(op.observed == ch or keys_adjacent(op.observed, ch) for ch in neighbours)
    if op.kind == "del":
        # Omitted key. Typing omissions are common anywhere in a word.
        return True
    return False


VOWELS = set("aeiou")


def vowel_transposition(op: EditOp) -> bool:
    """Reordering of two vowels (``recieve``, ``thier``): a classic spelling-knowledge error
    that is keyboard-shaped and usually pronunciation-neutral, so it is treated as ambiguous."""
    return op.kind == "trans" and all(c in VOWELS for c in op.intended)


def doubled_letter_change(op: EditOp, intended: str, observed: str) -> bool:
    """Omission of one of a doubled pair, or doubling of a single letter.

    These changes are both a plausible keyboard slip (a missed or repeated key press) *and*
    pronunciation-preserving for a human reader, even where the G2P model's vowel choice
    differs, so the classifier always treats them as ambiguous between categories A and B.
    """
    if op.kind == "del":
        p = op.pos
        return (p > 0 and intended[p - 1] == op.intended) or (p + 1 < len(intended) and intended[p + 1] == op.intended)
    if op.kind == "ins":
        p = op.pos
        return (p > 0 and intended[p - 1] == op.observed) or (p < len(intended) and intended[p] == op.observed)
    return False

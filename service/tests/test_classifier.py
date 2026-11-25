"""Known examples for each category, abstention, and character-level helpers."""

import pytest

from conftest import errors
from spelling_observatory.classify.orthography import dl_alignment, keys_adjacent


def only_error(analyzer, text):
    errs = errors(analyzer.analyze(text))
    assert len(errs) == 1, f"expected exactly one error in {text!r}, got {errs}"
    return errs[0]


@pytest.mark.parametrize("typed,intended", [
    ("sentury", "century"),       # spec example
    ("seperate", "separate"),
    ("grammer", "grammar"),
    ("existance", "existence"),
    ("sircle", "circle"),
    ("langwage", "language"),
])
def test_phonologically_plausible(analyzer, typed, intended):
    e = only_error(analyzer, f"i think the {typed} matters here")
    assert (e.intended, e.category) == (intended, "B")


@pytest.mark.parametrize("typed,intended,reason", [
    ("centruy", "century", "transposition"),   # spec example
    ("teh", "the", "transposition"),
    ("becuse", "because", "omission"),
    ("probaly", "probably", "omission"),
    ("lenght", "length", "transposition"),
])
def test_mechanical_like(analyzer, typed, intended, reason):
    e = only_error(analyzer, f"i think {typed} is the point here")
    assert (e.intended, e.category, e.reason) == (intended, "A", reason)


@pytest.mark.parametrize("typed,intended,reason", [
    ("recieve", "receive", "vowel_transposition"),
    ("ocasion", "occasion", "doubled_letter"),
    ("untill", "until", "doubled_letter"),
    ("goverment", "government", "both_explanations"),
    ("definately", "definitely", "no_reliable_explanation"),
])
def test_ambiguous_mechanism_is_unclassified(analyzer, typed, intended, reason):
    e = only_error(analyzer, f"i think the {typed} is fine")
    assert (e.intended, e.category, e.reason) == (intended, "C", reason)


@pytest.mark.parametrize("typed", ["erros", "nite", "wich", "thorugh"])
def test_abstains_when_intended_word_is_ambiguous(analyzer, typed):
    # Without context (no LLM) several intended words are equally plausible.
    e = only_error(analyzer, f"i think the {typed} is fine")
    assert e.intended is None
    assert e.category == "C"
    assert e.resolution == "ambiguous_intended"
    assert len(e.candidates) >= 2


def test_segmentation_error_is_unclassified(analyzer):
    e = only_error(analyzer, "i have alot of work today")
    assert e.category == "C"


def test_real_word_errors_are_not_detected(analyzer):
    # Documented limitation: "wether" is a valid English word.
    assert errors(analyzer.analyze("i do not know wether it works")) == []


def test_keyboard_geometry():
    assert keys_adjacent("s", "d") and keys_adjacent("g", "h") and keys_adjacent("e", "d")
    assert not keys_adjacent("s", "c") and not keys_adjacent("a", "l")


def test_alignment_operations():
    assert [op.kind for op in dl_alignment("centruy", "century")] == ["trans"]
    assert [op.kind for op in dl_alignment("erros", "errors")] == ["del"]
    assert [op.kind for op in dl_alignment("thee", "the")] == ["ins"]
    assert [(op.kind, op.observed, op.intended) for op in dl_alignment("sentury", "century")] == [("sub", "s", "c")]


def test_analysis_is_deterministic(analyzer):
    text = "the sentury was long and becuse of that i felt teh change, untill now"
    a1, a2 = analyzer.analyze(text), analyzer.analyze(text)
    assert [vars(w) for w in a1.words] == [vars(w) for w in a2.words]
    assert a1.excluded == a2.excluded


def test_fingerprint_is_stable(analyzer):
    assert analyzer.fingerprint() == analyzer.fingerprint()
    assert len(analyzer.fingerprint()) == 16

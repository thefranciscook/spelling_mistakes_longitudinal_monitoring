"""Exclusions, regional variants, language filtering and provenance heuristics."""

import pytest

from conftest import errors


def eligible(analysis):
    return [w.observed for w in analysis.words]


def test_intentional_abbreviations_are_not_errors(analyzer):
    a = analyzer.analyze("can you help me w this, ppl say it is broken bc of osx and tbh idk why")
    assert errors(a) == []
    for w in ("w", "ppl", "bc", "osx", "tbh", "idk"):
        assert w not in eligible(a)
    assert a.excluded["shorthand"] >= 5


def test_informal_contractions(analyzer):
    a = analyzer.analyze("i dont think it works and im not sure why thats the case")
    assert errors(a) == []
    assert a.excluded["informal_contraction"] == 3


@pytest.mark.parametrize("sentence", [
    "the colour of the centre is grey and i travelled there",          # Canadian / British
    "the color of the center is gray and i traveled there",            # American
    "we need to analyse the data and realise the cheque is late",       # British -ise
    "we need to analyze the data and realize the check is late",
])
def test_regional_variants_are_correct(analyzer, sentence):
    a = analyzer.analyze(sentence)
    assert errors(a) == []
    assert len(a.words) >= 8


def test_technical_vocabulary(analyzer):
    a = analyzer.analyze("i deployed it with kubectl on kubernetes after refactoring the dockerfile on github")
    assert errors(a) == []
    assert a.excluded["technical"] >= 4


def test_proper_names_identifiers_urls_and_code(analyzer):
    text = ("I asked Zsofia and Kovacs about it. Check https://example.com/x and my_var, "
            "fooBar, `sentury_x` and /usr/local/bin please. The API v2 is NASA approved.")
    a = analyzer.analyze(text)
    assert errors(a) == []
    for reason in ("proper_name", "url", "identifier", "code", "acronym"):
        assert a.excluded[reason] >= 1, reason


def test_code_blocks_are_excluded(analyzer):
    a = analyzer.analyze("look at this\n\n```\nconst sentury = teh(x);\nreturn sentury;\n```\n\nwhat is wrong")
    assert errors(a) == []
    assert a.excluded["code"] > 0


def test_hungarian_text_is_excluded(analyzer):
    a = analyzer.analyze("szia, holnap megnezem es irok neked valamit")
    assert a.words == []
    assert a.excluded["non_english"] > 0


def test_mixed_language_paragraph_is_excluded(analyzer):
    a = analyzer.analyze("nem tudom pontosan mit jelent ez a szo. i think the sentury is wrong")
    assert errors(a) == []
    assert a.excluded["mixed_language"] > 0 or a.excluded["non_english"] > 0


def test_hungarian_word_inside_english_sentence(analyzer):
    a = analyzer.analyze("thanks a lot for the help with the report, koszi")
    assert errors(a) == []


def test_other_languages_are_excluded(analyzer):
    a = analyzer.analyze("ich habe keine Ahnung was das bedeutet")
    assert a.words == []


def test_pasted_typography_is_excluded(analyzer):
    a = analyzer.analyze("Can you improve this?\n\n“Dear team, I’m writing to confirm the sentury schedule…”")
    assert errors(a) == []
    assert a.excluded["suspected_paste"] > 0
    assert "improve" in eligible(a)


def test_quoted_lines_and_tables_are_excluded(analyzer):
    a = analyzer.analyze("> the sentury was here\n| a | b |\nwhat do you think")
    assert errors(a) == []
    assert a.excluded["quoted"] >= 1 and a.excluded["structured"] >= 1


def test_error_free_text_is_never_excluded_for_being_error_free(analyzer):
    # Invariant: filtering never looks at spelling, so a clean sentence and the same
    # sentence with one misspelling are excluded identically.
    clean = analyzer.analyze("the last century was full of surprises for everyone")
    typo = analyzer.analyze("the last sentury was full of surprises for everyone")
    assert len(clean.words) == len(typo.words) == 9
    assert clean.excluded == typo.excluded
    assert len(errors(clean)) == 0 and len(errors(typo)) == 1


def test_unknown_tokens_without_candidates_are_not_errors(analyzer):
    a = analyzer.analyze("we should use the zxqwvplk approach for this")
    assert errors(a) == []
    assert a.unknown["zxqwvplk"] == 1


def test_capitalised_word_at_sentence_start_is_checked(analyzer):
    e = errors(analyzer.analyze("Sentury is a long time"))
    assert len(e) == 1 and e[0].intended == "century"


def test_common_proper_nouns_are_vocabulary(analyzer):
    a = analyzer.analyze("see you on Wednesday in February")
    assert "wednesday" in eligible(a) and "february" in eligible(a)

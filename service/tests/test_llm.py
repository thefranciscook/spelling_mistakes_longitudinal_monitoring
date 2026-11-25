"""Local-LLM disambiguation with a fake client (no Ollama needed)."""

import pytest

from conftest import errors
from spelling_observatory.classify.llm import LLMAnswer, OllamaClient, parse_answer
from spelling_observatory.config import LLMConfig


class FakeLLM:
    fingerprint = "fake:model@0/intended-word-v1"

    def __init__(self, answers):
        self.answers = answers
        self.calls = 0

    def ask(self, token, context, candidates):
        self.calls += 1
        ans = self.answers.get(token)
        if callable(ans):
            return ans(candidates)
        return parse_answer(ans or '{"intended": null, "intentional": false}', candidates)


class DictCache(dict):
    def put(self, k, v):
        self[k] = v


def run(analyzer, llm, text, cache=None):
    a = analyzer.analyze(text)
    old = analyzer.llm
    analyzer.llm = llm
    try:
        analyzer.resolve_pending([a], cache=cache)
    finally:
        analyzer.llm = old
    return a


def test_llm_resolves_with_context_then_rules_classify(analyzer):
    llm = FakeLLM({"nite": '{"intended": "night", "intentional": false}',
                   "erros": '{"intended": "errors", "intentional": false}'})
    a = run(analyzer, llm, "good nite everyone, there are many erros in the logs")
    got = {e.observed: (e.intended, e.category, e.resolution) for e in errors(a)}
    assert got["nite"] == ("night", "B", "llm_context")       # spec example: nite -> night is B
    assert got["erros"] == ("errors", "A", "llm_context")     # spec example: erros -> errors is A


def test_llm_cannot_invent_words(analyzer):
    llm = FakeLLM({"nite": '{"intended": "knight-rider", "intentional": false}'})
    a = run(analyzer, llm, "good nite everyone")
    e = errors(a)[0]
    assert e.intended is None and e.category == "C" and e.resolution == "llm_abstained"


def test_llm_intentional_tokens_are_excluded(analyzer):
    llm = FakeLLM({"nite": '{"intended": null, "intentional": true}'})
    a = run(analyzer, llm, "good nite everyone")
    assert errors(a) == []
    assert a.excluded["llm_intentional"] == 1


def test_llm_results_are_cached(analyzer):
    llm = FakeLLM({"nite": '{"intended": "night", "intentional": false}'})
    cache = DictCache()
    run(analyzer, llm, "good nite everyone", cache)
    run(analyzer, llm, "good nite everyone", cache)
    assert llm.calls == 1
    # The cache stores a hash key and the answer only, never the sentence.
    assert all("everyone" not in k for k in cache) and all("everyone" not in str(v) for v in cache.values())


def test_without_llm_pending_items_stay_unclassified(analyzer):
    a = analyzer.analyze("good nite everyone")
    stats = analyzer.resolve_pending([a])
    assert stats["unresolved_no_llm"] == 1
    assert errors(a)[0].category == "C"


def test_parse_answer_is_strict():
    assert parse_answer("not json", ["a"]) == LLMAnswer(None, False)
    assert parse_answer('{"intended": "B", "intentional": false}', ["a", "b"]) == LLMAnswer("b", False)
    assert parse_answer('["b"]', ["b"]) == LLMAnswer(None, False)


def test_llm_endpoint_must_be_loopback():
    with pytest.raises(ValueError):
        OllamaClient(LLMConfig(enabled=True, endpoint="http://192.168.1.5:11434"))

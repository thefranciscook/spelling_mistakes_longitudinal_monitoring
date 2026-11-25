"""Optional local-LLM intended-word inference (Ollama, loopback only).

The model is asked a closed question: which of the listed candidates did the writer intend,
or none. The answer is accepted only if it is one of the candidates. The model's
self-reported certainty is not requested or used. Results are cached by a hash of
(model digest, prompt version, token, context); the context itself is never stored.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

from ..config import LLMConfig

PROMPT_VERSION = "intended-word-v1"

SYSTEM_PROMPT = (
    "You help analyse a person's own typing. You receive one sentence they typed quickly and "
    "one marked token that is not a dictionary word. Decide which candidate word the writer "
    "most likely intended, using the sentence context. If the token looks intentional "
    "(abbreviation, slang, name, brand, technical term, or a non-English word), set "
    "intentional to true and intended to null. If none of the candidates fits, or more than "
    "one fits equally well, set intended to null. Never invent a word that is not in the "
    "candidate list. Reply with JSON only."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "intended": {"type": ["string", "null"]},
        "intentional": {"type": "boolean"},
    },
    "required": ["intended", "intentional"],
}


@dataclass
class LLMAnswer:
    intended: str | None
    intentional: bool


class LLMUnavailable(RuntimeError):
    pass


def _check_loopback(endpoint: str) -> None:
    host = urllib.parse.urlparse(endpoint).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"LLM endpoint must be on the loopback interface, got {host!r}")


class OllamaClient:
    def __init__(self, cfg: LLMConfig):
        _check_loopback(cfg.endpoint)
        self.cfg = cfg
        self.digest = self._model_digest()

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.cfg.endpoint.rstrip("/") + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        # Bypass any HTTP proxy configured in the environment: traffic must stay local.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=self.cfg.timeout_seconds) as resp:
                return json.loads(resp.read())
        except OSError as e:
            raise LLMUnavailable(str(e)) from e

    def _model_digest(self) -> str:
        info = self._post("/api/show", {"model": self.cfg.model})
        details = json.dumps(info.get("details", {}), sort_keys=True)
        # /api/show does not return the manifest digest directly; hash the modelfile + details.
        return hashlib.sha256((info.get("modelfile", "") + details).encode()).hexdigest()[:16]

    @property
    def fingerprint(self) -> str:
        return f"ollama:{self.cfg.model}@{self.digest}/{PROMPT_VERSION}"

    def ask(self, token: str, context: str, candidates: list[str]) -> LLMAnswer:
        user = json.dumps({"sentence": context, "token": token, "candidates": candidates}, ensure_ascii=False)
        resp = self._post("/api/chat", {
            "model": self.cfg.model,
            "stream": False,
            "format": RESPONSE_SCHEMA,
            "options": {"temperature": 0, "seed": 0, "num_predict": 64},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        })
        return parse_answer(resp.get("message", {}).get("content", ""), candidates)


def parse_answer(content: str, candidates: list[str]) -> LLMAnswer:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return LLMAnswer(None, False)
    if not isinstance(data, dict):
        return LLMAnswer(None, False)
    intentional = bool(data.get("intentional", False))
    intended = data.get("intended")
    if not isinstance(intended, str) or intended.strip().lower() not in candidates or intentional:
        intended = None
    else:
        intended = intended.strip().lower()
    return LLMAnswer(intended, intentional)


def cache_key(fingerprint: str, token: str, context: str, candidates: list[str]) -> str:
    raw = json.dumps([fingerprint, token, context, candidates], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def mark_token(sentence: str, token: str) -> str:
    """Return the sentence with the first occurrence of token wrapped in [[ ]]."""
    idx = sentence.lower().find(token.lower())
    if idx < 0:
        return sentence
    return sentence[:idx] + "[[" + sentence[idx:idx + len(token)] + "]]" + sentence[idx + len(token):]

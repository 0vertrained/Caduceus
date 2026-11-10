"""Model providers. Imported ONLY by caduceus.router; an import-lint test enforces it.

No vendor SDKs: every provider speaks HTTP through an httpx client so tests can capture the exact bytes that
would leave the process.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    vendor: str = ""        # who runs the model, as a person would name it
    cached: bool = False    # a saved reply to an identical earlier request, not a new call


class ProviderUnavailable(Exception):
    pass


def _why(e: httpx.HTTPError) -> str:
    """The failure in words that carry no content: the error type and, for a refusal, the HTTP status."""
    status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
    return f"{type(e).__name__}{f' {status}' if status else ''}"


class Provider:
    name: str
    model: str

    def complete(self, system: str, user: str, max_tokens: int = 800,
                 schema: dict[str, Any] | None = None) -> Completion:  # pragma: no cover
        """The model's reply. With `schema`, the reply is a JSON document of that shape."""
        raise NotImplementedError


class OpenAICompatibleProvider(Provider):
    """vLLM, Ollama and llama.cpp's server all expose this API inside the VPC."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None, client: httpx.Client | None = None,
                 name: str = "in_vpc", vendor: str = "Mesa Verde's own server", timeout: float = 120):
        self.name, self.model, self.vendor = name, model, vendor
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(timeout=timeout)  # a small model on a CPU takes tens of seconds

    def complete(self, system: str, user: str, max_tokens: int = 800,
                 schema: dict[str, Any] | None = None) -> Completion:
        body: dict[str, Any] = {
            "model": self.model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "reply", "strict": True, "schema": schema}}
        try:
            r = self._client.post(self._url, json=body, headers=self._headers)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"{self.name} provider unavailable: {type(e).__name__}") from e
        data = r.json()
        usage = data.get("usage", {})
        return Completion(data["choices"][0]["message"]["content"], self.model, self.name,
                          usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), vendor=self.vendor)


class AnthropicMessagesProvider(Provider):
    """Frontier API over HTTP. Only ever handed de-identified, stamped text by the router."""

    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None,
                 base_url: str = "https://api.anthropic.com", name: str = "external"):
        self.name, self.model = name, model
        self._url = base_url.rstrip("/") + "/v1/messages"
        self._headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        self._client = client or httpx.Client(timeout=60)

    def complete(self, system: str, user: str, max_tokens: int = 800,
                 schema: dict[str, Any] | None = None) -> Completion:
        if schema is not None:
            raise ValueError("this provider does not constrain replies to a schema")
        body = {"model": self.model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": user}]}
        try:
            r = self._client.post(self._url, json=body, headers=self._headers)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"{self.name} provider unavailable: {type(e).__name__}") from e
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        return Completion(text, self.model, self.name, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                          vendor="Anthropic")


class OllamaChatProvider(Provider):
    """Ollama's chat API: Ollama Cloud at ollama.com, or an Ollama server. Ollama Cloud is a subscription with fixed
    usage limits rather than per-token billing, so a key that leaks or is abused can exhaust a quota but never run
    up a bill."""

    def __init__(self, api_key: str | None, model: str, client: httpx.Client | None = None,
                 base_url: str = "https://ollama.com", name: str = "external", vendor: str = "Ollama Cloud",
                 timeout: float = 60):
        self.name, self.model, self.vendor = name, model, vendor
        self._url = base_url.rstrip("/") + "/api/chat"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(timeout=timeout)

    def complete(self, system: str, user: str, max_tokens: int = 800,
                 schema: dict[str, Any] | None = None) -> Completion:
        # Thinking is off for prose; a model that cannot turn it off refuses the option (400) and is asked again
        # without it. The token budget leaves room for a reasoning model to think first.
        body: dict[str, Any] = {
            "model": self.model, "stream": False, "think": False,
            "options": {"temperature": 0, "num_predict": max_tokens + 7200},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if schema is not None:
            body["format"] = schema
        try:
            r = self._client.post(self._url, json=body, headers=self._headers)
            if r.status_code == 400:
                body.pop("think")
                r = self._client.post(self._url, json=body, headers=self._headers)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"{self.name} provider unavailable: {_why(e)}") from e
        data = r.json()
        message = data.get("message") or {}
        text = message.get("content", "").strip()
        if not text:
            spent = "after thinking, out of tokens" if message.get("thinking") else "empty"
            raise ProviderUnavailable(f"{self.name} provider returned no answer ({spent})")
        return Completion(text, self.model, self.name, data.get("prompt_eval_count", 0), data.get("eval_count", 0),
                          vendor=self.vendor)


class CachingProvider(Provider):
    """Remembers replies by the exact request (model, instructions, text, limit), for the life of the process and
    across every demo visitor's copy, so an identical request is answered once. It can start from recorded replies
    (a JSON file written by scripts/record_completions.py), so a cold start makes no calls at all. With no live
    provider it answers only what was recorded. Only the de-identified route is ever cached."""

    LIMIT = 2_000
    _replies: OrderedDict[str, Completion] = OrderedDict()
    _lock = threading.Lock()

    def __init__(self, inner: Provider | None, recordings: Path | None = None, model: str | None = None):
        self._inner = inner
        self.live = True  # False while the demo seeds: a cold start must never wait on a model
        recorded = json.loads(recordings.read_text()) if recordings is not None and recordings.exists() else {}
        for key, c in recorded.items():
            self._remember(key, Completion(**{k: c[k] for k in Completion.__dataclass_fields__ if k in c}))
        models = [c["model"] for c in recorded.values()]
        self.name = inner.name if inner else "external"
        self.model = inner.model if inner else (model or max(set(models), key=models.count, default="recorded"))

    @staticmethod
    def key(model: str, system: str, user: str, max_tokens: int, schema: dict[str, Any] | None = None) -> str:
        request = [model, system, user, max_tokens] + ([schema] if schema is not None else [])
        return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()

    def _remember(self, key: str, completion: Completion) -> None:
        with self._lock:
            self._replies[key] = replace(completion, cached=False)
            self._replies.move_to_end(key)
            while len(self._replies) > self.LIMIT:
                self._replies.popitem(last=False)

    def complete(self, system: str, user: str, max_tokens: int = 800,
                 schema: dict[str, Any] | None = None) -> Completion:
        key = self.key(self.model, system, user, max_tokens, schema)
        with self._lock:
            hit = self._replies.get(key)
        if hit is not None:
            return replace(hit, cached=True)
        if self._inner is None or not self.live:
            raise ProviderUnavailable("no recorded reply for this request, and no live model")
        completion = self._inner.complete(system, user, max_tokens, schema)
        self._remember(key, completion)
        return completion

    def export(self) -> dict[str, dict]:
        with self._lock:
            return {k: asdict(v) for k, v in self._replies.items() if v.model == self.model}


class StubProvider(Provider):
    """Deterministic provider for tests and for running the demo with no model at all."""

    def __init__(self, responder: Callable[[str, str], str], name: str = "stub", model: str = "stub-1",
                 vendor: str = ""):
        self.name, self.model, self.vendor = name, model, vendor
        self._responder = responder
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, max_tokens: int = 800,
                 schema: dict[str, Any] | None = None) -> Completion:
        self.calls.append((system, user))
        text = self._responder(system, user)
        return Completion(text, self.model, self.name, len(user.split()), len(text.split()), vendor=self.vendor)

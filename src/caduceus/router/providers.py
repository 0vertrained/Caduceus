"""Model providers. Imported ONLY by caduceus.router; an import-lint test enforces it.

No vendor SDKs: every provider speaks HTTP through an httpx client so tests can capture the exact bytes that
would leave the process.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int


class ProviderUnavailable(Exception):
    pass


class Provider:
    name: str
    model: str

    def complete(self, system: str, user: str, max_tokens: int = 800) -> Completion:  # pragma: no cover
        raise NotImplementedError


class OpenAICompatibleProvider(Provider):
    """vLLM, Ollama and the AI Workload and Release Platform all expose this API inside the VPC."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None, client: httpx.Client | None = None,
                 name: str = "in_vpc"):
        self.name, self.model = name, model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(timeout=60)

    def complete(self, system: str, user: str, max_tokens: int = 800) -> Completion:
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            r = self._client.post(self._url, json=body, headers=self._headers)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"{self.name} provider unavailable: {type(e).__name__}") from e
        data = r.json()
        usage = data.get("usage", {})
        return Completion(data["choices"][0]["message"]["content"], self.model, self.name,
                          usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))


class AnthropicMessagesProvider(Provider):
    """Frontier API over HTTP. Only ever handed de-identified, stamped text by the router."""

    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None,
                 base_url: str = "https://api.anthropic.com", name: str = "external"):
        self.name, self.model = name, model
        self._url = base_url.rstrip("/") + "/v1/messages"
        self._headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        self._client = client or httpx.Client(timeout=60)

    def complete(self, system: str, user: str, max_tokens: int = 800) -> Completion:
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
        return Completion(text, self.model, self.name, usage.get("input_tokens", 0), usage.get("output_tokens", 0))


class StubProvider(Provider):
    """Deterministic provider for tests and for running the demo with no model at all."""

    def __init__(self, responder: Callable[[str, str], str], name: str = "stub", model: str = "stub-1"):
        self.name, self.model = name, model
        self._responder = responder
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, max_tokens: int = 800) -> Completion:
        self.calls.append((system, user))
        text = self._responder(system, user)
        return Completion(text, self.model, self.name, len(user.split()), len(text.split()))

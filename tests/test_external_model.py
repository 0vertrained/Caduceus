"""The external model: Ollama Cloud as a provider, the reply cache, recorded replies, and what the demo may hold."""

from __future__ import annotations

import json

import httpx
import pytest

from caduceus.app_factory import InsecureConfiguration, Settings
from caduceus.router.providers import CachingProvider, OllamaChatProvider, ProviderUnavailable, StubProvider
from caduceus.seed import fresh_demo_services
from tests.conftest import DATA


def ollama(handler) -> OllamaChatProvider:
    return OllamaChatProvider("ok-test", "gpt-oss:120b", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_ollama_chat_request_and_reply():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": " [NAME_1] qualifies. "},
                                         "prompt_eval_count": 120, "eval_count": 9})

    out = ollama(handler).complete("system words", "user words", max_tokens=200)
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://ollama.com/api/chat" and seen[0].headers["authorization"] == "Bearer ok-test"
    assert body["model"] == "gpt-oss:120b" and body["stream"] is False and body["options"]["temperature"] == 0
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert (out.text, out.input_tokens, out.output_tokens, out.vendor) == ("[NAME_1] qualifies.", 120, 9,
                                                                           "Ollama Cloud")


@pytest.mark.parametrize("response", [httpx.Response(429, json={"error": "usage limit"}),
                                      httpx.Response(200, json={"message": {"content": ""}})])
def test_a_spent_quota_or_an_empty_answer_is_unavailable_not_an_error(response):
    with pytest.raises(ProviderUnavailable):
        ollama(lambda _r: response).complete("s", "u")


def test_identical_requests_are_answered_once_and_recordings_replay(tmp_path):
    CachingProvider._replies.clear()
    stub = StubProvider(lambda _s, u: f"reply to {u}", name="external", model="m-1", vendor="Test")
    live = CachingProvider(stub)
    first, again = live.complete("s", "facts"), live.complete("s", "facts")
    assert len(stub.calls) == 1 and not first.cached and again.cached and again.text == first.text

    path = tmp_path / "recorded.json"
    path.write_text(json.dumps(live.export()))
    CachingProvider._replies.clear()
    replay = CachingProvider(None, path)
    assert replay.model == "m-1" and replay.complete("s", "facts").text == "reply to facts"
    with pytest.raises(ProviderUnavailable, match="no recorded reply"):
        replay.complete("s", "other facts")
    CachingProvider._replies.clear()


def test_the_demo_may_hold_only_a_fixed_quota_key():
    def demo(**kw) -> Settings:
        return Settings(data_dir=DATA, db_path=":memory:", auth_mode="demo", **kw)

    demo(external_provider="ollama", external_api_key="ok-test").check()
    with pytest.raises(InsecureConfiguration, match="metered"):
        demo(external_provider="anthropic", external_api_key="sk-test").check()
    with pytest.raises(InsecureConfiguration, match="development and tests only"):
        demo(external_provider="stub").check()
    with pytest.raises(InsecureConfiguration, match="anthropic, ollama or stub"):
        demo(external_provider="openai").check()


def test_the_demo_replays_recorded_drafts_and_shows_what_was_sent(tmp_path, monkeypatch):
    CachingProvider._replies.clear()
    from scripts.record_completions import main as record

    monkeypatch.setenv("CADUCEUS_EXTERNAL_PROVIDER", "stub")
    monkeypatch.setenv("CADUCEUS_DATA_DIR", str(DATA))
    path = tmp_path / "recorded.json"
    assert record([str(path)]) == 0
    CachingProvider._replies.clear()
    monkeypatch.delenv("CADUCEUS_EXTERNAL_PROVIDER")

    svc = fresh_demo_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="demo",
                                       model_recordings=str(path)))
    draft = svc.cases.get("2026-1043").draft
    patient = svc.store.record(svc.cases.get("2026-1043").patient_id).patient
    family = patient["name"][0]["family"]
    assert draft["route"] == "external" and draft["model"] == "stub-1" and draft["sent"]["cached"] is True
    assert family not in draft["sent"]["text"] and "[NAME_1]" in draft["sent"]["text"]
    assert family in draft["text"]  # filled back in, inside Caduceus
    assert draft["sent"]["tokens_filled_in"] >= 1 and draft["deid_removed"]["NAME"] >= 1
    CachingProvider._replies.clear()


def test_a_cold_start_never_waits_on_the_model(monkeypatch):
    CachingProvider._replies.clear()

    def must_not_be_called(*_a, **_k):
        raise AssertionError("the demo called the model while starting")

    monkeypatch.setattr(OllamaChatProvider, "complete", must_not_be_called)
    svc = fresh_demo_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="demo",
                                       external_provider="ollama", external_api_key="ok-test"))
    drafts = [c.draft for c in svc.cases.all()]
    assert drafts and all(d["route"] == "template" for d in drafts)  # nothing recorded here, so the template
    assert svc.router._external.live is True  # after startup, new requests may call the model again


def test_a_refusal_or_a_thinking_model_out_of_tokens_says_why():
    with pytest.raises(ProviderUnavailable, match="HTTPStatusError 429"):
        ollama(lambda _r: httpx.Response(429, json={"error": "limit"})).complete("s", "u")
    thinking = httpx.Response(200, json={"message": {"content": "", "thinking": "Let me consider..."}})
    with pytest.raises(ProviderUnavailable, match="after thinking, out of tokens"):
        ollama(lambda _r: thinking).complete("s", "u")


def test_thinking_is_turned_off_and_a_model_that_refuses_is_asked_again_without_it():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if "think" in bodies[-1]:
            return httpx.Response(400, json={"error": "this model does not support thinking options"})
        return httpx.Response(200, json={"message": {"content": "Reply."}, "prompt_eval_count": 3, "eval_count": 1})

    assert ollama(handler).complete("s", "u").text == "Reply."
    assert bodies[0]["think"] is False and "think" not in bodies[1]

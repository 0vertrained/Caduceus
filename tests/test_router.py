"""The PHI-aware router: in-VPC only for PHI, no fallback, stamped-only external route, clean telemetry."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from caduceus.deid.safe_harbor import DeidResult, StampedText
from caduceus.router import InVpcUnavailable, Router, UnstampedPayload
from caduceus.router.providers import AnthropicMessagesProvider, OpenAICompatibleProvider, StubProvider
from tests.test_deid import CTX, NOTE, PLANTED

SRC = Path(__file__).resolve().parents[1] / "src" / "caduceus"
EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(_provider)


class Wire:
    """Captures every byte an httpx client would send."""

    def __init__(self, status: int = 200, body: dict | None = None):
        self.requests: list[bytes] = []
        self._status, self._body = status, body

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request.content + str(request.headers).encode() + str(request.url).encode())
            if self._status != 200:
                return httpx.Response(self._status)
            return httpx.Response(200, json=self._body)
        return httpx.Client(transport=httpx.MockTransport(handler))

    @property
    def all_bytes(self) -> bytes:
        return b"".join(self.requests)


ANTHROPIC_OK = {"content": [{"type": "text", "text": "[NAME_1] meets the criteria."}],
                "usage": {"input_tokens": 50, "output_tokens": 6}}
OPENAI_OK = {"choices": [{"message": {"content": "summary"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 1}}


def make(deid, in_vpc_wire: Wire | None = None, external_wire: Wire | None = None):
    in_vpc = OpenAICompatibleProvider("http://vllm.internal:8000/v1", "open-weight-8b",
                                      client=in_vpc_wire.client()) if in_vpc_wire else None
    external = AnthropicMessagesProvider("sk-test", "frontier-model",
                                         client=external_wire.client()) if external_wire else None
    return Router(in_vpc, external, deid, capture_deid_content=True)


def test_phi_goes_only_to_in_vpc(deid):
    in_vpc, external = Wire(body=OPENAI_OK), Wire(body=ANTHROPIC_OK)
    out = make(deid, in_vpc, external).complete_phi("summarize", "Summarize.", NOTE, "case-1")
    assert out.route == "phi" and in_vpc.requests and not external.requests


def test_no_fallback_when_in_vpc_is_down(deid):
    in_vpc, external = Wire(status=503), Wire(body=ANTHROPIC_OK)
    with pytest.raises(InVpcUnavailable):
        make(deid, in_vpc, external).complete_phi("summarize", "Summarize.", NOTE, "case-1")
    assert not external.requests


def test_no_in_vpc_configured_means_phi_cannot_run(deid):
    external = Wire(body=ANTHROPIC_OK)
    with pytest.raises(InVpcUnavailable):
        make(deid, None, external).complete_phi("summarize", "Summarize.", NOTE, "case-1")
    assert not external.requests


@pytest.mark.parametrize("bad", ["raw string with Zebediah Quillfeather", None, {"text": "x"}])
def test_external_route_refuses_unstamped(deid, bad):
    external = Wire(body=ANTHROPIC_OK)
    with pytest.raises(UnstampedPayload):
        make(deid, Wire(body=OPENAI_OK), external).complete_deid("draft", "Draft.", [bad], "case-1", CTX)
    assert not external.requests


def test_external_route_refuses_tampered_stamp(deid):
    stamped = deid.stamp(deid.deidentify(NOTE, CTX))
    forged = StampedText(stamped.text + " Zebediah Quillfeather", stamped.stamp)
    external = Wire(body=ANTHROPIC_OK)
    with pytest.raises(UnstampedPayload):
        make(deid, None, external).complete_deid("draft", "Draft.", [forged], "case-1", CTX)
    assert not external.requests


def test_external_route_refuses_stamped_text_that_still_holds_an_identifier(deid):
    """Even a validly signed stamp is refused if a known identifier is inside (defence in depth)."""
    smuggled = deid.stamp(DeidResult("Patient Zebediah Quillfeather has back pain.", {}, {}))  # type: ignore[arg-type]
    external = Wire(body=ANTHROPIC_OK)
    with pytest.raises(UnstampedPayload):
        make(deid, None, external).complete_deid("draft", "Draft.", [smuggled], "case-1", CTX)
    assert not external.requests


def test_leakage_suite_planted_identifiers_never_reach_the_external_wire(deid):
    external = Wire(body=ANTHROPIC_OK)
    stamped = deid.stamp(deid.deidentify(NOTE, CTX))
    out = make(deid, None, external).complete_deid("draft", "Draft a justification.", [stamped], "case-1", CTX)
    assert out.route == "external" and external.requests
    wire = external.all_bytes.decode(errors="replace")
    leaked = [v for v in PLANTED if v.lower() in wire.lower()]
    assert leaked == []


def test_deid_work_stays_in_vpc_when_no_external_provider(deid):
    in_vpc = Wire(body=OPENAI_OK)
    stamped = deid.stamp(deid.deidentify(NOTE, CTX))
    out = make(deid, in_vpc, None).complete_deid("draft", "Draft.", [stamped], "case-1", CTX)
    assert out.route == "deid_in_vpc" and in_vpc.requests


def test_phi_spans_carry_no_content(deid):
    EXPORTER.clear()
    make(deid, Wire(body=OPENAI_OK), None).complete_phi("summarize", "Summarize.", NOTE, "case-1")
    span = EXPORTER.get_finished_spans()[-1]
    attrs = json.dumps(dict(span.attributes))
    assert span.attributes["caduceus.route"] == "phi"
    assert span.attributes["gen_ai.request.model"] == "open-weight-8b"
    assert "gen_ai.input.messages" not in span.attributes
    assert not [v for v in PLANTED if v in attrs]


def test_stub_provider_supports_offline_demo(deid):
    stub = StubProvider(lambda s, u: "ok")
    out = Router(stub, None, deid).complete_phi("t", "s", "u", "c")
    assert out.completion.text == "ok" and stub.calls


def test_import_lint_only_router_imports_providers():
    """No module outside caduceus.router may import model providers or vendor SDKs."""
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                if n.split(".")[0] in {"openai", "anthropic"}:
                    offenders.append(f"{rel}: {n}")
                if n.startswith("caduceus.router.providers") and not rel.startswith(("router/", "app_factory.py")):
                    offenders.append(f"{rel}: {n}")
    assert offenders == []

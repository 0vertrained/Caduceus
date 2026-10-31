"""The PHI-aware inference router: the only component in Caduceus allowed to call a model.

Two doors, enforced by types rather than by convention:

* ``complete_phi`` takes ordinary strings and only ever reaches the in-VPC provider. If that provider is down,
  the call fails. It never falls back to the external route.
* ``complete_deid`` takes only ``StampedText`` from the de-identification service. Every part's stamp is verified
  and re-checked for known identifiers before the external provider sees a byte. Unstamped or tampered input is
  refused. With no external provider configured, de-identified work runs in-VPC instead (always safe).

System prompts are code constants passed by callers; they never carry patient data.

Telemetry follows the OpenTelemetry GenAI semantic conventions. Spans on the PHI route carry model, route,
token counts and a content hash, never content.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace

from caduceus.deid.safe_harbor import PatientContext, SafeHarborDeidentifier, StampedText
from caduceus.router.providers import Completion, Provider, ProviderUnavailable

tracer = trace.get_tracer("caduceus.router")


class InVpcUnavailable(Exception):
    """The in-VPC model is down. PHI work waits; it is never sent elsewhere."""


class UnstampedPayload(Exception):
    """Something other than verified, stamped, de-identified text was offered to the external route."""


@dataclass(frozen=True)
class RoutedCompletion:
    completion: Completion
    route: str


class Router:
    def __init__(self, in_vpc: Provider | None, external: Provider | None, deid: SafeHarborDeidentifier,
                 capture_deid_content: bool | None = None):
        self._in_vpc = in_vpc
        self._external = external
        self._external_off = False  # an administrator's switch: nothing goes out until it is turned back on
        self._deid = deid
        self._capture = (os.environ.get("CADUCEUS_CAPTURE_DEID_CONTENT") == "1"
                         if capture_deid_content is None else capture_deid_content)

    @property
    def external_enabled(self) -> bool:
        return self._external is not None

    @property
    def external_on(self) -> bool:
        """Configured, and not switched off by an administrator."""
        return self._external is not None and not self._external_off

    def switch_external(self, on: bool) -> None:
        """Off: de-identified work runs on the in-VPC model when there is one, else falls back to the template."""
        self._external_off = not on

    def describe(self) -> dict[str, Any]:
        """Which models each route would use, for the administrator: names only, never keys or addresses."""
        def about(p: Provider | None) -> dict[str, Any] | None:
            return None if p is None else {"name": p.name, "model": p.model}
        return {"in_vpc": about(self._in_vpc), "external": about(self._external), "external_on": self.external_on}

    def complete_phi(self, task: str, system: str, user: str, case_id: str, max_tokens: int = 800) -> RoutedCompletion:
        if self._in_vpc is None:
            raise InVpcUnavailable("no in-VPC model is configured; PHI work cannot run")
        with tracer.start_as_current_span(f"chat {self._in_vpc.model}") as span:
            self._annotate(span, self._in_vpc, "phi", task, case_id, user)
            try:
                completion = self._in_vpc.complete(system, user, max_tokens)
            except ProviderUnavailable as e:
                span.set_attribute("error.type", "in_vpc_unavailable")
                raise InVpcUnavailable(str(e)) from e
            self._usage(span, completion)
            return RoutedCompletion(completion, "phi")

    @contextmanager
    def recorded_only(self) -> Iterator[None]:
        """Within this block the de-identified route answers only from recorded replies, never with a new call."""
        external = self._external
        live = getattr(external, "live", None)
        if live is not None:
            external.live = False  # type: ignore[union-attr]
        try:
            yield
        finally:
            if live is not None:
                external.live = live  # type: ignore[union-attr]

    def recordings(self) -> dict[str, dict]:
        """Replies the de-identified route has seen, for scripts/record_completions.py."""
        export = getattr(self._external, "export", None)
        return export() if export else {}

    def complete_deid(self, task: str, system: str, parts: list[StampedText], case_id: str,
                      ctx: PatientContext, max_tokens: int = 800) -> RoutedCompletion:
        for part in parts:
            if not isinstance(part, StampedText) or not self._deid.verify(part):
                raise UnstampedPayload("external route accepts only verified de-identified text")
            if self._deid.residual_identifiers(part.text, ctx):
                raise UnstampedPayload("a known identifier is present in stamped text; refusing")
        user = "\n\n".join(p.text for p in parts)
        external = None if self._external_off else self._external
        provider = external or self._in_vpc
        if provider is None:
            raise InVpcUnavailable("no model is configured" if self._external is None
                                   else "the external model is switched off and there is no in-VPC model")
        route = "external" if external is not None else "deid_in_vpc"
        with tracer.start_as_current_span(f"chat {provider.model}") as span:
            self._annotate(span, provider, route, task, case_id, user)
            if self._capture:
                span.set_attribute("gen_ai.input.messages", user)
            try:
                completion = provider.complete(system, user, max_tokens)
            except ProviderUnavailable:
                span.set_attribute("error.type", "provider_unavailable")
                raise
            self._usage(span, completion)
            return RoutedCompletion(completion, route)

    @staticmethod
    def _annotate(span, provider: Provider, route: str, task: str, case_id: str, content: str) -> None:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.provider.name", provider.name)
        span.set_attribute("gen_ai.request.model", provider.model)
        span.set_attribute("caduceus.route", route)
        span.set_attribute("caduceus.task", task)
        span.set_attribute("caduceus.case_id", case_id)
        span.set_attribute("caduceus.prompt_sha256", hashlib.sha256(content.encode()).hexdigest())

    @staticmethod
    def _usage(span, completion: Completion) -> None:
        span.set_attribute("gen_ai.response.model", completion.model)
        span.set_attribute("gen_ai.usage.input_tokens", completion.input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", completion.output_tokens)

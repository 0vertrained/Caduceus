"""The services a request reads: one set, or in the public demo each visitor's own copy of the seeded cases."""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timedelta

from caduceus.app_factory import Services
from caduceus.presenter import Presenter
from caduceus.seed import clone_demo_services

SANDBOX_ID = re.compile(r"[0-9a-f]{32}")
MAX_SANDBOXES = 60           # each is a few hundred kilobytes; the least recently used goes first
TEMPLATE_MAX_AGE = timedelta(hours=6)


class TooManySandboxes(Exception):
    pass


@dataclass
class Sandbox:
    svc: Services
    presenter: Presenter
    last_reset: datetime


class Live:
    """The services the API is serving right now.

    Outside the demo there is one set. In the public demo each visitor's browser sends a random sandbox id and gets
    its own copy of the seeded cases, so one visitor's clicks never change what another sees. Copies are cloned in
    milliseconds from a seeded template that is never served, are capped in number, and start over after the reset
    period. Requests without a sandbox id share one set. A request's sandbox is chosen once, in middleware, and
    handlers read through this holder."""

    def __init__(self, svc: Services, fresh: Callable[[], Services] | None):
        self._lock, self._template_lock = threading.Lock(), threading.Lock()
        self.fresh = fresh
        self.demo = svc.settings.auth_mode == "demo"
        self.reset_every = timedelta(hours=float(svc.settings.demo_reset_hours)) if self.demo else None
        self.shared = self._wrap(svc)
        self._template: tuple[Services, datetime] | None = None
        if self.demo:  # a pristine copy, taken before anyone can change the shared set
            now = svc.workflow.now()
            self._template = (clone_demo_services(svc, now, now), now)
        self._sandboxes: OrderedDict[str, Sandbox] = OrderedDict()
        self._current: ContextVar[Sandbox | None] = ContextVar("sandbox", default=None)

    @staticmethod
    def _wrap(svc: Services) -> Sandbox:
        return Sandbox(svc, Presenter(svc.store, svc.workflow.library, svc.tz), svc.workflow.now())

    @property
    def current(self) -> Sandbox:
        return self._current.get() or self.shared

    @property
    def svc(self) -> Services:
        return self.current.svc

    @property
    def presenter(self) -> Presenter:
        return self.current.presenter

    @property
    def last_reset(self) -> datetime:
        return self.current.last_reset

    def enter(self, sandbox_id: str | None, may_create: Callable[[], bool]) -> Token:
        sandbox = self._sandbox(sandbox_id, may_create) if self.demo and sandbox_id else self.shared
        return self._current.set(sandbox)

    def leave(self, token: Token) -> None:
        self._current.reset(token)

    def all(self) -> list[Sandbox]:
        with self._lock:
            return [self.shared, *self._sandboxes.values()]

    def reset(self) -> None:
        """Start the current visitor's queue over (or the shared one)."""
        if self.fresh is None:
            raise RuntimeError("only the demo can be reset")
        current = self.current
        fresh = self._wrap(self._clone(current.svc.workflow.now()))
        with self._lock:
            sid = next((k for k, v in self._sandboxes.items() if v is current), None)
            if sid is None:
                self.shared = fresh
            else:
                self._sandboxes[sid] = fresh
        self._current.set(fresh)

    def expire(self) -> None:
        """Drop visitors' copies older than the reset period, and start the shared set over when it is."""
        now = self.shared.svc.workflow.now()
        with self._lock:
            for sid in [k for k, v in self._sandboxes.items() if now - v.last_reset >= self.reset_every]:
                del self._sandboxes[sid]
            stale = now - self.shared.last_reset >= self.reset_every  # type: ignore[operator]
        if stale:
            fresh = self._wrap(self._clone(now))
            with self._lock:
                self.shared = fresh

    def _sandbox(self, sandbox_id: str, may_create: Callable[[], bool]) -> Sandbox:
        now = self.shared.svc.workflow.now()
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if sandbox is not None and now - sandbox.last_reset < self.reset_every:  # type: ignore[operator]
                self._sandboxes.move_to_end(sandbox_id)
                return sandbox
        if not may_create():
            raise TooManySandboxes
        sandbox = self._wrap(self._clone(now))
        with self._lock:
            sandbox = self._sandboxes.setdefault(sandbox_id, sandbox)  # two first requests at once share one copy
            self._sandboxes.move_to_end(sandbox_id)
            while len(self._sandboxes) > MAX_SANDBOXES:
                self._sandboxes.popitem(last=False)
        return sandbox

    def _clone(self, now: datetime) -> Services:
        with self._template_lock:
            template, seeded_at = self._template  # type: ignore[misc]
            if self.fresh is not None and now - seeded_at > TEMPLATE_MAX_AGE:
                template, seeded_at = self.fresh(), now
                self._template = (template, seeded_at)
            return clone_demo_services(template, now, seeded_at)

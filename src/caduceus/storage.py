"""What the workflow needs from its database, whichever database holds it.

Two implementations satisfy these, with the same meaning: SQLite (caduceus.cases and caduceus.audit.log), which the
public demo and the tests use, and Postgres (caduceus.postgres), for a deployment. app_factory chooses between them.
"""

from __future__ import annotations

from typing import Any, Protocol

from caduceus.audit.log import AuditEvent
from caduceus.cases import Case, Fax


class CaseStore(Protocol):
    """Cases, saved with an optimistic version check: saving a case that someone else saved after it was read raises
    StaleCase. A save returns the case with its new version."""

    def save(self, case: Case) -> Case: ...
    def get(self, case_id: str) -> Case | None: ...
    def all(self) -> list[Case]: ...


class FaxStore(Protocol):
    def save(self, fax: Fax) -> Fax: ...
    def get(self, fax_id: str) -> Fax | None: ...
    def all(self) -> list[Fax]: ...


class DocumentStore(Protocol):
    """Documents Caduceus files itself, such as a faxed referral's cover sheet."""

    def save(self, doc: dict[str, Any]) -> None: ...
    def get(self, doc_id: str) -> dict[str, Any] | None: ...


class AuditStore(Protocol):
    """The append-only, hash-chained audit log. verify() re-walks the chain and raises TamperDetected if it broke."""

    def append(self, actor: str, role: str, action: str, case_id: str | None = None,
               detail: dict | None = None) -> AuditEvent: ...
    def events(self, case_id: str | None = None) -> list[AuditEvent]: ...
    def verify(self) -> int: ...

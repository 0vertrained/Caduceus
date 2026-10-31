"""Where cases and the audit log are kept. The repositories behave the same whether SQLite or Postgres holds them,
and the settings choose between the two."""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from caduceus.api import create_app
from caduceus.app_factory import InsecureConfiguration, Settings, build_services
from caduceus.cases import Case, Fax, StaleCase
from caduceus.migrations import Migration, MigrationError, available
from caduceus.postgres import DatabaseUnavailable
from tests.conftest import DATA
from tests.test_workflow_api import DOLORES

T0 = datetime(2026, 10, 29, 19, 0, tzinfo=UTC)


def a_case(case_id: str = "2026-1043", **fields) -> Case:
    return Case(id=case_id, patient_id="patient-1", service={"code": "72148", "short": "Lumbar MRI"},
                criteria_set="dev-lumbar-mri@2026-09", as_of="2026-10-28", payer={"name": "Humana Medicare Advantage"},
                ordering_clinician={"id": "pract-reyes", "name": "Dr. Ana Reyes"}, packet=["doc-1"], received_at=T0,
                due_at=T0 + timedelta(hours=4), **fields)


def a_fax(fax_id: str, **fields) -> Fax:
    return Fax(id=fax_id, file=f"{fax_id}.png", pages=["doc-1"], received_at=T0, as_of="2026-10-28", **fields)


# Both databases -------------------------------------------------------------------------------------------------

def test_a_save_from_a_stale_read_is_refused(backend):
    cases = backend.store().cases
    assert cases.save(a_case()).version == 1
    mine, theirs = cases.get("2026-1043"), cases.get("2026-1043")
    assert cases.save(theirs.model_copy(update={"returned_reason": "theirs"})).version == 2
    with pytest.raises(StaleCase, match="changed by someone else"):
        cases.save(mine.model_copy(update={"returned_reason": "mine"}))
    kept = cases.get("2026-1043")
    assert (kept.version, kept.returned_reason) == (2, "theirs")


def test_a_new_case_saved_twice_from_one_object_is_stale_the_second_time(backend):
    cases = backend.store().cases
    new = a_case()
    cases.save(new)
    with pytest.raises(StaleCase):
        cases.save(new)


def test_cases_are_listed_in_the_order_they_were_last_saved(backend):
    """The queue keeps this order for cases due at the same moment, so both databases list them alike."""
    cases = backend.store().cases
    first, _, _ = (cases.save(a_case(case_id)) for case_id in ("2026-1001", "2026-1002", "2026-1003"))
    cases.save(first)
    assert [c.id for c in cases.all()] == ["2026-1002", "2026-1003", "2026-1001"]


def test_faxes_are_listed_by_id_in_byte_order(backend):
    """As SQLite sorts text, whatever collation a Postgres database was created with."""
    faxes = backend.store().faxes
    for fax_id in ("FAX-0010", "fax-0001", "FAX-0002", "FAX_0003"):
        faxes.save(a_fax(fax_id))
    assert [f.id for f in faxes.all()] == ["FAX-0002", "FAX-0010", "FAX_0003", "fax-0001"]


def test_what_is_saved_reads_back_exactly(backend):
    """Even a NUL character, which a typed note can hold and Postgres's jsonb would refuse, and numbers that jsonb
    would rewrite."""
    store = backend.store()
    note = "Ruled out on exam.\x00 Strength normal; café at 10°."
    case = store.cases.save(a_case(returned_reason=note, determination={"big": 1e16, "zero": -0.0, "list": [1, None]}))
    assert store.cases.get(case.id).model_dump_json() == case.model_dump_json()

    fax = store.faxes.save(a_fax("FAX-0001", reading={"fields": [{"value": note, "confidence": 0.87}]}))
    store.faxes.save(fax.model_copy(update={"status": "read"}))
    assert store.faxes.get("FAX-0001").model_dump_json() == fax.model_copy(update={"status": "read"}).model_dump_json()

    doc = {"id": "cover-fax-0001", "resourceType": "DocumentReference", "description": note,
           "content": [{"attachment": {"data": "UmVmZXJyYWw=", "url": "fax:FAX-0001.png"}}]}
    store.documents.save(doc)
    assert store.documents.get("cover-fax-0001") == doc
    store.documents.save(doc | {"description": "Fax cover sheet"})
    assert store.documents.get("cover-fax-0001")["description"] == "Fax cover sheet"
    assert store.cases.get("2026-9999") is None and store.faxes.get("FAX-9999") is None
    assert store.documents.get("cover-fax-9999") is None


def test_an_id_no_row_can_have_is_simply_not_found(backend):
    """Postgres text cannot hold a NUL, so a lookup with one finds nothing, as on SQLite, and does not fail."""
    store = backend.store()
    store.cases.save(a_case())
    store.audit.append("dolores", "pa_specialist", "case.viewed", "2026-1043")
    assert store.cases.get("2026-1043\x00") is None and store.faxes.get("FAX-0001\x00") is None
    assert store.documents.get("cover-fax-0001\x00") is None and store.audit.events("2026-1043\x00") == []
    client = TestClient(create_app(backend.services(auth_mode="dev"), dispatch_interval=None))
    assert client.get("/api/cases/2026-1043%00", headers=DOLORES).status_code == 404
    assert client.get("/api/intake/FAX-0001%00", headers=DOLORES).status_code == 404


# Choosing the database ------------------------------------------------------------------------------------------

def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_postgres_is_chosen_by_a_postgres_url_and_never_for_the_public_demo(monkeypatch):
    monkeypatch.setenv("CADUCEUS_DATABASE_URL", "")
    assert Settings().database_url is None  # set but empty: SQLite, as before
    monkeypatch.setenv("CADUCEUS_DATABASE_URL", "postgresql://caduceus_app:a-password@db:5432/caduceus")
    assert Settings().database_url.startswith("postgresql://")
    assert "a-password" not in repr(Settings())
    Settings(data_dir=DATA, auth_mode="dev", database_url="postgres://db/caduceus").check()
    with pytest.raises(InsecureConfiguration, match="in memory, never in a database"):
        Settings(data_dir=DATA, auth_mode="demo", database_url="postgresql://db/caduceus").check()
    with pytest.raises(InsecureConfiguration, match="must be a postgresql:// URL"):
        Settings(data_dir=DATA, auth_mode="dev", database_url="sqlite:///var/caduceus.sqlite3").check()


def test_an_unreachable_database_stops_the_service_at_startup_and_says_where():
    port = closed_port()
    url = f"postgresql://caduceus_app:not-a-real-password-7731@127.0.0.1:{port}/caduceus"
    with pytest.raises(DatabaseUnavailable) as refused:
        build_services(Settings(data_dir=DATA, auth_mode="dev", database_url=url))
    said = str(refused.value)
    assert said.startswith(f"Cannot connect to Postgres at 127.0.0.1, port {port} (database caduceus, user "
                           "caduceus_app): ")
    assert "Connection refused" in said and "Check CADUCEUS_DATABASE_URL" in said
    assert "not-a-real-password-7731" not in said and refused.value.__cause__ is None


def test_a_url_postgres_cannot_read_is_refused_without_repeating_it():
    url = "postgresql://caduceus_app:half%zzencoded@db:5432/caduceus"  # libpq's own message would quote the password
    with pytest.raises(DatabaseUnavailable, match="not a connection URL Postgres can read") as refused:
        build_services(Settings(data_dir=DATA, auth_mode="dev", database_url=url))
    assert "half%zz" not in str(refused.value) and refused.value.__cause__ is None


def test_migration_files_are_named_to_apply_in_order():
    shipped = available()
    versions = [m.version for m in shipped]
    assert shipped[0].file == "0001_initial.sql" and versions == sorted(set(versions))
    assert all(m.sql.strip() for m in shipped)  # the SQL ships with the package
    with pytest.raises(MigrationError, match="not named like 0001_what_it_does.sql"):
        Migration.of("1_initial.sql", "SELECT 1;")
    windows = Migration.of("0001_initial.sql", shipped[0].sql.replace("\n", "\r\n"))
    assert windows.sha256 == shipped[0].sha256  # a checkout with Windows line endings is the same migration

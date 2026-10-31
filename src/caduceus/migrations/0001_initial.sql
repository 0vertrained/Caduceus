-- The Postgres store: cases, faxes, filed documents and the audit log. Applied once, by caduceus.migrations.
--
-- JSON is kept in json columns, not jsonb. json keeps the text exactly as it was written; jsonb rewrites numbers
-- (1e+16 reads back as 10000000000000000, -0.0 as 0.0) and refuses \u0000, which a typed note can contain. A case
-- must read back as it was saved, and the audit log's hashes are over the exact text.
--
-- Privileges are granted separately, in infra/sql/audit.sql: the application role may INSERT and SELECT the audit
-- log and nothing more, and reads and writes the other tables. Only the owner, which applies these migrations, can
-- change the schema or switch the trigger below off.

CREATE TABLE cases (
    id       TEXT PRIMARY KEY,
    state    TEXT NOT NULL,
    data     JSON NOT NULL,
    version  INTEGER NOT NULL,
    -- The order of each case's latest save. Listing cases in this order matches SQLite, where a save replaces the
    -- row; the queue keeps that order for cases due at the same time.
    saved    BIGINT GENERATED ALWAYS AS IDENTITY
);

CREATE TABLE faxes (
    id    TEXT PRIMARY KEY,
    data  JSON NOT NULL
);

CREATE TABLE documents (
    id    TEXT PRIMARY KEY,
    data  JSON NOT NULL
);

-- Append-only and hash-chained. Each row's hash covers the previous row's hash, the time as SQLite writes it (ISO
-- 8601 in UTC, to the microsecond) and the detail exactly as stored, so both databases give the same hashes for the
-- same events. The service supplies ts itself, because the time is part of the hash: there is no default.
CREATE TABLE audit_events (
    seq        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL,
    actor      TEXT NOT NULL,
    role       TEXT NOT NULL,
    case_id    TEXT,
    action     TEXT NOT NULL,
    detail     JSON NOT NULL,
    prev_hash  CHAR(64) NOT NULL,
    hash       CHAR(64) NOT NULL,
    -- Each event has one successor at most. Appends are serialized by an advisory lock; this refuses a second event
    -- after the same one even from a writer that skipped the lock, so the chain cannot fork.
    CONSTRAINT audit_no_fork UNIQUE (prev_hash)
);

CREATE INDEX audit_case ON audit_events (case_id, seq);

-- Defence in depth: the application role has no UPDATE, DELETE or TRUNCATE grant, and even the owner gets an error
-- unless the trigger is explicitly disabled.
CREATE FUNCTION audit_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit log is append-only';
END;
$$;

CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only();

-- Fire even in a session that sets session_replication_role = replica, which skips ordinary triggers.
ALTER TABLE audit_events ENABLE ALWAYS TRIGGER audit_append_only;

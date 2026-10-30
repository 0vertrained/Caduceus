-- Postgres version of the append-only audit log used in the deployed environment.
-- The application role may INSERT and SELECT; nobody but the break-glass owner can UPDATE or DELETE.

CREATE TABLE IF NOT EXISTS audit_events (
    seq        BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor      TEXT NOT NULL,
    role       TEXT NOT NULL,
    case_id    TEXT,
    action     TEXT NOT NULL,
    detail     JSONB NOT NULL,
    prev_hash  CHAR(64) NOT NULL,
    hash       CHAR(64) NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_case ON audit_events (case_id, seq);

REVOKE ALL ON audit_events FROM PUBLIC;
GRANT SELECT, INSERT ON audit_events TO caduceus_app;
GRANT USAGE ON SEQUENCE audit_events_seq_seq TO caduceus_app;
GRANT SELECT ON audit_events TO caduceus_auditor;

-- Defence in depth: even the owner gets an error unless the trigger is explicitly disabled.
CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_no_update_delete ON audit_events;
CREATE TRIGGER audit_no_update_delete BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();

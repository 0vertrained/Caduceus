-- Privileges for the deployed database, where the service never connects as the owner. The tables, the audit log's
-- append-only trigger among them, are created by the migrations in src/caduceus/migrations, which the owner applies
-- (python -m caduceus.postgres migrate). Run this after them, as the owner, and again after any migration that adds
-- a table.
--
--   caduceus_admin    owns the schema and applies migrations; break-glass only (the RDS master user)
--   caduceus_app      the service: reads and writes cases, faxes and filed documents, and may only INSERT and SELECT
--                     the audit log, so it can add to the record but never change it
--   caduceus_auditor  reads the audit log
--
-- The roles, and how they sign in (IAM authentication for caduceus_app), are made outside this file.

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON cases, faxes, documents, audit_events, schema_migrations FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO caduceus_app, caduceus_auditor;

-- At start the service checks that no migration is pending; it applies none itself.
GRANT SELECT ON schema_migrations TO caduceus_app;
GRANT SELECT, INSERT, UPDATE ON cases, faxes, documents TO caduceus_app;

-- No UPDATE, DELETE or TRUNCATE: the log is append-only, and the trigger refuses them even to the owner. seq is an
-- identity column, so INSERT needs no grant on its sequence.
GRANT SELECT, INSERT ON audit_events TO caduceus_app;
GRANT SELECT ON audit_events TO caduceus_auditor;

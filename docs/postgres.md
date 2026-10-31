# The Postgres store

Cases, faxed referrals, the documents Caduceus files and the audit log live in SQLite unless told otherwise, as in
the public demo and the tests. Set `CADUCEUS_DATABASE_URL` and they live in Postgres instead, as they would on RDS in
the target deployment:

```
CADUCEUS_DATABASE_URL=postgresql://caduceus_app:<password>@db.internal:5432/caduceus?sslmode=verify-full
```

Both behave the same, and the same tests run on both ([below](#tests)). The code is `src/caduceus/postgres.py`; the
interfaces the two share are in `src/caduceus/storage.py`. The public demo stays on SQLite: it copies an in-memory
database for each visitor, and it refuses to start with a database URL.

## Run it

With Docker, set `CADUCEUS_DB_PASSWORD` in `.env` (the compose file says how to make one), then
`docker compose up --build`. Postgres 17 starts on an internal network beside the API. The API waits until Postgres
answers, applies the migrations, seeds the demo cases if the database holds none, and serves on
http://127.0.0.1:8000.

Without Docker, point the URL at Postgres 16 or later and run the API as usual:

```
export CADUCEUS_DATABASE_URL=postgresql://caduceus:<password>@127.0.0.1:5432/caduceus
.venv/bin/python -m caduceus.seed --if-missing
make dev
```

If the database cannot be reached, the service stops at startup and says where it tried and why, never with the
password:

```
DatabaseUnavailable: Cannot connect to Postgres at db.internal, port 5432 (database caduceus, user caduceus_app):
connection failed: ... Connection refused. Is the server running on that host and accepting TCP/IP connections?
Check CADUCEUS_DATABASE_URL, and that the database is running.
```

Seeding works only on a database with no cases yet. The audit log cannot be cleared, so a Postgres database cannot
start over in place the way `make seed` replaces the SQLite file. To start over, use a new database; with compose,
`docker compose down -v` deletes the volume and everything in it.

Leave the server's statement logging off, as it is by default (`log_statement = none`): query parameters carry PHI.

## Migrate

The schema is versioned SQL in `src/caduceus/migrations`: `0001_initial.sql`, then the next number. When the service
starts it applies any it has not had, in order, in one transaction under an advisory lock. Two instances starting
together cannot both apply one, and a migration that fails changes nothing. Each one applied is recorded in
`schema_migrations` with a hash of its text. A migration edited after it ran stops the service at startup
("changed after it was applied"), so a change always goes in a new file.

To apply them without starting the service:

```
python -m caduceus.postgres migrate
Applied 0001_initial.sql.
```

In a deployment the service does not own its tables. It connects as `caduceus_app`, which reads and writes cases,
faxes and filed documents, may only `INSERT` and `SELECT` the audit log, and cannot change the schema. So a release
runs two steps as the owner (`caduceus_admin`, the RDS master user): `python -m caduceus.postgres migrate`, then
`infra/sql/audit.sql`, which grants each role what it needs and is run again after any migration that adds a table.
The service then starts, finds nothing to apply, and carries on. If a migration is still pending, it stops and says
that the owner must apply it.

## The audit log

It is the same chain as on SQLite. Each event's hash covers the previous event's hash, the time as SQLite writes it
(ISO 8601 in UTC, to the microsecond, whatever the session's time zone) and the detail exactly as stored, so the same
events give the same hashes on either database. The detail is kept in a `json` column, not `jsonb`: `jsonb` rewrites
numbers (`1e+16` reads back as `10000000000000000`) and refuses `\u0000`, and the hash is over the exact text.

It is append-only three times over:

* `caduceus_app` has no `UPDATE`, `DELETE` or `TRUNCATE` grant on it.
* A trigger refuses all three even to the owner, unless the owner first switches it off. It fires even in a session
  set up for replication, which skips ordinary triggers.
* Every append takes the same advisory lock, so appends from any number of connections and instances follow one
  another and the chain never forks. A unique constraint on each event's predecessor refuses a fork even from a
  writer that skipped the lock.

## Verify the chain

An auditor can verify it from the case page, or through the API (`GET /api/audit/verify`, for the auditor and
administrator roles). From a shell with the database's URL:

```
python -m caduceus.postgres verify
The audit chain is intact: 1042 events verified.
```

If any event was changed, it exits with status 1 and names the first broken one: `Audit chain broken at event 518.`
It applies no migrations, so it can run as `caduceus_auditor`, which may only read the log.

## Tests

`pytest` alone runs everything on SQLite. Name a Postgres server and the repository, audit and workflow tests run on
it as well, together with tests of what only Postgres does: the triggers, eight threads appending at once, instances
starting together, a stale save from another instance, and the roles in `infra/sql/audit.sql`.

```
CADUCEUS_TEST_DATABASE_URL=postgresql://caduceus:<password>@127.0.0.1:5432/caduceus pytest
CADUCEUS_TEST_DATABASE_URL=... pytest -m postgres      # only the tests that use Postgres
```

The run makes a database of its own on that server, starts each test with it empty, and drops it at the end, so the
URL's user must be able to create databases. A few tests also need to create roles or be a superuser, and skip
otherwise. Every Postgres session runs in Asia/Kathmandu (UTC+5:45), to show that nothing read back depends on the
session's time zone. CI runs `pytest -m postgres` against a `postgres:17` service container and fails if any of those
tests skipped.

## Not done yet

* The outbox sends each approved request once per process: its lock is in memory (ADR 4). Until that lock is in the
  database, run one API instance per database, or two instances could both send the same request to the payer.
* Signing in with IAM authentication tokens, which the Terraform skeleton grants `caduceus_app`; the URL carries a
  password today. In the deployment the URL should also ask for `sslmode=verify-full` with the RDS certificate
  bundle, although RDS already refuses unencrypted connections.
* CI and `docker-compose.yml` name the Postgres image by tag, not yet by digest.

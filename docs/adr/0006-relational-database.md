# ADR 0006: Relational database; Postgres reached via the session pooler

**Status:** Accepted — SQLite default, Supabase Postgres in deployment

## Context

The system stores users, travel requests, plans, per-agent findings, options,
A2A messages, audit events, planning jobs, intake messages, agent runs, and plan
feedback — twelve tables, heavily cross-referenced by `request_id`.

Explainability is an assessed requirement: every claim in a plan must be
traceable to the row that produced it.

## Options

1. **Relational** (SQLite locally, Postgres deployed).
2. **Document store** (MongoDB) — plans are nested documents, so this fits the
   write shape well.
3. **Vector database** — the default reflex for anything LLM-shaped. Treated
   separately in [ADR 0007](0007-no-vector-database.md).

## Decision

Option 1, with the *same schema* expressed twice in `database.py` — one DDL set
for SQLite, one for Postgres — and a placeholder translation layer (`?` → `%s`).
`is_postgres()` picks at connect time from the `DATABASE_URL` shape.

Relational wins because the queries the requirement generates are joins:
*"which agent produced this option, in which run, under which request, and what
did the audit chain say at the time."* A document store optimises for fetching
one nested plan whole — which is the write pattern, not the audit pattern.

Dual DDL rather than an ORM: two explicit schemas are more readable for a team
report than one abstraction that hides both, and the dialect differences are
small enough (`AUTOINCREMENT` vs `BIGSERIAL`, `TEXT` vs `JSONB`) to state twice.
The cost is real — they can drift, and only integration tests catch it.

## Consequences

- SQLite needs no setup, so a new team member runs the app with zero
  infrastructure. Postgres is opt-in via one environment variable.
- **Supabase must be reached through the session pooler.** The direct host
  resolves only to IPv6, and Render cannot route to it. The failure is a
  connection timeout with no hint of the cause, so `connect()` attaches
  `CONNECTION_HELP` to `OperationalError`. *This is infrastructure reality that
  no diagram predicts.*
- Tables live in a non-`public`, case-sensitive schema (`Travelplanner_schema`),
  quoted by `connect()`.
- `psycopg` maps `numeric` to `Decimal`, which Flask's JSON encoder rejects — a
  `FloatLoader` is registered to undo it.
- **Traces are the exception and a known flaw.** JSONL under `TRACE_DIR` sits on
  Render's ephemeral disk, so `GET /api/v1/traces/<request_id>` 404s after a
  redeploy even though the same events remain in `audit_events`. The fix is
  object storage or reading the trace back from the table.

**Revisit when:** the audit tables outgrow single-node Postgres, or trace
retention becomes a compliance requirement rather than a debugging aid.

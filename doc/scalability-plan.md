# Scalability Plan — Database & Deployment

Snapshot of the current architecture, the concrete bottlenecks it has today,
and a phased plan for scaling it. Written against the state of the repo as of
2026-08-20 — re-check the "Current State" numbers before acting on this if
it's been a while.

## Current state

**App**: single FastAPI service (`app/main.py`), no middleware, no caching
layer, no background job queue. Auth is a single authorized owner via Google
SSO + our own JWT (`app/security.py`) — this is a single-tenant app for one
business, not a multi-tenant SaaS.

**Deployment**: Cloud Run service `oura-backend-jkt` (asia-southeast2):
- 1 vCPU / 512Mi memory per instance
- `containerConcurrency: 80` (up to 80 concurrent requests per instance
  before Cloud Run spins up another)
- `maxScale: 20`, **no `minScale` set** — scales to zero when idle, so the
  first request after an idle period pays a cold start (container boot +
  DB connection pool warm-up)

**Database**: Supabase-managed Postgres, accessed directly via SQLAlchemy +
psycopg2 (`app/database.py`) — no PgBouncer/pooler abstraction on our side
beyond whatever pooler variant is in `SUPABASE_DB_URL` (Session or
Transaction pooler, per `.env.example`). `create_engine()` is called with
only `pool_pre_ping=True` — every other pool setting is SQLAlchemy's default:
**`pool_size=5`, `max_overflow=10`**, i.e. at most 15 concurrent DB
connections per running container.

**Backups**: `scripts/backup_supabase.py` — daily `pg_dump` to GCS via a
Cloud Run Job + Cloud Scheduler (see `doc/backup-setup.md`). No read
replica, no point-in-time-recovery setup beyond what Supabase's own plan
tier provides.

## Where this breaks first

Given the current shape of the app, these are the bottlenecks in the order
they'd actually get hit, not a generic checklist:

1. **DB connection exhaustion under concurrency, not CPU.** Each Cloud Run
   *instance* opens up to 15 DB connections (5 pool + 10 overflow). With
   `maxScale: 20`, a traffic spike could open up to **300 connections**
   simultaneously against Supabase's Postgres — Supabase's connection cap
   depends on plan tier (commonly 60–200 direct, more via the pooler). This
   is the single most likely production incident: `FATAL: too many
   connections` under load, not slow queries.
2. **Cold starts.** `minScale` unset means idle periods (normal for a
   single-business owner app used a few times a day) scale to zero. The
   iOS app's first request after idle pays full cold start + a fresh
   SQLAlchemy pool warming up against the Supabase pooler.
3. **No caching for read-heavy report endpoints.** `app/routers/reports.py`
   and the HPP/cutting-optimizer calculations (`app/services/hpp.py`,
   `app/services/cutting_optimizer.py`) recompute from scratch on every
   request. Fine at current volume; becomes the CPU bottleneck before the
   DB does if report traffic grows.
4. **Single point of DB failure.** No read replica — reporting/analytics
   queries compete with transactional writes (sales orders, stock
   adjustments) for the same connections.

## Scaling plan

### Now (low effort, do before it's a fire)

**Cap DB connections per container explicitly** so a scale-out event can't
exceed Supabase's connection limit. In `app/database.py`:
```python
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=2,
    pool_recycle=1800,
)
```
5 max connections/instance × `maxScale: 20` = 100 connections ceiling — pick
the actual number based on your Supabase plan's connection limit, with
headroom for the backup job and any manual `psql` sessions.

**Set `minScale: 1`** on the Cloud Run service to keep one warm instance and
eliminate cold starts for the primary user-facing path:
```bash
gcloud run services update oura-backend-jkt \
  --region=asia-southeast2 --min-instances=1
```
Trade-off: this switches the idle-time cost from $0 to ~1 vCPU/512Mi
always-on billing. Worth it once the app is used throughout the business
day rather than sporadically — check actual Cloud Run request logs before
flipping this.

**Confirm `SUPABASE_DB_URL` uses the pooler, not a direct connection** —
already correct per `.env.example`/`doc/backup-setup.md` guidance, but this
is the first thing to double check if connection errors show up, since a
direct connection has a much lower concurrent-connection ceiling than the
pooler.

**Add DB indexes as query patterns emerge.** No proactive index plan is
warranted yet — Supabase's Postgres query planner + `pg_stat_statements` (or
Supabase's own dashboard slow-query view) should drive this reactively, not
speculatively.

### Next (once there's sustained multi-user or reporting load)

**Read replica for reports.** If `app/routers/reports.py` traffic grows
enough to compete with transactional writes, add a Supabase read replica
(available on higher plan tiers) and route report/analytics queries to a
second SQLAlchemy engine bound to the replica's connection string. This is a
config + a second `get_db`-style dependency, not a rewrite.

**Cache computed report/HPP results.** Redis (Cloud Memorystore) or even an
in-process TTL cache (`cachetools`) in front of the heaviest endpoints in
`app/services/reports.py` and `app/services/hpp.py`, keyed by the relevant
date range / product IDs, invalidated on the writes that affect them
(new sales order, new production batch confirmation).

**Tune Cloud Run concurrency against real numbers.** `containerConcurrency:
80` and 512Mi memory were reasonable defaults, not measured — once there's
real traffic, load-test (e.g. `hey` or `k6` against a staging revision) to
find the actual concurrency-per-instance ceiling before OOM/latency
degrades, then set `--concurrency` and `--memory` from that data instead of
the default.

**Structured monitoring.** Cloud Run + Cloud SQL Insights-equivalent for
Supabase (their dashboard, or export `pg_stat_statements` to Cloud
Monitoring) so DB bottlenecks are visible before they're incidents. Alert on
Cloud Run `container/instance_count` hitting `maxScale` and on Postgres
connection count approaching the plan limit.

### Later (only if the business model changes)

These are **not** worth building speculatively — listed so the option is
understood, not as a roadmap commitment:

- **Multi-tenant / multi-store**: the schema and auth model
  (`authorized_owner_email`, single JWT) assume one business. Supporting
  multiple stores/tenants is a schema change (tenant_id on every table +
  row-level scoping), not a deployment change — much bigger effort than
  anything above, only justified if the product direction actually becomes
  multi-business.
- **Splitting services** (e.g. cutting-optimizer or reports as a separate
  deployable): only justified if one subsystem has a genuinely different
  scaling profile (e.g. cutting optimization becomes CPU-heavy batch work
  that shouldn't share an instance with request-serving code). Nothing in
  the current codebase indicates this yet.
- **Multi-region deployment**: irrelevant for a single-business Indonesia-
  based app; would only matter if Supabase's own region became a latency
  problem, which a single Jakarta-region Cloud Run deployment already
  avoids.

## Non-goals

Explicitly not recommending, given the current single-tenant, low-volume
shape of this app: Kubernetes, a message queue/event bus, microservices
decomposition, or a dedicated caching tier before there's measured load to
justify it. Revisit this document if traffic, tenant count, or team size
changes meaningfully — the numbers above (`pool_size`, `maxScale`,
`minScale`) should be re-derived from real Cloud Run/Supabase metrics at
that point, not carried forward as guesses.

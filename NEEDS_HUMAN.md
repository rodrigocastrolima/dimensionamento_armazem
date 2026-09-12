# Needs human action

Everything here is blocked on something only a human can supply (an account,
a credential, a business decision) — I skipped only the specific step that
needed it and kept going with everything else. Branch: `protocol/standardization`.

---

## 1. Supabase project (Phase 3 — data layer)

No Supabase project or credentials exist anywhere in this environment (no
`$DATABASE_URL`, no `.env` file, nothing under this project). The schema,
the dual-backend data-access layer (`warehouse/data_access.py` +
`warehouse/db_supabase.py`), and the one-time import script
(`warehouse/import_to_supabase.py`) are all written and unit-tested against
a mocked connection — but none of it has touched a real database yet.

**Three manual steps, in order:**

1. **Create a Supabase project** (or point at an existing self-hosted
   Postgres — the schema is plain SQL, nothing Supabase-specific). This
   needs an account with billing/project-creation access that I don't have.
   Kaizen already has a self-hosted Supabase used for other internal apps
   (see the `kaizen-institute-database` MCP connector and the
   `supabase-development` skill) — worth checking with whoever administers
   that whether this project should get its own database/schema there,
   or a separate project. Either way, note down the resulting Postgres
   connection string.

2. **Apply the SQL migrations** in `supabase/migrations/` (`0001` through
   `0005`, in that numeric order) against that database:
   ```bash
   for f in supabase/migrations/*.sql; do psql "$DATABASE_URL" -f "$f"; done
   ```
   or `supabase db push` if using the Supabase CLI. See `supabase/README.md`
   for what the tables are and why RLS is deliberately not enabled on them.

3. **Set the connection env var** wherever this app runs (a local `.env`
   for development, or the deployment platform's secret/env config —
   see Phase 6 below):
   ```
   DATABASE_URL=postgresql://...
   ```
   Also `pip install "psycopg[binary]"` (already in `requirements.txt`,
   commented as optional — only needed once a client actually sets
   `"data_source": "supabase"`).

**Once those three exist**, run the one-time import for SIA:
```bash
python -m warehouse.import_to_supabase --client sia
```
This reads `dados/master_data_classificado_v3.xlsx` (445 articles, 6
box_catalog rows, 2 rack_types, 5 zones — confirmed by a dry run against the
real file in this session) and writes it into `clients`/`articles`/
`box_catalog`/`rack_types`/`zones`, then **verifies the round trip itself**:
rereads through `db_supabase.load_*` (the same functions the pipeline would
use) and compares every field against what `io_excel` read, not just row
counts. It exits non-zero and prints exactly which rows disagree if
anything doesn't match.

I did not switch `dados/clients/sia.json`'s `"data_source"` to `"supabase"`
myself — SIA stays on `"excel"` (today's behavior, unchanged) until a human
runs the import and confirms it, then flips that one field.

If you set `$DATABASE_URL` before this session ends, tell me and I'll run
the import and report the verification result directly instead of leaving
this as a manual step.

---

## 2. Verify the Docker build (Phase 6 — deployment)

No Docker is installed in the environment this was written in (checked:
no `docker` on `PATH`). The `Dockerfile` is a straightforward
`python:3.12-slim` + `pip install -r requirements.txt` + copy + run, and
was reviewed carefully, but I could not actually run:
```bash
docker build -t warehouse-sizing .
docker run -p 8000:8000 --env-file .env warehouse-sizing
curl http://localhost:8000/healthz
```
Please run this once and tell me if it fails — I'll fix whatever the
error shows.

## 3. Pick and set up a hosting platform (Phase 6 — deployment)

The Dockerfile + `$HOST`/`$PORT` env vars are the generic part of
deployment; actually hosting this needs an account somewhere (a VM, a
container platform, whatever your team already uses) that I don't have
access to create. Once you've picked one:
1. Build and push the image there (or point its build step at this repo).
2. Set the environment variables from `.env.example` in that platform's
   secret/env config — at minimum `WAREHOUSE_CLIENT`; `DATABASE_URL` too,
   once item 1 above is done and a client is actually on Supabase.
3. Point it at `/healthz` for its health check, if it supports one.

See `DEPLOYMENT.md` for the full picture, including why an
`"excel"`-data_source client needs extra thought (its workbook is
deliberately not in the image by default — see that doc's "data
persistence" section) and why a `"supabase"`-data_source client doesn't.

---

## Template for future entries

Each new blocked item during later phases gets its own numbered section
above, in the same shape: what's needed, why I can't do it myself, and the
exact command/action that unblocks it.

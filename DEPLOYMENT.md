# Deployment

Before this, the app only ever ran on `127.0.0.1` via `python run_web.py`
or `.claude/launch.json` — no container, no CI, no hosting target at all.
This is the minimal path to run it for real, not a specific hosting
provider recommendation (that choice needs a human with an account —
see `NEEDS_HUMAN.md`).

## What changed to make this possible

- `run_web.py` now reads `$HOST`/`$PORT` (falls back to the same
  `127.0.0.1:8000` as always if unset) instead of hardcoding them.
- `warehouse/web.py` has a `GET /healthz` endpoint (`{"status": "ok",
  "client": ..., "data_source": ...}`) for a load balancer/orchestrator's
  readiness probe.
- `Dockerfile` + `.dockerignore` containerize the app.
- `.env.example` documents every environment variable the app reads.

None of this touches the pipeline or the data model — it's purely "how
does the existing process get a port and its config."

## Build and run locally

```bash
docker build -t warehouse-sizing .
docker run -p 8000:8000 --env-file .env warehouse-sizing
```

Then open `http://localhost:8000`. `/healthz` should return
`{"status":"ok","client":"sia","data_source":"excel"}` (or whichever
client `$WAREHOUSE_CLIENT` selects).

**I could not verify this `docker build` actually succeeds** — there is no
Docker installed in the environment this was written in (checked: no
`docker`/`psql` on `PATH`). The Dockerfile is straightforward (standard
`python:3.12-slim` + `pip install -r requirements.txt` + copy + run) and
was reviewed carefully, but a human needs to run the build once — see
`NEEDS_HUMAN.md`.

## The data-persistence question

A container is ephemeral and stateless by default. This app has two very
different answers depending on a client's `data_source`:

- **`"supabase"` clients**: fully compatible with a normal stateless
  container. The only thing the container needs is `$DATABASE_URL` — no
  local file, no volume, no baked-in data. This is the recommended target
  for anything actually deployed, not just run locally.
- **`"excel"` clients**: the workbook is a local file
  (`dados/master_data_classificado_v3.xlsx` for SIA today), and it is
  **deliberately excluded** from both git (`.gitignore`, Phase 4) and the
  default Docker build context (`.dockerignore`) — the same "never let
  real client data become a binary blob living somewhere uncontrolled"
  reasoning applies doubly to a container image that might get pushed to
  a registry. To actually deploy an Excel-backed client, pick one,
  deliberately:
  1. Temporarily remove/comment the relevant `dados/*.xlsx` line in
     `.dockerignore` for that one build, so the workbook gets baked into
     the image — simplest, but means rebuilding the image every time the
     workbook changes, and the data now lives inside a container image.
  2. Mount the workbook as a volume at the path
     `dados/clients/<name>.json` points `master_data` to
     (`docker run -v /host/path/master_data.xlsx:/app/dados/master_data.xlsx ...`)
     — data stays outside the image, but the container is no longer
     fully stateless/portable.
  3. **Migrate the client to Supabase first** (see `supabase/README.md`,
     `warehouse/import_to_supabase.py`) and flip their config's
     `data_source` — then neither of the above is needed.

Option 3 is the actual fix; options 1-2 are there because Supabase isn't
live yet for anyone (see `NEEDS_HUMAN.md`).

## Environment variables

See `.env.example` for the full list with explanations. In short:
`WAREHOUSE_CLIENT` (which `dados/clients/<name>.json`), optionally
`WAREHOUSE_MASTER_DATA` (override), `DATABASE_URL` (Supabase clients
only), `HOST`/`PORT` (most platforms set `PORT` for you automatically).

## What's still needed from a human

Picking an actual hosting platform, creating an account there, and
wiring `$DATABASE_URL`/other secrets into its environment/secret store —
none of that can be done without an account. See `NEEDS_HUMAN.md`.

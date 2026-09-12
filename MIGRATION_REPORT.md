# Migration report — protocol/standardization

For Younes and Rodrigo, one pass. Branch: `protocol/standardization`
(not pushed anywhere — local only, see "What was not done" below).
Detailed running commentary lives in `MIGRATION_LOG.md`; this is the
summary.

## Before you look at the diff: one commit isn't mine

`git log --oneline main..HEAD` shows 7 commits. The first,
`ed99a11 "Snapshot pre-existing uncommitted engine work..."`, is **not**
from this migration — it's ~2,800 lines of already-existing, uncommitted
work that was sitting in the working tree on `main` before this branch
existed (the OVERSIZE handling, autonomy sizing, shelf-packing layout,
etc. narrated in `CLAUDE.md`). It predates this session. I committed it
as its own clearly-labelled commit purely so it wouldn't be lost and so
it wouldn't get mixed into the migration's own diffs, making them harder
to review. **Everything from `Phase 1` onward is this migration's work.**

## What changed, by phase

**Phase 1 — client config** (`ee481e2`): `Config.for_client(name)` loads
`dados/clients/<name>.json` instead of hardcoded values in `config.py`.
`dados/clients/sia.json` holds SIA's values (byte-identical output
verified: every cell of every `resultados.xlsx` sheet, not just the
totals). `dados/clients/_template.json` is the copy-and-fill starting
point for a new client. `main.py`/`web.py` updated to use it (`--client`
flag / `$WAREHOUSE_CLIENT`, default `"sia"` — unchanged behavior for
anyone not setting it).

**Phase 2 — two correctness fixes** (`fe2a972`):
1. The stale "Rack Cantilever" row (`Aceita=LONGO`, a load type removed
   from the code per `CLAUDE.md`) in the **live SIA workbook** was
   actually **removed**, not just flagged — it was silently parsing to a
   dead rack typology (accepts nothing, contributes zero capacity). A
   safety backup was kept alongside the file
   (`dados/master_data_classificado_v3.xlsx.pre-longo-cleanup.bak`, now
   under `dados/archive/`).
2. Two new warnings so this class of failure can't be silent again:
   `io_excel.load_rack_types` warns on any unrecognized `Aceita` token;
   `racking.verificar_chaves_substring_sem_correspondencia` warns when a
   *configured* rack-name-matching rule (oversize/pairing/module-limits/
   picking-manual) matches zero real racks — "a business rule is silently
   dead but the run still looks clean" was the single most dangerous
   failure mode identified in the original audit.
3. **Found and fixed a live instance of exactly that bug while wiring the
   check in**: `web.py`'s `/api/gerar` and `/api/render-layout` were
   building each request's config from a bare `Config(...)`, silently
   discarding the loaded client config on every single request. Only
   ever "worked" because the hardcoded defaults happened to equal SIA's
   values. Fixed with `dataclasses.replace(cfg, ...)`.
4. Running the fixed pipeline against live SIA data surfaced one real,
   previously invisible gap: SIA's config expects a "wide-span" rack
   typology that, per `CLAUDE.md`, was proposed but seemingly never
   actually added to the real `Racks` sheet. Not a code bug — it
   correctly falls through to the paired-rack fallback — but it's now a
   visible warning instead of silent.

**Phase 3 — Supabase data layer** (`23f51a8`): SQL schema
(`supabase/migrations/`) mirroring the four sheets plus a `clients`
table for multi-tenancy. `Config.data_source` ("excel" default |
"supabase") switches `warehouse/data_access.py` between `io_excel.py`
and the new `warehouse/db_supabase.py` — every caller gets the same
dataclasses either way. `warehouse/import_to_supabase.py` is the
one-time import, with **built-in verification** (rereads through the
real read path and diffs every field, not just row counts). **Not yet
run against anything real** — no Supabase project/credentials exist in
this environment (see "Needs human action" below).

**Phase 4 — repo cleanup** (`8b7024f`): broadened `.gitignore` for real
client data (was 3 narrow filenames); untracked (`git rm --cached`, kept
on disk) the 6 data files that were already committed on `main` before
this branch — their content stays in git history, purging that too
needs a separate history rewrite I did not do. Removed the abandoned
FBX→GLB Blender conversion scripts (already documented as producing an
unusable result) and rewrote their README as a permanent post-mortem
instead of deleting the knowledge. Moved 23 superseded/ad-hoc-versioned
data files into `dados/archive/` with a manifest; deleted pure
regenerable pipeline output (`resultados*.xlsx`, `pecas_tipo_caixa.xlsx`,
a stray `scratch_out.xlsx`).

**Phase 5 — `PROTOCOL.md`** (`b61b9a0`): what this is, the CORE engine
(named files), the CLIENT-SPECIFIC surface, exact onboarding steps, and
— asked for explicitly — an honest account of what is **not** yet
client-agnostic. Most importantly: `pecas_por_carro` ("pieces per
vehicle") is structural to the sizing math, not a config value; a
non-automotive client can't use this system without a real modeling
conversation first, and this migration did not attempt to change that.

**Phase 6 — deployment readiness** (`e70d0c5`): `Dockerfile` +
`.dockerignore`, `$HOST`/`$PORT` support in `run_web.py`, a `/healthz`
endpoint, `.env.example`, `DEPLOYMENT.md`. Also removed three
dependencies (`py3dbp`, `ortools`, `pandas`) confirmed unused anywhere in
the codebase, since `ortools` in particular would bloat every image
build. The `docker build` itself is **not verified** — no Docker in this
environment.

## Files touched (Phase 1 → 6, excludes the pre-existing snapshot commit)

```
 .dockerignore                                       | new
 .env.example                                        | new
 .gitignore                                          | modified
 DEPLOYMENT.md                                        | new
 Dockerfile                                          | new
 MIGRATION_LOG.md                                    | new (running log)
 NEEDS_HUMAN.md                                      | new
 PROTOCOL.md                                         | new
 dados/ARMAZÉM_final Copy.xlsm                       | untracked (git rm --cached)
 dados/archive/README.md                             | new
 dados/clients/_template.json                        | new
 dados/clients/sia.json                               | new
 dados/master_data.xlsx                              | untracked (git rm --cached)
 dados/master_data_classificado_v1*.xlsx (4 files)    | untracked (git rm --cached)
 requirements.txt                                     | modified (removed 3 unused deps, added psycopg)
 run_web.py                                            | modified ($HOST/$PORT)
 supabase/README.md + migrations/0001-0005            | new
 tests/test_db_supabase.py                            | new
 tests/test_import_to_supabase.py                     | new
 tests/test_smoke.py                                  | modified (+10 tests)
 tools/README_conversao_3d.md                        | modified (post-mortem framing)
 tools/fbx_para_glb.py, tools/reenquadrar_glb.py      | deleted
 warehouse/cenario_autonomia.py                       | modified (--client, data_access)
 warehouse/config.py                                  | modified (for_client, data_source)
 warehouse/data_access.py                             | new
 warehouse/db_supabase.py                             | new
 warehouse/import_to_supabase.py                      | new
 warehouse/io_excel.py                                | modified (dimensao_segura extracted, Aceita warning)
 warehouse/main.py                                    | modified (--client, data_access, config-match warning)
 warehouse/racking.py                                 | modified (verificar_chaves_substring_sem_correspondencia)
 warehouse/web.py                                     | modified (data_access, dataclasses.replace fix, /healthz)
```
40 files changed, 2065 insertions(+), 564 deletions(-) across Phases 1-6.

Not touched by this migration at all: `boxing.py`, `area.py`, `layout.py`
(the actual binning/racking/layout math — exactly as intended, this was a
protocol/config/data migration, not an algorithm change).

## Test results

- `pytest`: **59/59 passed** (was 42 before this branch; 17 new tests
  across `test_smoke.py`, `test_db_supabase.py`, `test_import_to_supabase.py`).
- Full pipeline (`python -m warehouse.main --client sia`): re-run after
  every phase, **total area unchanged throughout: 89.9 m² racks / 194.4 m²
  layout / 298.3 m² total** — confirmed via a full `openpyxl` sheet-by-sheet
  diff after Phase 1 (every cell, not just totals), and via the printed
  totals after every subsequent phase.
- `/api/gerar`, `/api/articles`, `/api/racks`, `/healthz`: exercised
  end-to-end via FastAPI's `TestClient` (no Docker/server process needed).
- `cenario_autonomia.py --client sia --carros 10`: re-run successfully
  through the new `data_access` layer.
- Supabase backend (`db_supabase.py`, `import_to_supabase.py`): unit
  tested against a mocked connection (no real Postgres available — see
  below). Not yet validated against a real database.
- Docker build: not run (no Docker in this environment).

## What was not done / needs a decision

- **Nothing was pushed anywhere.** This all lives on the local
  `protocol/standardization` branch. `main` is untouched.
- **`dados/clients/sia.json` still has `"data_source": "excel"`** —
  deliberately not flipped to `"supabase"`, since nobody has run the
  import against a real database yet.
- **Git history still contains the old client-data blobs** for the 6
  files untracked in Phase 4 (old commits, before this branch). Fully
  purging those needs a history rewrite (`git filter-repo` or similar) +
  a force-push, which I did not do without an explicit ask — flagging it
  here as a decision for you two, not something I decided unilaterally.
- **The Excel schema is still brittle** (see `PROTOCOL.md`'s honest
  section) — `Caixas`/`Racks`/`Zonas` are still read by column position,
  not header name. Not fixed in this migration; flagged as a known gap.

## NEEDS_HUMAN.md — current contents in full

1. **Supabase project**: no project/credentials exist anywhere in this
   environment. Three steps once you have an account: create the
   project (or point at existing self-hosted Postgres), apply
   `supabase/migrations/0001`-`0005` (`psql` or `supabase db push`), set
   `$DATABASE_URL`. Then `python -m warehouse.import_to_supabase --client sia`
   does the one-time import and verifies itself. If you set
   `$DATABASE_URL` and tell me, I'll run it and report back instead of
   leaving it manual.
2. **Verify the Docker build** — no Docker installed here to test
   `docker build -t warehouse-sizing .` / `docker run` / `curl /healthz`.
3. **Pick and set up a hosting platform** — needs an account I can't
   create; `DEPLOYMENT.md` has the full picture once you've chosen one.

## How to review this

```bash
git log --oneline main..HEAD          # 7 commits, first one isn't mine (see above)
git diff ed99a11..HEAD --stat         # my work only
```

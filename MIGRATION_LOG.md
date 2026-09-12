# Migration log — protocol/standardization

Running checklist for turning `dimensionamento_armazem` into a reusable
protocol. Branch: `protocol/standardization`. Base commit before this work:
`ed99a11` (snapshot of pre-existing uncommitted engine work, not authored in
this session — see its commit message).

Do not push. Do not commit real client data beyond what was already tracked
before this branch. See `NEEDS_HUMAN.md` for anything blocked on a human.

## Phase 0 — setup
- [x] Created branch `protocol/standardization` off `main`.
- [x] Committed pre-existing uncommitted engine changes as a baseline snapshot
      (code/docs only, no data files) — commit `ed99a11`.
- [x] This log file.

## Phase 1 — client-config loader
- [x] `Config.for_client(name)` loader in `warehouse/config.py`.
- [x] `dados/clients/sia.json` with every CLIENT-SPECIFIC value from the audit.
- [x] `dados/clients/_template.json` placeholder client.
- [x] `warehouse/main.py` uses `Config.for_client(...)` (`--client`/`$WAREHOUSE_CLIENT`, default "sia").
- [x] `warehouse/web.py` uses `Config.for_client(...)` (`$WAREHOUSE_CLIENT`, default "sia"; `$WAREHOUSE_MASTER_DATA` still overrides on top).
- [x] Verify: `pytest` passes (42/42, unchanged).
- [x] Verify: before/after run of `python -m warehouse.main` on SIA data —
      every cell of every sheet in resultados.xlsx identical (openpyxl diff,
      not just the printed totals).
- [x] Verify: `Config.for_client("_template")` instantiates without error;
      `Config.for_client("does_not_exist")` raises `FileNotFoundError`.
- Commit: `ee481e2`.

## Phase 2 — correctness gaps
- [x] Stale "Rack Cantilever"/LONGO row: **removed from the live workbook**
      (`dados/master_data_classificado_v3.xlsx`, Racks sheet), not just
      warned about — it's dead per CLAUDE.md's own "cantilever/LONGO
      removida" decision, so leaving it was actively misleading. Surgical
      zip-XML edit (backup kept: `dados/master_data_classificado_v3.xlsx.pre-longo-cleanup.bak`),
      verified via openpyxl + `io_excel.load_rack_types` + full pipeline
      re-run (totals unchanged: 89.9/194.4/298.3 m², as expected since the
      row already contributed 0 capacity). This edit is **not committed**
      (real client data) — see MIGRATION_REPORT.md.
- [x] `io_excel.load_rack_types` now warns loudly (AVISO, matching the
      existing print-based convention) whenever an "Aceita" token doesn't
      match a `LoadType`, and specifically flags a rack left with an empty
      `aceita` (accepts nothing, contributes 0 capacity everywhere) —
      defense-in-depth so this class of drift can't be silent again, even
      though the one known instance is now actually fixed above.
- [x] New `racking.verificar_chaves_substring_sem_correspondencia(rack_types, cfg)`:
      warns when a *configured* substring key (`oversize_racks`/
      `oversize_par_racks`/`max_modulos_por_tipologia`/`empilhamento_por_nivel`/
      `picking_manual_racks`) matches zero real rack names — the "looks like
      clean success while a business rule is silently dead" failure mode.
      Wired into `main.run()` and `web.py /api/gerar`, before
      `com_par_oversize` (which would otherwise mask the check with its own
      synthetic rack name).
- [x] **Found and fixed a live instance of exactly this bug while wiring the
      check in**: `web.py`'s `/api/gerar` and `/api/render-layout` built each
      request's `local_cfg` from a bare `Config(...)`, silently discarding
      the loaded client config (only "worked" because the dataclass
      defaults happened to equal SIA's values). Fixed with
      `dataclasses.replace(cfg, ...)` in both places.
- [x] Confirmed against the live SIA workbook: `oversize_racks=("wide","bulk")`
      matches **no** rack in the current "Racks" sheet — a real, previously
      invisible gap (CLAUDE.md documents this as a "tipologia proposta" that
      was apparently never actually added to the sheet). Not a code bug —
      correctly falls through to the paired-rack/floor-stacking fallback —
      but now visible in the Avisos sheet / `/api/gerar` JSON instead of silent.
- [x] Tests added: `test_chave_sem_correspondencia_gera_aviso`,
      `test_chave_que_corresponde_nao_gera_aviso`, `test_chave_vazia_nunca_gera_aviso`,
      `test_max_modulos_por_tipologia_usa_as_chaves_do_dict`,
      `test_token_desconhecido_na_coluna_aceita_avisa`, `test_token_conhecido_nao_avisa`.
      Full suite: 48/48 passed.

## Phase 3 — Supabase data layer
- [x] SQL migration files: `supabase/migrations/0001_clients.sql` through
      `0005_zones.sql` — mirror `articles`/`box_catalog`/`rack_types`/`zones`
      field-for-field against `warehouse/models.py`'s dataclasses, plus
      `clients` for multi-tenancy (`client_slug` FK, matches the Phase 1
      `dados/clients/<slug>.json` naming). `supabase/README.md` explains the
      access model and why RLS is deliberately not enabled (server-side
      direct-Postgres access only, no browser-facing app on top yet).
- [x] `Config.data_source: str = "excel"` (new client-config field, default
      "excel" — zero behavior change until a client's JSON opts in).
- [x] `warehouse/data_access.py` — the Excel/Supabase switch; `main.py`,
      `web.py`, `cenario_autonomia.py` now all call it instead of
      `io_excel.load_*` directly.
- [x] `warehouse/db_supabase.py` — Supabase-backed reader, returns the exact
      same `Article`/`BoxType`/`RackType`/`Zona` dataclasses as `io_excel.py`.
      `psycopg` import is lazy (only paid for by clients who use it).
      Reuses `io_excel.dimensao_segura` (extracted as a shared helper) so
      the sem-geometria fallback behaves identically on both backends, and
      the Phase-2 "unrecognized token in Aceita" warning too.
- [x] `warehouse/import_to_supabase.py` — one-time import + built-in
      verification (rereads via `db_supabase.load_*`, the actual pipeline
      path, and diffs every field against what `io_excel` read — not just
      row counts). Dry-run against the real SIA workbook confirms exact
      counts: 445 articles, 6 box_catalog, 2 rack_types, 5 zones.
- [x] `NEEDS_HUMAN.md` created: no Supabase project/credentials exist in
      this environment (checked: no `$DATABASE_URL`, no `.env` anywhere) —
      the 3 manual steps are there (create project, apply migrations, set
      `$DATABASE_URL`). SIA's `dados/clients/sia.json` stays on
      `"data_source": "excel"` until a human runs the import and flips it.
- Verified: pytest 59/59 (10 new tests: `test_db_supabase.py`,
  `test_import_to_supabase.py`, mocked-connection only, no real DB in this
  environment); full pipeline + `/api/gerar` + `cenario_autonomia.py` CLI
  all re-run end-to-end through the new `data_access` layer, totals
  unchanged (89.9/194.4/298.3 m²).

## Phase 4 — repo cleanup
- [ ] `.gitignore` excludes live client workbooks / real data.
- [ ] Decision + action on abandoned FBX/Blender tooling.
- [ ] Decision + action on ad-hoc versioned filenames in `dados/`.

## Phase 5 — PROTOCOL.md
- [ ] Written at project root per the requested outline.

## Phase 6 — deployment readiness (if time allows)
- [ ] Minimal deploy path + `NEEDS_HUMAN.md` entries.

## Finish
- [ ] `MIGRATION_REPORT.md` written.

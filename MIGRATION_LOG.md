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
- [ ] `Config.for_client(name)` loader in `warehouse/config.py`.
- [ ] `dados/clients/sia.json` with every CLIENT-SPECIFIC value from the audit.
- [ ] `dados/clients/_template.json` placeholder client.
- [ ] `warehouse/main.py` uses `Config.for_client(...)`.
- [ ] `warehouse/web.py` uses `Config.for_client(...)`.
- [ ] Verify: `pytest` passes.
- [ ] Verify: before/after run of `python -m warehouse.main` on SIA data
      produces identical output.
- [ ] Verify: `Config.for_client("_template")` instantiates without error.

## Phase 2 — correctness gaps
- [ ] Decide + fix stale "Rack Cantilever"/LONGO row (remove from live data
      vs. loud warning on unrecognized `Aceita` token).
- [ ] Warning when a client's rack names match none of the configured
      substring keys (oversize/pairing/module-limit/picking-manual all inert).

## Phase 3 — Supabase data layer
- [ ] SQL migration files: `clients`, `articles`, `box_catalog`, `rack_types`,
      `zones`.
- [ ] Data-access layer selectable Excel vs Supabase by config.
- [ ] One-time import script (SIA workbook → Supabase).
- [ ] Row-count verification against the Excel loader.
- [ ] `NEEDS_HUMAN.md` entries if no project/credentials exist.

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

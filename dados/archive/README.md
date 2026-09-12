# Archive — superseded client data

Everything in this folder is **historical**: old versions of the SIA master
data, and the exploratory analysis exports that backed decisions already
made and documented in `CLAUDE.md`. None of it is read by any code path —
the pipeline only ever reads the current file at `dados/master_data_classificado_v3.xlsx`
(see `dados/clients/sia.json` → `master_data`).

This whole folder is gitignored (real client data, never committed as a
binary blob — see `.gitignore` and `MIGRATION_LOG.md` Phase 4). It exists
only so the ad-hoc `v1`/`v2`/`_antigo`/`.bak`/`Copy` filename-versioning that
used to sit directly in `dados/` has one clearly-labelled place to live
instead of crowding the folder that also holds the live file, not so it can
keep growing the same way going forward.

**Going forward, this pattern should stop being used at all**: once SIA's
data is imported into Supabase (see `supabase/README.md` and
`warehouse/import_to_supabase.py` — not yet done, see `NEEDS_HUMAN.md`),
Supabase becomes the single current copy. There is no more "v4.xlsx" to
create by hand — re-running the import script replaces the client's rows
outright (see its docstring), and if real version history is ever wanted,
that's a job for actual database migrations/audit tables, not filenames.

## What's here

Master-data snapshots (superseded by `master_data_classificado_v3.xlsx`,
the current file):
- `ARMAZÉM_final Copy.xlsm` — earliest raw file found in the working tree
  (7.3 MB — looks like the original client-supplied workbook before any
  processing/classification).
- `master_data.xlsx` — earliest unversioned processed copy.
- `master_data_classificado_v1.xlsx` + `.backup.xlsx` + `.pre_xs.bak.xlsx` +
  `.pre_zonas_montagem.bak.xlsx` — the v1 family and its own internal
  checkpoints (filenames suggest "before adding the XS box size" and
  "before adding assembly zones").
- `master_data_classificado_v2.xlsx` + `_16h27_antigo.xlsx` + `_antigo.xlsx`
  + `_pre_corredores_antigo.xlsx` — the v2 family (the last one: "before
  adding aisles/corridors").
- `master_data_classificado_v3.xlsx.pre-longo-cleanup.bak` — safety copy
  made during *this* migration, immediately before removing the stale
  "Rack Cantilever"/LONGO row from the live v3 file (see MIGRATION_LOG.md
  Phase 2). Kept here rather than deleted, same reasoning as everything
  else in this folder.

Derived analysis exports (not master data itself — comparisons/estimates
that fed a decision, per `CLAUDE.md`):
- `comparacao_pecas_por_sku.xlsx`, `peso_max_estimado_v3.xlsx`.
- `estudo_*.csv` (12 files) — the exploratory studies behind the closed
  decisions in `CLAUDE.md` (pallet overhang tolerance, box-size dimensional
  limits, autonomy-box-count experiments, etc.). Kept as the evidence
  trail for those decisions, not as data the pipeline depends on.

**Not archived** (deleted instead, because they're pure pipeline output —
regenerated identically by rerunning the tool, never a source of anything):
`dados/resultados.xlsx`, `dados/resultados_v3.xlsx`,
`dados/pecas_tipo_caixa.xlsx`, and a stray `scratch_out.xlsx` at the
project root.

# Protocol

## What this is

A warehouse-sizing engine: given a client's parts list, box/pallet catalog,
rack typologies, and functional zones, it decides how each part gets
packed, how many racks are needed, lays them out physically (aisles,
double rows, corridors), and renders the result in 3D — see
`docs/LOGICA_BACKEND.md` for the full pipeline.

This document says what part of that is CORE (works the same for any
client) versus CLIENT-SPECIFIC (changes per client), how to onboard a new
one today, and — just as importantly — what still isn't actually
client-agnostic yet, so nobody overclaims what this migration achieved.

---

## The CORE engine

Everything below takes the same input shape and produces the same output
shape regardless of which client's data it's fed. None of it names SIA, a
SKU, or a rack-name substring anywhere in its logic:

- **Domain model** — `warehouse/models.py`: `Article`, `BoxType`,
  `RackType`, `Zona`, and every OUTPUT dataclass (`BoxAssignment`,
  `PosicaoChao`, `WarehouseLayout`, ...).
- **Passo 0 (autonomy sizing)** — `warehouse/boxing.py`
  (`stock_para_autonomia`), driven by generic parameters
  (`Config.autonomia_minima_carros`), not client identity.
- **Passo 1 (repacking / best-fit box choice)** — `warehouse/boxing.py`
  (`pieces_per_box`, `choose_load`, `_assenta_na_palete`).
- **Passo 2 (racking / 2D bin packing)** — `warehouse/racking.py`
  (`rack_capacity`, `allocate_to_racks`, the `rectpack`-based packer).
- **Passo 3 (floor area)** — `warehouse/area.py`.
- **Passo 3b (physical layout — double rows, aisles, shelf-packing across
  typologies, perimeter minimization)** — `warehouse/layout.py`, in full.
- **The data-access switch and both backends' *mechanism*** —
  `warehouse/data_access.py`, `warehouse/io_excel.py`,
  `warehouse/db_supabase.py` (the *code* is core; the *data* they read is
  not — see below).
- **The web API and 3D viewer scaffolding** — `warehouse/web.py`'s
  endpoints and `warehouse/static/index.html`'s Three.js rendering,
  canvas 2D arrange tool, and drag/rotate mechanics — as *machinery*; what
  gets drawn inside them is largely client-specific (see below).
- **Tests** — `tests/test_smoke.py`, `tests/test_db_supabase.py`,
  `tests/test_import_to_supabase.py` (59 tests as of this migration).

## The CLIENT-SPECIFIC surface

Everything a new client needs to supply, and nothing else, if the answer
below to "what's not yet client-agnostic" doesn't apply to them:

1. **One config file**: `dados/clients/<slug>.json`, loaded by
   `Config.for_client(slug)` (`warehouse/config.py`). Holds exactly the
   values the CORE vs CLIENT-SPECIFIC audit identified as varying per
   client — file paths, `autonomia_minima_carros`, per-SKU overrides, and
   the rack-name substring keys (`oversize_racks`, `oversize_par_racks`,
   `max_modulos_por_tipologia`, `empilhamento_por_nivel`,
   `picking_manual_racks`) that decide which special-case business rules
   apply to which of *this client's* rack typologies. Unknown fields in
   this file raise loudly at load time — a client config can't
   accidentally smuggle in a CORE tuning parameter. `dados/clients/_template.json`
   is the copy-and-fill starting point.
2. **Their data**, in one of two forms, chosen by that file's
   `data_source` field:
   - `"excel"` (works today, no setup): a workbook with the same four
     sheets and column contract as `dados/master_data_classificado_v3.xlsx`
     — see the schema note below, this is the part that *isn't* fully solved.
   - `"supabase"` (schema and import script built, not yet live for
     anyone — see `NEEDS_HUMAN.md`): one row per client in the `clients`
     table (`client_slug`), and their `articles`/`box_catalog`/
     `rack_types`/`zones` rows — see `supabase/README.md` and
     `warehouse/import_to_supabase.py`.
3. **The 3D vehicle prop and building name, if they want their own look**:
   `warehouse/static/carro.js` (the procedural "BEN" model + its
   `BEN_DIM` constant) and the "Cubo" building-outline references
   scattered through `warehouse/static/index.html`. This is cosmetic only
   — it never changes a single number the pipeline computes — but it is
   still hardcoded, per-client, in JS, not config. Not addressed by this
   migration.

---

## Onboarding a new client today

1. Pick a slug (e.g. `acme`).
2. Get their parts/boxes/racks/zones data into the same shape as
   `dados/master_data_classificado_v3.xlsx`: sheets named exactly `Peças`,
   `Caixas`, `Racks`, `Zonas`; `Peças` columns matched **by header name**
   (`io_excel.load_articles`); `Caixas`/`Racks`/`Zonas` columns matched
   **by position** (`io_excel.load_catalog`/`load_rack_types`/`load_zones`
   — still positional, a real gap, see below) — *or* get it into Supabase
   instead (`warehouse/import_to_supabase.py --client acme`, once
   `NEEDS_HUMAN.md`'s Supabase setup is done).
3. `cp dados/clients/_template.json dados/clients/acme.json` and fill it
   in: the data path (or confirm `data_source: "supabase"`), their
   autonomy policy, and — this step needs a human who's looked at their
   actual `Racks` sheet — the substring keys that identify which of
   *their* rack typology names should get which special handling.
4. Run `python -m warehouse.main --client acme` (or
   `$WAREHOUSE_CLIENT=acme` for the web UI) and **read the Avisos sheet /
   `avisos` JSON**. `racking.verificar_chaves_substring_sem_correspondencia`
   (added in this migration) will name any configured substring key that
   matched none of this client's rack names — a wrong or stale key is now
   a loud warning, not a silently-dead business rule. Fix the JSON or the
   rack names, or explicitly accept the warning if that rule genuinely
   doesn't apply to this client.
5. If they want a bespoke 3D look: swap `carro.js`'s model/dimensions and
   the "Cubo" references in `index.html`. Optional, cosmetic, still code.

That's the honest minimum: a slug, their real data (this is not small —
it's their whole catalog — but it's *data*, not a code change), a handful
of config values, and one human judgment call about rack-name matching
per step 3. No edit to `boxing.py`/`racking.py`/`area.py`/`layout.py` is
needed for any client whose business fits the model below.

---

## What is honestly NOT yet client-agnostic

**"Pieces per vehicle" is structural, not a config value.** The entire
Passo 0 sizing engine (`boxing.stock_para_autonomia`) and the picking-zone
logic in `racking.allocate_to_racks` (`num_unidades_picking`) are built
around `Article.pecas_por_carro` — literally "pieces consumed per car
built." `io_excel.load_articles` (and `db_supabase.load_articles`) *raise
an error* if a row doesn't have it. A client whose stock-sizing logic
isn't "N units of this part per unit of something we produce/ship" — a
distribution warehouse with no assembly line, say — cannot use this
system as-is. Someone would have to decide what their equivalent
consumption-rate unit is and populate every SKU's row with it, and that's
a modeling decision, not a config change. This migration did not attempt
to generalize that concept; it would touch the actual sizing math, which
was explicitly out of scope.

**The Excel schema is still a brittle string/position contract.** Sheet
names (`Peças`/`Caixas`/`Racks`/`Zonas`) and the `Peças` header names are
hardcoded literal strings in `io_excel.py`; a client whose workbook uses
different names (even a translated set) fails loudly — which is at least
honest, but it's still a manual data-reshaping step for every new Excel
client, not something `dados/clients/<slug>.json` can paper over.
`Caixas`/`Racks`/`Zonas` are still read **by column position**, not by
header name — a reordered column there is silently wrong, not a loud
error, unlike `Peças` (fixed in an earlier pass, not this one). Supabase
side-steps this (real columns, no position/CSV-cell parsing), but a
client has to actually be migrated there for that to help.

**The rack-name substring-matching mechanism is inherently manual.**
Phase 2 of this migration made it *safe* (a wrong or stale key now warns
loudly, see `verificar_chaves_substring_sem_correspondencia`) but did not
make it *automatic*. Whether "porta-paletes" or "wide-span" or some other
string identifies a client's pallet racks is still something a human
decides by reading that client's `Racks` sheet.

**The 3D viewer's cosmetics are 100% hardcoded per client** (see above) —
this migration didn't touch `carro.js`/`index.html`'s client-specific
content at all, only confirmed it's cosmetic (never feeds the actual
area/rack-count numbers).

**Supabase is built but not live.** The schema, dual-backend
data-access layer, and import script all exist and are unit-tested
(mocked connection — no real Postgres available in the environment this
was built in). Nobody's data has actually gone through it yet. See
`NEEDS_HUMAN.md`.

---

## Definition of done: "a new client is fully onboarded"

1. `dados/clients/<slug>.json` exists and `Config.for_client(<slug>)`
   loads without error.
2. Their data is reachable: either the Excel workbook at the configured
   path parses through `io_excel.load_articles/load_catalog/load_rack_types/load_zones`
   without error, or (Supabase clients) `warehouse/import_to_supabase.py`
   has been run and its built-in verification passed (Excel-vs-Supabase
   row-and-field diff, zero problems).
3. `python -m warehouse.main --client <slug>` completes and produces a
   `resultados.xlsx` with a plausible total area.
4. **A human has read the Avisos sheet for this run** and either resolved
   every "chave ... não corresponde a nenhuma tipologia" warning or
   explicitly decided it doesn't apply to this client. This is the one
   step in this whole list that cannot be automated away — it's the
   judgment call the whole substring-matching mechanism depends on.
5. `WAREHOUSE_CLIENT=<slug>` + the web UI has been smoke-tested at least
   once (`/api/gerar` returns 200, the 3D view renders something).
6. (Optional, cosmetic) the 3D vehicle prop / building references have
   been swapped if the client wants their own look — not required for
   the dimensioning numbers to be correct.

No step above requires editing `boxing.py`, `racking.py`, `area.py`, or
`layout.py` — if it does, something in this list broke down, or this
client falls into the "pieces per vehicle" gap above and needs a real
modeling conversation before onboarding, not just a config file.

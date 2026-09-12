# Supabase schema

Mirrors the four sheets of the client master-data workbook (`Peças` ->
`articles`, `Caixas` -> `box_catalog`, `Racks` -> `rack_types`, `Zonas` ->
`zones`), plus a `clients` table so more than one client's data can coexist
in the same database. See `warehouse/db_supabase.py` for the reader that
turns these rows back into `Article`/`BoxType`/`RackType`/`Zona` (the exact
same dataclasses `io_excel.py` produces from Excel), and
`warehouse/data_access.py` for the `Config.data_source` switch between the
two.

## Applying the migrations

Run the files in `migrations/` in numeric order against your Supabase
project's Postgres database. Either:

```bash
supabase db push          # if you use the Supabase CLI and have it linked
```

or directly with `psql` (works against any Postgres, Supabase or not):

```bash
for f in supabase/migrations/*.sql; do
  psql "$DATABASE_URL" -f "$f"
done
```

## Access model

These tables are read by the Python backend over a direct Postgres
connection (`$DATABASE_URL`), never by a browser client with a public/anon
key -- there is no end-user-facing app on top of this data. Row Level
Security is deliberately **not** enabled here: RLS exists to safely scope a
public anon key to "what this signed-in visitor may see," and there is no
such key or visitor in this project's access pattern. If a browser-facing
app is ever built on top of these tables directly (rather than through this
Python service), RLS should be added at that point, scoped to whatever that
app's auth model turns out to be -- see the Kaizen `supabase-development`
skill for the house conventions on that (RLS, publishable keys, the
`app_`-prefix table-naming convention, magic-link/OTP auth). It does not
apply here because this project has no such app yet.

## Multi-client isolation

Every data table carries `client_slug` (FK to `clients.slug`). A row's
`client_slug` must match `Config.client_name` (the same client identity
Phase 1 already uses for `dados/clients/<name>.json`) for it to be loaded --
see `warehouse/db_supabase.py`. Nothing in the schema prevents one Postgres
database serving many clients side by side.

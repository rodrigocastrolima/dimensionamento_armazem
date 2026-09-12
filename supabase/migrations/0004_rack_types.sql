-- Mirrors the "Racks" sheet -> warehouse.models.RackType (see
-- warehouse/io_excel.py:load_rack_types).
--
-- `aceita` is a native Postgres array here, not a comma-joined string in a
-- single cell -- one of the few places the DB schema is a genuine
-- improvement over the spreadsheet, not just a mirror: no CSV-in-a-cell
-- parsing, and a value that doesn't match a LoadType is a normal "look at
-- this array" data-quality check instead of a silently-dropped token (see
-- MIGRATION_LOG.md Phase 2, the "Rack Cantilever"/LONGO cleanup).
--
-- profundidade_inclui_par (warehouse.models.RackType) is NOT a column here:
-- it is a synthetic, in-memory-only flag set by racking.com_par_oversize
-- for a rack type derived at runtime, never real client data.
create table if not exists rack_types (
    id            bigint generated always as identity primary key,
    client_slug   text not null references clients(slug) on delete cascade,
    nome          text not null,
    larg_m        double precision not null,
    prof_m        double precision not null,
    -- altura UTIL POR NIVEL, nao o total do prumo (ver CLAUDE.md)
    alt_nivel_m   double precision not null,
    niveis        integer not null,
    -- tokens LoadType; um token que nao corresponda a nenhum LoadType e
    -- ignorado no loader (ver warehouse/db_supabase.py), com o mesmo aviso
    -- que o loader de Excel ja da para a folha "Racks".
    aceita        text[] not null default '{}',
    footprint_m2  double precision not null default 0,
    corredor_m    double precision not null default 0,
    unique (client_slug, nome)
);

create index if not exists rack_types_client_slug_idx on rack_types (client_slug);

comment on table rack_types is
    'One row per rack typology. Mirrors the "Racks" sheet.';

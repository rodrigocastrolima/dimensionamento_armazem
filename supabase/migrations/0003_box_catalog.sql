-- Mirrors the "Caixas" sheet -> warehouse.models.BoxType (see
-- warehouse/io_excel.py:load_catalog). One row per load-unit type
-- (XS/S/M/L/MEIA/PALLET -- warehouse.models.LoadType); the pallet and
-- half-pallet are ordinary catalog rows, not a special case (see CLAUDE.md).
create table if not exists box_catalog (
    id           bigint generated always as identity primary key,
    client_slug  text not null references clients(slug) on delete cascade,
    -- must be a valid LoadType value: XS | S | M | L | MEIA | PALLET
    nome         text not null,
    int_comp_m   double precision not null,
    int_larg_m   double precision not null,
    int_alt_m    double precision not null,
    ext_comp_m   double precision not null,
    ext_larg_m   double precision not null,
    ext_alt_m    double precision not null,
    peso_max_kg  double precision not null,
    unique (client_slug, nome)
);

create index if not exists box_catalog_client_slug_idx on box_catalog (client_slug);

comment on table box_catalog is
    'One row per load-unit type (S/M/L/MEIA/PALLET/...). Mirrors the "Caixas" sheet.';

-- Mirrors the "Zonas" sheet -> warehouse.models.Zona (see
-- warehouse/io_excel.py:load_zones). Pure input: no SKU/stock associated,
-- dimension given directly (comp_m x larg_m). Optional sheet in Excel
-- (load_zones returns [] if missing); optional here too, an empty result
-- set for a client_slug means the same thing as no "Zonas" sheet.
create table if not exists zones (
    id            bigint generated always as identity primary key,
    client_slug   text not null references clients(slug) on delete cascade,
    nome          text not null,
    categoria     text not null default 'Outra',
    comp_m        double precision not null,
    larg_m        double precision not null,
    justificacao  text not null default ''
);

create index if not exists zones_client_slug_idx on zones (client_slug);

comment on table zones is
    'One row per functional zone (reception, shipping, staging, ...). Mirrors the "Zonas" sheet.';

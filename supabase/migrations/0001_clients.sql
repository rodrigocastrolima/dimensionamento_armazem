-- Multi-tenant root: one row per client. Every data table below carries a
-- client_slug FK to this table, so more than one client's data can coexist
-- safely in the same database.
--
-- slug matches the client-config filename from Phase 1 on purpose
-- (dados/clients/<slug>.json <-> clients.slug = '<slug>') -- one identity,
-- two places it shows up (a small local file for the non-secret business
-- rules, a DB row for the data), never two different names for the same
-- client.
create table if not exists clients (
    slug         text primary key,
    display_name text not null,
    created_at   timestamptz not null default now()
);

comment on table clients is
    'One row per client (warehouse-sizing customer). slug is the same name used for dados/clients/<slug>.json.';

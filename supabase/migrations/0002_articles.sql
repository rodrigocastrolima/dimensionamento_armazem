-- Mirrors the "Peças" sheet -> warehouse.models.Article (see
-- warehouse/io_excel.py:load_articles and docs/LOGICA_BACKEND.md section 2).
--
-- stock_max_un_bruto is the RAW 'Stock_máx_un' column -- input, never
-- computed. It deliberately does NOT store the pipeline's decided
-- stock_max_un: that value comes from Passo 0 (boxing.stock_para_autonomia),
-- computed in Python from pecas_por_carro on every run, identically
-- regardless of whether the row underneath came from Excel or here.
create table if not exists articles (
    id                  bigint generated always as identity primary key,
    client_slug         text not null references clients(slug) on delete cascade,
    sku                 text not null,
    descricao           text not null default '',
    comp_m              double precision not null,
    larg_m              double precision not null,
    alt_m               double precision not null,
    peso_kg             double precision not null default 0,
    stock_max_un_bruto  integer not null default 0,
    -- fracionário por natureza (0.5 = 1 peça a cada 2 carros) -- ver CLAUDE.md
    -- "pecas_por_carro é FLOAT, não int". NUNCA arredondar/truncar ao gravar.
    pecas_por_carro     double precision not null,
    orientacao          text not null default 'Livre',
    -- NAO_EMPILHAVEL | EMPILHAVEL | ANINHAVEL (warehouse.models.Empilhamento)
    empilhamento        text not null default 'EMPILHAVEL',
    fragilidade         text not null default 'Normal',
    geometria           text not null default 'Regular',
    -- RIGIDO | FLEXIVEL_CONTENTOR
    classe              text not null default 'RIGIDO',
    unique (client_slug, sku)
);

create index if not exists articles_client_slug_idx on articles (client_slug);

comment on table articles is
    'One row per SKU. Mirrors the "Peças" sheet of the client master-data workbook.';

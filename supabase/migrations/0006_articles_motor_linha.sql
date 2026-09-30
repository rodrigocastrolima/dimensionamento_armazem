-- Alinha `articles` com o motor do dimensionamento_linha (ver
-- docs/LOGICA_BACKEND.md): 'Orientação_permitida' foi substituída por
-- ALTURA_OVERRIDE, a aninhagem passou a ter eixo próprio (EIXO_ANINHAGEM) e
-- há exceção manual de caixa (CAIXA_FORCADA / PECAS_CAIXA_FORCADA).
--
-- A coluna `orientacao` fica por limpar (já ninguém a lê) para esta migração
-- não destruir dados; pode ser removida mais tarde.
alter table articles add column if not exists altura_override text not null default '';      -- '' | x | y | z
alter table articles add column if not exists eixo_aninhagem text not null default '';        -- '' | x | y | z
alter table articles add column if not exists caixa_forcada text not null default '';         -- '' | XS|S|M|L|MEIA|PALLET
alter table articles add column if not exists pecas_caixa_forcada integer not null default 0; -- 0 = sem quantidade manual

comment on column articles.altura_override is
    'x/y/z força essa cota como vertical nas unidades de carga; vazio = livre (testa as 3).';
comment on column articles.eixo_aninhagem is
    'x/y/z = cota onde a peça aninha na anterior; vazio = não aninha. Independente de empilhamento.';

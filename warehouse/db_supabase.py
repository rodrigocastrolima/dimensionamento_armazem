"""Backend Supabase/Postgres da camada de acesso a dados -- ver
data_access.py (o dispatcher que decide se se chama isto ou io_excel.py) e
supabase/README.md (schema, como aplicar as migrations, modelo de acesso).

Ligação por `$DATABASE_URL` (nunca por dados/clients/<nome>.json, que pode
ser commitado) -- ver NEEDS_HUMAN.md para os 3 passos manuais de setup.
Só é importado quando `Config.data_source == "supabase"`, por isso um
cliente que continua no Excel nunca precisa de `psycopg` instalado.

Devolve exatamente os mesmos dataclasses que io_excel.py (Article/BoxType/
RackType/Zona) -- o resto do pipeline não sabe, nem precisa de saber, de
onde vieram."""
from __future__ import annotations
import os

from .models import Article, BoxType, RackType, Zona, Empilhamento, LoadType
from .io_excel import dimensao_segura


def _connect():
    """Import de `psycopg` adiado para aqui -- só paga o custo (e a
    dependência) quem realmente usa data_source='supabase'."""
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as e:
        raise RuntimeError(
            "data_source='supabase' precisa do pacote 'psycopg' -- "
            "pip install 'psycopg[binary]' (ver requirements.txt)."
        ) from e

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "data_source='supabase' mas $DATABASE_URL não está definida -- "
            "ver NEEDS_HUMAN.md (secção Supabase) para os passos de setup."
        )
    return psycopg.connect(url, row_factory=dict_row)


def load_articles(client_slug: str) -> list[Article]:
    articles: list[Article] = []
    skus_sem_geometria: list[str] = []
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select sku, descricao, comp_m, larg_m, alt_m, peso_kg, "
            "       stock_max_un_bruto, pecas_por_carro, altura_override, "
            "       eixo_aninhagem, empilhamento, fragilidade, geometria, classe, "
            "       caixa_forcada, pecas_caixa_forcada "
            "from articles where client_slug = %s order by sku",
            (client_slug,),
        )
        for row in cur.fetchall():
            comp, larg, alt, sem_geometria = dimensao_segura(
                row["comp_m"], row["larg_m"], row["alt_m"])
            if sem_geometria:
                skus_sem_geometria.append(row["sku"])
            stock_bruto = int(row["stock_max_un_bruto"] or 0)
            articles.append(Article(
                sku=row["sku"],
                descricao=row["descricao"] or "",
                comp_m=comp, larg_m=larg, alt_m=alt,
                peso_kg=float(row["peso_kg"] or 0.0),
                # placeholder, tal como io_excel.load_articles: o Passo 0
                # (boxing.aplicar_stock_autonomia) é que decide o valor final.
                stock_max_un=stock_bruto,
                stock_atual_un=stock_bruto,
                pecas_por_carro=float(row["pecas_por_carro"]),
                altura_override=row["altura_override"] or "",
                eixo_aninhagem=row["eixo_aninhagem"] or "",
                empilhamento=Empilhamento(row["empilhamento"] or "EMPILHAVEL"),
                fragilidade=row["fragilidade"] or "Normal",
                geometria=row["geometria"] or "Regular",
                classe=row["classe"] or "RIGIDO",
                caixa_forcada=row["caixa_forcada"] or "",
                pecas_caixa_forcada=int(row["pecas_caixa_forcada"] or 0),
            ))
    if skus_sem_geometria:
        print(f"AVISO: {len(skus_sem_geometria)} SKU(s) sem geometria (0x0x0) em "
              f"articles (client_slug={client_slug!r}) -- a usar dimensão mínima "
              f"genérica (0.05 x 0.05 x 0.01 m): {', '.join(skus_sem_geometria)}")
    return articles


def load_catalog(client_slug: str) -> list[BoxType]:
    catalog: list[BoxType] = []
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select nome, int_comp_m, int_larg_m, int_alt_m, "
            "       ext_comp_m, ext_larg_m, ext_alt_m, peso_max_kg "
            "from box_catalog where client_slug = %s order by nome",
            (client_slug,),
        )
        for row in cur.fetchall():
            catalog.append(BoxType(
                nome=LoadType(row["nome"]),
                int_comp_m=float(row["int_comp_m"]), int_larg_m=float(row["int_larg_m"]),
                int_alt_m=float(row["int_alt_m"]),
                ext_comp_m=float(row["ext_comp_m"]), ext_larg_m=float(row["ext_larg_m"]),
                ext_alt_m=float(row["ext_alt_m"]),
                peso_max_kg=float(row["peso_max_kg"]),
            ))
    return catalog


def load_rack_types(client_slug: str) -> list[RackType]:
    racks: list[RackType] = []
    tokens_desconhecidos: dict[str, list[str]] = {}
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select nome, larg_m, prof_m, alt_nivel_m, niveis, aceita, "
            "       footprint_m2, corredor_m "
            "from rack_types where client_slug = %s order by nome",
            (client_slug,),
        )
        for row in cur.fetchall():
            aceita: list[LoadType] = []
            for token in (row["aceita"] or []):
                try:
                    aceita.append(LoadType(token))
                except ValueError:
                    tokens_desconhecidos.setdefault(row["nome"], []).append(token)
            racks.append(RackType(
                nome=row["nome"],
                larg_m=float(row["larg_m"]), prof_m=float(row["prof_m"]),
                alt_nivel_m=float(row["alt_nivel_m"]), niveis=int(row["niveis"]),
                aceita=aceita,
                footprint_m2=float(row["footprint_m2"] or 0.0),
                corredor_m=float(row["corredor_m"] or 0.0),
            ))
    # mesmo aviso que io_excel.load_rack_types (ver Phase 2 do MIGRATION_LOG):
    # uma tipologia com `aceita` vazio é dead weight silencioso sem isto.
    for nome, tokens in tokens_desconhecidos.items():
        rack = next((r for r in racks if r.nome == nome), None)
        ficou_morta = rack is not None and not rack.aceita
        print(f"AVISO: tipologia '{nome}' (client_slug={client_slug!r}): "
              f"token(s) desconhecido(s) em rack_types.aceita ignorado(s) -- "
              f"{', '.join(tokens)} não corresponde(m) a nenhum LoadType válido."
              + (f" Esta tipologia ficou com 'aceita' VAZIO -- não aceita nenhum "
                 f"tipo de carga, contribui zero capacidade em tudo."
                 if ficou_morta else ""))
    return racks


def load_zones(client_slug: str) -> list[Zona]:
    zonas: list[Zona] = []
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select nome, categoria, comp_m, larg_m, justificacao "
            "from zones where client_slug = %s order by nome",
            (client_slug,),
        )
        for row in cur.fetchall():
            zonas.append(Zona(
                nome=row["nome"],
                categoria=row["categoria"] or "Outra",
                comp_m=float(row["comp_m"]), larg_m=float(row["larg_m"]),
                justificacao=row["justificacao"] or "",
            ))
    return zonas

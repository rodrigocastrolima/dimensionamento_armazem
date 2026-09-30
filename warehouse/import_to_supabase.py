"""Import único: lê o workbook Excel de um cliente (via io_excel, o MESMO
loader do fluxo normal) e escreve-o no Supabase (clients/articles/
box_catalog/rack_types/zones -- ver supabase/README.md), depois VERIFICA
relendo pelo db_supabase e comparando com o que o Excel deu -- não só a
contagem de linhas, o conteúdo linha a linha.

Precisa de $DATABASE_URL e do schema já aplicado (ver supabase/README.md e
NEEDS_HUMAN.md) -- este script nunca cria o projeto nem corre as migrations,
só insere linhas.

Idempotente: cada tabela é limpa (DELETE WHERE client_slug = ...) e
reescrita dentro da MESMA transação -- correr duas vezes para o mesmo
cliente substitui os dados, não duplica.

Uso:
    python -m warehouse.import_to_supabase --client sia
"""
from __future__ import annotations
import argparse
from pathlib import Path

from . import io_excel
from .config import Config


def _connect():
    try:
        import psycopg
    except ImportError as e:
        raise RuntimeError(
            "Precisa do pacote 'psycopg' -- pip install 'psycopg[binary]' "
            "(ver requirements.txt)."
        ) from e
    import os
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "$DATABASE_URL não está definida -- ver NEEDS_HUMAN.md (secção Supabase).")
    return psycopg.connect(url)


def import_client(client_slug: str, display_name: str, master_data: Path) -> None:
    articles = io_excel.load_articles(master_data)
    catalogo = io_excel.load_catalog(master_data)
    rack_types = io_excel.load_rack_types(master_data)
    zonas = io_excel.load_zones(master_data)

    print(f"Lido do Excel ({master_data}): {len(articles)} articles, "
          f"{len(catalogo)} box_catalog, {len(rack_types)} rack_types, "
          f"{len(zonas)} zones.")

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into clients (slug, display_name) values (%s, %s) "
                "on conflict (slug) do update set display_name = excluded.display_name",
                (client_slug, display_name),
            )

            cur.execute("delete from articles where client_slug = %s", (client_slug,))
            for a in articles:
                # a.stock_atual_un, NÃO a.stock_max_un: à saída de
                # io_excel.load_articles os dois são iguais (o placeholder
                # bruto), mas stock_atual_un é o nome que deixa claro que
                # isto é o "Stock_máx_un" cru, nunca o já decidido pelo
                # Passo 0 -- ver db_supabase.load_articles/models.Article.
                cur.execute(
                    "insert into articles (client_slug, sku, descricao, comp_m, larg_m, "
                    "alt_m, peso_kg, stock_max_un_bruto, pecas_por_carro, altura_override, "
                    "eixo_aninhagem, empilhamento, fragilidade, geometria, classe, "
                    "caixa_forcada, pecas_caixa_forcada) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (client_slug, a.sku, a.descricao, a.comp_m, a.larg_m, a.alt_m,
                     a.peso_kg, a.stock_atual_un, a.pecas_por_carro, a.altura_override,
                     a.eixo_aninhagem, a.empilhamento.value, a.fragilidade,
                     a.geometria, a.classe, a.caixa_forcada, a.pecas_caixa_forcada),
                )

            cur.execute("delete from box_catalog where client_slug = %s", (client_slug,))
            for b in catalogo:
                cur.execute(
                    "insert into box_catalog (client_slug, nome, int_comp_m, int_larg_m, "
                    "int_alt_m, ext_comp_m, ext_larg_m, ext_alt_m, peso_max_kg) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (client_slug, b.nome.value, b.int_comp_m, b.int_larg_m, b.int_alt_m,
                     b.ext_comp_m, b.ext_larg_m, b.ext_alt_m, b.peso_max_kg),
                )

            cur.execute("delete from rack_types where client_slug = %s", (client_slug,))
            for r in rack_types:
                cur.execute(
                    "insert into rack_types (client_slug, nome, larg_m, prof_m, "
                    "alt_nivel_m, niveis, aceita, footprint_m2, corredor_m) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (client_slug, r.nome, r.larg_m, r.prof_m, r.alt_nivel_m, r.niveis,
                     [lt.value for lt in r.aceita], r.footprint_m2, r.corredor_m),
                )

            cur.execute("delete from zones where client_slug = %s", (client_slug,))
            for z in zonas:
                cur.execute(
                    "insert into zones (client_slug, nome, categoria, comp_m, larg_m, "
                    "justificacao) values (%s,%s,%s,%s,%s,%s)",
                    (client_slug, z.nome, z.categoria, z.comp_m, z.larg_m, z.justificacao),
                )
        conn.commit()

    print("Escrito no Supabase. A verificar (relendo pelo db_supabase)...")
    problemas = verify_import(client_slug, articles, catalogo, rack_types, zonas)
    if problemas:
        print(f"FALHOU a verificação ({len(problemas)} problema(s)):")
        for p in problemas[:20]:
            print(" -", p)
        if len(problemas) > 20:
            print(f"   ... e mais {len(problemas) - 20}.")
        raise SystemExit(1)

    print(f"OK -- {len(articles)} articles, {len(catalogo)} box_catalog, "
          f"{len(rack_types)} rack_types, {len(zonas)} zones: contagens E "
          f"conteúdo batem certo, linha a linha, entre o Excel e o Supabase.")


def verify_import(client_slug: str, articles, catalogo, rack_types, zonas) -> list[str]:
    """Relê pelo MESMO caminho que o pipeline usaria em produção
    (db_supabase.load_*, não uma query ad-hoc) e compara dataclass a
    dataclass com o que io_excel leu -- não só len(), para apanhar também
    um valor trocado/arredondado, não só uma linha perdida."""
    from . import db_supabase
    problemas: list[str] = []

    def _check(nome_tabela, esperados, obtidos, key):
        if len(esperados) != len(obtidos):
            problemas.append(
                f"{nome_tabela}: {len(esperados)} no Excel vs {len(obtidos)} no Supabase")
            return
        for e, o in zip(sorted(esperados, key=key), sorted(obtidos, key=key)):
            if e != o:
                problemas.append(f"{nome_tabela} {key(e)!r}: Excel={e!r} != Supabase={o!r}")

    _check("articles", articles, db_supabase.load_articles(client_slug), lambda a: a.sku)
    _check("box_catalog", catalogo, db_supabase.load_catalog(client_slug), lambda b: b.nome.value)
    _check("rack_types", rack_types, db_supabase.load_rack_types(client_slug), lambda r: r.nome)
    _check("zones", zonas, db_supabase.load_zones(client_slug),
           lambda z: (z.nome, z.comp_m, z.larg_m))
    return problemas


def main() -> None:
    p = argparse.ArgumentParser(
        description="Importa o master data Excel de um cliente para o Supabase")
    p.add_argument("--client", required=True, help="slug do cliente (ex.: 'sia')")
    p.add_argument("--display-name", help="nome apresentável (default: o slug)")
    p.add_argument("--data", type=Path,
                   help="master data .xlsx (default: master_data de dados/clients/<client>.json)")
    args = p.parse_args()

    master_data = args.data
    if master_data is None:
        cfg = Config.for_client(args.client)
        if cfg.data_source != "excel":
            raise SystemExit(
                f"dados/clients/{args.client}.json já tem data_source="
                f"'{cfg.data_source}' -- passa --data explicitamente se "
                f"queres reimportar na mesma (ex.: para atualizar as tabelas "
                f"a partir de uma versão nova do Excel).")
        master_data = cfg.master_data

    import_client(args.client, args.display_name or args.client, master_data)


if __name__ == "__main__":
    main()

"""Pipeline de dimensionamento de armazém.

    stock máximo --(1 boxing)--> caixas/paletes/LONGO
                 --(2 racking)-> nº de racks
                 --(3 area)----> área de chão
"""
from __future__ import annotations
import argparse
from pathlib import Path

from . import io_excel, boxing, racking, area, layout as layout_mod
from .config import Config


def _flatten_para_excel(posicoes) -> list:
    """Achata PosicaoChao -> linhas simples (rack_numero, rack_tipologia, sku,
    descricao, load_type, num_unidades) para a sheet 'Detalhe' do Excel.
    rack_numero é sintético: 1 por módulo físico, sequencial por tipologia."""
    from types import SimpleNamespace
    linhas = []
    rack_numero = 0
    for pos in posicoes:
        for bloco in pos.blocos:
            rack_numero += 1
            for nivel in bloco.niveis:
                for caixa in nivel.caixas:
                    linhas.append(SimpleNamespace(
                        rack_numero=rack_numero, rack_tipologia=pos.rack_nome,
                        sku=caixa.sku, descricao=caixa.descricao,
                        load_type=caixa.load_type, num_unidades=1,
                    ))
    return linhas


def run(cfg: Config) -> None:
    articles = io_excel.load_articles(cfg.master_data)
    catalogo = io_excel.load_catalog(cfg.master_data)          # S/M/L + PALLET
    rack_types = io_excel.load_rack_types(cfg.master_data)
    catalogo_por_tipo = {b.nome: b for b in catalogo}
    rack_by_name = {r.nome: r for r in rack_types}

    from .models import LoadType
    article_idx = {a.sku: a for a in articles}

    assignments = boxing.plan_all(articles, catalogo, cfg)                    # Passo 1

    comp_max_longo = max(
        (article_idx[a.sku].comp_m for a in assignments
         if a.load_type == LoadType.LONGO and a.sku in article_idx),
        default=1.0,
    )
    requirements, avisos_estimativa = racking.racks_needed(assignments, rack_types,  # Passo 2 (resumo)
                                        catalogo_por_tipo, cfg, comp_max_longo,
                                        cfg.altura_picking_m)

    posicoes, avisos_alocacao = racking.allocate_to_racks(
        assignments, requirements, rack_types, catalogo_por_tipo, articles, comp_max_longo,
        cfg.altura_picking_m, cfg.altura_armazem_m)

    avisos = avisos_estimativa + avisos_alocacao

    dim = area.floor_area(posicoes, rack_by_name)                             # Passo 3
    zonas = io_excel.load_zones(cfg.master_data)
    wl = layout_mod.gerar_layout(posicoes, rack_by_name, zonas)               # Passo 3b
    resumo = area.resumo_area_total(dim, wl, zonas, cfg.margem_estrutural_pct)

    slots = _flatten_para_excel(posicoes)
    io_excel.write_results(cfg.output, assignments, requirements, dim, slots, avisos, wl,
                           zonas, resumo)
    print(f"Area de racks: {dim.area_racks_m2:,.1f} m2")
    print(f"Layout fisico: {wl.largura_total_m:,.1f} x {wl.profundidade_total_m:,.1f} m "
         f"= {wl.area_total_m2:,.1f} m2 (corredores: {wl.area_corredores_m2:,.1f} m2)")
    print(f"Area bruta total estimada: {resumo.area_bruta_total_m2:,.1f} m2")
    if avisos:
        print(f"ATENCAO: {len(avisos)} aviso(s) -- ver sheet 'Avisos' em {cfg.output}")


def main() -> None:
    p = argparse.ArgumentParser(description="Dimensionamento de armazem")
    p.add_argument("--data", type=Path, help="master data .xlsx")
    p.add_argument("--out", type=Path, help="resultados .xlsx")
    p.add_argument("--altura", type=float,
                   help="altura util do armazem em metros (ativa empilhamento de modulos de rack)")
    p.add_argument("--picking", type=float,
                   help="altura da zona de picking em metros (ativa modo armazem picking)")
    args = p.parse_args()
    cfg = Config()
    if args.data:
        cfg.master_data = args.data
    if args.out:
        cfg.output = args.out
    if args.altura:
        cfg.altura_armazem_m = args.altura
    if args.picking:
        cfg.altura_picking_m = args.picking
    run(cfg)


if __name__ == "__main__":
    main()

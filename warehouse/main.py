"""Pipeline de dimensionamento de armazém.

    stock máximo --(1 boxing)--> caixas/paletes
                 --(2 racking)-> nº de racks
                 --(3 area)----> área de chão
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path

from . import io_excel, boxing, racking, area, layout as layout_mod
from .config import Config
from .models import Article


def _flatten_para_excel(posicoes) -> list:
    """Achata PosicaoChao -> linhas simples (rack_numero, rack_tipologia, sku,
    descricao, load_type, pecas_nesta_caixa) para a sheet 'Detalhe' do Excel.
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
                        load_type=caixa.load_type, pecas_nesta_caixa=caixa.pecas_nesta_caixa,
                    ))
    return linhas


def run(cfg: Config, articles: list[Article] | None = None) -> area.ResumoAreaTotal:
    """articles: se fornecido, substitui o load_articles + Passo 0 do
    master_data (usado por cenario_autonomia.py para injetar stock_max_un já
    escalado, sem reescrever o Excel nem voltar a decidir a autonomia mínima).
    None (default) = comportamento normal: lê do master_data e o Passo 0
    (boxing.aplicar_stock_autonomia) decide o stock_max_un de cada artigo."""
    catalogo = io_excel.load_catalog(cfg.master_data)          # S/M/L + PALLET
    autonomia_resultados = None
    if articles is None:
        articles = io_excel.load_articles(cfg.master_data)
        articles, autonomia_resultados = boxing.aplicar_stock_autonomia(articles, catalogo, cfg)  # Passo 0
    rack_types = io_excel.load_rack_types(cfg.master_data)
    # ANTES de com_par_oversize: a tipologia sintética "(emparelhada)" também
    # casaria a própria chave (herda o nome base), mascarando exatamente o
    # caso "chave configurada, nenhuma tipologia real corresponde" que este
    # aviso existe para apanhar (ver racking.verificar_chaves_substring_sem_correspondencia).
    avisos_config = racking.verificar_chaves_substring_sem_correspondencia(rack_types, cfg)
    rack_types = racking.com_par_oversize(rack_types, cfg)     # par emparelhado p/ OVERSIZE
    catalogo_por_tipo = {b.nome: b for b in catalogo}
    rack_by_name = {r.nome: r for r in rack_types}

    from .models import LoadType
    article_idx = {a.sku: a for a in articles}

    assignments = boxing.plan_all(articles, catalogo, cfg)                    # Passo 1
    justificacoes = boxing.justificar_all(articles, catalogo, cfg, assignments)

    requirements, avisos_estimativa = racking.racks_needed(assignments, rack_types,  # Passo 2 (resumo)
                                        catalogo_por_tipo, cfg,
                                        cfg.altura_picking_m, articles)

    posicoes, avisos_alocacao = racking.allocate_to_racks(
        assignments, requirements, rack_types, catalogo_por_tipo, articles,
        cfg.altura_picking_m, cfg.altura_armazem_m, cfg)

    avisos = avisos_config + avisos_estimativa + avisos_alocacao

    zonas = io_excel.load_zones(cfg.master_data)

    # OVERSIZE ao chão (só se não houver tipologia wide-span NEM par emparelhado:
    # o rack tem prioridade). Antes do layout, para as pilhas entrarem como
    # fiada do bloco.
    artigos_chao: list[tuple] = []
    area_chao_m2 = 0.0
    if cfg.oversize_ao_chao and not racking._melhor_oversize_ativo(rack_types, cfg):
        artigos_chao = [
            (a.sku, art.comp_m, art.larg_m, art.alt_m, art.peso_kg, a.num_unidades, art.orientacao)
            for a in assignments if a.load_type == LoadType.OVERSIZE
            for art in [article_idx.get(a.sku)] if art
        ]
        area_chao_m2, _ = area.area_oversize_chao(
            artigos_chao, cfg.oversize_chao_altura_max_m,
            cfg.oversize_chao_peso_max_kg, cfg.oversize_chao_acesso_pct)

    wl = layout_mod.gerar_layout(posicoes, rack_by_name, zonas,               # Passo 3b
                                 cfg.largura_max_m, cfg.profundidade_max_m,
                                 artigos_chao, cfg.oversize_chao_altura_max_m,
                                 cfg.oversize_chao_peso_max_kg, cfg.picking_manual_racks)
    avisos = avisos + wl.avisos

    # Passo 3 depois do 3b: o footprint das pilhas conta como área ocupada
    dim = area.floor_area(posicoes, rack_by_name, wl.pilhas_chao)
    resumo = area.resumo_area_total(dim, wl, zonas, cfg.margem_estrutural_pct, area_chao_m2)

    slots = _flatten_para_excel(posicoes)
    io_excel.write_results(cfg.output, assignments, requirements, dim, slots, avisos, wl,
                           zonas, resumo, articles, justificacoes, autonomia_resultados)
    print(f"Area de racks: {dim.area_racks_m2:,.1f} m2")
    print(f"Layout fisico: {wl.largura_total_m:,.1f} x {wl.profundidade_total_m:,.1f} m "
         f"= {wl.area_total_m2:,.1f} m2 (corredores: {wl.area_corredores_m2:,.1f} m2)")
    print(f"Area bruta total estimada: {resumo.area_bruta_total_m2:,.1f} m2")
    if avisos:
        print(f"ATENCAO: {len(avisos)} aviso(s) -- ver sheet 'Avisos' em {cfg.output}")
    return resumo


def main() -> None:
    p = argparse.ArgumentParser(description="Dimensionamento de armazem")
    p.add_argument("--client", default=os.environ.get("WAREHOUSE_CLIENT", "sia"),
                   help="nome do cliente (dados/clients/<nome>.json); default 'sia' "
                        "ou $WAREHOUSE_CLIENT")
    p.add_argument("--data", type=Path, help="master data .xlsx (substitui o do cliente)")
    p.add_argument("--out", type=Path, help="resultados .xlsx (substitui o do cliente)")
    p.add_argument("--altura", type=float,
                   help="altura util do armazem em metros (ativa empilhamento de modulos de rack)")
    p.add_argument("--picking", type=float,
                   help="altura da zona de picking em metros (ativa modo armazem picking)")
    args = p.parse_args()
    cfg = Config.for_client(args.client)
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

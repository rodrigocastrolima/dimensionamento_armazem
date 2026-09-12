"""Cenários de autonomia (nº de carros) sobre o master data atual, sem o alterar.

O stock_max_un de cada artigo já reflete uma autonomia-base (10 carros,
decidida algoritmicamente pelo Passo 0 -- ver boxing.aplicar_stock_autonomia e
Config.autonomia_minima_carros). Este script escala esse stock proporcionalmente
a um alvo de autonomia diferente e corre o pipeline normal (boxing/racking/
area/layout) sobre o resultado, sem tocar no Excel de entrada. Cada cenário
escreve o seu próprio resultados_<N>carros.xlsx.
"""
from __future__ import annotations
import argparse
import copy
import math
from pathlib import Path

from . import boxing, io_excel, main as main_mod
from .config import Config
from .models import Article


def aplicar_autonomia(articles: list[Article], carros_alvo: float,
                      carros_base: float = 10.0) -> list[Article]:
    """stock_max_un(N) = ceil(stock_max_un_atual x N / carros_base).

    Escala o stock já decidido (com os arredondamentos de embalagem/MOQ que lá
    estiverem) em vez de o recalcular do zero a partir de Peças_por_carro --
    preserva essas decisões de negócio. Devolve cópias; não muta `articles`."""
    novos = []
    for a in articles:
        art = copy.copy(a)
        art.stock_max_un = math.ceil(a.stock_max_un * carros_alvo / carros_base)
        novos.append(art)
    return novos


def main() -> None:
    p = argparse.ArgumentParser(
        description="Área de armazém para diferentes autonomias (nº de carros)")
    p.add_argument("--data", type=Path, help="master data .xlsx")
    p.add_argument("--carros", type=float, nargs="+", required=True,
                   help="um ou mais valores de autonomia-alvo em carros (ex.: --carros 10 15 20)")
    p.add_argument("--carros-base", type=float, default=10.0,
                   help="autonomia já embutida no master data atual (default 10)")
    p.add_argument("--out-dir", type=Path, default=Path("dados/cenarios"),
                   help="pasta onde escrever um resultados_<N>carros.xlsx por cenário")
    p.add_argument("--altura", type=float,
                   help="altura util do armazem em metros (empilhamento de módulos de rack)")
    p.add_argument("--picking", type=float,
                   help="altura da zona de picking em metros")
    args = p.parse_args()

    cfg = Config()
    if args.data:
        cfg.master_data = args.data
    if args.altura:
        cfg.altura_armazem_m = args.altura
    if args.picking:
        cfg.altura_picking_m = args.picking

    args.out_dir.mkdir(parents=True, exist_ok=True)
    catalogo = io_excel.load_catalog(cfg.master_data)
    base_articles = io_excel.load_articles(cfg.master_data)
    base_articles, _ = boxing.aplicar_stock_autonomia(base_articles, catalogo, cfg)  # Passo 0

    linhas_resumo = []
    for carros in args.carros:
        articles = aplicar_autonomia(base_articles, carros, args.carros_base)
        cfg_cenario = copy.copy(cfg)
        cfg_cenario.output = args.out_dir / f"resultados_{carros:g}carros.xlsx"
        print(f"\n=== Cenário: {carros:g} carros de autonomia ===")
        resumo = main_mod.run(cfg_cenario, articles=articles)
        linhas_resumo.append((carros, resumo))

    print("\n=== Resumo comparativo ===")
    print(f"{'Carros':>8}  {'Racks (m2)':>12}  {'Corredores (m2)':>16}  "
          f"{'Zonas (m2)':>12}  {'TOTAL (m2)':>12}")
    for carros, r in linhas_resumo:
        print(f"{carros:>8g}  {r.area_racks_m2:>12,.1f}  {r.area_corredores_m2:>16,.1f}  "
              f"{r.area_zonas_m2:>12,.1f}  {r.area_bruta_total_m2:>12,.1f}")


if __name__ == "__main__":
    main()

"""Parâmetros e decisões de negócio. Os pontos 'A CONFIRMAR' mudam resultados."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum


class BoxStrategy(str, Enum):
    FEWEST_BOXES = "fewest_boxes"    # minimizar nº de caixas (default)
    MIN_WASTE = "min_waste"          # minimizar volume desperdiçado
    SMALLEST_FIT = "smallest_fit"    # menor caixa com ocupação >= limiar


@dataclass
class Config:
    # caminhos
    master_data: Path = Path("dados/master_data_classificado_v1.xlsx")
    output: Path = Path("dados/resultados.xlsx")

    # Passo 1 — escolha de caixa  (A CONFIRMAR)
    box_strategy: BoxStrategy = BoxStrategy.FEWEST_BOXES
    min_fill_ratio: float = 0.0          # usado por SMALLEST_FIT
    pallet_threshold_unidades: int = 6   # acima disto em caixas L -> palete

    # Empilhamento de artigos (dentro de caixa/palete), eixo da altura.
    # Classificação em 3 vias vinda da coluna 'Empilhável' (ver Empilhamento):
    #   NAO_EMPILHAVEL -> 1 peça de altura.
    #   EMPILHAVEL     -> altura cheia somada (sem nesting), só limitado pela
    #                     altura disponível e pelo peso máximo da caixa.
    #   ANINHAVEL      -> cada peça extra do stack acrescenta apenas
    #                     fator_empilhamento x altura_artigo (encaixa/aninha).
    fator_empilhamento: float = 0.3

    # Fator de enchimento real no plano horizontal (comp x larg) para artigos
    # RIGIDO (classe), substituindo o floor() teórico de encaixe perfeito.
    # Folgas de manuseio/forma irregular reduzem o nº de peças por nível face
    # ao máximo geométrico. Não se aplica a FLEXIVEL/CONTENTOR (já comprimível).
    fill_factor_rigido: float = 0.7

    # Encaminhamento para LONGO (cantilever): geometrias que não vão a caixa
    long_geometrias: tuple = ("Comprida",)

    # Dentro de long_geometrias, subtipos que NÃO são material 1D de
    # cantilever (não se apoiam em braços) -> caem para o best-fit normal
    # (S/M/L/PALLET) em vez de ir para a cantilever.
    long_subtipos_excluidos: tuple = ("PAINEL_GRANDE", "VOLUMOSO_OVERSIZE")

    # Passo 3 — área  (A CONFIRMAR)
    aisle_factor: float = 0.6        # corredores como % da footprint dos racks

    # Altura útil do armazém (pé-direito disponível para empilhar módulos de
    # rack). Qualquer tipologia pode ser empilhada na vertical: nº de módulos
    # por posição de chão = floor(altura_armazem_m / (alt_nivel_m x niveis))
    # desse RackType. 0 = sem limite de altura (não empilha módulos, niveis
    # do Excel já são o total).
    altura_armazem_m: float = 0.0

    # Modo de armazém picking: altura MÁXIMA (m) de alcance do operador. Um
    # nível conta como zona de picking se o seu TOPO (base + altura do nível)
    # ficar dentro deste valor -- o nível inteiro tem de estar ao alcance, por
    # isso conta o limite superior, não a base (ex.: nível de 1.8 m com picking
    # a 2 m é picking; o nível seguinte, topo a 3.6 m, já não).
    # Cada SKU distinto tem garantida >=1 caixa nessa zona; o resto
    # do stock vai para reserva acima. Se a capacidade de picking não chegar
    # para todos os SKUs, abrem-se mais posições de chão até caber.
    # 0 = modo picking desativado.
    altura_picking_m: float = 0.0

    # Margem estrutural (paredes, pilares, etc.) aplicada sobre racks + corredores +
    # zonas funcionais, para chegar à área bruta total do edifício.
    # 0 = sem margem estrutural aplicada.
    margem_estrutural_pct: float = 0.0

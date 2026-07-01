"""Passo 3b — Layout físico: dispõe as PosicaoChao alocadas (Passo 2) em filas
duplas costas-com-costas, com corredores de serviço reais entre filas.

Sem limite externo de armazém: escolhe o nº de posições por fiada que
aproxima um retângulo compacto (minimiza o perímetro do envolvente), tipologia
a tipologia, e empilha as tipologias umas atrás das outras com um corredor de
transição entre elas.

Unidades: metros / m².
"""
from __future__ import annotations
import math

from .models import RackType, PosicaoChao, PosicaoLayout, WarehouseLayout
from .area import rack_footprint_m2

CORREDOR_DEFAULT_M = 2.5         # usado quando RackType.corredor_m == 0
CORREDOR_TRANSICAO_M = 3.0       # entre o bloco de uma tipologia e a seguinte


def _corredor_m(rack: RackType) -> float:
    return rack.corredor_m if rack.corredor_m > 0 else CORREDOR_DEFAULT_M


def _melhor_num_por_fiada(n: int, larg_m: float, prof_dupla_m: float, corredor_m: float) -> int:
    """Escolhe quantas posições por fiada (frente OU trás) minimizam o
    perímetro do retângulo envolvente da tipologia, testando todas as
    divisões possíveis de n posições em filas duplas de até 2x esse nº."""
    melhor_k, melhor_perimetro = 1, float("inf")
    for k in range(1, n + 1):
        num_filas_duplas = math.ceil(n / (2 * k))
        largura = k * larg_m
        profundidade = num_filas_duplas * prof_dupla_m + max(num_filas_duplas - 1, 0) * corredor_m
        perimetro = 2 * (largura + profundidade)
        if perimetro < melhor_perimetro:
            melhor_perimetro, melhor_k = perimetro, k
    return melhor_k


def _layout_tipologia(rack: RackType, posicoes: list[PosicaoChao],
                      y0: float) -> tuple[list[PosicaoLayout], float, float]:
    """Dispõe as posições de UMA tipologia em filas duplas costas-com-costas,
    a partir de y0. Devolve (layout, largura_ocupada_m, profundidade_ocupada_m)."""
    n = len(posicoes)
    if n == 0:
        return [], 0.0, 0.0

    larg, prof = rack.larg_m, rack.prof_m
    corredor = _corredor_m(rack)
    prof_dupla = 2 * prof

    k = _melhor_num_por_fiada(n, larg, prof_dupla, corredor)

    resultado: list[PosicaoLayout] = []
    idx = 0
    fila_idx = 0
    y_fila = y0
    largura_max = 0.0
    while idx < n:
        for lado, y_lado in (("frente", y_fila), ("tras", y_fila + prof)):
            for col in range(k):
                if idx >= n:
                    break
                pos = posicoes[idx]
                x = col * larg
                resultado.append(PosicaoLayout(
                    posicao=pos, x_m=round(x, 4), y_m=round(y_lado, 4),
                    largura_m=larg, profundidade_m=prof,
                    fila_idx=fila_idx, lado=lado,
                ))
                idx += 1
        largura_max = max(largura_max, k * larg)
        y_fila += prof_dupla + corredor
        fila_idx += 1

    profundidade_total = y_fila - corredor - y0  # remove o último corredor não usado
    return resultado, largura_max, profundidade_total


def gerar_layout(posicoes: list[PosicaoChao],
                 rack_types: dict[str, RackType]) -> WarehouseLayout:
    """Gera o layout físico completo: uma tipologia a seguir à outra (eixo Y),
    cada uma com as suas filas duplas costas-com-costas, separadas por um
    corredor de transição entre tipologias diferentes."""
    por_tipologia: dict[str, list[PosicaoChao]] = {}
    for pos in posicoes:
        por_tipologia.setdefault(pos.rack_nome, []).append(pos)

    todas: list[PosicaoLayout] = []
    y_cursor = 0.0
    largura_total = 0.0
    area_racks = 0.0

    for rack_nome, lista in por_tipologia.items():
        rack = rack_types[rack_nome]
        layout_tipo, largura, profundidade = _layout_tipologia(rack, lista, y_cursor)
        todas.extend(layout_tipo)
        largura_total = max(largura_total, largura)
        y_cursor += profundidade + CORREDOR_TRANSICAO_M
        area_racks += rack_footprint_m2(rack) * len(lista)

    profundidade_total = max(y_cursor - CORREDOR_TRANSICAO_M, 0.0)
    area_envolvente = largura_total * profundidade_total
    area_corredores = max(area_envolvente - area_racks, 0.0)

    return WarehouseLayout(
        posicoes=todas,
        largura_total_m=round(largura_total, 3),
        profundidade_total_m=round(profundidade_total, 3),
        area_total_m2=round(area_envolvente, 3),
        area_corredores_m2=round(area_corredores, 3),
    )

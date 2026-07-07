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

from .models import RackType, PosicaoChao, PosicaoLayout, WarehouseLayout, Zona, ZonaLayout
from .area import rack_footprint_m2

CORREDOR_DEFAULT_M = 2.5         # usado quando RackType.corredor_m == 0
CORREDOR_TRANSICAO_M = 3.0       # entre o bloco de uma tipologia e a seguinte
GAP_ZONAS_M = 1.0                # espaçamento entre zonas funcionais, na sua fila


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


def _layout_zonas(zonas: list[Zona], y0: float) -> list[ZonaLayout]:
    """Dispõe as zonas funcionais em prateleiras (shelf packing): tamanhos
    variáveis, por isso não dá para replicar o truque de "k por fiada" dos
    racks (todos iguais). Ordena por profundidade decrescente e vai enchendo
    uma fiada até à largura-alvo (~raiz da área total), quebrando para a
    fiada seguinte -- aproxima um retângulo compacto em vez de uma tira única.
    Só dá coordenadas para não sobrepor o bloco de racks na cena 3D -- a área
    das zonas é reportada à parte (area.resumo_area_total), não entra em
    largura_total_m/profundidade_total_m/area_total_m2."""
    if not zonas:
        return []

    area_total = sum(z.area_m2 for z in zonas)
    largura_alvo = max(math.sqrt(area_total), max(z.comp_m for z in zonas))
    ordenadas = sorted(zonas, key=lambda z: z.larg_m, reverse=True)

    resultado = []
    x, y, altura_fiada = 0.0, y0, 0.0
    for zona in ordenadas:
        if x > 0 and x + zona.comp_m > largura_alvo:
            y += altura_fiada + GAP_ZONAS_M
            x, altura_fiada = 0.0, 0.0
        resultado.append(ZonaLayout(
            zona=zona, x_m=round(x, 4), y_m=round(y, 4),
            largura_m=zona.comp_m, profundidade_m=zona.larg_m,
        ))
        x += zona.comp_m + GAP_ZONAS_M
        altura_fiada = max(altura_fiada, zona.larg_m)
    return resultado


def gerar_layout(posicoes: list[PosicaoChao],
                 rack_types: dict[str, RackType],
                 zonas: list[Zona] | None = None) -> WarehouseLayout:
    """Gera o layout físico completo: zonas funcionais primeiro (entrada do
    armazém, longe dos racks), depois uma tipologia de rack a seguir à outra
    (eixo Y), cada uma com as suas filas duplas costas-com-costas, separadas
    por um corredor de transição -- entre o bloco de zonas e o de racks, e
    entre tipologias diferentes."""
    por_tipologia: dict[str, list[PosicaoChao]] = {}
    for pos in posicoes:
        por_tipologia.setdefault(pos.rack_nome, []).append(pos)

    zonas_layout = _layout_zonas(zonas, 0.0) if zonas else []
    y0_racks = 0.0
    if zonas_layout:
        profundidade_zonas = max(zl.y_m + zl.profundidade_m for zl in zonas_layout)
        y0_racks = profundidade_zonas + CORREDOR_TRANSICAO_M

    todas: list[PosicaoLayout] = []
    y_cursor = y0_racks
    largura_total = 0.0
    area_racks = 0.0

    for rack_nome, lista in por_tipologia.items():
        rack = rack_types[rack_nome]
        layout_tipo, largura, profundidade = _layout_tipologia(rack, lista, y_cursor)
        todas.extend(layout_tipo)
        largura_total = max(largura_total, largura)
        y_cursor += profundidade + CORREDOR_TRANSICAO_M
        area_racks += rack_footprint_m2(rack) * len(lista)

    profundidade_total = max(y_cursor - CORREDOR_TRANSICAO_M - y0_racks, 0.0)
    area_envolvente = largura_total * profundidade_total
    area_corredores = max(area_envolvente - area_racks, 0.0)

    return WarehouseLayout(
        posicoes=todas,
        largura_total_m=round(largura_total, 3),
        profundidade_total_m=round(profundidade_total, 3),
        area_total_m2=round(area_envolvente, 3),
        area_corredores_m2=round(area_corredores, 3),
        zonas=zonas_layout,
    )

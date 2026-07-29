"""Passo 3 — Footprint dos racks. Unidades: metros / m²."""
from __future__ import annotations
from dataclasses import dataclass, field

from .models import RackType, PosicaoChao, WarehouseDimensioning, WarehouseLayout, Zona


def rack_footprint_m2(rack: RackType) -> float:
    """Área ocupada por um rack (larg_m x prof_m). Usa rack.footprint_m2 se != 0."""
    if rack.footprint_m2:
        return rack.footprint_m2
    return rack.larg_m * rack.prof_m


def area_oversize_chao(artigos_stock: list[tuple], altura_max_m: float,
                       peso_max_kg: float, acesso_pct: float = 0.0) -> tuple[float, list[dict]]:
    """Área de chão para peças volumosas arrumadas EM PILHA, sem rack
    ("block stacking"). Alternativa à wide-span quando são poucas referências.

    artigos_stock: [(sku, comp_m, larg_m, alt_m, peso_kg, unidades), ...]

    A peça assenta DEITADA (menor cota = altura, as duas maiores no chão) e
    empilham-se peças DO MESMO SKU. Dois limites, vale o menor:
      - altura_max_m: alcance do operador / estabilidade da pilha;
      - peso_max_kg: carga que a peça do fundo aguenta.

    Devolve (area_total_m2, detalhe_por_sku). A área inclui `acesso_pct` de
    corredor/manuseio: sem rack não há filas nem corredores medidos pelo layout,
    por isso o acesso tem de entrar aqui ou fica subcontado.
    """
    detalhe = []
    area_pilhas = 0.0
    for sku, comp_m, larg_m, alt_m, peso_kg, unidades in artigos_stock:
        if unidades <= 0 or min(comp_m, larg_m, alt_m) <= 0:
            continue
        cotas = sorted([comp_m, larg_m, alt_m], reverse=True)
        footprint = cotas[0] * cotas[1]
        altura_peca = cotas[2]

        por_altura = max(int(altura_max_m / altura_peca), 1) if altura_max_m > 0 else 1
        por_peso = (max(int(peso_max_kg / peso_kg), 1)
                    if peso_max_kg > 0 and peso_kg > 0 else por_altura)
        por_pilha = max(min(por_altura, por_peso), 1)

        pilhas = -(-unidades // por_pilha)      # ceil
        area = pilhas * footprint
        area_pilhas += area
        detalhe.append({
            "sku": sku, "unidades": unidades,
            "footprint_peca_m2": round(footprint, 3),
            "por_pilha": por_pilha, "pilhas": pilhas,
            "limitado_por": "altura" if por_altura <= por_peso else "peso",
            "peso_pilha_kg": round(por_pilha * peso_kg, 1),
            "area_m2": round(area, 2),
        })
    return round(area_pilhas * (1.0 + max(acesso_pct, 0.0)), 2), detalhe


def floor_area(posicoes: list[PosicaoChao],
               rack_types: dict[str, RackType],
               pilhas_chao: list | None = None) -> WarehouseDimensioning:
    """Footprint total = soma(footprint_rack x nº posições de chão) por tipologia.

    Módulos empilhados na mesma posição (PosicaoChao.num_blocos, decidido
    posição a posição por allocate_to_racks) partilham o footprint — só as
    posições de chão contam para a área.
    """
    detalhe: dict[str, dict] = {}
    for pos in posicoes:
        fp_unit = rack_footprint_m2(rack_types[pos.rack_nome])
        d = detalhe.setdefault(pos.rack_nome, {
            "num_racks": 0, "posicoes_chao": 0, "footprint_total_m2": 0.0,
        })
        d["num_racks"] += pos.num_blocos
        d["posicoes_chao"] += 1
        d["footprint_total_m2"] = round(d["footprint_total_m2"] + fp_unit, 4)

    for d in detalhe.values():
        d["blocos_empilhados"] = max(round(d["num_racks"] / d["posicoes_chao"]), 1) \
            if d["posicoes_chao"] else 1

    # Pilhas no chão: são uma fiada do bloco de racks (layout.gerar_layout), por
    # isso o footprint delas é área OCUPADA como a dos racks. Sem isto, o layout
    # contava-as (para medir corredores = envolvente - ocupado) mas o resumo não,
    # e os m² apareciam como corredor.
    if pilhas_chao:
        fp = sum(p.comp_m * p.larg_m for p in pilhas_chao)
        detalhe["Armazenagem ao chão"] = {
            "num_racks": 0, "posicoes_chao": len(pilhas_chao),
            "footprint_total_m2": round(fp, 4), "blocos_empilhados": 1,
        }

    area_racks = sum(v["footprint_total_m2"] for v in detalhe.values())
    return WarehouseDimensioning(area_racks_m2=area_racks, detalhe=detalhe)


@dataclass
class ResumoAreaTotal:
    """Área bruta total do armazém: racks + corredores + zonas funcionais + margem
    estrutural. Zonas são somadas à parte dos corredores de racks (que são
    medidos pelo layout físico, não estimados por um fator)."""
    area_racks_m2: float
    area_corredores_m2: float
    area_zonas_m2: float
    margem_estrutural_m2: float
    area_bruta_total_m2: float
    zonas: list[Zona] = field(default_factory=list)
    # Peças volumosas em pilha no chão (ver area_oversize_chao). INFORMATIVO: as
    # pilhas são uma fiada do bloco de racks (layout.gerar_layout), por isso a sua
    # área já está no envolvente -- somá-la aqui contava duas vezes.
    area_oversize_chao_m2: float = 0.0


def resumo_area_total(dim: WarehouseDimensioning, wl: WarehouseLayout,
                      zonas: list[Zona], margem_estrutural_pct: float = 0.0,
                      area_oversize_chao_m2: float = 0.0) -> ResumoAreaTotal:
    """area_oversize_chao_m2 é INFORMATIVO e NÃO entra no total: as pilhas estão
    agrupadas no bloco de racks, logo o seu footprint já vem em dim.area_racks_m2
    e o espaço em volta já está medido em wl.area_corredores_m2."""
    area_racks = dim.area_racks_m2
    area_corredores = wl.area_corredores_m2
    area_zonas = sum(z.area_m2 for z in zonas)
    margem = (area_racks + area_corredores + area_zonas) * margem_estrutural_pct
    total = area_racks + area_corredores + area_zonas + margem
    return ResumoAreaTotal(area_racks, area_corredores, area_zonas, margem, total,
                           zonas, max(area_oversize_chao_m2, 0.0))

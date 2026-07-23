"""Passo 3 — Footprint dos racks. Unidades: metros / m²."""
from __future__ import annotations
from dataclasses import dataclass, field

from .models import RackType, PosicaoChao, WarehouseDimensioning, WarehouseLayout, Zona


def rack_footprint_m2(rack: RackType) -> float:
    """Área ocupada por um rack (larg_m x prof_m). Usa rack.footprint_m2 se != 0."""
    if rack.footprint_m2:
        return rack.footprint_m2
    return rack.larg_m * rack.prof_m


def floor_area(posicoes: list[PosicaoChao],
               rack_types: dict[str, RackType]) -> WarehouseDimensioning:
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


def resumo_area_total(dim: WarehouseDimensioning, wl: WarehouseLayout,
                      zonas: list[Zona], margem_estrutural_pct: float = 0.0) -> ResumoAreaTotal:
    area_racks = dim.area_racks_m2
    area_corredores = wl.area_corredores_m2
    area_zonas = sum(z.area_m2 for z in zonas)
    margem = (area_racks + area_corredores + area_zonas) * margem_estrutural_pct
    total = area_racks + area_corredores + area_zonas + margem
    return ResumoAreaTotal(area_racks, area_corredores, area_zonas, margem, total, zonas)

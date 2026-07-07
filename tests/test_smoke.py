"""Testes mínimos: a aritmética dos passos implementados (área) e os imports."""
from warehouse import area
from warehouse.models import (RackType, LoadType, PosicaoChao, BlocoRack, NivelRack,
                              Zona, WarehouseDimensioning, WarehouseLayout)


def test_longo_existe():
    assert LoadType.LONGO.value == "LONGO"


def test_footprint():
    r = RackType("A", larg_m=2.7, prof_m=1.1, alt_nivel_m=1.5, niveis=4)
    assert abs(area.rack_footprint_m2(r) - 2.97) < 1e-9


def test_floor_area():
    r = RackType("A", larg_m=2.7, prof_m=1.1, alt_nivel_m=1.5, niveis=4)
    posicoes = [
        PosicaoChao(rack_nome="A", indice=i,
                   blocos=[BlocoRack(bloco_idx=0, niveis=[NivelRack(nivel_idx=0, eh_picking=False)])])
        for i in range(10)
    ]
    dim = area.floor_area(posicoes, {"A": r})
    assert abs(dim.area_racks_m2 - 29.7) < 1e-3
    assert dim.detalhe["A"]["num_racks"] == 10
    assert abs(dim.detalhe["A"]["footprint_total_m2"] - 29.7) < 1e-3


def test_zona_area():
    z = Zona(nome="Zona IN", categoria="Receção", comp_m=6, larg_m=5)
    assert abs(z.area_m2 - 30) < 1e-9


def test_resumo_area_total():
    dim = WarehouseDimensioning(area_racks_m2=100.0)
    wl = WarehouseLayout(area_corredores_m2=50.0)
    zonas = [Zona(nome="Zona IN", categoria="Receção", comp_m=6, larg_m=5),
             Zona(nome="Zona OUT", categoria="Expedição", comp_m=5, larg_m=4)]
    resumo = area.resumo_area_total(dim, wl, zonas, margem_estrutural_pct=0.1)
    assert abs(resumo.area_zonas_m2 - 50.0) < 1e-9
    assert abs(resumo.margem_estrutural_m2 - 20.0) < 1e-9  # (100+50+50) * 0.1
    assert abs(resumo.area_bruta_total_m2 - 220.0) < 1e-9


if __name__ == "__main__":
    test_longo_existe(); test_footprint(); test_floor_area()
    test_zona_area(); test_resumo_area_total()
    print("smoke OK")

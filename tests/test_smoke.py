"""Testes mínimos: a aritmética dos passos implementados (área) e os imports."""
from warehouse import area
from warehouse.models import RackType, LoadType, PosicaoChao, BlocoRack, NivelRack


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


if __name__ == "__main__":
    test_longo_existe(); test_footprint(); test_floor_area()
    print("smoke OK")

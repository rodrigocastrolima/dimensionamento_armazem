"""Testes mínimos: a aritmética dos passos implementados (área) e os imports."""
from warehouse import area, racking
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


def test_picking_nivel_pelo_topo():
    """Bug do nível de picking: um nível só é de picking se o TOPO (base +
    altura do nível) couber na altura de picking (alcance máximo), não a base."""
    pp = RackType("PP", larg_m=2.7, prof_m=1.1, alt_nivel_m=1.8, niveis=4)
    bloco = racking._abrir_bloco(pp, 0, altura_picking_m=2.0)
    # topo n0 = 1.8 <= 2 -> picking; topo n1 = 3.6 > 2 -> reserva
    assert [n.eh_picking for n in bloco.niveis] == [True, False, False, False]

    pl = RackType("PL", larg_m=1.0, prof_m=0.5, alt_nivel_m=0.7, niveis=3)
    bl = racking._abrir_bloco(pl, 0, altura_picking_m=2.0)
    # topo n0=0.7, n1=1.4 <= 2 -> picking; n2=2.1 > 2 -> reserva
    assert [n.eh_picking for n in bl.niveis] == [True, True, False]


def test_sem_modulo_fantasma_picking():
    """Bug da altura: uma caixa de picking que não cabe não deve deixar um
    módulo empilhado vazio agarrado à posição (módulos empilhados não têm
    níveis de picking, logo a caixa nunca lá entra)."""
    from warehouse.racking import _ItemParaColocar, _abrir_bloco, _tentar_colocar_em_posicao
    r = RackType("PL", larg_m=1.0, prof_m=1.0, alt_nivel_m=0.7, niveis=1)
    pos = PosicaoChao(rack_nome="PL", indice=0)
    pos.blocos.append(_abrir_bloco(r, 0, altura_picking_m=0.8))  # nível 0: topo 0.7 <= 0.8 -> picking

    # 1.ª caixa de picking ocupa o footprint todo do único nível de picking
    item1 = _ItemParaColocar("A", "", LoadType.M, 1.0, 1.0, 0.5, 1, True)
    assert _tentar_colocar_em_posicao(pos, r, item1, 0.8, max_blocos=3,
                                      preferir_picking=True, permitir_fallback=False)
    assert pos.num_blocos == 1

    # 2.ª caixa de picking não cabe (nível cheio); o módulo empilhado que se
    # abriria é reserva -> não deve ser criado nenhum módulo-fantasma
    item2 = _ItemParaColocar("B", "", LoadType.M, 1.0, 1.0, 0.5, 1, True)
    assert not _tentar_colocar_em_posicao(pos, r, item2, 0.8, max_blocos=3,
                                          preferir_picking=True, permitir_fallback=False)
    assert pos.num_blocos == 1


if __name__ == "__main__":
    test_longo_existe(); test_footprint(); test_floor_area()
    test_zona_area(); test_resumo_area_total()
    test_picking_nivel_pelo_topo(); test_sem_modulo_fantasma_picking()
    print("smoke OK")

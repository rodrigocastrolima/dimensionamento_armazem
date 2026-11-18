"""Medidas dos racks da SIA desde 2026-11-18 (decisão do utilizador): passo de nível
do porta-paletes 1,747 m (antes 1,8) e Prateleira Leve com vão de 1,825 m (antes 1,80).

Alturas ÚTEIS (deck + carga, já com os 10 cm de manuseamento):
  níveis 1 e 2 = 1,747 - viga 0,09 - folga 0,10 = 1,557
  nível 3      = 4,6 - 2 x 1,747 - folga 0,10   = 1,006 (sem viga por cima).
Lê o master data real.
"""
import pytest

from warehouse import data_access, io_excel, racking
from warehouse.config import Config
from warehouse.models import LoadType

PASSO, PE_DIREITO, DECK = 1.747, 4.6, 0.144


@pytest.fixture(scope="module")
def cfg():
    return Config.for_client("sia")


@pytest.fixture(scope="module")
def racks(cfg):
    return {r.nome: r for r in io_excel.load_rack_types(cfg.master_data)}


def test_alturas_uteis_do_porta_paletes(cfg, racks):
    util = PASSO - cfg.viga_m - cfg.folga_manuseio_m
    topo = PE_DIREITO - 2 * PASSO - cfg.folga_manuseio_m
    assert util == pytest.approx(1.557) and topo == pytest.approx(1.006)
    for nome in ("Rack Porta-Paletes", "Rack Porta-Paletes 2"):
        r = racks[nome]
        assert r.alturas_niveis_m == pytest.approx((util, util, topo))
        assert r.alt_nivel_m == pytest.approx(util)                       # máximo, só referência
        assert [racking.altura_nivel(r, i) for i in range(3)] == pytest.approx([util, util, topo])


def test_cotas_fisicas_vigas_a_1_747_e_3_494_e_prumo_de_4_6(cfg, racks):
    cotas, total = racking.cotas_fisicas_niveis(racks["Rack Porta-Paletes"], 3, cfg)
    assert cotas == pytest.approx([0.0, PASSO, 2 * PASSO])
    assert total == pytest.approx(PE_DIREITO)             # o último nível não tem viga por cima


def test_palete_e_meia_acompanham_a_altura_util(cfg):
    cat = {b.nome: b for b in io_excel.load_catalog(cfg.master_data)}
    for tipo in (LoadType.PALLET, LoadType.MEIA):
        assert cat[tipo].ext_alt_m == pytest.approx(1.557)
        assert cat[tipo].int_alt_m == pytest.approx(1.557 - DECK)         # deck da palete 0,144


def test_terceiro_nivel_1_006_com_carga_de_0_862_sobre_o_deck(racks):
    r = racks["Rack Porta-Paletes"]
    assert racking.altura_nivel(r, 0) == racking.altura_nivel(r, 1) == pytest.approx(1.557)
    assert racking.altura_nivel(r, 2) == pytest.approx(1.006)             # antes 0,81
    assert racking.altura_nivel(r, 2) - DECK == pytest.approx(0.862)      # carga máxima sobre o deck
    # FRONT END (0,84 m) e REAR END (0,87 m), já com o deck, passam a caber no 3.º nível
    for alt_palete in (0.84, 0.87):
        assert alt_palete <= racking.altura_nivel(r, 2) + 1e-9


def test_prateleira_leve_1825(racks):
    r = racks["Prateleira Leve"]
    assert r.larg_m == pytest.approx(1.825) and r.prof_m == pytest.approx(0.6)
    assert r.footprint_m2 == pytest.approx(1.825 * 0.6)


def test_prateleira_fila_dupla_com_12_5_cm_entre_costas(cfg):
    assert cfg.folga_costas_rack_m["prateleira"] == 0.125
    pl = next(r for r in data_access.load_rack_types(cfg) if r.nome == "Prateleira Leve")
    assert pl.prof_dupla_m == pytest.approx(2 * 0.6 + 0.125)

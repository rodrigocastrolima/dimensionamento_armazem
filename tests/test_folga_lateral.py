"""Folga lateral entre caixas (Config.folga_lateral_rack_m, SIA: 1 cm nas
prateleiras) e prateleira própria para as XS (folha «Racks»: «Prateleira XS»).

A folga só existe ao longo do vão (eixo x): n caixas levam (n-1) folgas e nenhuma
encosta ao pilar. Na profundidade as caixas ficam encostadas (2 S de 0,30 m
enchem os 0,60 m do nível)."""
from warehouse import data_access, racking
from warehouse.config import Config
from warehouse.models import LoadType, NivelRack, RackType


def _leve(folga=0.0):
    return RackType("Prateleira Leve", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7, niveis=6,
                    folga_lateral_m=folga)


def _item(comp, larg, lt=LoadType.S, sku="X", alt=0.2):
    return racking._ItemParaColocar(sku=sku, descricao="", load_type=lt, comp_m=comp, larg_m=larg,
                                    alt_m=alt, pecas_nesta_caixa=1, eh_primeira_do_sku=False)


def _encher(rack, comp, larg, n, lt=LoadType.S):
    """Mete até n caixas iguais num nível; devolve quantas coube."""
    nivel = NivelRack(nivel_idx=0, eh_picking=False)
    colocadas = 0
    for i in range(n):
        item = _item(comp, larg, lt, sku=f"S{i}")
        sol = racking._empacotar_nivel(nivel, rack, item)
        if sol is None:
            break
        racking._commit_nivel(nivel, item, sol)
        colocadas += 1
    return colocadas, nivel


def test_sem_folga_nada_muda():
    # caixas de 0,60 x 0,30: 6 de pé (6 x 0,30 = 1,80 m) ou 3 x 2 deitadas (3 x 0,60 = 1,80 m)
    n, _ = _encher(_leve(0.0), 0.60, 0.30, 8)
    assert n == 6


def test_com_folga_deixa_de_caber_o_que_enchia_o_vao_ao_milimetro():
    # com 1 cm, 6 x 0,31 = 1,86 m e 3 x 0,61 = 1,83 m passam de 1,81 m: só cabem 5
    n, _ = _encher(_leve(0.01), 0.60, 0.30, 8)
    assert n == 5


def test_8_S_continuam_a_caber_com_1_cm_e_ha_folga_entre_elas():
    n, nivel = _encher(_leve(0.01), 0.40, 0.30, 8)
    assert n == 8      # 4 x 0,40 + 3 folgas = 1,63 m; a profundidade NÃO leva folga (2 x 0,30 = 0,60)
    cs = sorted(nivel.caixas, key=lambda c: (c.y_m, c.x_m))
    for fila in (cs[:4], cs[4:]):
        for a, b in zip(fila, fila[1:]):
            assert b.x_m - (a.x_m + a.comp_m) >= 0.01 - 1e-6
    # as dimensões gravadas são as REAIS da caixa (sem a folga)
    assert all(abs(c.comp_m - 0.40) < 1e-6 and abs(c.larg_m - 0.30) < 1e-6 for c in cs)
    # nenhuma encosta ao fundo: x + comp <= vão
    assert all(c.x_m + c.comp_m <= 1.8 + 1e-6 for c in cs)


def test_4_S_e_1_XS_cabem_lado_a_lado_com_1_cm():
    rack = _leve(0.01)
    nivel = NivelRack(nivel_idx=0, eh_picking=False)
    itens = [_item(0.40, 0.30, sku=f"S{i}") for i in range(4)] + [_item(0.30, 0.15, LoadType.XS, "XS", 0.12)]
    for it in itens:
        sol = racking._empacotar_nivel(nivel, rack, it)
        assert sol is not None
        racking._commit_nivel(nivel, it, sol)


def test_rack_capacity_usa_a_folga_no_vao():
    from warehouse.models import BoxType
    xs = BoxType(LoadType.XS, 0.28, 0.13, 0.11, 0.30, 0.15, 0.12, 10)
    m = BoxType(LoadType.M, 0.58, 0.38, 0.40, 0.60, 0.40, 0.42, 30)
    # sem folga: XS 12 x 2 = 24 por nível; com 1 cm: 11 x 2 = 22
    assert racking.rack_capacity(_leve(0.0), xs) == 24 * 6
    assert racking.rack_capacity(_leve(0.01), xs) == 22 * 6
    # M: 4 por nível (0,40 no vão) nos dois casos
    assert racking.rack_capacity(_leve(0.0), m) == racking.rack_capacity(_leve(0.01), m) == 4 * 6


def test_aplicar_folgas_laterais_por_substring_case_insensitive():
    cfg = Config(folga_lateral_rack_m={"PRATELEIRA": 0.01})
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.8, niveis=1)
    pl, xs, pp2 = data_access.aplicar_folgas_laterais(
        [_leve(), RackType("Prateleira XS", 1.8, 0.6, 0.7, 6), pp], cfg)
    assert pl.folga_lateral_m == 0.01 and xs.folga_lateral_m == 0.01 and pp2.folga_lateral_m == 0.0
    assert data_access.aplicar_folgas_laterais([_leve()], Config())[0].folga_lateral_m == 0.0


def test_sia_tem_3_cm_nas_prateleiras():
    # 2026-11-12: a «Prateleira XS» (só XS) foi um erro e saiu do master data (decisão do
    # utilizador) -- as XS voltam a ir para a Prateleira Leve. Folga: 1 cm -> 3 cm (decisão do
    # utilizador: com 1 cm uma XS entrava à justa no canto ao lado de 4 caixas M)
    cfg = Config.for_client("sia")
    assert cfg.folga_lateral_rack_m == {"prateleira": 0.03}
    racks = {r.nome: r for r in data_access.load_rack_types(cfg)}
    assert racks["Prateleira Leve"].folga_lateral_m == 0.03
    assert racks["Rack Porta-Paletes"].folga_lateral_m == 0.0
    assert "Prateleira XS" not in racks
    assert {LoadType.XS, LoadType.S, LoadType.M, LoadType.L} <= set(racks["Prateleira Leve"].aceita)

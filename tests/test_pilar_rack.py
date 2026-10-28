"""Pilar do porta-paletes: `Larg_m` é o vão LIVRE (capacidade); o pilar só soma
ao espaço de chão (layout, fila manual da sandbox, área)."""
import dataclasses

from warehouse import area, data_access, layout as layout_mod, racking
from warehouse.config import Config
from warehouse.models import RackType, PosicaoChao


def _pp(**kw):
    return RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.8, niveis=1, **kw)


def test_sem_pilar_nada_muda():
    r = _pp()
    assert r.larg_total_m == 2.7
    assert r.area_chao_m2 == 2.7 * 1.2
    assert area.rack_footprint_m2(r) == r.area_chao_m2


def test_pilar_soma_so_ao_chao_nao_ao_vao_util():
    r = _pp(pilar_m=0.09)
    assert abs(r.larg_total_m - 2.79) < 1e-9
    assert abs(r.area_chao_m2 - 2.79 * 1.2) < 1e-9
    # a capacidade continua a usar o vão livre: 3 paletes de 0.8 em 2.7 m
    assert r.larg_m == 2.7


def test_override_de_footprint_tambem_leva_o_pilar():
    r = _pp(pilar_m=0.09, footprint_m2=3.24)      # 3.24 = módulo SEM pilar
    assert abs(r.area_chao_m2 - 3.24 * 2.79 / 2.7) < 1e-9


def test_aplicar_pilares_por_substring_case_insensitive():
    cfg = Config(pilar_rack_m={"PORTA-paletes": 0.09})
    leve = RackType("Prateleira Leve", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7, niveis=6)
    pp, pl = data_access.aplicar_pilares([_pp(), leve], cfg)
    assert pp.pilar_m == 0.09 and pl.pilar_m == 0.0
    assert data_access.aplicar_pilares([_pp()], Config())[0].pilar_m == 0.0


def test_fila_manual_e_layout_usam_passo_com_pilar():
    r = _pp(pilar_m=0.09)
    fila = layout_mod.gerar_fila_manual(r, 3, False)
    assert [round(p.x_m, 4) for p in fila] == [0.0, 2.79, 5.58]
    assert all(abs(p.largura_m - 2.79) < 1e-9 for p in fila)

    posicoes = [PosicaoChao(rack_nome=r.nome, indice=i) for i in range(4)]
    sem = layout_mod.gerar_layout(posicoes, {r.nome: _pp()})
    com = layout_mod.gerar_layout(posicoes, {r.nome: r})
    assert com.largura_total_m > sem.largura_total_m
    assert all(abs(p.largura_m - 2.79) < 1e-9 for p in com.posicoes)


def test_par_emparelhado_herda_o_pilar():
    cfg = Config(oversize_par_racks=("porta-paletes",))
    sint = racking.com_par_oversize([_pp(pilar_m=0.09)], cfg)[-1]
    assert sint.pilar_m == 0.09 and sint.profundidade_inclui_par


def test_sia_json_define_pilar_do_porta_paletes():
    cfg = Config.for_client("sia")
    assert cfg.pilar_rack_m.get("porta-paletes", 0) > 0
    assert "prateleira" not in cfg.pilar_rack_m


def _posicoes(r, n):
    return [PosicaoChao(rack_nome=r.nome, indice=i) for i in range(n)]


def test_fila_tem_pilar_de_fecho_k_mais_1_pilares():
    """k módulos: k x vão + (k+1) x pilar (pilar do meio partilhado; as pontas têm o seu)."""
    r = _pp(pilar_m=0.09)
    wl = layout_mod.gerar_layout(_posicoes(r, 3), {r.nome: r}, largura_max_m=100)
    # 3 módulos numa só fiada (frente+trás = 2 por coluna -> k=2 colunas)
    colunas = len({round(p.x_m, 4) for p in wl.posicoes})
    assert abs(wl.largura_total_m - (colunas * 2.7 + (colunas + 1) * 0.09)) < 1e-3
    sem = layout_mod.gerar_layout(_posicoes(_pp(), 3), {r.nome: _pp()}, largura_max_m=100)
    assert abs(sem.largura_total_m - colunas * 2.7) < 1e-3


def test_area_de_racks_inclui_fechos_e_corredores_nao_inflacionam():
    r, r0 = _pp(pilar_m=0.09), _pp()
    pos = _posicoes(r, 8)
    wl = layout_mod.gerar_layout(pos, {r.nome: r})
    wl0 = layout_mod.gerar_layout(_posicoes(r0, 8), {r0.nome: r0})
    assert wl.fechos_pilar_m2.get(r.nome, 0) > 0
    dim = area.floor_area(pos, {r.nome: r}, wl.pilhas_chao, wl.fechos_pilar_m2)
    # racks + corredores = envolvente (nada do pilar vira "corredor" por engano)
    assert abs(dim.area_racks_m2 + wl.area_corredores_m2 - wl.area_total_m2) < 5e-3
    # os pilares custam área; o corredor é praticamente o mesmo (só alonga com a fila)
    assert wl.area_total_m2 > wl0.area_total_m2
    assert abs(wl.area_corredores_m2 - wl0.area_corredores_m2) < 0.5 * wl0.area_corredores_m2 + 1.0


def test_largura_max_conta_o_pilar_de_fecho():
    r = _pp(pilar_m=0.09)
    # 3 módulos = 3*2.79 + 0.09 = 8.46 m: cabe em 8.46, não em 8.40
    wl = layout_mod.gerar_layout(_posicoes(r, 6), {r.nome: r}, largura_max_m=8.46)
    assert wl.largura_total_m <= 8.46 + 1e-6

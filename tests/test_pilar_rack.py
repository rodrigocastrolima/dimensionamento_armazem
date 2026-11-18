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


def test_sia_json_define_pilar_do_porta_paletes_e_da_prateleira():
    cfg = Config.for_client("sia")
    assert cfg.pilar_rack_m.get("porta-paletes") == 0.09
    # 2026-11-18: Prateleira Leve = vão 1,825 m + 5 cm de pilar (antes 1,80 m «total»)
    assert cfg.pilar_rack_m.get("prateleira") == 0.05


def _leve(**kw):
    return RackType("Prateleira Leve", larg_m=1.825, prof_m=0.6, alt_nivel_m=0.7, niveis=6, **kw)


def test_prateleira_leve_1825_mais_5cm_de_pilar_partilhado():
    """Vão 1,825 m + 5 cm: cada módulo mede 1,875 m e a fila k x 1,875 + 0,05 --
    o pilar entre módulos que se tocam é UM só (não 2)."""
    r = _leve(pilar_m=0.05)
    assert abs(r.larg_total_m - 1.875) < 1e-9
    assert abs(r.area_chao_m2 - 1.875 * 0.6) < 1e-9
    fila = layout_mod.gerar_fila_manual(r, 4, False)
    assert [round(p.x_m, 4) for p in fila] == [0.0, 1.875, 3.75, 5.625]
    wl = layout_mod.gerar_layout(_posicoes(r, 4), {r.nome: r}, largura_max_m=100)
    colunas = len({round(p.x_m, 4) for p in wl.posicoes})
    # k módulos: k x vão + (k+1) x pilar (nunca 2k pilares)
    assert abs(wl.largura_total_m - (colunas * 1.825 + (colunas + 1) * 0.05)) < 1e-3
    # a capacidade continua a usar o vão livre
    assert r.larg_m == 1.825


# ----------------------------------------------------------------- fila dupla
def test_aplicar_folga_costas_por_substring():
    cfg = Config(folga_costas_rack_m={"PORTA-paletes": 0.10})
    pp, pl = data_access.aplicar_folgas_costas([_pp(), _leve()], cfg)
    assert pp.folga_costas_m == 0.10 and pl.folga_costas_m == 0.0
    assert data_access.aplicar_folgas_costas([_pp()], Config())[0].folga_costas_m == 0.0


def test_fila_dupla_do_porta_paletes_tem_2_5_m_de_fundo():
    r = _pp(folga_costas_m=0.10)
    assert abs(r.prof_dupla_m - 2.5) < 1e-9
    fila = layout_mod.gerar_fila_manual(r, 2, True)
    frente = [p for p in fila if p.lado == "frente"]
    tras = [p for p in fila if p.lado == "tras"]
    assert all(p.y_m == 0.0 for p in frente)
    assert all(abs(p.y_m - 1.3) < 1e-9 for p in tras)          # 1,2 de frente + 0,10 entre costas
    assert max(p.y_m + p.profundidade_m for p in fila) == 2.5   # a rack continua com 1,2 m
    assert all(p.profundidade_m == 1.2 for p in fila)

    pos = _posicoes(r, 2)                                         # 1 coluna x 2 lados
    wl = layout_mod.gerar_layout(pos, {r.nome: r}, largura_max_m=100)
    assert abs(wl.profundidade_total_m - 2.5) < 1e-3
    sem = layout_mod.gerar_layout(_posicoes(_pp(), 2), {r.nome: _pp()}, largura_max_m=100)
    assert abs(sem.profundidade_total_m - 2.4) < 1e-3


def test_fila_simples_nao_leva_folga_entre_costas():
    r = _pp(folga_costas_m=0.10)
    assert [p.y_m for p in layout_mod.gerar_fila_manual(r, 3, False)] == [0.0] * 3
    wl = layout_mod.gerar_layout(_posicoes(r, 1), {r.nome: r}, largura_max_m=100)
    assert abs(wl.profundidade_total_m - 1.2) < 1e-3             # só frente: sem folga


def test_folga_entre_costas_e_area_da_fila_nao_corredor():
    """A folga conta como área de rack (fila dupla de 2,5 m), não como corredor."""
    r = _pp(pilar_m=0.09, folga_costas_m=0.10)
    pos = _posicoes(r, 8)
    wl = layout_mod.gerar_layout(pos, {r.nome: r})
    assert wl.fechos_pilar_m2[r.nome] > 0.09 * 1.2                # fechos + folgas
    dim = area.floor_area(pos, {r.nome: r}, wl.pilhas_chao, wl.fechos_pilar_m2)
    assert abs(dim.area_racks_m2 + wl.area_corredores_m2 - wl.area_total_m2) < 5e-3


def test_emparelhada_herda_a_folga_entre_costas_na_profundidade():
    cfg = Config(oversize_par_racks=("porta-paletes",))
    sint = racking.com_par_oversize([_pp(folga_costas_m=0.10)], cfg)[-1]
    assert sint.profundidade_inclui_par and abs(sint.prof_m - 2.5) < 1e-9
    assert sint.folga_costas_m == 0.0 and abs(sint.prof_dupla_m - 2.5) < 1e-9   # não dobra outra vez
    assert abs(layout_mod._layout_tipologia(sint, _posicoes(sint, 1), 0.0)[2] - 2.5) < 1e-9


def test_sia_json_define_a_folga_entre_costas():
    cfg = Config.for_client("sia")
    assert cfg.folga_costas_rack_m.get("porta-paletes") == 0.10
    assert abs(2 * 1.2 + cfg.folga_costas_rack_m["porta-paletes"] - 2.5) < 1e-9


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


def test_fiadas_encostadas_da_mesma_tipologia_partilham_o_pilar():
    """Só o lado solto leva pilar sozinho: duas fiadas da mesma tipologia lado a
    lado medem (kA+kB) x total + pilar, não kA x total + kB x total + 2 pilares."""
    r = _pp(pilar_m=0.09)
    chunk = lambda x0, k: {                                        # noqa: E731
        "nome": r.nome, "pilar": 0.09, "largura": k * 2.79 + 0.09, "profundidade": 1.2,
        "corredor_transicao": 3.0, "corredores": [],
        "posicoes": [layout_mod.PosicaoLayout(posicao=PosicaoChao(rack_nome=r.nome, indice=i),
                                              x_m=i * 2.79, y_m=0.0, largura_m=2.79,
                                              profundidade_m=1.2, fila_idx=0, lado="frente")
                     for i in range(k)]}
    a, b = chunk(0, 2), chunk(0, 3)
    assert abs(layout_mod._largura_prateleira([a, b]) - (5 * 2.79 + 0.09)) < 1e-9
    pos, _, _, largura, _ = layout_mod._empacotar_tipologias([a, b], 5 * 2.79 + 0.09, 0.0)
    xs = sorted(round(p.x_m, 4) for p in pos)
    assert xs == [0.0, 2.79, 5.58, 8.37, 11.16]                  # sem folga entre as duas
    assert abs(largura - (5 * 2.79 + 0.09)) < 1e-6
    assert abs(sum(layout_mod._fechos_pilar_m2(pos, {r.nome: r}).values()) - 0.09 * 1.2) < 1e-9
    # tipologias DIFERENTES não partilham pilar
    c = dict(b, nome="Outra")
    assert abs(layout_mod._largura_prateleira([a, c]) - (a["largura"] + c["largura"])) < 1e-9

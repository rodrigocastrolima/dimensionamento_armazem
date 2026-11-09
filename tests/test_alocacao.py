"""Alocação às racks do arranjo (warehouse/alocacao.py): regras em casos
pequenos feitos à mão + integração com o bloco dimensionado real."""
import dataclasses
from pathlib import Path

import pytest

from warehouse import alocacao as A
from warehouse.config import Config
from warehouse.models import LoadType, RackType
from warehouse.procura import Taxa
from warehouse.racking import _ItemParaColocar, altura_nivel

CFG = Config()


def _item(sku, lt=LoadType.XS, comp=1.8, larg=0.6, alt=0.5, n=1):
    return _ItemParaColocar(sku=sku, descricao=sku, load_type=lt, comp_m=comp, larg_m=larg, alt_m=alt,
                            pecas_nesta_caixa=10 * n, eh_primeira_do_sku=False, num_boxes_in_stack=n)


def _pos(pid, rack, x, niveis=None):
    return A.PosicaoFisica(pid, "F", int(x) + 1, rack, niveis or rack.niveis, x, 0.0, rack.larg_m, rack.prof_m)


def _taxas(**vis):
    return {s: Taxa(s, v, v, ("Caixas",)) for s, v in vis.items()}


# um nível leva exatamente 1 item de 1,8 x 0,6
PRAT1 = RackType("Prateleira", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7, niveis=1, aceita=[LoadType.XS])
PRAT2 = dataclasses.replace(PRAT1, niveis=2)
PP = RackType("PP", larg_m=1.3, prof_m=1.0, alt_nivel_m=1.61, niveis=3, aceita=[LoadType.PALLET],
              alturas_niveis_m=(1.61, 1.61, 0.81))


def test_o_sku_com_mais_visitas_fica_mais_perto():
    pos = [_pos("perto", PRAT1, 0.0), _pos("longe", PRAT1, 20.0)]
    itens = [_item("FREQ"), _item("RARO")]
    for ordem in (itens, itens[::-1]):
        res = A.alocar(pos, ordem, {LoadType.XS: [PRAT1]}, _taxas(FREQ=5, RARO=0.01), (0, 0), CFG)
        sitio = {c.sku: c.posicao.id for c in res.colocacoes}
        assert sitio == {"FREQ": "perto", "RARO": "longe"}
        assert not res.nao_alocados


def test_palete_alta_nunca_vai_para_o_nivel_de_081():
    # 2 paletes altas (pouca saída) + 1 baixa (muita saída), 1 posição de 3 níveis:
    # por frequência a baixa ia para o nível 1, mas aí as altas ficavam sem lugar -- tem de ir
    # para o nível 3 (0,81 m), o único onde as altas não cabem
    pos = [_pos("P", PP, 0.0)]
    itens = [_item("ALTA1", LoadType.PALLET, 1.2, 0.8, 1.4), _item("ALTA2", LoadType.PALLET, 1.2, 0.8, 1.4),
             _item("BAIXA", LoadType.PALLET, 1.2, 0.8, 0.6)]
    res = A.alocar(pos, itens, {LoadType.PALLET: [PP]}, _taxas(ALTA1=0.1, ALTA2=0.1, BAIXA=9), (0, 0), CFG)
    assert not res.nao_alocados
    nivel = {c.sku: c.nivel_idx for c in res.colocacoes}
    assert nivel["BAIXA"] == 2 and {nivel["ALTA1"], nivel["ALTA2"]} == {0, 1}
    for c in res.celulas:
        assert all(cx.alt_m <= altura_nivel(PP, c.nivel.nivel_idx) + 1e-9 for cx in c.nivel.caixas)


def test_sem_rack_compativel_no_arranjo_fica_por_alocar_com_motivo():
    res = A.alocar([_pos("P", PRAT1, 0.0)], [_item("X", LoadType.PALLET, 1.2, 0.8, 1.0)],
                   {LoadType.PALLET: [PP], LoadType.XS: [PRAT1]}, _taxas(X=1), (0, 0), CFG)
    (n,) = res.nao_alocados
    assert n.sku == "X" and "não tem nenhuma rack que a aceite" in n.motivo and not res.colocacoes


def test_sem_espaco_fica_por_alocar_com_motivo():
    res = A.alocar([_pos("P", PRAT1, 0.0)], [_item("X"), _item("Y")], {LoadType.XS: [PRAT1]},
                   _taxas(X=1, Y=2), (0, 0), CFG)
    assert len(res.colocacoes) == 1 and len(res.nao_alocados) == 1
    assert "sem espaço" in res.nao_alocados[0].motivo


def test_fixacao_manda_mesmo_contra_a_frequencia():
    pos = [_pos("perto", PRAT1, 0.0), _pos("longe", PRAT1, 20.0)]
    longe = pos[1].endereco()
    res = A.alocar(pos, [_item("FREQ"), _item("RARO")], {LoadType.XS: [PRAT1]},
                   _taxas(FREQ=5, RARO=0.01), (0, 0), CFG, fixacoes={"FREQ": longe})
    assert {c.sku: c.posicao.id for c in res.colocacoes} == {"FREQ": "longe", "RARO": "perto"}


def test_fixacao_para_posicao_inexistente_da_aviso():
    res = A.alocar([_pos("P", PRAT1, 0.0)], [_item("X")], {LoadType.XS: [PRAT1]}, _taxas(X=1), (0, 0), CFG,
                   fixacoes={"X": "não existe · M09"})
    assert res.colocacoes and any("ignorada" in a for a in res.avisos)


def test_incremental_mantem_onde_estava_mesmo_com_a_frequencia_trocada():
    pos = [_pos("perto", PRAT1, 0.0), _pos("longe", PRAT1, 20.0)]
    itens = [_item("A1"), _item("B1")]
    r1 = A.alocar(pos, itens, {LoadType.XS: [PRAT1]}, _taxas(A1=5, B1=1), (0, 0), CFG)
    estado = A.estado_da_alocacao(r1)
    # a BOM mudou: agora B1 sai mais -- de raiz trocava, em incremental fica
    de_raiz = A.alocar(pos, itens, {LoadType.XS: [PRAT1]}, _taxas(A1=1, B1=5), (0, 0), CFG)
    inc = A.alocar(pos, itens, {LoadType.XS: [PRAT1]}, _taxas(A1=1, B1=5), (0, 0), CFG, anterior=estado)
    assert {c.sku: c.posicao.id for c in de_raiz.colocacoes}["B1"] == "perto"
    assert {c.sku: c.posicao.id for c in inc.colocacoes} == {"A1": "perto", "B1": "longe"}
    assert inc.mudancas == []


@pytest.mark.parametrize("tolerancia, esperado", [(1.0, {"P1"}), (0.0, {"P1", "P2"})])
def test_contiguidade_com_tolerancia(tolerancia, esperado):
    # P1: 2 níveis de 1,0 m (o topo do 2.º, a 2 m, passa 1,6 m: +0,5 min); P2: 1 nível 3 m mais longe
    # (+0,07 min de order-picker, ida e volta). X tem 2 unidades: com tolerância fica junta em P1.
    alto = dataclasses.replace(PRAT2, alt_nivel_m=1.0)
    pos = [_pos("P1", alto, 0.0), A.PosicaoFisica("P2", "G", 1, PRAT1, 1, 3.0, 0.0, 1.8, 0.6)]
    cfg = dataclasses.replace(CFG, alocacao_tolerancia_contiguo_min=tolerancia)
    res = A.alocar(pos, [_item("X"), _item("X"), _item("Y")], {LoadType.XS: [PRAT1, alto]},
                   _taxas(X=5, Y=0.1), (0, 0), cfg)
    assert {c.posicao.id for c in res.colocacoes if c.sku == "X"} == esperado


def _p(pid, fila, mod, x, y, rack=PRAT1):
    return A.PosicaoFisica(pid, fila, mod, rack, rack.niveis, x, y, rack.larg_m, rack.prof_m)


def test_vizinhanca_lado_e_frente_do_corredor_mas_nao_costas_com_costas():
    # A (y 0-0,6) e B (y 0,6-1,2) costas com costas; C (y 2,4) em frente de B, corredor de 1,2 m
    pos = [_p(f"{f}{m}", f, m, 1.8 * (m - 1), y) for f, y in (("A", 0.0), ("B", 0.6), ("C", 2.4))
           for m in (1, 2, 3)]
    v = A.vizinhanca(pos)
    assert v["A1"]["A2"] == 1 and v["A1"]["A3"] == 2
    assert "B1" not in v["A1"]                       # costas com costas: outro corredor
    assert v["B1"]["C1"] == 1 and v["B1"]["C2"] == 2  # em frente (mesma paragem) e o do lado dele


def test_unidade_seguinte_fica_no_modulo_ao_lado_e_nao_noutra_fila():
    # X tem 2 unidades e cada posição leva 1. G1 (costas com costas com F1) está mais perto do
    # que F2, mas F2 é o módulo ao lado: X fica em F1 + F2 e Y vai para G1.
    pos = [_p("F1", "F", 1, 0.0, 0.0), _p("F2", "F", 2, 1.8, 0.0), _p("G1", "G", 1, 0.0, 0.6)]
    res = A.alocar(pos, [_item("X"), _item("X"), _item("Y")], {LoadType.XS: [PRAT1]},
                   _taxas(X=5, Y=0.1), (0, 0), CFG)
    sitio = {}
    for c in res.colocacoes:
        sitio.setdefault(c.sku, set()).add(c.posicao.id)
    assert sitio == {"X": {"F1", "F2"}, "Y": {"G1"}} and not res.nao_alocados


def test_troca_de_posicoes_inteiras_leva_os_modulos_de_um_sku_juntos():
    # Duas filas noutros corredores (H mais perto da carga, à direita), com as distâncias
    # intercaladas: H3 < H2 < H1 < F3 < F2 < F1. BIG ocupa 2 módulos (unidades de outra forma,
    # por isso a troca de unidades não as mistura). Trocar posição a posição punha BIG em H1 + F3
    # (filas diferentes); em bloco fica em 2 módulos seguidos da mesma fila.
    pos = [_p(f"F{m}", "F", m, 1.8 * (m - 1), 0.0) for m in (1, 2, 3)] + \
          [_p(f"H{m}", "H", m, 9.0 + 1.8 * (m - 1), 5.0) for m in (1, 2, 3)]
    big = _item("BIG", comp=1.2)
    itens = [big, dataclasses.replace(big), _item("HOT"), _item("MID1"), _item("MID2"), _item("COLD")]
    res = A.alocar(pos, itens, {LoadType.XS: [PRAT1]}, _taxas(BIG=0.4, HOT=5, MID1=1, MID2=0.05, COLD=0.01),
                   (20.0, 0.0), CFG)
    assert not res.nao_alocados
    sitio = {}
    for c in res.colocacoes:
        sitio.setdefault(c.sku, set()).add(c.posicao.id)
    a, b = sorted(sitio["BIG"])
    assert b in A.vizinhanca(pos)[a]                             # módulos vizinhos, mesma fila
    assert sitio["HOT"] == {"H3"}


def test_posicoes_do_render_ids_estaveis_e_modulos_ao_longo_da_fila():
    racks = {"Prateleira": PRAT1}
    itens = [dict(rack_nome="Prateleira", indice=i, x=10.0 - 1.8 * i, y=5.0, larg_m=1.8, prof_m=0.6, niveis=1,
                  lado="frente", bloco_id="b1", bloco_nome="Fila A") for i in range(3)]
    pos = A.posicoes_do_render(itens, racks)
    assert [p.modulo for p in pos] == [1, 2, 3] and [p.x_m for p in pos] == sorted(p.x_m for p in pos)
    assert pos[0].id == "b1|frente|2" and pos[0].fila == "Fila A frente"
    assert pos[0].endereco(1) == "Fila A frente · M01 · N2"


def test_ponto_de_carga_e_o_centro_da_zona_do_comboio():
    zonas = [{"nome": "Receção", "x": 0, "y": 0, "larg_m": 5, "prof_m": 5},
             {"nome": "Zona armazenamento COMBOIO", "x": 10, "y": 2, "larg_m": 3.5, "prof_m": 1.2}]
    assert A.ponto_de_carga(zonas) == (11.75, 2.6)
    assert A.ponto_de_carga(zonas[:1]) is None
    # nome igual primeiro: «espera» não pode apanhar a «Espera para Armazenagem» antes da «ZONA DE ESPERA»
    zonas2 = [{"nome": "Espera para Armazenagem", "x": 0, "y": 0, "larg_m": 2, "prof_m": 2},
              {"nome": "ZONA DE ESPERA", "x": 20, "y": 0, "larg_m": 10, "prof_m": 6}]
    assert A.ponto_de_carga(zonas2, "ZONA DE ESPERA") == (25.0, 3.0)
    assert A.ponto_de_carga(zonas2, "zona de espera ") == (25.0, 3.0)


def test_comparar_bom():
    antes = {"A": ["XS", 10, 2, 1.0], "B": ["S", 5, 1, 0.5], "C": ["M", 4, 1, 1.0]}
    agora = {"A": ["XS", 10, 2, 1.0], "B": ["S", 5, 2, 1.0], "D": ["L", 1, 3, 1.0]}
    d = A.comparar_bom(antes, agora)
    assert d == {"novos": ["D"], "removidos": ["C"], "alterados": ["B"], "n": 3}
    assert A.comparar_bom(antes, antes)["n"] == 0


def test_assinatura_logica_muda_com_as_regras():
    base = A.assinatura_logica(CFG)
    assert A.assinatura_logica(dataclasses.replace(CFG)) == base
    assert A.assinatura_logica(dataclasses.replace(CFG, alocacao_tolerancia_contiguo_min=0.3)) != base
    assert A.assinatura_logica(dataclasses.replace(CFG, carros_por_dia=2)) == base   # entrada, não regra


def test_classes_abc():
    c = A.classes_abc({"a": 80, "b": 15, "c": 5, "z": 0})
    assert c == {"a": "A", "b": "B", "c": "C", "z": "—"}


# ----------------------------------------------------------------- dados reais
LIVRO = (Path(__file__).resolve().parents[1] / "../../../Linha de Montagem/Assembly Ceiia - Hangar + BDL/"
         "dimensionamento_linha/dados/comboio bulk + armazem 23-10 D2 auto.xlsx").resolve()


@pytest.mark.skipif(not LIVRO.exists(), reason="livro do comboio não acessível nesta máquina")
def test_o_bloco_dimensionado_leva_todo_o_stock(tmp_path):
    from warehouse import boxing, data_access, io_excel, layout as L, racking
    cfg = dataclasses.replace(Config.for_client("sia"), pressupostos_picking=LIVRO,
                              alocacao_estado=tmp_path / "estado.json",
                              alocacao_fixacoes=tmp_path / "fixacoes.json",
                              tempos_calibracao=tmp_path / "calibracao.json")
    local = io_excel.com_carrinhos_da_linha(dataclasses.replace(cfg, altura_armazem_m=4.6))
    cat = data_access.load_catalog(local)
    arts, _ = boxing.aplicar_stock_autonomia(data_access.load_articles(local), cat, local)
    asg = boxing.plan_all(arts, cat, local)
    rts = racking.com_par_oversize(data_access.load_rack_types(local), local)
    cpt = {b.nome: b for b in cat}
    req, _ = racking.racks_needed(asg, rts, cpt, local, 0.0, arts)
    posd, _ = racking.allocate_to_racks(asg, req, rts, cpt, arts, 0.0, 4.6, local)
    wl = L.gerar_layout(posd, {r.nome: r for r in rts}, None, 0, 0, [], 1.8, 1000, local.picking_manual_racks)
    blocos = [{"id": "racking", "type": "racking", "nome": "Bloco", "x": 0, "y": 0,
               "w": wl.largura_total_m, "h": wl.profundidade_total_m, "rot": 0},
              {"id": "z", "type": "zone", "nome": "Zona armazenamento comboio",
               "x": wl.largura_total_m + 1, "y": 0, "w": 3.5, "h": 1.2, "rot": 0}]
    res, _ = A.run(cfg, blocos, 4.6, out=tmp_path / "alocacao.xlsx")
    # sem estado: de raiz, e esta alocação passa a ser o estado (sem perguntar)
    assert res.estado["modo"] == "de raiz" and res.estado["guardado"] and not res.estado["pode_guardar"]
    assert (tmp_path / "estado.json").exists() and res.estado["calibracao"] == "refeita"
    assert not res.nao_alocados
    assert sum(c.n_unidades for c in res.colocacoes) == sum(a.num_unidades for a in asg)
    # tempos: com os tempos do livro bate com a «7b Voltas», o «Bulk» e o «Gantt»
    falhas = [f"{v.nome}: {v.detalhe}" for v in res.tempos.validacao if not v.ok]
    assert not falhas, falhas
    assert (tmp_path / "calibracao.json").exists()
    # cronograma de um dia: armazém e comboio, com os picks das preparações
    from warehouse import tempos
    cr = tempos.cronograma(res.tempos, 2)
    recursos = {t["recurso"] for t in cr["tarefas"]}
    assert recursos == {"Armazém", "Comboio"}
    assert any(t["n_picks"] for t in cr["tarefas"] if t["recurso"] == "Armazém")
    assert res.planta["percursos"]       # calor do chão
    # a seguir, sem nada mudado: incremental sozinho, ninguém sai do sítio, nada para guardar,
    # calibração lida (não refeita)
    res2, _ = A.run(cfg, blocos, 4.6, out=tmp_path / "alocacao2.xlsx")
    assert res2.estado["modo"] == "incremental" and res2.mudancas == [] and not res2.nao_alocados
    assert not res2.estado["pode_guardar"] and res2.estado["bom"]["n"] == 0 and res2.estado["calibracao"] == "lida"
    # a lógica mudou (outra tolerância de contiguidade): de raiz, e pode-se guardar
    cfg3 = dataclasses.replace(cfg, alocacao_tolerancia_contiguo_min=0.0)
    res3, _ = A.run(cfg3, blocos, 4.6, out=tmp_path / "alocacao3.xlsx")
    assert res3.estado["modo"] == "de raiz" and res3.estado["logica_mudou"] and res3.estado["pode_guardar"]


def test_zona_de_bulk_fica_mais_perto_mesmo_com_menos_visitas():
    # o bulk sai todos os dias e prepara-se de uma vez: fica junto ao destino mesmo que uma
    # referência das voltas tenha mais visitas por dia (sem a zona, manda só a frequência)
    pos = [_pos("perto", PRAT1, 0.0), _pos("longe", PRAT1, 20.0)]
    taxas = {"BULK": Taxa("BULK", 1, 1, ("Bulk",)), "VOLTA": Taxa("VOLTA", 5, 5, ("Caixas",))}
    itens = [_item("BULK"), _item("VOLTA")]
    com = A.alocar(pos, itens, {LoadType.XS: [PRAT1]}, taxas, (0, 0), CFG)
    sem = A.alocar(pos, itens, {LoadType.XS: [PRAT1]}, taxas, (0, 0),
                   dataclasses.replace(CFG, alocacao_zona_bulk=False))
    assert {c.sku: c.posicao.id for c in com.colocacoes} == {"BULK": "perto", "VOLTA": "longe"}
    assert {c.sku: c.posicao.id for c in sem.colocacoes} == {"BULK": "longe", "VOLTA": "perto"}
    assert A.zona_do_sku(None) == A.ZONA_SEM_PICKING
    assert A.zona_do_sku(Taxa("C", 1, 1, ("Reposição semanal",))) == A.ZONA_SEMANAL


def test_arrumacao_por_frequencia_cai_para_a_por_area_se_deixar_algo_de_fora():
    # 2 posições de 1,8 m: por frequência as 2 caixas pequenas (0,6) ficavam juntas na 1.ª e as 2
    # grandes (1,2) já não cabiam as duas; por área (1,2 + 0,6 em cada) cabe tudo
    pos = [_pos("P1", PRAT1, 0.0), _pos("P2", PRAT1, 5.0)]
    itens = [_item("S1", comp=0.6), _item("S2", comp=0.6), _item("B1", comp=1.2), _item("B2", comp=1.2)]
    res = A.alocar(pos, itens, {LoadType.XS: [PRAT1]}, _taxas(S1=5, S2=5, B1=0.1, B2=0.1), (0, 0), CFG)
    assert not res.nao_alocados
    assert sum(c.n_unidades for c in res.colocacoes) == 4


def test_por_frequencia_o_mais_frequente_fica_mais_perto():
    # mesma forma não, área diferente: por área a grande (pouca saída) ia para a de perto
    pos = [_pos("perto", PRAT1, 0.0), _pos("longe", PRAT1, 20.0)]
    itens = [_item("GRANDE", comp=1.8), _item("PEQUENA", comp=0.6)]
    res = A.alocar(pos, itens, {LoadType.XS: [PRAT1]}, _taxas(GRANDE=0.1, PEQUENA=5), (0, 0), CFG)
    assert {c.sku: c.posicao.id for c in res.colocacoes} == {"PEQUENA": "perto", "GRANDE": "longe"}


def test_oversize_num_par_frente_tras_de_porta_paletes_com_dois_corredores():
    # fila dupla de porta-paletes (frente y 0-1,2 e trás y 1,2-2,4, costas com costas), corredor dos dois
    # lados: o módulo frente+trás aceita uma OVERSIZE (1,4 m de fundo) como uma emparelhada, e esse nível
    # deixa de poder levar paletes nos dois módulos reais
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.61, niveis=1,
                  aceita=[LoadType.PALLET])
    pos = [A.PosicaoFisica("F", "Fila frente", 1, pp, 1, 0.0, 0.0, 2.7, 1.2),
           A.PosicaoFisica("T", "Fila trás", 1, pp, 1, 0.0, 1.2, 2.7, 1.2)]
    cfg = dataclasses.replace(CFG, oversize_par_racks=("porta-paletes",))
    extra, pares, racks = A.posicoes_em_par(pos, [], (1.35, -3.0), cfg)
    assert len(extra) == 1 and pares[extra[0].id] == ("F", "T") and extra[0].prof_m == pytest.approx(2.4)
    cand = {LoadType.PALLET: [pp], LoadType.OVERSIZE: list(racks.values())}
    itens = [_item("OVER", LoadType.OVERSIZE, 1.4, 2.0, 1.4), _item("PAL", LoadType.PALLET, 1.2, 0.8, 1.0)]
    res = A.alocar(pos + extra, itens, cand, _taxas(OVER=1, PAL=1), (1.35, -3.0), cfg, pares=pares)
    sitio = {c.sku: c.posicao.id for c in res.colocacoes}
    assert sitio.get("OVER") == extra[0].id
    assert "PAL" not in sitio and len(res.nao_alocados) == 1      # o único nível ficou para a OVERSIZE
    # sem corredor atrás (um obstáculo encostado), não há par
    sem, _, _ = A.posicoes_em_par(pos, [{"x": -1.0, "y": 2.4, "w": 5.0, "h": 3.0}], (1.35, -3.0), cfg)
    assert sem == []


def test_json_mostra_as_oversize_do_par_nos_dois_modulos_reais():
    # a vista de frente e o mapa leem os níveis de cada módulo real: o nível ocupado pelo par tem de
    # aparecer lá (antes aparecia «vazio»), com metade das visitas do par em cada módulo
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.61, niveis=1,
                  aceita=[LoadType.PALLET])
    pos = [A.PosicaoFisica("F", "Fila frente", 1, pp, 1, 0.0, 0.0, 2.7, 1.2),
           A.PosicaoFisica("T", "Fila trás", 1, pp, 1, 0.0, 1.2, 2.7, 1.2)]
    cfg = dataclasses.replace(CFG, oversize_par_racks=("porta-paletes",))
    extra, pares, racks = A.posicoes_em_par(pos, [], (1.35, -3.0), cfg)
    cand = {LoadType.PALLET: [pp], LoadType.OVERSIZE: list(racks.values())}
    taxas = _taxas(OVER=2)
    res = A.alocar(pos + extra, [_item("OVER", LoadType.OVERSIZE, 1.4, 2.0, 1.4)], cand, taxas, (1.35, -3.0),
                   cfg, pares=pares)
    js = {p["id"]: p for p in A.para_json(res, taxas)["posicoes"]}
    for real in ("F", "T"):
        n1 = js[real]["niveis"][0]
        assert n1["caixas"] == [] and [c["sku"] for c in n1["par"]["caixas"]] == ["OVER"]
        assert js[real]["visitas_dia"] == pytest.approx(js[extra[0].id]["visitas_dia"] / 2)
    assert js[extra[0].id]["par"] is True

"""Tempos de picking (warehouse/tempos.py): percursos, custo de uma visita,
calibração e Gantt do armazém, em casos pequenos feitos à mão. A validação
contra o livro real está em tests/test_alocacao.py (bloco dimensionado)."""
import math

import pytest

from warehouse import tempos as T
from warehouse.alocacao import PosicaoFisica
from warehouse.config import Config
from warehouse.models import LoadType, RackType
from warehouse.procura import Pick

CFG = Config()
PRAT = RackType("Prateleira", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7, niveis=6, aceita=[LoadType.XS])
PP = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.1, alt_nivel_m=1.61, niveis=3,
              aceita=[LoadType.PALLET], alturas_niveis_m=(1.61, 1.61, 0.81))


def _pos(pid, x, y, w, h, rack=PRAT):
    return PosicaoFisica(pid, "F", 1, rack, rack.niveis, x, y, w, h)


def test_percurso_contorna_a_fila_de_racks():
    # fila contínua de x 0..10 em y 2..3; o módulo B está por trás dela (y 4..5)
    fila = [_pos(f"A{i}", 2.0 * i, 2.0, 2.0, 1.0) for i in range(5)]
    b = _pos("B", 4.0, 4.0, 2.0, 1.0)
    dist, avisos = T.distancias_acesso(fila + [b], [], (5.0, 0.5), passo=0.25)
    # em linha reta seriam ~3,5 m; pelo chão tem de dar a volta a uma ponta da fila
    assert dist["B"] > 10.0 and dist["B"] < 16.0
    assert dist["A2"] < 2.0          # a frente da fila está logo ali
    assert not avisos


def test_so_se_chega_pelos_lados_compridos_e_posicao_fechada_da_aviso():
    # B entre duas racks pelos lados compridos: só as cabeceiras ficam livres -> sem acesso
    cima = _pos("C", 0.0, 0.0, 4.0, 1.0)
    b = _pos("B", 1.0, 1.0, 2.0, 0.6)
    baixo = _pos("D", 0.0, 1.6, 4.0, 1.0)
    dist, avisos = T.distancias_acesso([cima, b, baixo], [], (-3.0, 1.3), passo=0.1)
    assert avisos and "sem acesso" in avisos[0]
    assert dist["B"] == pytest.approx(abs(2.0 - (-3.0)) + abs(1.3 - 1.3))   # caiu para a distância em planta


def test_fresta_mais_estreita_que_a_passagem_minima_nao_e_caminho():
    # duas filas com uma fresta de 0,4 m entre elas; B está do outro lado
    a1 = _pos("A1", 0.0, 2.0, 4.0, 1.0)
    a2 = _pos("A2", 4.4, 2.0, 4.0, 1.0)
    b = _pos("B", 3.4, 4.0, 2.0, 1.0)
    sem, _ = T.distancias_acesso([a1, a2, b], [], (4.2, 0.5), passo=0.1)
    com, _ = T.distancias_acesso([a1, a2, b], [], (4.2, 0.5), passo=0.1, folga=0.4)
    assert sem["B"] < 4.0              # passava pela fresta
    assert com["B"] > 6.0              # com passagem mínima de 0,8 m dá a volta à fila
    calor, caminhos = T.rotas([a1, a2, b], [], (4.2, 0.5), {"B": 1.0}, passo=0.1, folga=0.4)
    for x, y, lado, v in calor:        # nenhum pedaço de caminho dentro de uma rack
        cx, cy = x + lado / 2, y + lado / 2
        assert not any(p.x_m < cx < p.x_m + p.larg_m and p.y_m < cy < p.y_m + p.prof_m for p in (a1, a2, b))
    assert len(caminhos["B"]) >= 2


def test_obstaculo_tambem_e_parede():
    b = _pos("B", 4.0, 0.0, 2.0, 1.0)
    livre, _ = T.distancias_acesso([b], [], (5.0, 3.0), passo=0.25)
    com, _ = T.distancias_acesso([b], [{"x": -2.0, "y": 1.5, "w": 14.0, "h": 0.5, "rot": 0}], (5.0, 3.0),
                                 passo=0.25)
    assert com["B"] > livre["B"] + 5.0


def test_custo_de_uma_visita_ida_e_volta_mais_altura():
    v = CFG.picking_velocidade_m_s["order-picker"]
    # prateleira de 6 níveis de 0,7 m: o 3.º nível (base 1,4, topo 2,1) já passa 1,6 m
    assert T.custo_visita_min(30.0, PRAT, 0, CFG) == pytest.approx(2 * 30 / v / 60)
    assert T.custo_visita_min(30.0, PRAT, 1, CFG) == pytest.approx(2 * 30 / v / 60)   # topo 1,4 m
    assert T.custo_visita_min(30.0, PRAT, 2, CFG) == pytest.approx(2 * 30 / v / 60 + 0.5)
    # porta-paletes: empilhador; o 1.º nível tem topo 1,61 m > 1,6 -> já conta
    ve = CFG.picking_velocidade_m_s["empilhador"]
    assert T.custo_visita_min(10.0, PP, 0, CFG) == pytest.approx(2 * 10 / ve / 60 + 0.5)
    assert T.custo_visita_min(10.0, PP, 0, CFG, T.A_PE) == pytest.approx(2 * 10 / 0.8 / 60 + 0.5)


def _tl(**kw):
    base = dict(caixa=2.75, caixa_picking=2.5, palete=4.0, palete_picking=3.0, carrinho=0.75, peca_carrinho=3.0,
                peca_extra=0.5, caixa_kit=0.75, kit_pequena=2.0, kit_grande=3.0, carr_caixas=0.5, fixo_caixas=2.0,
                carr_paletes=0.5, fixo_paletes=2.0, ocupacao=0.85, tempo_dia=450.0, pausas=(120.0, 225.0, 345.0),
                partidas={"Caixas": [0.0, 75.0], "Paletes": [42.0, 103.0]})
    base.update(kw)
    return T.TemposLivro(**base)


def _pick(sku, unidade="XS", n=1, origem="Bordo de linha", pecas=None):
    return Pick(1, "Caixas", 1, 0.0, sku, sku, unidade, n, pecas if pecas is not None else n, "C1", origem)


def test_calibracao_mantem_a_media_do_livro():
    tl = _tl()
    desl = {("PERTO", T.GRUPO_CAIXA): 0.2, ("LONGE", T.GRUPO_CAIXA): 1.0, ("CONS", T.GRUPO_CAIXA): 5.0}
    picks = [_pick("PERTO", n=3), _pick("LONGE", n=1), _pick("CONS", n=9, origem="Consumível")]
    cal, avisos = T.calibrar(picks, desl, tl)
    media = (3 * 0.2 + 1 * 1.0) / 4                       # os consumíveis não entram
    assert cal[T.GRUPO_CAIXA]["deslocacao_media_min"] == pytest.approx(media)
    assert cal[T.GRUPO_CAIXA]["manuseio_min"] == pytest.approx(2.5 - media)
    t_perto, _ = T.tempo_pick(picks[0], desl, cal, tl)
    t_longe, _ = T.tempo_pick(picks[1], desl, cal, tl)
    assert (3 * t_perto + t_longe) / 4 == pytest.approx(2.5) and t_perto < t_longe
    assert not avisos


def test_calibracao_com_deslocacao_maior_que_o_livro_da_aviso():
    cal, avisos = T.calibrar([_pick("X")], {("X", T.GRUPO_CAIXA): 4.0}, _tl())
    assert cal[T.GRUPO_CAIXA]["manuseio_min"] == 0.0 and avisos


def test_reposicao_semanal_tem_tempo_proprio_e_nao_entra_no_gantt():
    tl = _tl()
    desl = {("CONS", T.GRUPO_CAIXA): 1.0}
    cal, _ = T.completar_calibracao({g: {"deslocacao_media_min": 0.5} for g in T.GRUPOS}, tl)
    picks = [Pick(6, T.SEMANAL, 0, 0.0, "CONS", "c", "XS", 4, 40, "", "Consumível")]
    p = T._prep_semanal(6, picks, desl, cal, tl)
    # 4 × (esvaziar 0,25 + manuseio 2,0 + deslocação 1,0) + lista e verificação 2
    assert p.modelo == pytest.approx(4 * (0.25 + 2.0 + 1.0) + 2.0) and p.consumiveis == p.modelo and p.livro == 0
    base = [T.Prep(1, "Caixas", 1, 0.0, 10, 10, 0, 1), T.Prep(1, "Paletes", 1, 42.0, 5, 5, 0, 1),
            T.Prep(1, "Caixas", 2, 75.0, 5, 5, 0, 1), T.Prep(1, "Paletes", 2, 103.0, 5, 5, 0, 1)]
    assert T.gantt(base + [p], tl).sobra_min == pytest.approx(T.gantt(base, tl).sobra_min)


def test_horas_de_relogio_e_segmentos_cortados_nas_pausas():
    tl = _tl(pausas=(120.0, 225.0), pausas_dur=(15.0, 60.0))
    assert T._hora(tl, 0.0) == "08:00"
    assert T._hora(tl, 120.0) == "10:15" and T._hora(tl, 120.0, False) == "10:00"   # começa depois / acaba antes
    assert T._hora(tl, 294.0) == "14:09"                                          # a viagem forçada do livro
    assert T._segmentos(tl, 110.0, 130.0) == [[110.0, 120.0], [135.0, 145.0]]
    assert T._pausas_relogio(tl) == [[120.0, 135.0], [240.0, 300.0]]


def test_cronograma_encadeado_sem_horas_fixas():
    # voltas na ordem do livro (C1 P1 C2 P2), cada uma parte quando o trem está pronto e o comboio livre
    tl = _tl(t_volta={("Caixas", 1): 20.0, ("Paletes", 1): 15.0, ("Caixas", 2): 20.0, ("Paletes", 2): 15.0},
             pausas_dur=(15.0, 60.0, 15.0))
    pior = {("Caixas", 1): 10, ("Paletes", 1): 30, ("Caixas", 2): 30, ("Paletes", 2): 10,
            ("Bulk caixas", 0): 90, ("Bulk paletes", 0): 100}
    tar, r = T._dia(pior, tl, horario=False)   # encadeamento puro (sem o horário do livro)
    m = 1 / 0.85 - 1
    voltas = {t.nome: t for t in tar if t.tipo == "volta"}
    prep = {t.nome: t for t in tar if t.tipo == "preparação"}
    assert voltas["Volta Caixas 1"].ini == 0.0                       # preparada na véspera
    assert prep["Preparar Paletes 1"].ini == 0.0
    assert voltas["Volta Paletes 1"].ini == pytest.approx(30.0)      # mal fica pronta (o comboio já voltou)
    # a preparação seguinte começa quando a anterior + margem acaba (e a volta anterior já partiu)
    assert prep["Preparar Caixas 2"].ini == pytest.approx(30 + 30 * m)
    c2 = 30 + 30 * m + 30
    assert voltas["Volta Caixas 2"].ini == pytest.approx(c2)
    # o comboio só pega na seguinte depois de início + duração ÷ 85 % da anterior
    assert voltas["Volta Paletes 2"].ini == pytest.approx(max(c2 + 30 * m + 10, c2 + 20 / 0.85))
    # bulk: caixas primeiro (D2), margem entre blocos e no fim; depois a 1.ª de amanhã
    t = c2 + 30 * m + 10 + 10 * m
    assert prep["Preparar bulk — Caixas (bulk)"].ini == pytest.approx(t)
    t += 90 + 90 * m
    assert prep["Preparar bulk — Paletes (bulk)"].ini == pytest.approx(t)
    t += 100 + 100 * m
    assert prep["Preparar Caixas 1 de amanhã"].ini == pytest.approx(t)
    assert r["fim"] == pytest.approx(t + 10 + 10 * m) and r["sobra"] == pytest.approx(450 - r["fim"])


def test_gantt_por_dia_so_falha_o_dia_do_pico():
    tl = _tl()
    P = T.Prep

    def dia(d, bulk):
        return [P(d, "Caixas", 1, 0.0, 10, 10, 0, 1), P(d, "Paletes", 1, 42.0, 5, 5, 0, 1),
                P(d, "Caixas", 2, 75.0, 10, 10, 0, 1), P(d, "Paletes", 2, 103.0, 5, 5, 0, 1),
                P(d, "Bulk paletes", 0, 450.0, bulk, bulk, 0, 1), P(d, "Bulk caixas", 0, 450.0, 50, 50, 0, 1)]
    preps = dia(1, 50) + dia(2, 400) + dia(3, 50)       # dia 2: bulk de paletes enorme -> passa do turno
    por = dict(T.gantt_por_dia(preps, tl, "livro"))
    assert por[1].sobra_min > 0 and por[2].sobra_min < 0 and por[3].sobra_min > 0
    # a pior preparação de cada posição (como o livro) junta o pico do dia 2
    g = T.gantt(preps, tl, "livro")
    assert g.sobra_min == pytest.approx(por[2].sobra_min)


def test_comboio_nao_atravessa_pausas_e_sem_partidas_forcadas():
    tl = _tl(t_volta={("Caixas", 1): 20.0, ("Caixas", 2): 20.0, ("Paletes", 1): 15.0, ("Paletes", 2): 15.0},
             pausas=(120.0, 225.0, 345.0), pausas_dur=(15.0, 60.0, 15.0), dolly_min=10.0,
             viagens_bulk=[{"nome": "Paletes 1", "tipo": "Paletes", "rel": 330.0, "dur": 20.0, "forcada": 300.0,
                            "prep": 30.0, "postos": "C1", "carruagens": 1},
                           {"nome": "Caixas 1", "tipo": "Caixas", "rel": 100.0, "dur": 30.0, "forcada": None,
                            "prep": 50.0, "postos": "C1", "carruagens": 1}])
    pior = {("Caixas", 1): 10, ("Caixas", 2): 10, ("Paletes", 1): 10, ("Paletes", 2): 10,
            ("Bulk paletes", 0): 30, ("Bulk caixas", 0): 50}
    tar, r = T._dia(pior, tl)
    v = {t.nome: t for t in tar if t.recurso == "Comboio"}
    # Paletes 1: disponível às 330 (posto), 20 min atravessariam a pausa das 345 -> depois dela
    assert v["Viagem do bulk — Paletes 1"].ini == pytest.approx(345.0)
    for t in tar:
        if t.recurso == "Comboio":
            assert not any(t.ini < p - 1e-9 < t.fim - 2e-9 for p in tl.pausas), t.nome
    assert v["Dollys C → P1 (à mão)"].ini >= v["Viagem do bulk — Paletes 1"].ini + 20 / 0.85 - 1e-9


def test_encaixar_comboio():
    tl = _tl(pausas=(120.0, 225.0), pausas_dur=(15.0, 60.0))
    assert T._encaixar_comboio(tl, 100.0, 15.0) == 100.0     # acaba antes da pausa
    assert T._encaixar_comboio(tl, 110.0, 15.0) == 120.0     # atravessava: depois da pausa
    assert T._encaixar_comboio(tl, 215.0, 120.0) == 225.0    # passa a 1.ª e cai em cima da 2.ª: depois desta


def test_encaixar_comboio_com_a_folga_do_livro_d2_auto():
    # «Parâmetros» C107: só fica antes da pausa se início + duração ÷ 85 % acabar antes dela
    tl = _tl(pausas=(120.0,), pausas_dur=(15.0,))
    assert T._encaixar_comboio(tl, 100.0, 17.0) == 100.0     # 100 + 20 = 120: cabe à justa
    assert T._encaixar_comboio(tl, 101.0, 17.0) == 120.0     # 101 + 17 acabava antes, mas sem folga
    # ... mesmo com a preparação atrasada pela margem: pronta a 95 com 90 min -> 95 + 15,9 + 10 > 120
    assert T._encaixar_comboio(tl, 100.0, 8.5, pronto=95.0, prep=90.0) == 120.0
    assert T._encaixar_comboio(tl, 100.0, 8.5, pronto=95.0, prep=20.0) == 100.0   # 95 + 3,5 + 10 < 120


def test_viagem_do_bulk_vai_num_intervalo_entre_voltas_e_dollys_depois_das_saidas_c():
    # P1 parte às 40 e volta às 55; C2 parte às 75: uma viagem de 15 min pronta às 50 cabe em [55, 75]
    tl = _tl(t_volta={("Caixas", 1): 20.0, ("Paletes", 1): 15.0, ("Caixas", 2): 20.0, ("Paletes", 2): 15.0},
             pausas=(300.0,), pausas_dur=(15.0,), dolly_min=10.0, dolly_rel=200.0,
             partidas={"Caixas": [0.0, 75.0], "Paletes": [40.0, 103.0]},
             viagens_bulk=[{"nome": "Paletes 1", "tipo": "Paletes", "rel": 50.0, "dur": 15.0, "forcada": None,
                            "prep": 1.0, "postos": "C1", "carruagens": 1}])
    pior = {("Caixas", 1): 10, ("Paletes", 1): 30, ("Caixas", 2): 40, ("Paletes", 2): 10,
            ("Bulk paletes", 0): 1, ("Bulk caixas", 0): 0}
    tar, _ = T._dia(pior, tl, horario=True)
    v = {t.nome: t for t in tar if t.recurso == "Comboio"}
    assert v["Volta Paletes 1"].ini == pytest.approx(40.0)    # horário do livro (pronta às 30)
    # nenhuma viagem atrasa uma volta regular: a do bulk vai depois do regresso da última que a impede
    voltas = sorted((t.ini, t.fim) for t in tar if t.tipo == "volta")
    b = v["Viagem do bulk — Paletes 1"]
    assert all(b.fim <= ini + 1e-9 or b.ini >= fim - 1e-9 for ini, fim in voltas)
    assert v["Dollys C → P1 (à mão)"].ini == pytest.approx(200.0)   # só depois das saídas das estações C
    # sem horário (simulação dia a dia): parte mal esteja pronta e o comboio livre
    tar2, _ = T._dia(pior, tl, horario=False)
    v2 = {t.nome: t for t in tar2 if t.recurso == "Comboio"}
    assert v2["Volta Paletes 1"].ini == pytest.approx(max(30.0, 20 / 0.85))


def test_carruagens_so_cheias():
    assert T._carruagens_so_cheias({"XS": 61}) == 2
    assert T._carruagens_so_cheias({"XS": 30, "S": 12, "M": 1, "PALLET": 1}) == 3   # B 1,0 + D + P
    assert T._carruagens_so_cheias({}) == 0
    assert math.isinf(T.INF)


def test_percurso_unico_do_bulk_de_caixas():
    # duas estantes seguidas, destino à direita: o percurso passa pela de perto a caminho da de longe
    from warehouse.alocacao import PosicaoFisica
    from warehouse.models import LoadType, RackType
    rack = RackType("Prateleira", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7, niveis=1, aceita=[LoadType.XS])
    pos = [PosicaoFisica("A", "F", 1, rack, 1, 0.0, 0.0, 1.8, 0.6),
           PosicaoFisica("B", "F", 2, rack, 1, 1.8, 0.0, 1.8, 0.6)]
    pc = T.Percursos(pos, [], (10.0, 0.3), 0.25, 0.4)
    r = pc.volta({"A": 2, "B": 1})
    assert r["paragens"] == 2 and r["ordem"] in (["A", "B"], ["B", "A"])
    assert r["idas_voltas_m"] == pytest.approx(2 * pc.ida_e_volta_m("A") + pc.ida_e_volta_m("B"), abs=0.1)
    assert r["metros"] == pytest.approx(pc.ida_e_volta_m("A"), rel=0.15)   # a de longe manda
    assert r["metros"] < r["idas_voltas_m"]
    assert r["pontos"][0] == pytest.approx(r["pontos"][-1])                # fechado: volta ao destino


def test_horario_do_livro_todos_os_dias_e_bulk_nunca_antes_das_13():
    # regras do utilizador (2026-11-05): o mesmo horário das voltas todos os dias (o do livro, pior
    # dos 30 dias) -- num dia mais pesado a volta sai atrasada; nenhuma viagem do bulk antes de C104
    tl = _tl(t_volta={("Caixas", 1): 20.0, ("Paletes", 1): 15.0, ("Caixas", 2): 20.0, ("Paletes", 2): 15.0},
             pausas=(120.0, 225.0, 345.0), pausas_dur=(15.0, 60.0, 15.0), bulk_hora_min=225.0,
             partidas={"Caixas": [0.0, 75.0], "Paletes": [42.0, 103.0]},
             viagens_bulk=[{"nome": "Paletes 1", "tipo": "Paletes", "rel": 0.0, "dur": 20.0, "forcada": None,
                            "prep": 5.0, "postos": "C1", "carruagens": 1}])
    leve = {("Caixas", 1): 10, ("Paletes", 1): 10, ("Caixas", 2): 10, ("Paletes", 2): 10,
            ("Bulk paletes", 0): 5, ("Bulk caixas", 0): 0}
    tar, r = T._dia(leve, tl)
    v = {t.nome: t for t in tar if t.recurso == "Comboio"}
    assert [v[f"Volta {n}"].ini for n in ("Paletes 1", "Caixas 2", "Paletes 2")] == pytest.approx([42.0, 75.0, 103.0])
    assert r["atrasadas"] == 0
    assert v["Viagem do bulk — Paletes 1"].ini == pytest.approx(225.0)     # pronta cedo, mas só às 13:00
    pesado = {**leve, ("Paletes", 1): 60}                                  # prep de 0 a 60 > 42
    tar, r = T._dia(pesado, tl)
    v = {t.nome: t for t in tar if t.recurso == "Comboio"}
    assert v["Volta Paletes 1"].ini == pytest.approx(60.0) and v["Volta Paletes 1"].atraso == pytest.approx(18.0)
    assert r["atrasadas"] >= 1

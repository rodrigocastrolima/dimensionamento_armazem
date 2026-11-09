"""Procura de picking por ciclo de reposição (warehouse/procura.py).

Pressupostos sintéticos (sem Excel) para as regras; um teste de integração
contra o livro real do comboio, saltado se o ficheiro não estiver acessível."""
from pathlib import Path

import pytest

from warehouse import procura as P

DIA = 450.0


def _linha(n, ref, unidade="S", snp=10, volta=P.CAIXAS, emb_dia=0.5, atraso=0.0, carrinho="",
           posto="C1", estacao="C1"):
    return P.LinhaPFEP(linha=n, ref=ref, descricao=ref, estacao=estacao, posto=posto, qtd_carro=1,
                       unidade=unidade, snp=snp, tipo_volta=volta, emb_h=emb_dia * 60 / DIA,
                       nivel_inicial=0.0, atraso_min=atraso, carrinho=carrinho)


def _pressupostos(linhas, eventos=None, bulk=P.BULK_TUDO, partidas=None):
    return P.Pressupostos(
        caminho=Path("sintetico.xlsx"), tempo_dia_min=DIA, carros_por_dia_base=1.0, dias_simulados=10,
        partidas=partidas or {P.CAIXAS: [0.0, 225.0], P.PALETES: [100.0]}, bulk_modo=bulk,
        bulk_minimo_emb_dia=1.0, kit_autonomia_max_dias=1.0, linhas=linhas, eventos=eventos or {}, kits={})


def test_esvaziadas_reproduz_a_formula_do_livro():
    # 0,5 emb./dia; consumo de 0,5 emb. aos 300 min de cada dia
    x = _linha(7, "A")
    p = _pressupostos([x], {("A", "C1"): [(300.0, 0.5)]})
    c = P._Contador(x, p, 1)
    assert c.esvaziadas(299) == 0          # INT(0 + 0) antes do consumo
    assert c.esvaziadas(300) == 0          # INT(0,5)
    assert c.esvaziadas(DIA + 300) == 1    # INT(1 × 0,5 + 0,5)
    assert c.esvaziadas(2 * DIA) == 1      # INT(2 × 0,5 + 0)


def test_caixa_com_atraso_so_e_entregue_depois_do_atraso():
    x = _linha(7, "A", atraso=DIA)          # 1 dia de atraso de reposição
    p = _pressupostos([x], {("A", "C1"): [(300.0, 0.5)]}, bulk=P.BULK_NENHUM)
    res = P.simular(p, 1, 4)
    dias = [k.dia for k in res.picks]
    # sem atraso a 1.ª entrega seria no dia 3 (esvaziada no dia 2 às 300 min, volta das 0 do dia 3);
    # com 1 dia de atraso passa para o dia 4
    assert dias == [4]


def test_bulk_tira_a_referencia_das_voltas():
    x = _linha(7, "A", emb_dia=1.0)
    eventos = {("A", "C1"): [(300.0, 1.0)]}
    so_paletes = P.simular(_pressupostos([x], eventos, bulk=P.BULK_SO_PALETES), 1, 5)
    tudo = P.simular(_pressupostos([x], eventos, bulk=P.BULK_TUDO), 1, 5)
    assert {k.ciclo for k in so_paletes.picks} == {P.CAIXAS}     # caixa: só vai no bulk com «tudo»
    assert {k.ciclo for k in tudo.picks} == {P.BULK}
    assert len(tudo.picks) == 5 and all(k.unidades == 1 for k in tudo.picks)


def test_carrinho_entregue_e_um_enchimento_de_todas_as_suas_referencias():
    a = _linha(7, "A", unidade=P.CARRINHO, snp=2, volta=P.PALETES, emb_dia=0.5, carrinho="1")
    b = _linha(8, "B", unidade=P.CARRINHO, snp=3, volta=P.PALETES, emb_dia=0.0, carrinho="1")  # 2.ª linha
    p = _pressupostos([a, b], {("A", "C1"): [(50.0, 0.5)]}, bulk=P.BULK_NENHUM)
    res = P.simular(p, 1, 4)
    assert res.entregas_por_volta and all(v == {P.CARRINHO: 1} for v in res.entregas_por_volta.values())
    por_volta = {}
    for k in res.picks:
        por_volta.setdefault((k.dia, k.volta), []).append((k.sku, k.pecas, k.unidade))
    assert all(sorted(v) == [("A", 2, "Peças"), ("B", 3, "Peças")] for v in por_volta.values())


def test_mais_carros_por_dia_multiplica_as_embalagens():
    x = _linha(7, "A", emb_dia=0.5)
    eventos = {("A", "C1"): [(300.0, 0.5)]}
    p = _pressupostos([x], eventos, bulk=P.BULK_NENHUM)
    um = sum(k.unidades for k in P.simular(p, 1, 20).picks)
    dois = sum(k.unidades for k in P.simular(p, 2, 20).picks)
    # última volta: dia 20 às 225 min. 1 carro: INT(19 × 0,5) = 9 (o consumo do dia é às 300).
    # 2 carros: 1 emb./dia, consumos às 150 e 375 min -> INT(19 × 1 + 0,5) = 19
    assert um == 9
    assert dois == 19


def test_carros_por_dia_tem_de_ser_inteiro():
    p = _pressupostos([_linha(7, "A")])
    with pytest.raises(ValueError):
        P.simular(p, 1.5, 5)
    with pytest.raises(ValueError):
        P.simular(p, 0, 5)


def test_consumivel_consumo_regular_reposto_nas_voltas_do_seu_tipo():
    p = _pressupostos([], bulk=P.BULK_TUDO)
    cs = [P.Consumivel("PARAFUSO", "parafuso", "XS", pecas_por_unidade=500, pecas_por_carro=25),  # 0,05/dia
          P.Consumivel("PALETE", "palete", "PALLET", pecas_por_unidade=4, pecas_por_carro=1),     # 0,25/dia
          P.Consumivel("CLIP", "clip", "XS", pecas_por_unidade=10, pecas_por_carro=12)]           # 1,2/dia -> bulk
    # 41 dias: a 1.ª volta do dia 41 parte com 40 dias completos consumidos
    res = P.simular(p, 1, 41, cs)
    por = {}
    for k in res.picks:
        por.setdefault(k.sku, []).append(k)
    assert sum(k.unidades for k in por["PARAFUSO"]) == 2 and {k.ciclo for k in por["PARAFUSO"]} == {P.CAIXAS}
    assert sum(k.unidades for k in por["PALETE"]) == 10 and {k.ciclo for k in por["PALETE"]} == {P.PALETES}
    assert {k.ciclo for k in por["CLIP"]} == {P.BULK} and sum(k.unidades for k in por["CLIP"]) == 49  # 41 × 1,2
    assert all(k.origem == "Consumível" for k in res.picks)


def test_consumivel_em_reposicao_semanal_a_parte():
    p = _pressupostos([], bulk=P.BULK_TUDO)
    cs = [P.Consumivel("PARAFUSO", "parafuso", "XS", pecas_por_unidade=10, pecas_por_carro=1),  # 0,1/dia
          P.Consumivel("CLIP", "clip", "XS", pecas_por_unidade=10, pecas_por_carro=12)]        # 1,2/dia
    res = P.simular(p, 1, 30, cs, consumiveis_modo=P.MODO_SEMANAL, periodo_dias=5)
    assert {k.ciclo for k in res.picks} == {P.SEMANAL}             # nem voltas nem bulk
    assert {k.dia for k in res.picks} <= {1, 6, 11, 16, 21, 26}     # de 5 em 5 dias, no início do dia
    clip = [k for k in res.picks if k.sku == "CLIP"]
    assert [k.unidades for k in clip] == [6, 6, 6, 6, 6]          # as 6 que esvaziaram na semana anterior
    assert sum(k.unidades for k in res.picks if k.sku == "PARAFUSO") == 2   # INT(25 × 0,1)
    assert any("mais de 1 unidade por semana" in a for a in res.avisos)   # o CLIP gasta 6 por semana
    t = P.taxas_por_sku(p, 1, cs, consumiveis_modo=P.MODO_SEMANAL, periodo_dias=5)
    assert t["CLIP"].ciclos == (P.SEMANAL,) and t["CLIP"].visitas_dia == pytest.approx(0.2)
    assert t["PARAFUSO"].visitas_dia == pytest.approx(0.1)
    with pytest.raises(ValueError):
        P.simular(p, 1, 5, cs, consumiveis_modo="mensal")


def test_mapa_skus_pelo_codigo_sem_roubar_um_sku_ja_ligado():
    skus = ["BEN10.CDF005_____LATCH", "SKT10.BGB003_____CMS,FR", "SKT10.BGB003_____CMS,FR RR", "BOL0001"]
    refs = ["BEN10.CDF005", "SKT10.BGB003_____CMS,FR RR", "SKT10.BGB003", "XPTO"]
    m = P.mapa_skus(refs, skus)
    assert m == {"BEN10.CDF005": "BEN10.CDF005_____LATCH",
                 "SKT10.BGB003_____CMS,FR RR": "SKT10.BGB003_____CMS,FR RR",
                 "SKT10.BGB003": "SKT10.BGB003_____CMS,FR"}


def test_resumo_por_sku_da_visitas_por_dia():
    x = _linha(7, "A", emb_dia=1.0)
    res = P.simular(_pressupostos([x], {("A", "C1"): [(300.0, 1.0)]}, bulk=P.BULK_TUDO), 1, 10)
    (r,) = P.resumo_por_sku(res)
    assert r["sku"] == "A" and r["visitas_dia"] == pytest.approx(1.0) and r["pecas_dia"] == pytest.approx(10.0)


def test_taxas_em_regime_com_visitas_limitadas_as_voltas():
    caixa = _linha(7, "A", emb_dia=0.5)                    # 0,5 emb./dia, voltas de caixas
    muita = _linha(8, "B", emb_dia=3.0, volta=P.PALETES)  # 3/dia -> bulk: 1 visita por dia
    carr1 = _linha(9, "C1", unidade=P.CARRINHO, volta=P.PALETES, emb_dia=0.25, carrinho="7")
    carr2 = _linha(10, "C2", unidade=P.CARRINHO, volta=P.PALETES, emb_dia=0.0, carrinho="7")
    p = _pressupostos([caixa, muita, carr1, carr2], bulk=P.BULK_SO_PALETES)
    t = P.taxas_por_sku(p, 1)
    assert t["A"].unidades_dia == pytest.approx(0.5) and t["A"].ciclos == (P.CAIXAS,)
    assert t["B"].unidades_dia == pytest.approx(3.0) and t["B"].visitas_dia == pytest.approx(1.0)
    assert t["B"].ciclos == (P.BULK,)
    # cada carrinho cheio = uma visita a cada referência dele
    assert t["C1"].visitas_dia == pytest.approx(0.25) and t["C2"].visitas_dia == pytest.approx(0.25)
    assert P.taxas_por_sku(p, 2)["A"].unidades_dia == pytest.approx(1.0)


# ----------------------------------------------------------------- livro real
LIVRO = (Path(__file__).resolve().parents[1] / "../../../Linha de Montagem/Assembly Ceiia - Hangar + BDL/"
         "dimensionamento_linha/dados/comboio bulk + armazem 23-10 D2 auto.xlsx").resolve()


@pytest.mark.skipif(not LIVRO.exists(), reason="livro do comboio não acessível nesta máquina")
def test_bate_com_o_livro_do_comboio_a_1_carro_por_dia():
    p = P.carregar_pressupostos(LIVRO)
    res = P.simular(p, int(p.carros_por_dia_base))
    falhas = [v for v in P.validar_contra_excel(p, res) if not v.ok]
    assert not falhas, [f"{v.nome}: {v.detalhe}" for v in falhas]

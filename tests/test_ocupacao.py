"""Ocupação do espaço de armazenagem (racking.resumo_ocupacao /
capacidade_por_posicao) -- o que alimenta o bloco «Ocupação do armazém» do
painel «Informações»."""
from warehouse import racking
from warehouse.config import Config
from warehouse.models import (RackType, LoadType, PosicaoChao, BlocoRack,
                              NivelRack, CaixaColocada)


def _rack():
    # 1 nível de 2.0 x 1.0 m = 2.0 m2; altura de nível 1.5 m -> 3.0 m3
    return RackType("A", larg_m=2.0, prof_m=1.0, alt_nivel_m=1.5, niveis=2)


def _caixa(comp, larg, alt, stack=1):
    return CaixaColocada(sku="X", descricao="x", load_type=LoadType.M,
                         x_m=0.0, y_m=0.0, comp_m=comp, larg_m=larg, alt_m=alt,
                         pecas_nesta_caixa=1, num_boxes_in_stack=stack)


def _posicao(rack, caixas_por_nivel):
    """caixas_por_nivel: lista (um item por nível) de listas de CaixaColocada."""
    pos = PosicaoChao(rack_nome=rack.nome, indice=0)
    bloco = BlocoRack(bloco_idx=0)
    for i, caixas in enumerate(caixas_por_nivel):
        bloco.niveis.append(NivelRack(nivel_idx=i, eh_picking=False, caixas=list(caixas)))
    pos.blocos.append(bloco)
    return pos


def test_ocupacao_conta_niveis_area_e_volume():
    r = _rack()
    # nível 0: uma caixa de 1.0 x 1.0 x 0.75 -> 1.0 m2 de 2.0, 0.75 m3 de 3.0
    # nível 1: vazio
    pos = _posicao(r, [[_caixa(1.0, 1.0, 0.75)], []])
    t = racking.resumo_ocupacao([pos], {"A": r})["total"]
    assert t["posicoes"] == 1 and t["posicoes_ocupadas"] == 1
    assert t["niveis"] == 2 and t["niveis_ocupados"] == 1
    assert t["area_total_m2"] == 4.0 and t["area_usada_m2"] == 1.0
    assert t["volume_total_m3"] == 6.0 and t["volume_usado_m3"] == 0.75


def test_posicao_totalmente_vazia_nao_conta_como_ocupada():
    r = _rack()
    t = racking.resumo_ocupacao([_posicao(r, [[], []])], {"A": r})["total"]
    assert t["posicoes"] == 1 and t["posicoes_ocupadas"] == 0
    assert t["niveis"] == 2 and t["niveis_ocupados"] == 0
    assert t["area_usada_m2"] == 0.0
    # é isto que faz uma fila SANDBOX vazia baixar a percentagem: entra toda no
    # denominador e nada no numerador
    assert t["area_total_m2"] == 4.0


def test_volume_nao_multiplica_pelo_stack():
    """CaixaColocada.alt_m JÁ é a altura do stack inteiro (racking, stack_height).
    Multiplicar por num_boxes_in_stack contava a dobrar -- regressão real,
    apanhada a medir os dados da SIA."""
    r = _rack()
    pos = _posicao(r, [[_caixa(1.0, 1.0, 1.2, stack=4)], []])
    t = racking.resumo_ocupacao([pos], {"A": r})["total"]
    assert t["volume_usado_m3"] == 1.2      # não 4.8
    assert t["unidades_carga"] == 4         # o stack conta 4 unidades de carga


def test_tipologia_desconhecida_e_ignorada_sem_rebentar():
    r = _rack()
    orfa = _posicao(RackType("Z", larg_m=1.0, prof_m=1.0, alt_nivel_m=1.0, niveis=1), [[]])
    t = racking.resumo_ocupacao([_posicao(r, [[], []]), orfa], {"A": r})["total"]
    assert t["posicoes"] == 1


def test_capacidade_por_posicao_usa_a_config_nao_o_catalogo():
    r = _rack()   # catálogo: 2 níveis, 1.5 m cada
    base = racking.capacidade_por_posicao(r, Config())
    assert base["niveis_por_posicao"] == 2 and base["blocos_por_posicao"] == 1
    assert base["area_nivel_m2"] == 2.0 and base["volume_nivel_m3"] == 3.0

    # pé-direito de 9 m -> cabem 3 módulos de 3 m empilhados
    alto = racking.capacidade_por_posicao(r, Config(altura_armazem_m=9.0))
    assert alto["blocos_por_posicao"] == 3
    assert alto["niveis_por_posicao_total"] == 6

    # teto por tipologia trava o empilhamento mesmo com pé-direito de sobra
    travado = racking.capacidade_por_posicao(
        r, Config(altura_armazem_m=9.0, max_modulos_por_tipologia={"A": 1}))
    assert travado["blocos_por_posicao"] == 1
    assert travado["niveis_por_posicao_total"] == 2

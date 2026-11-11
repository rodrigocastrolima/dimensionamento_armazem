"""Testes mínimos: a aritmética dos passos implementados (área) e os imports."""
from warehouse import area, racking
from warehouse.models import (RackType, LoadType, PosicaoChao, BlocoRack, NivelRack,
                              Zona, WarehouseDimensioning, WarehouseLayout)


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


def test_niveis_efetivos_com_alturas_heterogeneas_conta_altura_acumulada():
    """Porta-paletes de 3 níveis com o último a 1.0 m (4.6 - 2 x 1.8): num
    pé-direito de 4.6 m cabem os 3, não floor(4.6 / 1.8) = 2."""
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.8, niveis=3,
                  alturas_niveis_m=(1.8, 1.8, 1.0))
    por_nivel = ("porta-paletes",)
    assert racking.niveis_efetivos(pp, 4.6, por_nivel) == 3
    assert racking.niveis_efetivos(pp, 4.5, por_nivel) == 2     # 3.º nível já não cabe
    assert racking.niveis_efetivos(pp, 1.0, por_nivel) == 1     # nunca menos de 1


def _pp_161():
    """Porta-paletes SIA (2026-10-27): passo 1.8 m, viga 0.09, folga 0.10 ->
    alturas úteis 1.61 / 1.61 / 0.81 (4.6 - 2 x 1.8 - 0.09 - 0.10)."""
    return RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.61, niveis=3,
                    aceita=[LoadType.PALLET, LoadType.MEIA], alturas_niveis_m=(1.61, 1.61, 0.81))


def _palete_161():
    from warehouse.models import BoxType
    return BoxType(nome=LoadType.PALLET, int_comp_m=1.2, int_larg_m=0.8, int_alt_m=1.466,
                   ext_comp_m=1.2, ext_larg_m=0.8, ext_alt_m=1.61, peso_max_kg=1000.0)


def test_niveis_efetivos_niveis_uteis_de_1_61():
    """1.61 + 1.61 + 0.81 = 4.03 <= 4.6 -> 3 níveis; floor(4.6 / 1.61) dava 2."""
    pp = _pp_161()
    assert racking.niveis_efetivos(pp, 4.6, ("porta-paletes",)) == 3
    assert racking.niveis_efetivos(pp, 4.0, ("porta-paletes",)) == 2


def test_cotas_fisicas_niveis_voltam_ao_passo():
    """O 3D desenha as vigas no passo físico (útil + folga + viga), não na
    altura útil acumulada (que as punha a 1.61 / 3.22 m)."""
    from warehouse.config import Config
    assert racking.cotas_fisicas_niveis(_pp_161(), 3, Config()) == ([0, 1.8, 3.6], 4.6)
    pl = RackType("Prateleira Leve", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7, niveis=6)
    assert racking.cotas_fisicas_niveis(pl, 6, Config()) == ([0, 0.7, 1.4, 2.1, 2.8, 3.5], 4.2)


def test_altura_real_unidade_conta_so_as_camadas_precisas():
    """Palete 1.2 x 0.8 (+0.10 por lado = 1.4 x 1.0), deck 1.61 - 1.466 = 0.144.
    Peça 0.5 x 0.5 x 0.2 de pé (z): 2 x 2 = 4 no plano x 0.8 = 3/plano, 7
    camadas no máximo (floor(1.466 / 0.2))."""
    from warehouse import boxing
    from warehouse.config import Config
    from warehouse.models import Article, Empilhamento
    base = dict(sku="P", descricao="", comp_m=0.5, larg_m=0.5, alt_m=0.2, peso_kg=0.0,
                stock_max_un=10, pecas_por_carro=1, altura_override="z",
                empilhamento=Empilhamento.EMPILHAVEL)
    cfg = Config()
    pal = _palete_161()
    art = Article(**base)
    assert boxing.altura_real_unidade(art, pal, 7, cfg) == 0.744     # ceil(7/3)=3 camadas
    assert boxing.altura_real_unidade(art, pal, 21, cfg) == 1.544    # cheia: 7 camadas
    assert boxing.altura_real_unidade(art, pal, 500, cfg) == 1.544   # nunca acima do máximo
    # aninhada na vertical: 0.2 x (1 + 2 x 0.3) = 0.32 de carga para 3 camadas
    aninha = Article(**{**base, "eixo_aninhagem": "z"})
    assert boxing.altura_real_unidade(aninha, pal, 7, cfg) == 0.464


def test_altura_real_unidade_escolhe_a_orientacao_com_mais_pecas():
    """Peça 0.4 x 0.4 x 0.2, orientação livre: de pé (0.4 na vertical) leva 36
    (12/plano x 3), deitada (0.2) leva 28 (4/plano x 7) -- fica a de 36 mesmo
    sendo cada camada mais alta: 10 peças = 1 camada de 0.4 m."""
    from warehouse import boxing
    from warehouse.config import Config
    from warehouse.models import Article, Empilhamento
    art = Article(sku="P", descricao="", comp_m=0.4, larg_m=0.4, alt_m=0.2, peso_kg=0.0,
                  stock_max_un=10, pecas_por_carro=1, empilhamento=Empilhamento.EMPILHAVEL)
    assert boxing.pieces_per_box(art, _palete_161(), 0.3, 0.8, 0.10) == 36
    assert boxing.altura_real_unidade(art, _palete_161(), 10, Config()) == 0.544


def _artigo_palete(sku, alt_m):
    """Uma peça por palete (1.3 x 0.9 de pé, não empilhável): altura real =
    0.144 + alt_m."""
    from warehouse.models import Article, Empilhamento
    return Article(sku=sku, descricao="", comp_m=1.3, larg_m=0.9, alt_m=alt_m, peso_kg=0.0,
                   stock_max_un=1, pecas_por_carro=1, altura_override="z",
                   empilhamento=Empilhamento.NAO_EMPILHAVEL)


def test_paletes_baixas_vao_para_o_3o_nivel_e_as_altas_primeiro():
    """3 paletes baixas (0.644 m) e 6 altas (1.544 m), com as baixas PRIMEIRO na
    lista. Cabem 3 paletes por nível. As altas ocupam os níveis 1 e 2 e as
    baixas o 3.º (0.81 m): 1 só módulo. Pela altura nominal (1.61) as baixas
    nunca entravam no 3.º nível; pela ordem dos SKUs roubavam o nível 1 às
    altas e abria-se um 2.º módulo."""
    from warehouse.config import Config
    from warehouse.models import BoxAssignment
    pp = _pp_161()
    arts = [_artigo_palete("BAIXA", 0.5), _artigo_palete("ALTA", 1.4)]
    asg = [BoxAssignment("BAIXA", LoadType.PALLET, 1, 3), BoxAssignment("ALTA", LoadType.PALLET, 1, 6)]
    pos, avisos = racking.allocate_to_racks(asg, [], [pp], {LoadType.PALLET: _palete_161()}, arts,
                                            0.0, 4.6, Config())
    assert avisos == []
    assert len(pos) == 1
    niveis = pos[0].blocos[0].niveis
    assert [sorted(c.sku for c in n.caixas) for n in niveis] == [["ALTA"] * 3, ["ALTA"] * 3, ["BAIXA"] * 3]
    assert {c.alt_m for c in niveis[2].caixas} == {0.644}


def test_stock_que_nao_cabe_em_nivel_nenhum_da_aviso():
    """Antes, um item sem nível compatível era descartado sem aviso."""
    from warehouse.config import Config
    from warehouse.models import BoxAssignment
    baixo = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.0, niveis=2,
                     aceita=[LoadType.PALLET])
    pos, avisos = racking.allocate_to_racks(
        [BoxAssignment("ALTA", LoadType.PALLET, 1, 2)], [], [baixo],
        {LoadType.PALLET: _palete_161()}, [_artigo_palete("ALTA", 1.4)], 0.0, 4.6, Config())
    assert pos == []
    assert len(avisos) == 1 and "ALTA (2)" in avisos[0] and "NAO" in avisos[0]


def test_oversize_nao_desconta_folga_em_niveis_uteis():
    """SIDE BODY (1.582 m de pé, ALTURA_OVERRIDE='x') num nível útil de 1.61 m:
    a folga de manuseio já está nos 1.61, não se desconta a OVERSIZE (0.05)
    outra vez. Num nível uniforme de 1.61 a folga continua a aplicar-se."""
    from warehouse.models import Article, Empilhamento
    peca = Article(sku="SB", descricao="", comp_m=1.5817, larg_m=0.2483, alt_m=1.2828,
                   peso_kg=0.0, stock_max_un=10, pecas_por_carro=1, altura_override="x",
                   empilhamento=Empilhamento.NAO_EMPILHAVEL)
    emp = RackType("Rack Porta-Paletes (emparelhada)", larg_m=2.7, prof_m=2.4, alt_nivel_m=1.61,
                   niveis=3, alturas_niveis_m=(1.61, 1.61, 0.81), profundidade_inclui_par=True)
    assert racking._oversize_plano_altura(emp, peca, 1000.0, 0.05)[:2] == (18, 1)
    uniforme = RackType("Wide", larg_m=2.7, prof_m=2.4, alt_nivel_m=1.61, niveis=3)
    assert racking._oversize_plano_altura(uniforme, peca, 1000.0, 0.05)[:2] == (0, 0)


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


def test_eixos_possiveis():
    """ALTURA_OVERRIDE vazio/não reconhecido -> livre (testa as 3 cotas);
    'x'/'y'/'z' força o eixo correspondente (0=Comprimento, 1=Largura,
    2=Altura), case-insensitive e tolerante a espaços."""
    from warehouse import boxing
    assert boxing.eixos_possiveis("") == range(3)
    assert boxing.eixos_possiveis(None) == range(3)      # em falta -> livre
    assert boxing.eixos_possiveis("Livre") == range(3)   # valor antigo/desconhecido -> livre
    assert boxing.eixos_possiveis("x") == (0,)
    assert boxing.eixos_possiveis("Y") == (1,)
    assert boxing.eixos_possiveis(" z ") == (2,)


def test_orientacao_livre_deita_peca():
    """Peça comprida (1.3 m) não assenta deitada na footprint da palete
    (1.2 x 0.8), mas de pé cabe na altura (1.5 m). Com ALTURA_OVERRIDE='z'
    (Z medido = 0.3 m como altura) não cabe -> 0. Vazio (livre) o packer põe
    a cota de 1.3 m na vertical e encaixa muitas peças no plano fino."""
    from warehouse import boxing
    from warehouse.models import Article, BoxType, Empilhamento
    pallet = BoxType(nome=LoadType.PALLET, int_comp_m=1.2, int_larg_m=0.8, int_alt_m=1.5,
                     ext_comp_m=1.2, ext_larg_m=0.8, ext_alt_m=1.644, peso_max_kg=1000.0)
    base = dict(descricao="", comp_m=1.3, larg_m=0.05, alt_m=0.3, peso_kg=0.0,
                stock_max_un=100, pecas_por_carro=1, empilhamento=Empilhamento.EMPILHAVEL)
    travado = Article(sku="T", altura_override="z", **base)
    livre = Article(sku="L", altura_override="", **base)

    assert boxing.pieces_per_box(travado, pallet) == 0        # 1.3 m não assenta
    assert boxing.pieces_per_box(livre, pallet) == 64         # de pé: 0.05x0.3 x64
    assert boxing.pieces_per_box(livre, pallet) > boxing.pieces_per_box(travado, pallet)


def test_aninhamento_so_no_eixo_designado_seja_ele_qual_for():
    """ANINHAVEL foi removido de Empilhamento -- aninhar é agora Article.
    eixo_aninhagem ('x'/'y'/'z'), independente do empilhamento rígido (ver
    CLAUDE.md "separar aninhável de empilhável"). Caixa 1x1x1 m, peça
    0.3x0.3x0.3 m, fator 0.5, eixo_aninhagem='x' (Comprimento_m):
      eixo de aninhagem: floor((1/0.3-1)/0.5)+1 = 5
      os outros 2 (rígidos, mesmo que um deles seja o vertical): floor(1/0.3) = 3
      total = 5x3x3 = 45, SEMPRE -- não importa qual dos 3 acaba vertical
      (`ALTURA_OVERRIDE` vazio testa as 3 orientações), porque o eixo de
      aninhagem é uma propriedade fixa da peça, não da orientação escolhida
      (pedido explícito: "isto não vai significar que esse eixo tenha de ir
      para cima... simplesmente limita que direção podemos aninhar").
    Sem eixo de aninhagem nenhum (tudo rígido): 3x3x3 = 27.
    Com o código antigo (ANINHAVEL aninhava nos 3 eixos ao mesmo tempo) dava
    5x5x5 = 125 -- este teste falharia com esse comportamento."""
    from warehouse import boxing
    from warehouse.models import Article, BoxType, Empilhamento
    caixa = BoxType(nome=LoadType.M, int_comp_m=1.0, int_larg_m=1.0, int_alt_m=1.0,
                    ext_comp_m=1.0, ext_larg_m=1.0, ext_alt_m=1.0, peso_max_kg=1000.0)
    base = dict(sku="x", descricao="", comp_m=0.3, larg_m=0.3, alt_m=0.3, peso_kg=0.0,
                stock_max_un=1, pecas_por_carro=1, altura_override="",
                empilhamento=Empilhamento.EMPILHAVEL)
    aninha_x = Article(eixo_aninhagem="x", **base)
    sem_aninhar = Article(eixo_aninhagem="", **base)

    assert boxing.pieces_per_box(aninha_x, caixa, fator_empilhamento=0.5) == 45
    assert boxing.pieces_per_box(sem_aninhar, caixa, fator_empilhamento=0.5) == 27
    assert boxing.pieces_per_box(aninha_x, caixa, fator_empilhamento=0.5) > \
           boxing.pieces_per_box(sem_aninhar, caixa, fator_empilhamento=0.5)


def test_nao_empilhavel_ainda_aninha_se_tombada():
    """O caso que justifica separar Empilhamento de eixo_aninhagem: uma peça
    NAO_EMPILHAVEL com eixo_aninhagem='z' (a Altura_m é a cota que encaixa).
    Caixa 1x1x1 m, peça 0.2x0.2x0.3 m, fator 0.5.

    De pé (ALTURA_OVERRIDE='z', o eixo de aninhagem fica vertical): aninha
    normalmente -- NAO_EMPILHAVEL não trava nada, porque só se aplica a eixos
    QUE NÃO SÃO o de aninhagem. floor(1/0.2)^2 x [floor((1/0.3-1)/0.5)+1=5] =
    25 x 5 = 125.

    "Tombada" (ALTURA_OVERRIDE='x', outra cota fica vertical): agora sim o
    NAO_EMPILHAVEL trava o eixo vertical a 1 camada -- mas a Altura_m, deitada
    no plano, continua a ser o eixo de aninhagem e aninha aí: floor(1/0.2) x
    [floor((1/0.3-1)/0.5)+1=5] x 1 camada = 5 x 5 x 1 = 25. Mais do que o
    puro rígido sem aninhar nenhuma (floor(1/0.2) x floor(1/0.3) x 1 = 15),
    porque a peça continua fisicamente a encaixar na Altura_m, esteja ela
    onde estiver."""
    from warehouse import boxing
    from warehouse.models import Article, BoxType, Empilhamento
    caixa = BoxType(nome=LoadType.M, int_comp_m=1.0, int_larg_m=1.0, int_alt_m=1.0,
                    ext_comp_m=1.0, ext_larg_m=1.0, ext_alt_m=1.0, peso_max_kg=1000.0)
    base = dict(sku="x", descricao="", comp_m=0.2, larg_m=0.2, alt_m=0.3, peso_kg=0.0,
                stock_max_un=1, pecas_por_carro=1, empilhamento=Empilhamento.NAO_EMPILHAVEL,
                eixo_aninhagem="z")
    de_pe = Article(altura_override="z", **base)
    tombada = Article(altura_override="x", **base)

    assert boxing.pieces_per_box(de_pe, caixa, fator_empilhamento=0.5) == 125
    assert boxing.pieces_per_box(tombada, caixa, fator_empilhamento=0.5) == 25


def test_pecas_no_eixo_nunca_pior_que_rigido():
    """pecas_no_eixo nunca dá MENOS peças do que o floor() rígido, mesmo com
    um fator_empilhamento mal configurado (>= 1, que tornaria a fórmula de
    aninhagem pior) -- o max() interno protege sempre o resultado."""
    from warehouse.boxing import pecas_no_eixo
    assert pecas_no_eixo(1.0, 0.3, aninhavel=True, fator_empilhamento=1.5) == \
           pecas_no_eixo(1.0, 0.3, aninhavel=False, fator_empilhamento=1.5)
    assert pecas_no_eixo(1.0, 0.3, aninhavel=False, fator_empilhamento=0.5) == 3  # rígido, ignora fator
    assert pecas_no_eixo(1.0, 0.3, aninhavel=True, fator_empilhamento=0.5) == 5   # aninhado, mais favorável


def test_meias_paletes_ficam_numa_grelha_alinhada_de_6_por_nivel():
    """Porta-paletes 2.7 x 1.2: 6 meias-paletes (0.8 x 0.6) por nível, TODAS com
    a mesma orientação (3 x 2). O packer de rotação livre dava 5 por nível
    misturando 0.6 x 0.8 e 0.8 x 0.6, com frentes desalinhadas."""
    from warehouse.racking import _ItemParaColocar, _abrir_bloco, _tentar_colocar_em_posicao
    r = RackType("PP", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.8, niveis=1)
    pos = PosicaoChao(rack_nome="PP", indice=0)
    pos.blocos.append(_abrir_bloco(r, 0, altura_picking_m=0.0))
    for i in range(6):
        item = _ItemParaColocar(f"M{i}", "", LoadType.MEIA, 0.8, 0.6, 1.644, 1, False)
        assert _tentar_colocar_em_posicao(pos, r, item, 0.0, max_blocos=1,
                                          preferir_picking=False, permitir_fallback=False), i
    cx = pos.blocos[0].niveis[0].caixas
    assert len(cx) == 6
    assert {(round(c.comp_m, 3), round(c.larg_m, 3)) for c in cx} == {(0.8, 0.6)}
    assert {round(c.x_m, 3) for c in cx} == {0.0, 0.8, 1.6}      # 3 colunas
    assert {round(c.y_m, 3) for c in cx} == {0.0, 0.6}           # 2 filas
    # a 7.ª não cabe
    item = _ItemParaColocar("M6", "", LoadType.MEIA, 0.8, 0.6, 1.644, 1, False)
    assert not _tentar_colocar_em_posicao(pos, r, item, 0.0, max_blocos=1,
                                          preferir_picking=False, permitir_fallback=False)


def test_caixa_rodada_fica_gravada_na_orientacao_colocada():
    """Regressão do 3D: uma caixa que só cabe RODADA (a cota maior não cabe na
    profundidade da rack) tem de ficar gravada já na footprint colocada
    (comp_m/larg_m = retângulo do packer). Antes gravava-se a orientação de
    catálogo, e o viewer desenhava-a a sair da rack / a sobrepor-se às vizinhas."""
    from warehouse.racking import _ItemParaColocar, _abrir_bloco, _tentar_colocar_em_posicao
    r = RackType("R", larg_m=1.0, prof_m=0.3, alt_nivel_m=0.5, niveis=1)
    pos = PosicaoChao(rack_nome="R", indice=0)
    pos.blocos.append(_abrir_bloco(r, 0, altura_picking_m=0.0))
    # 0.28 x 0.9: 0.9 > prof(0.3) -> só cabe com o 0.9 ao longo da larg (1.0)
    item = _ItemParaColocar("A", "", LoadType.S, 0.28, 0.9, 0.4, 1, False)
    assert _tentar_colocar_em_posicao(pos, r, item, 0.0, max_blocos=1,
                                      preferir_picking=False, permitir_fallback=False)
    c = pos.blocos[0].niveis[0].caixas[0]
    # footprint colocada cabe na rack (larg x prof), na orientação do packer
    assert c.x_m + c.comp_m <= r.larg_m + 1e-6
    assert c.y_m + c.larg_m <= r.prof_m + 1e-6
    assert abs(c.comp_m - 0.9) < 1e-6 and abs(c.larg_m - 0.28) < 1e-6


def _catalogo_minimo():
    from warehouse.models import BoxType
    return [
        BoxType(nome=LoadType.S, int_comp_m=0.40, int_larg_m=0.30, int_alt_m=0.24,
                ext_comp_m=0.42, ext_larg_m=0.32, ext_alt_m=0.25, peso_max_kg=15),
        BoxType(nome=LoadType.PALLET, int_comp_m=1.20, int_larg_m=0.80, int_alt_m=1.50,
                ext_comp_m=1.20, ext_larg_m=0.80, ext_alt_m=1.64, peso_max_kg=1000),
    ]


def test_oversize_nao_e_inventado_como_uma_palete():
    """Regressão: uma peça que NÃO assenta na footprint da palete em nenhuma
    orientação não pode sair como (PALLET, 1). Antes o fallback devolvia
    sempre 1/palete, inventando capacidade inexistente e subdimensionando a
    área sem qualquer aviso."""
    from warehouse import boxing
    from warehouse.models import Article, Empilhamento
    from warehouse.config import Config
    cfg = Config()
    cat = _catalogo_minimo()
    # 1.75 x 1.37: as duas menores cotas (0.17, 1.37) NAO cabem em 1.20 x 0.80
    grande = Article(sku="OVS", descricao="", comp_m=1.75, larg_m=1.37, alt_m=0.17,
                     peso_kg=200.0, stock_max_un=4, pecas_por_carro=1,
                     altura_override="z", empilhamento=Empilhamento.NAO_EMPILHAVEL)
    lt, ppu = boxing.choose_load(grande, cat, cfg)
    assert lt == LoadType.OVERSIZE, f"esperado OVERSIZE, veio {lt}"


def test_altura_override_forcado_pode_ficar_oversize_mas_livre_cabe():
    """A mesma peça (1.05 x 1.31 x 0.75) com ALTURA_OVERRIDE='z' (força
    Altura_m=0.75 como vertical) NÃO cabe na palete (1.20 x 0.80) nem com a
    tolerância lateral de 0.10 m/lado (efetivo 1.40 x 1.00: nenhuma das duas
    cotas do plano, 1.05 e 1.31, apoia nas duas ao mesmo tempo) -> OVERSIZE,
    sem exceção (a antiga _assenta_na_palete ignorava a restrição de
    orientação e "encontrava" outra orientação por trás das costas do
    utilizador -- removida de propósito, ver CLAUDE.md). Com ALTURA_OVERRIDE
    vazio (livre), o packer testa as 3 cotas e encontra uma orientação (Largura_m
    ou Comprimento_m na vertical, ambas cabem) -> PALLET, 1."""
    from warehouse import boxing
    from warehouse.models import Article, Empilhamento
    from warehouse.config import Config
    cfg = Config()
    cat = _catalogo_minimo()
    base = dict(sku="OK", descricao="", comp_m=1.05, larg_m=1.31, alt_m=0.75,
                peso_kg=7.85, stock_max_un=1, pecas_por_carro=1,
                empilhamento=Empilhamento.NAO_EMPILHAVEL)
    travado = Article(altura_override="z", **base)
    livre = Article(altura_override="", **base)

    lt, _ = boxing.choose_load(travado, cat, cfg)
    assert lt == LoadType.OVERSIZE, f"esperado OVERSIZE, veio {lt}"

    lt, ppu = boxing.choose_load(livre, cat, cfg)
    assert lt == LoadType.PALLET and ppu == 1


def test_tolerancia_lateral_palete():
    """Config.pallet_tolerancia_lateral_m (0.10 m/lado) só entra em PALLET/
    MEIA, somada aos DOIS lados de CADA cota do plano. Peça 1.35 x 0.50 x 0.20
    (ALTURA_OVERRIDE='z'): sem tolerância não cabe (1.35 > 1.20 do comp da
    palete); com 0.10 m/lado (efetivo 1.40 x 1.00) cabe. Caixa S (sem
    tolerância, nunca aplicada fora de PALLET/MEIA) continua a 0."""
    from warehouse import boxing
    from warehouse.models import Article, Empilhamento
    cat = {b.nome: b for b in _catalogo_minimo()}
    pallet = cat[LoadType.PALLET]
    peca = Article(sku="x", descricao="", comp_m=1.35, larg_m=0.50, alt_m=0.20,
                   peso_kg=0.0, stock_max_un=1, pecas_por_carro=1,
                   altura_override="z", empilhamento=Empilhamento.EMPILHAVEL)

    assert boxing.pieces_per_box(peca, pallet, tolerancia_palete_m=0.0) == 0
    assert boxing.pieces_per_box(peca, pallet, tolerancia_palete_m=0.10) > 0

    s = cat[LoadType.S]  # int_comp_m=0.40 -- pequena demais em qualquer caso
    assert boxing.pieces_per_box(peca, s, tolerancia_palete_m=0.10) == 0

    # De ponta a ponta via choose_load (usa Config.pallet_tolerancia_lateral_m
    # por omissão, 0.10): a mesma peça sai como PALLET, não OVERSIZE.
    from warehouse.config import Config
    lt, ppu = boxing.choose_load(peca, list(cat.values()), Config())
    assert lt == LoadType.PALLET and ppu > 0


# ------------------------------------------- pallet_ultimo_recurso (Config)
# Decisão de negócio por cliente: trocar chão por movimentabilidade -- caixas
# pequenas em vez de paletes sempre que fisicamente possível, mesmo custando
# mais unidades de carga.

def test_pallet_ultimo_recurso_prefere_caixa_pequena_quando_cabe():
    from warehouse import boxing
    from warehouse.models import Article, Empilhamento
    from warehouse.config import Config
    cat = _catalogo_minimo()
    # peça pequena o suficiente para caber na S (0.40x0.30x0.24 util) e,
    # claro, também na palete (1.20x0.80x1.50) -- com stock alto a palete
    # minimiza sempre o nº de unidades (é maior), por isso o default
    # (pallet_ultimo_recurso=False) escolhe PALLET.
    art = Article(sku="x", descricao="", comp_m=0.10, larg_m=0.10, alt_m=0.10,
                  peso_kg=0.5, stock_max_un=500, pecas_por_carro=1,
                  empilhamento=Empilhamento.EMPILHAVEL)

    lt_default, _ = boxing.choose_load(art, cat, Config())
    assert lt_default == LoadType.PALLET

    lt_recurso, _ = boxing.choose_load(art, cat, Config(pallet_ultimo_recurso=True))
    assert lt_recurso == LoadType.S      # mesma peça, PALLET excluída da competição


def test_pallet_ultimo_recurso_ainda_cai_em_pallet_se_nada_mais_couber():
    """A palete continua a ser o fallback -- pallet_ultimo_recurso nunca perde
    stock: só evita a palete QUANDO há alternativa menor que serve."""
    from warehouse import boxing
    from warehouse.models import Article, Empilhamento
    from warehouse.config import Config
    cat = _catalogo_minimo()
    # 1.05 x 0.90 x 0.75 -- maior que a S em todas as orientações, mas assenta
    # na palete (1.20 x 0.80 + tolerância lateral de 0.10 m por lado).
    art = Article(sku="OK", descricao="", comp_m=1.05, larg_m=0.90, alt_m=0.75,
                  peso_kg=7.85, stock_max_un=50, pecas_por_carro=1,
                  altura_override="z", empilhamento=Empilhamento.NAO_EMPILHAVEL)
    lt, ppu = boxing.choose_load(art, cat, Config(pallet_ultimo_recurso=True))
    assert lt == LoadType.PALLET and ppu == 1


def test_pallet_ultimo_recurso_off_por_omissao_nao_muda_nada():
    """Config() nu (sem cliente) continua com o comportamento de sempre --
    ver também os testes de choose_load/best-fit já existentes."""
    from warehouse.config import Config
    assert Config().pallet_ultimo_recurso is False


# ----------------------------------------------- unidade_da_linha (Config)
# Decisão SIA (2026-10-14): o armazém herda a unidade que a linha escolhe
# para 1 carro; só muda o nº de unidades. SKUs em carrinho na linha não herdam.

def test_unidade_da_linha_herda_a_caixa_escolhida_para_1_carro():
    from warehouse import boxing
    from warehouse.models import Article, Empilhamento
    from warehouse.config import Config
    cat = _catalogo_minimo()
    # 5 peças/carro: para 1 carro cabe numa S (empate com a PALLET -> a menor);
    # para os 10 carros do armazém (50 peças) a PALLET minimiza unidades.
    art = Article(sku="x", descricao="", comp_m=0.10, larg_m=0.10, alt_m=0.10,
                  peso_kg=0.5, stock_max_un=50, pecas_por_carro=5,
                  empilhamento=Empilhamento.EMPILHAVEL)

    assert boxing.choose_load(art, cat, Config())[0] == LoadType.PALLET
    lt, ppu = boxing.choose_load(art, cat, Config(unidade_da_linha=True))
    assert lt == LoadType.S
    assert ppu == boxing.choose_load(art, cat, Config(unidade_da_linha=True))[1]

    # em carrinho na linha -> não herda, segue o best-fit do armazém
    cfg_carrinho = Config(unidade_da_linha=True, skus_carrinho_linha=frozenset({"x"}))
    assert boxing.choose_load(art, cat, cfg_carrinho)[0] == LoadType.PALLET


# --------------------------------------------------------- envelope do bloco
# Config.largura_max_m / profundidade_max_m: restrição de FORMA, não de
# capacidade -- nunca se perdem posições, avisa-se quando não cabe.

def _posicoes_para_layout(nome: str, n: int) -> list:
    return [PosicaoChao(rack_nome=nome, indice=i,
                        blocos=[BlocoRack(bloco_idx=0,
                                          niveis=[NivelRack(nivel_idx=0, eh_picking=False)])])
            for i in range(n)]


def test_largura_max_e_teto_reduz_e_cresce_em_profundidade():
    from warehouse import layout as layout_mod
    r = RackType("A", larg_m=2.0, prof_m=1.0, alt_nivel_m=1.5, niveis=4, corredor_m=2.5)
    pos = _posicoes_para_layout("A", 24)
    livre = layout_mod.gerar_layout(pos, {"A": r})
    # 10 m de largura => 5 posições por fiada, nunca mais
    limitado = layout_mod.gerar_layout(pos, {"A": r}, largura_max_m=10.0)
    assert limitado.largura_total_m <= 10.0 + 1e-6
    assert limitado.profundidade_total_m > livre.profundidade_total_m
    # nenhuma posição se perde ao apertar a forma
    assert len(limitado.posicoes) == len(pos) == len(livre.posicoes)
    assert not limitado.avisos


def test_pilhas_chao_recebem_coordenadas_sem_sobrepor():
    """As pilhas têm de receber (x,y) para o 3D as desenhar -- a área sozinha era
    invisível na cena. Ficam a seguir ao bloco de racks e não se sobrepõem."""
    from warehouse import layout as layout_mod
    r = RackType("A", larg_m=2.0, prof_m=1.0, alt_nivel_m=1.5, niveis=4, corredor_m=2.5)
    pos = _posicoes_para_layout("A", 12)
    artigos = [("P", 2.26, 1.37, 1.38, 84.4, 4, "z"),      # não empilha (1.38 alto)
               ("Q", 1.75, 1.37, 0.17, 100.0, 4, "z")]     # empilha 4
    wl = layout_mod.gerar_layout(pos, {"A": r}, None, 0.0, 0.0,
                                 artigos, 1.8, 1000.0)
    assert len(wl.pilhas_chao) == 4 + 1      # 4 pilhas de 1 + 1 pilha de 4
    # AGRUPADAS no bloco: ficam a seguir à última fiada de racks mas DENTRO do
    # envolvente, como qualquer outra tipologia
    ultimo_rack_y = max(p.y_m + p.profundidade_m for p in wl.posicoes)
    assert min(p.y_m for p in wl.pilhas_chao) >= ultimo_rack_y
    assert max(p.y_m + p.larg_m for p in wl.pilhas_chao) <= wl.profundidade_total_m + 1e-6
    # e a largura do bloco acomoda-as
    assert max(p.x_m + p.comp_m for p in wl.pilhas_chao) <= wl.largura_total_m + 1e-6
    # não se sobrepõem entre si
    for i, a in enumerate(wl.pilhas_chao):
        for b in wl.pilhas_chao[i + 1:]:
            separadas = (a.x_m + a.comp_m <= b.x_m + 1e-6 or b.x_m + b.comp_m <= a.x_m + 1e-6
                         or a.y_m + a.larg_m <= b.y_m + 1e-6 or b.y_m + b.larg_m <= a.y_m + 1e-6)
            assert separadas, f"{a.sku} sobrepõe {b.sku}"
    # a altura da pilha reflete as peças empilhadas
    q = next(p for p in wl.pilhas_chao if p.sku == "Q")
    assert q.n_pecas == 4 and abs(q.altura_total_m - 0.68) < 1e-6


def test_pilhas_chao_tem_corredor_a_cada_2_fiadas():
    """Mesma regra dos racks: fiadas em pares costas-com-costas e um CORREDOR a
    seguir a cada par. Sem isto o bloco de pilhas ficava maciço (só 0.30 m de
    folga entre fiadas) e não havia como ir buscar as peças do meio."""
    from warehouse import layout as layout_mod
    r = RackType("A", larg_m=2.0, prof_m=1.0, alt_nivel_m=1.5, niveis=4, corredor_m=2.5)
    pos = _posicoes_para_layout("A", 4)
    # 30 peças de 1.0 x 1.0 x 1.8 (não empilham) -> muitas fiadas
    artigos = [("P", 1.0, 1.0, 1.8, 10.0, 30, "z")]
    wl = layout_mod.gerar_layout(pos, {"A": r}, None, 6.0, 0.0, artigos, 1.8, 1000.0)
    ys = sorted({p.y_m for p in wl.pilhas_chao})
    assert len(ys) >= 4, "precisa de várias fiadas para o teste ser útil"

    y0 = min(ys)
    cors_pilhas = [c for c in wl.corredores if c.y_m >= y0 - 1e-6]
    assert cors_pilhas, "nenhum corredor emitido na zona das pilhas"

    # gaps entre fiadas consecutivas: alternam folga-de-manuseio / corredor
    gaps = []
    for i in range(len(ys) - 1):
        prof = max(p.larg_m for p in wl.pilhas_chao if abs(p.y_m - ys[i]) < 1e-6)
        gaps.append(round(ys[i + 1] - (ys[i] + prof), 2))
    # o 2.º gap (a seguir ao 1.º par) tem de ser um corredor a sério
    assert gaps[1] >= 2.0, f"esperava corredor entre pares, gaps={gaps}"
    # e nunca há 3 fiadas seguidas sem corredor
    for i in range(len(gaps) - 1):
        assert max(gaps[i], gaps[i + 1]) >= 2.0, f"3 fiadas sem corredor: {gaps}"


def test_sem_oversize_chao_nao_gera_pilhas():
    from warehouse import layout as layout_mod
    r = RackType("A", larg_m=2.0, prof_m=1.0, alt_nivel_m=1.5, niveis=4, corredor_m=2.5)
    pos = _posicoes_para_layout("A", 6)
    assert layout_mod.gerar_layout(pos, {"A": r}).pilhas_chao == []
    assert layout_mod.gerar_layout(pos, {"A": r}, None, 0.0, 0.0, []).pilhas_chao == []


def test_oversize_ao_chao_empilha_e_soma_acesso():
    """Peças volumosas em pilha no chão (block stacking), sem rack. A peça
    assenta deitada e empilham-se peças do mesmo SKU; o menor de (altura, peso)
    manda. O acesso entra na área porque não há corredores medidos pelo layout."""
    # 4 peças de 1.75x1.37x0.17, 215.8 kg: por altura 1.8/0.17 = 10, por peso
    # 1000/215.8 = 4 -> o peso manda
    artigos = [("A", 1.75, 1.37, 0.17, 215.8, 4, "z")]
    a, det = area.area_oversize_chao(artigos, 1.8, 1000.0, 0.0)
    assert det[0]["por_pilha"] == 4 and det[0]["limitado_por"] == "peso"
    assert det[0]["pilhas"] == 1
    assert abs(a - 1.75 * 1.37) < 0.01
    # o acesso soma-se por cima
    a45, _ = area.area_oversize_chao(artigos, 1.8, 1000.0, 0.45)
    assert abs(a45 - a * 1.45) < 0.02


def test_oversize_ao_chao_peca_alta_nao_empilha():
    """Peça de 1.38 m de altura (orientação travada: Z medido = altura) com
    pilha máx. de 1.8 m: só 1 por pilha, logo cada unidade ocupa o seu lugar
    no chão. Footprint = as outras duas cotas (comp x larg), não a "deitada"
    genérica -- a orientação está travada, o Z é sempre a vertical."""
    artigos = [("B", 2.26, 1.37, 1.38, 84.4, 7, "z")]
    a, det = area.area_oversize_chao(artigos, 1.8, 1000.0, 0.0)
    assert det[0]["por_pilha"] == 1 and det[0]["pilhas"] == 7
    assert abs(a - 7 * 2.26 * 1.37) < 0.05


def test_oversize_ao_chao_orientacao_livre_fica_de_pe():
    """SIDE BODY LH-like: 1.58 x 0.25 x 1.28, ALTURA_OVERRIDE vazio (livre).
    Deitada (footprint = as 2 maiores, 1.58x1.28 = 2.02 m2) desperdiça espaço
    -- a peça é fina (0.25 m) e cabe perfeitamente de pé, com a MAIOR cota
    (1.58) na vertical e a footprint reduzida a 0.25x1.28 = 0.32 m2. Como só há
    1 unidade (não empilha em nenhuma orientação), a escolha de orientação é
    pura minimização de footprint -- tem de ficar de pé."""
    artigos = [("SIDE", 1.58, 0.25, 1.28, 12.0, 1, "")]
    a, det = area.area_oversize_chao(artigos, 1.8, 1000.0, 0.0)
    assert abs(det[0]["footprint_peca_m2"] - 0.25 * 1.28) < 1e-6
    assert abs(a - 0.25 * 1.28) < 1e-6


def test_oversize_ao_chao_orientacao_travada_fica_deitada_mesmo_pior():
    """A mesma peça do teste anterior, mas com ALTURA_OVERRIDE='z' (força
    Altura_m como vertical): mantém-se deitada (footprint = comp x larg) mesmo
    sendo pior -- a peça não pode ser reorientada, ponto final."""
    artigos = [("SIDE", 1.58, 0.25, 1.28, 12.0, 1, "z")]
    a, det = area.area_oversize_chao(artigos, 1.8, 1000.0, 0.0)
    assert abs(det[0]["footprint_peca_m2"] - 1.58 * 0.25) < 1e-6
    assert abs(a - 1.58 * 0.25) < 0.01   # `a` arredonda a 2 casas decimais


def test_oversize_ao_chao_ignora_sem_stock_ou_sem_geometria():
    artigos = [("Z", 1.0, 1.0, 0.5, 10.0, 0, ""),      # sem stock
               ("Y", 0.0, 1.0, 0.5, 10.0, 4, "")]      # sem geometria
    a, det = area.area_oversize_chao(artigos, 1.8, 1000.0, 0.45)
    assert a == 0.0 and det == []


def test_area_ao_chao_nao_conta_duas_vezes_no_resumo():
    """As pilhas são uma fiada do bloco de racks: o footprint já vem em
    dim.area_racks_m2 (floor_area recebe-as) e o espaço em volta já está medido
    em wl.area_corredores_m2. Somar area_oversize_chao_m2 ao total contava duas
    vezes -- por isso o campo é INFORMATIVO."""
    dim = WarehouseDimensioning(area_racks_m2=100.0)
    wl = WarehouseLayout(area_corredores_m2=50.0)
    r = area.resumo_area_total(dim, wl, [], margem_estrutural_pct=0.0,
                              area_oversize_chao_m2=38.5)
    assert r.area_oversize_chao_m2 == 38.5          # reportado
    assert abs(r.area_bruta_total_m2 - 150.0) < 1e-9  # mas NÃO somado
    # nem entra na base da margem estrutural
    r2 = area.resumo_area_total(dim, wl, [], margem_estrutural_pct=0.1,
                               area_oversize_chao_m2=38.5)
    assert abs(r2.margem_estrutural_m2 - 15.0) < 1e-9


def test_floor_area_conta_footprint_das_pilhas():
    """O footprint das pilhas é área OCUPADA (como os racks), não corredor."""
    from warehouse.models import PilhaChao
    r = RackType("A", larg_m=2.0, prof_m=1.0, alt_nivel_m=1.5, niveis=4)
    pos = [PosicaoChao(rack_nome="A", indice=0,
                       blocos=[BlocoRack(bloco_idx=0,
                                         niveis=[NivelRack(nivel_idx=0, eh_picking=False)])])]
    pilhas = [PilhaChao(sku="P", descricao="", x_m=0, y_m=0, comp_m=2.0, larg_m=1.5,
                        alt_peca_m=0.2, n_pecas=3)]
    sem = area.floor_area(pos, {"A": r})
    com = area.floor_area(pos, {"A": r}, pilhas)
    assert abs(com.area_racks_m2 - (sem.area_racks_m2 + 3.0)) < 1e-6
    assert "Armazenagem ao chão" in com.detalhe
    assert com.detalhe["Armazenagem ao chão"]["posicoes_chao"] == 1


def test_pecas_por_carro_fracionario_pede_picking():
    """`pecas_por_carro` é FRACIONÁRIO (0.5 = 1 peça a cada 2 carros). Era lido
    com int() e truncava: 138 SKUs a 0.5 viravam 0, e como
    num_unidades_picking = ceil(0 / n) = 0, deixavam de pedir zona de picking
    SEM aviso (o aviso só dispara para quem pediu e não conseguiu)."""
    import math
    from warehouse.models import Article, Empilhamento
    for valor, esperado in [(0.5, 1), (0.03, 1), (1.0, 1), (1.5, 2), (2.0, 2)]:
        assert math.ceil(valor / 1) == esperado, valor
    # o modelo tem de guardar o float sem truncar
    art = Article(sku="F", descricao="", comp_m=0.4, larg_m=0.3, alt_m=0.2,
                  peso_kg=1.0, stock_max_un=10, pecas_por_carro=0.5,
                  empilhamento=Empilhamento.EMPILHAVEL)
    assert art.pecas_por_carro == 0.5                # não 0
    assert math.ceil(art.pecas_por_carro / 1) == 1   # pede >= 1 caixa em picking


def test_layout_prefere_compacidade_a_area_minima():
    """O critério é PERÍMETRO mínimo, não área mínima. A área do envolvente
    premiava blocos absurdamente alongados (numa base real: 5.49 x 48.20 m,
    razão 9:1, contra 13.50 x 23.80 m) -- e em parte por artefacto, porque menos
    filas paralelas = menos corredores contados. Filas curtas são percurso de
    picking a menos."""
    from warehouse import layout as layout_mod
    r = RackType("A", larg_m=2.0, prof_m=1.0, alt_nivel_m=1.5, niveis=4, corredor_m=2.5)
    pos = _posicoes_para_layout("A", 60)
    wl = layout_mod.gerar_layout(pos, {"A": r})
    lado_maior = max(wl.largura_total_m, wl.profundidade_total_m)
    lado_menor = min(wl.largura_total_m, wl.profundidade_total_m)
    assert lado_menor > 0
    # forma comedida: sem isto o ótimo por área saía muito mais alongado
    assert lado_maior / lado_menor < 3.0
    assert len(wl.posicoes) == len(pos)


def test_largura_max_acima_do_otimo_nao_muda_nada():
    """É um TETO, não um alvo: dar folga não pode piorar o resultado. A versão
    anterior enchia as filas até à largura dada e AUMENTAVA a área ao
    "respeitar" um limite que nunca era violado."""
    from warehouse import layout as layout_mod
    r = RackType("A", larg_m=2.0, prof_m=1.0, alt_nivel_m=1.5, niveis=4, corredor_m=2.5)
    pos = _posicoes_para_layout("A", 24)
    livre = layout_mod.gerar_layout(pos, {"A": r})
    folgado = layout_mod.gerar_layout(pos, {"A": r},
                                     largura_max_m=livre.largura_total_m * 2)
    assert folgado.largura_total_m == livre.largura_total_m
    assert folgado.profundidade_total_m == livre.profundidade_total_m
    assert folgado.area_total_m2 <= livre.area_total_m2 + 1e-6
    assert not folgado.avisos


def test_profundidade_max_avisa_sem_truncar():
    from warehouse import layout as layout_mod
    r = RackType("A", larg_m=2.0, prof_m=1.0, alt_nivel_m=1.5, niveis=4, corredor_m=2.5)
    pos = _posicoes_para_layout("A", 24)
    wl = layout_mod.gerar_layout(pos, {"A": r}, largura_max_m=10.0,
                                 profundidade_max_m=5.0)
    assert wl.profundidade_total_m > 5.0          # não trunca: o stock manda
    assert len(wl.posicoes) == len(pos)           # nada ficou de fora
    assert any("profundidade" in a for a in wl.avisos)


def test_largura_max_menor_que_a_posicao_avisa():
    from warehouse import layout as layout_mod
    r = RackType("A", larg_m=2.7, prof_m=1.1, alt_nivel_m=1.5, niveis=4)
    pos = _posicoes_para_layout("A", 4)
    wl = layout_mod.gerar_layout(pos, {"A": r}, largura_max_m=1.0)
    assert len(wl.posicoes) == 4                  # continua a arrumar tudo
    assert wl.largura_total_m > 1.0               # impossível respeitar
    assert any("menor que uma posicao" in a for a in wl.avisos)


def _sem_sobreposicoes(posicoes):
    def _retangulo(p):
        return (p.x_m, p.y_m, p.x_m + p.largura_m, p.y_m + p.profundidade_m)
    for i, a in enumerate(posicoes):
        ra = _retangulo(a)
        for b in posicoes[i + 1:]:
            rb = _retangulo(b)
            separadas = (ra[2] <= rb[0] + 1e-6 or rb[2] <= ra[0] + 1e-6
                        or ra[3] <= rb[1] + 1e-6 or rb[3] <= ra[1] + 1e-6)
            assert separadas, f"posições sobrepostas: {a} / {b}"


def test_tipologias_diferentes_partilham_a_mesma_banda_quando_compensa():
    """Decisão fechada: continua a mandar o PERÍMETRO mínimo do bloco (não se
    força mistura só porque dá para encaixar) -- mas agora a busca decompõe
    cada tipologia nas suas fiadas duplas (_fiadas_tipologia) e testa TODAS as
    somas de subconjuntos de larguras como largura-alvo candidata (não só
    somas cumulativas por profundidade), com um packer FFDH (_empacotar_por_
    shelf) que tenta cada fiada em QUALQUER prateleira já aberta, não só na
    última. Isto significa que quando misturar tipologias diferentes na MESMA
    faixa Y dá mesmo um perímetro melhor, o algoritmo encontra-o -- 13 Rack
    Porta-Paletes (sobra 1 na última fiada dupla) + 2 Prateleira Leve (cabem
    exatamente nessa sobra) é um desses casos."""
    from warehouse import layout as layout_mod
    pl = RackType("Prateleira Leve", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7,
                  niveis=3, corredor_m=1.2)
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.8,
                  niveis=4, corredor_m=3.0)
    pos = _posicoes_para_layout("Prateleira Leve", 2) + _posicoes_para_layout("Rack Porta-Paletes", 13)
    rack_types = {"Prateleira Leve": pl, "Rack Porta-Paletes": pp}

    wl = layout_mod.gerar_layout(pos, rack_types)
    assert len(wl.posicoes) == len(pos)
    _sem_sobreposicoes(wl.posicoes)

    # as duas tipologias partilham pelo menos uma faixa Y (prova de mistura)
    ys_pl = [(p.y_m, p.y_m + p.profundidade_m) for p in wl.posicoes
            if p.posicao.rack_nome == "Prateleira Leve"]
    ys_pp = [(p.y_m, p.y_m + p.profundidade_m) for p in wl.posicoes
            if p.posicao.rack_nome == "Rack Porta-Paletes"]
    partilhada = any(a[0] < b[1] - 1e-6 and b[0] < a[1] - 1e-6 for a in ys_pl for b in ys_pp)
    assert partilhada, "as duas tipologias nunca partilham a mesma faixa Y"


def test_picking_manual_nunca_fica_fragmentado():
    """Decisão fechada: tipologias de picking manual (Config.picking_manual_racks,
    default ("prateleira",)) podem partilhar prateleira com outras -- mas as
    SUAS PRÓPRIAS posições nunca podem ficar espalhadas por bandas do bloco
    não-adjacentes (só o operador a pé perde tempo a atravessar o armazém).

    Sem tratamento especial, o shelf packing normal (uma fiada de cada vez,
    _fiadas_tipologia) podia deixar a fiada "resto" da Prateleira Leve (mais
    rasa) presa a uma prateleira de Rack Porta-Paletes, enquanto as fiadas
    cheias ficavam noutra -- caso confirmado com 7 Prateleira Leve + 8
    Porta-Paletes + 10 Rack C: sem correção dava dois grupos em Y = (0.0,1.2)
    e (5.4,6.6), com Porta-Paletes/Rack C no meio."""
    from warehouse import layout as layout_mod
    pl = RackType("Prateleira Leve", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7,
                  niveis=3, corredor_m=1.2)
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.8,
                  niveis=4, corredor_m=3.0)
    rc = RackType("Rack C", larg_m=1.5, prof_m=0.9, alt_nivel_m=1.0,
                  niveis=3, corredor_m=2.0)
    pos = (_posicoes_para_layout("Prateleira Leve", 7)
          + _posicoes_para_layout("Rack Porta-Paletes", 8)
          + _posicoes_para_layout("Rack C", 10))
    rack_types = {"Prateleira Leve": pl, "Rack Porta-Paletes": pp, "Rack C": rc}

    wl = layout_mod.gerar_layout(pos, rack_types)
    assert len(wl.posicoes) == len(pos)
    _sem_sobreposicoes(wl.posicoes)

    # todas as posições de Prateleira Leve, ordenadas por Y, formam UM SÓ
    # cluster contíguo -- só se pode "saltar" pelo próprio corredor de
    # serviço dela (1.2m), nunca por uma fiada de outra tipologia no meio.
    ys_pl = sorted((p.y_m, p.y_m + p.profundidade_m) for p in wl.posicoes
                  if p.posicao.rack_nome == "Prateleira Leve")
    clusters = []
    tol = pl.corredor_m + 1e-3
    for s, e in ys_pl:
        if clusters and s <= clusters[-1][1] + tol:
            clusters[-1] = (clusters[-1][0], max(clusters[-1][1], e))
        else:
            clusters.append((s, e))
    assert len(clusters) == 1, f"Prateleira Leve fragmentada em {len(clusters)} clusters: {clusters}"

    # confirma que o cenário SEM a proteção fragmentava mesmo (não é um teste vazio)
    sem_protecao = layout_mod.gerar_layout(pos, rack_types, picking_manual_racks=())
    ys_sem = sorted((p.y_m, p.y_m + p.profundidade_m) for p in sem_protecao.posicoes
                    if p.posicao.rack_nome == "Prateleira Leve")
    clusters_sem = []
    for s, e in ys_sem:
        if clusters_sem and s <= clusters_sem[-1][1] + tol:
            clusters_sem[-1] = (clusters_sem[-1][0], max(clusters_sem[-1][1], e))
        else:
            clusters_sem.append((s, e))
    assert len(clusters_sem) > 1, "cenario de teste deixou de fragmentar sem a proteção -- rever"


def test_nao_forca_mistura_quando_piora_o_perimetro():
    """O perímetro mínimo continua a decidir: nunca aceita uma combinação que
    piore o bloco, mesmo quando `_ajustar_decomposicoes` testa formas mais
    fragmentadas de cada tipologia à procura de um encaixe melhor -- só troca
    se genuinamente reduzir o perímetro (nunca agrava; ver
    test_teto_com_multiplas_tipologias_nao_agrava_quando_nao_viola)."""
    from warehouse import layout as layout_mod
    pl = RackType("Prateleira Leve", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7,
                  niveis=3, corredor_m=1.2)
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.8,
                  niveis=4, corredor_m=3.0)
    pos = _posicoes_para_layout("Prateleira Leve", 12) + _posicoes_para_layout("Rack Porta-Paletes", 21)
    rack_types = {"Prateleira Leve": pl, "Rack Porta-Paletes": pp}

    wl = layout_mod.gerar_layout(pos, rack_types)
    assert len(wl.posicoes) == len(pos)
    _sem_sobreposicoes(wl.posicoes)
    # nunca pior que a forma "cada tipologia na sua fiada, sem ajuste fino"
    # (16.2 x 12.0, perímetro 56.4) nem que "tudo misturado numa só
    # prateleira" (21.69 x 7.8, perímetro 58.98) -- verificado manualmente.
    perimetro = 2 * (wl.largura_total_m + wl.profundidade_total_m)
    assert perimetro <= 56.4 + 1e-6


def test_teto_com_multiplas_tipologias_nao_agrava_quando_nao_viola():
    """Mesma doutrina de largura_max_m (TETO, não alvo) já validada para uma
    só tipologia (test_largura_max_acima_do_otimo_nao_muda_nada), agora com
    várias: um teto acima do ótimo livre não pode piorar o resultado."""
    from warehouse import layout as layout_mod
    pl = RackType("Prateleira Leve", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7,
                  niveis=3, corredor_m=1.2)
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.8,
                  niveis=4, corredor_m=3.0)
    pos = _posicoes_para_layout("Prateleira Leve", 12) + _posicoes_para_layout("Rack Porta-Paletes", 21)
    rack_types = {"Prateleira Leve": pl, "Rack Porta-Paletes": pp}

    livre = layout_mod.gerar_layout(pos, rack_types)
    folgado = layout_mod.gerar_layout(pos, rack_types, largura_max_m=livre.largura_total_m * 2)
    assert (folgado.largura_total_m, folgado.profundidade_total_m) == \
           (livre.largura_total_m, livre.profundidade_total_m)
    assert not folgado.avisos

    apertado = layout_mod.gerar_layout(pos, rack_types, largura_max_m=10.0)
    assert apertado.largura_total_m <= 10.0 + 1e-6
    assert len(apertado.posicoes) == len(pos)
    assert not apertado.avisos


def test_sem_envelope_mantem_comportamento():
    from warehouse import layout as layout_mod
    r = RackType("A", larg_m=2.0, prof_m=1.0, alt_nivel_m=1.5, niveis=4, corredor_m=2.5)
    pos = _posicoes_para_layout("A", 24)
    a = layout_mod.gerar_layout(pos, {"A": r})
    b = layout_mod.gerar_layout(pos, {"A": r}, largura_max_m=0.0, profundidade_max_m=0.0)
    assert (a.largura_total_m, a.profundidade_total_m) == (b.largura_total_m, b.profundidade_total_m)
    assert not b.avisos


# ------------------------------------------------- limite de módulos empilhados
# Config.max_modulos_por_tipologia: trava racks INTEIROS empilhados na mesma
# posição de chão (não RackType.niveis, que são as prateleiras dentro de um
# módulo). O stock que subiria espalha-se por mais posições ao lado.

def test_limite_modulos_por_substring_do_nome():
    pl = RackType("Prateleira Leve", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7, niveis=3)
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.1, alt_nivel_m=1.8, niveis=4)
    lim = {"prateleira": 2}
    # 10 m / 2.10 m de prumo = 4 módulos; travado a 2
    assert racking.blocos_empilhados(pl, 10.0) == 4
    assert racking.blocos_empilhados(pl, 10.0, lim) == 2
    # porta-paletes não é travado (não casa a substring)
    assert racking.blocos_empilhados(pp, 10.0, lim) == racking.blocos_empilhados(pp, 10.0)


def test_limite_modulos_nao_inventa_capacidade():
    """O limite só pode REDUZIR: se a altura já só permite 1 módulo, um teto de
    2 não passa a permitir 2."""
    pl = RackType("Prateleira Leve", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7, niveis=3)
    assert racking.blocos_empilhados(pl, 2.5, {"prateleira": 2}) == 1   # 2.5/2.1 = 1
    # sem altura definida continua a ser 1 módulo, com ou sem teto
    assert racking.blocos_empilhados(pl, 0.0, {"prateleira": 2}) == 1


def test_sem_limite_mantem_comportamento():
    pl = RackType("Prateleira Leve", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7, niveis=3)
    for lim in (None, {}):
        assert racking.blocos_empilhados(pl, 10.0, lim) == 4


def test_limite_modulos_case_insensitive():
    r = RackType("PRATELEIRA LEVE 1.8", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7, niveis=3)
    assert racking.blocos_empilhados(r, 10.0, {"Prateleira": 2}) == 2


# ------------------------- alturas de nível heterogéneas (flow-rack de bordo de linha)
# RackType.alturas_niveis_m: uma altura por nível (índice 0 = base) em vez de
# alt_nivel_m uniforme para todos os níveis -- flow-rack/estante dinâmica onde o
# nível que leva L/M precisa de 0.8 m mas os outros podem ser bem mais baixos.

def _flow_rack() -> RackType:
    """Flow-rack sintética: nível 0 alto (0.8 m, para L/M), níveis 1-2 baixos
    (0.3 m, para S/XS). Soma 1.4 m -- dentro do teto ergonómico de 1.8 m."""
    return RackType("Flow Rack", larg_m=1.22, prof_m=1.22, alt_nivel_m=0.8, niveis=3,
                    aceita=[LoadType.L, LoadType.XS],
                    alturas_niveis_m=(0.8, 0.3, 0.3))


def _box_l():
    from warehouse.models import BoxType
    return BoxType(nome=LoadType.L, int_comp_m=0.88, int_larg_m=0.53, int_alt_m=0.67,
                   ext_comp_m=0.90, ext_larg_m=0.55, ext_alt_m=0.68, peso_max_kg=30)


def _box_xs():
    from warehouse.models import BoxType
    return BoxType(nome=LoadType.XS, int_comp_m=0.28, int_larg_m=0.13, int_alt_m=0.11,
                   ext_comp_m=0.30, ext_larg_m=0.15, ext_alt_m=0.12, peso_max_kg=10)


def test_altura_nivel_e_altura_ate_niveis_helpers():
    r = _flow_rack()
    assert racking.altura_nivel(r, 0) == 0.8
    assert racking.altura_nivel(r, 1) == 0.3
    assert racking.altura_nivel(r, 2) == 0.3
    assert racking.altura_ate_niveis(r, 0) == 0.0
    assert racking.altura_ate_niveis(r, 1) == 0.8
    assert racking.altura_ate_niveis(r, 2) == 1.1
    assert racking.altura_ate_niveis(r, 3) == 1.4


def test_rack_capacity_respeita_alturas_por_nivel():
    """Uma caixa L (0.68 m) só conta o nível alto (0 de 3); uma XS (0.12 m)
    cabe nos 3 -- mesma footprint, capacidade bem diferente."""
    r = _flow_rack()
    cap_l = racking.rack_capacity(r, _box_l())
    cap_xs = racking.rack_capacity(r, _box_xs())
    # por_nivel(L) = floor(1.22/0.90)*floor(1.22/0.55) = 1*2 = 2; só 1 nível compatível
    assert cap_l == 2
    # por_nivel(XS) = floor(1.22/0.30)*floor(1.22/0.15) = 4*8 = 32; os 3 níveis compatíveis
    assert cap_xs == 32 * 3


def test_empacotar_nivel_recusa_caixa_alta_em_nivel_baixo():
    """O ponto crítico: _empacotar_nivel tem de recusar uma caixa mais alta do
    que o nível onde está a tentar entrar -- nível a nível, não um valor único
    para o rack inteiro."""
    r = _flow_rack()
    bloco = racking._abrir_bloco(r, bloco_idx=0, altura_picking_m=0.0)
    nivel_alto, nivel_baixo1, _ = bloco.niveis

    item_l = racking._ItemParaColocar(
        sku="L1", descricao="", load_type=LoadType.L, comp_m=0.90, larg_m=0.55, alt_m=0.68,
        pecas_nesta_caixa=1, eh_primeira_do_sku=False)
    item_xs = racking._ItemParaColocar(
        sku="XS1", descricao="", load_type=LoadType.XS, comp_m=0.30, larg_m=0.15, alt_m=0.12,
        pecas_nesta_caixa=1, eh_primeira_do_sku=False)

    assert racking._empacotar_nivel(nivel_alto, r, item_l) is not None
    assert racking._empacotar_nivel(nivel_baixo1, r, item_l) is None
    assert racking._empacotar_nivel(nivel_baixo1, r, item_xs) is not None


def test_blocos_empilhados_soma_alturas_heterogeneas():
    """altura do módulo = SOMA das alturas por nível (1.4 m), não
    alt_nivel_m(=max, 0.8) x niveis(3) = 2.4 m -- floor(4.0/1.4)=2 vs floor(4.0/2.4)=1."""
    r = _flow_rack()
    assert racking.blocos_empilhados(r, altura_armazem_m=4.0) == 2


def test_racktype_sem_alturas_niveis_mantem_comportamento_antigo():
    """Guarda de regressão: sem alturas_niveis_m, os helpers novos e
    rack_capacity/blocos_empilhados reduzem exatamente ao cálculo de sempre."""
    r = RackType("Prateleira Leve", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.8, niveis=3,
                aceita=[LoadType.L, LoadType.XS])
    assert r.alturas_niveis_m == ()
    for n in range(r.niveis):
        assert racking.altura_nivel(r, n) == 0.8
    assert racking.altura_ate_niveis(r, r.niveis) == 0.8 * 3
    cap_l = racking.rack_capacity(r, _box_l())
    # por_nivel(L) em 1.8x0.6 = floor(1.8/0.55)*floor(0.6/0.9)=3*0=0 ou floor(1.8/0.9)*floor(0.6/0.55)=2*1=2
    assert cap_l == 2 * 3  # todos os 3 níveis compatíveis (0.68 <= 0.8)
    assert racking.blocos_empilhados(r, altura_armazem_m=10.0) == 4  # 10/2.4 = 4, como sempre


# ------------------------------------------------ OVERSIZE em rack wide-span
# Peças que não assentam na palete ficam POUSADAS no nível de uma wide-span
# (bulk rack), sem palete nem caixa.

def _ws() -> RackType:
    """Wide-span proposta: vão 2.7 x prof 1.5 m, 4 níveis de 1.6 m."""
    return RackType("Rack Wide-Span", larg_m=2.7, prof_m=1.5, alt_nivel_m=1.6,
                    niveis=4, aceita=[LoadType.OVERSIZE])


def _art(sku, c, l, a, peso, stock=1):
    from warehouse.models import Article, Empilhamento
    return Article(sku=sku, descricao="", comp_m=c, larg_m=l, alt_m=a,
                   peso_kg=peso, stock_max_un=stock, pecas_por_carro=1,
                   empilhamento=Empilhamento.EMPILHAVEL)


def test_oversize_assenta_deitado_nao_ao_alto():
    """A peça pousa-se: a MENOR cota é a altura, as duas maiores vão ao plano.
    2.26 x 1.37 no plano cabe em 2.7 x 1.5; ao alto (1.38 de base) não faria
    sentido para um painel."""
    cap = racking.rack_capacity_oversize(_ws(), _art("A", 2.26, 1.37, 1.38, 84.4))
    assert cap == 4          # 1 por nível x 4 níveis (1.37 de altura só cabe 1)


def test_oversize_peso_trava_antes_da_altura():
    """Peça fina (0.17 m), orientação Livre: deitada dá 1 por nível (plano
    1.75x1.37 não cabe repetido no vão 2.7x1.5) mas empilha 9 em 1.55 m úteis
    -- 9 no total. De pé (vertical=1.37, plano 1.75x0.17) cabem 8 lado a lado
    mas só 1 de altura (1.37 já quase esgota o nível) -- 8 no total. Sem
    limite de peso, a orientação deitada vence (9 > 8): sem_peso = 9x4 = 36.
    Com 1000 kg/nível, a deitada cai para 4 (1000/215.8=4) = 4x4=16, enquanto
    a de pé nem chega a cortar (1 já cabe no peso) = 8x4=32 -- a orientação
    de pé passa a vencer. É o peso que manda a troca de orientação, não só
    a contagem dentro da mesma orientação."""
    ws = _ws()
    sem_peso = racking.rack_capacity_oversize(ws, _art("B", 1.75, 1.37, 0.17, 215.8),
                                             peso_max_nivel_kg=0.0, folga_altura_m=0.05)
    com_peso = racking.rack_capacity_oversize(ws, _art("B", 1.75, 1.37, 0.17, 215.8),
                                             peso_max_nivel_kg=1000.0, folga_altura_m=0.05)
    assert sem_peso == 9 * 4          # deitada: 1 por nível x 9 de altura x 4 níveis
    assert com_peso == 8 * 4          # de pé: 8 por nível x 1 de altura x 4 níveis
    assert sem_peso > com_peso


def test_oversize_que_nao_assenta_no_nivel_da_zero():
    """Peça maior que o vão/profundidade não tem capacidade -- não se inventa 1."""
    assert racking.rack_capacity_oversize(_ws(), _art("C", 3.5, 2.0, 0.2, 10.0)) == 0


def test_aceita_oversize_por_substring():
    assert racking.aceita_oversize(_ws(), ("wide", "bulk"))
    assert racking.aceita_oversize(RackType("Bulk Rack XL", larg_m=2.7, prof_m=1.5,
                                            alt_nivel_m=1.6, niveis=3), ("wide", "bulk"))
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.1, alt_nivel_m=1.8, niveis=4)
    assert not racking.aceita_oversize(pp, ("wide", "bulk"))
    assert not racking.aceita_oversize(_ws(), ())       # sem chaves: nenhuma aceita


def test_oversize_sem_widespan_continua_em_aviso():
    """Sem wide-span na folha «Racks», o comportamento anterior mantém-se:
    aviso e NÃO dimensionado (nunca contado como 1/palete)."""
    from warehouse import boxing
    from warehouse.config import Config
    from warehouse.models import BoxType
    cfg = Config()
    pallet = BoxType(nome=LoadType.PALLET, int_comp_m=1.2, int_larg_m=0.8, int_alt_m=1.5,
                     ext_comp_m=1.2, ext_larg_m=0.8, ext_alt_m=1.64, peso_max_kg=1000)
    grande = _art("OVS", 1.75, 1.37, 0.17, 100.0, stock=4)
    asg = boxing.plan_all([grande], [pallet], cfg)
    assert asg[0].load_type == LoadType.OVERSIZE
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.1, alt_nivel_m=1.8, niveis=4,
                  aceita=[LoadType.PALLET])
    reqs, avisos = racking.racks_needed(asg, [pp], {LoadType.PALLET: pallet}, cfg,
                                        articles=[grande])
    assert not any("Wide" in r.rack_nome for r in reqs)
    assert any("OVERSIZE" in a for a in avisos)


# -------------------------------------------------- avisos de chave sem match
# racking.verificar_chaves_substring_sem_correspondencia: o modo de falha mais
# perigoso (uma chave de substring configurada -- oversize_racks/
# oversize_par_racks/max_modulos_por_tipologia/empilhamento_por_nivel/
# picking_manual_racks -- que não corresponde a NENHUMA tipologia real) nunca
# pode passar por um sucesso limpo e silencioso.

def test_chave_sem_correspondencia_gera_aviso():
    from warehouse.config import Config
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.8, niveis=4)
    cfg = Config(oversize_racks=("wide", "bulk"), oversize_par_racks=(),
                 max_modulos_por_tipologia={}, empilhamento_por_nivel=(),
                 picking_manual_racks=())
    avisos = racking.verificar_chaves_substring_sem_correspondencia([pp], cfg)
    assert len(avisos) == 1
    assert "oversize_racks" in avisos[0]
    assert "Rack Porta-Paletes" in avisos[0]   # nomeia a tipologia real disponível


def test_chave_que_corresponde_nao_gera_aviso():
    from warehouse.config import Config
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.8, niveis=4)
    cfg = Config(oversize_racks=(), oversize_par_racks=("porta-paletes",),
                 max_modulos_por_tipologia={}, empilhamento_por_nivel=(),
                 picking_manual_racks=())
    assert racking.verificar_chaves_substring_sem_correspondencia([pp], cfg) == []


def test_chave_vazia_nunca_gera_aviso():
    """Cliente que simplesmente não usa a regra (chave vazia) não é um erro."""
    from warehouse.config import Config
    pp = RackType("Rack Porta-Paletes", larg_m=2.7, prof_m=1.2, alt_nivel_m=1.8, niveis=4)
    cfg = Config(oversize_racks=(), oversize_par_racks=(), max_modulos_por_tipologia={},
                 empilhamento_por_nivel=(), picking_manual_racks=())
    assert racking.verificar_chaves_substring_sem_correspondencia([pp], cfg) == []


def test_max_modulos_por_tipologia_usa_as_chaves_do_dict():
    """max_modulos_por_tipologia é um dict (chave = substring, valor = teto) --
    o aviso tem de olhar para as CHAVES, não tentar tratar o dict como tuple."""
    from warehouse.config import Config
    pl = RackType("Prateleira Leve", larg_m=1.8, prof_m=0.6, alt_nivel_m=0.7, niveis=3)
    cfg = Config(oversize_racks=(), oversize_par_racks=(),
                 max_modulos_por_tipologia={"tipologia inexistente": 2},
                 empilhamento_por_nivel=(), picking_manual_racks=())
    avisos = racking.verificar_chaves_substring_sem_correspondencia([pl], cfg)
    assert len(avisos) == 1 and "max_modulos_por_tipologia" in avisos[0]


# ------------------------------------------------- io_excel: tokens desconhecidos
# Um token na coluna "Aceita" que não bate com nenhum LoadType (ex.: "LONGO",
# removido do enum) não pode voltar a ser ignorado em silêncio -- ver
# io_excel.load_rack_types e a limpeza da linha "Rack Cantilever" stale no
# master data real (CLAUDE.md "cantilever/LONGO removida").

def _wb_racks(path, aceita_str: str):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Racks"
    ws.append(["Nome", "Larg_m", "Prof_m", "Alt_nivel_m", "Níveis", "Aceita", "Footprint_m2"])
    ws.append(["Rack X", 3.0, 0.6, 0.6, 4, aceita_str, 1.8])
    wb.save(path)


def test_token_desconhecido_na_coluna_aceita_avisa(tmp_path, capsys):
    from warehouse import io_excel
    path = tmp_path / "racks_longo.xlsx"
    _wb_racks(path, "LONGO")
    racks = io_excel.load_rack_types(path)
    assert racks[0].aceita == []          # token desconhecido continua a ser ignorado...
    out = capsys.readouterr().out
    assert "AVISO" in out and "Rack X" in out and "LONGO" in out   # ...mas agora avisa
    assert "aceita" in out.lower() and "VAZIO" in out   # sinaliza a tipologia morta


def test_token_conhecido_nao_avisa(tmp_path, capsys):
    from warehouse import io_excel
    path = tmp_path / "racks_ok.xlsx"
    _wb_racks(path, "PALLET, L")
    racks = io_excel.load_rack_types(path)
    assert [lt.value for lt in racks[0].aceita] == ["PALLET", "L"]
    out = capsys.readouterr().out
    assert "AVISO" not in out


# ------------------------------------------------------- caixas especiais

def test_caixa_especial_intercepta_choose_load():
    """SKU com CaixaEspecial (Config.caixas_especiais) sai sempre OVERSIZE2
    com a quantidade DESSE SKU, ignorando por completo a geometria -- mesmo
    uma peça minúscula que caberia facilmente numa XS."""
    from warehouse import boxing
    from warehouse.config import Config
    from warehouse.models import Article, Empilhamento, CaixaEspecial, BoxType
    especial = CaixaEspecial(quantidades={"X1": 4},
                             comp_m=1.2, larg_m=1.6, alt_m=1.7, notas="Carrinho lateral")
    cfg = Config(caixas_especiais={"X1": especial})
    a = Article(sku="X1", descricao="", comp_m=0.05, larg_m=0.05, alt_m=0.05,
               peso_kg=0.0, stock_max_un=10, pecas_por_carro=1,
               empilhamento=Empilhamento.EMPILHAVEL)
    s = BoxType(nome=LoadType.S, int_comp_m=0.4, int_larg_m=0.3, int_alt_m=0.24,
               ext_comp_m=0.42, ext_larg_m=0.32, ext_alt_m=0.25, peso_max_kg=15)
    lt, ppu = boxing.choose_load(a, [s], cfg)
    assert lt == LoadType.OVERSIZE2 and ppu == 4


def test_caixa_especial_cobre_varios_skus_com_quantidades_diferentes():
    """Um só registo (mesmo equipamento) pode cobrir vários SKUs -- MAS cada
    um com a SUA PRÓPRIA quantidade (pedido explícito: "posso querer 4 de
    uma referência e 2 de outra"). caixas_especiais_por_sku expande para um
    dict com uma chave por SKU, todas apontando para o MESMO objeto
    CaixaEspecial (mesmas dimensões), mas cada SKU sai com a sua quantidade."""
    from warehouse import io_excel, boxing
    from warehouse.config import Config
    from warehouse.models import Article, Empilhamento, CaixaEspecial
    especial = CaixaEspecial(quantidades={"A1": 4, "A2": 2},
                             comp_m=1.2, larg_m=1.6, alt_m=1.7)
    por_sku = io_excel.caixas_especiais_por_sku([especial])
    assert por_sku["A1"] is por_sku["A2"] is especial

    cfg = Config(caixas_especiais=por_sku)
    base = dict(descricao="", comp_m=0.3, larg_m=0.3, alt_m=0.3, peso_kg=0.0,
               stock_max_un=1, pecas_por_carro=1, empilhamento=Empilhamento.EMPILHAVEL)
    lt1, ppu1 = boxing.choose_load(Article(sku="A1", **base), [], cfg)
    lt2, ppu2 = boxing.choose_load(Article(sku="A2", **base), [], cfg)
    assert lt1 == LoadType.OVERSIZE2 and ppu1 == 4
    assert lt2 == LoadType.OVERSIZE2 and ppu2 == 2


def test_carrinho_cabe_na_rack_oversize_por_altura():
    """carrinho_cabe_na_rack_oversize recebe a altura por parâmetro (não um
    valor global) -- cada caixa especial pode ter uma altura diferente e
    caber ou não caber, independentemente das outras."""
    from warehouse.config import Config
    ws = _ws()   # wide-span: alt_nivel_m=1.6
    cfg = Config(carrinhos_em_rack=True)
    assert racking.carrinho_cabe_na_rack_oversize([ws], cfg, 1.5) is ws
    # por omissão (armazém) o carrinho fica no chão, nunca em rack
    assert racking.carrinho_cabe_na_rack_oversize([ws], Config(), 1.5) is None
    assert racking.carrinho_cabe_na_rack_oversize([ws], cfg, 1.7) is None


def test_carrinhos_ao_chao_nao_empilham():
    """Por omissão os carrinhos vão TODOS ao chão e nunca empilham, mesmo os
    baixos (0.5 m caberiam 3 em 1.8 m de altura_max): 1 carrinho por pilha."""
    from warehouse import area
    from warehouse.config import Config
    from warehouse.models import BoxAssignment, CaixaEspecial
    baixo = CaixaEspecial(quantidades={"B": 2}, comp_m=1.0, larg_m=1.0, alt_m=0.5, id="1")
    cfg = Config(caixas_especiais={"B": baixo})
    ws = RackType("Wide", larg_m=2.7, prof_m=1.5, alt_nivel_m=1.6, niveis=4, aceita=[LoadType.OVERSIZE])
    linhas = area.artigos_chao_oversize2([BoxAssignment("B", LoadType.OVERSIZE2, 2, 6)], cfg, [ws])
    assert len(linhas) == 1           # vai ao chão, apesar de haver rack que o aceitaria em teoria
    a, det = area.area_oversize_chao(linhas, cfg.oversize_chao_altura_max_m,
                                     cfg.oversize_chao_peso_max_kg, 0.0)
    assert det[0]["por_pilha"] == 1 and det[0]["pilhas"] == 6
    assert abs(a - 6 * 1.0) < 1e-6


def test_carrinho_partilhado_conta_o_maximo_e_nao_a_soma():
    """Side body LH e RH vão juntos no MESMO carrinho (2 de cada por carrinho,
    stock 10 de cada): são 5 carrinhos, não 5 + 5. Um SKU noutro carrinho conta à parte."""
    from warehouse import boxing
    from warehouse.config import Config
    from warehouse.models import BoxAssignment, CaixaEspecial
    par = CaixaEspecial(quantidades={"LH": 2, "RH": 2}, comp_m=1.4, larg_m=1.6, alt_m=1.4, id="1")
    so = CaixaEspecial(quantidades={"X": 4}, comp_m=1.0, larg_m=1.0, alt_m=0.5, id="2")
    cfg = Config(caixas_especiais={"LH": par, "RH": par, "X": so})
    asg = [BoxAssignment("LH", LoadType.OVERSIZE2, 2, 5), BoxAssignment("RH", LoadType.OVERSIZE2, 2, 5),
           BoxAssignment("X", LoadType.OVERSIZE2, 4, 5), BoxAssignment("N", LoadType.S, 1, 9)]
    assert boxing.carrinhos_fisicos(asg, cfg) == {"LH": 5, "RH": 0, "X": 5}
    # o SKU que pede mais carrinhos manda
    asg[1] = BoxAssignment("RH", LoadType.OVERSIZE2, 2, 8)
    assert boxing.carrinhos_fisicos(asg, cfg)["LH"] == 8


def test_artigos_chao_oversize2_decide_por_sku():
    """Duas caixas especiais com alturas diferentes: uma cabe no nível de
    chão da wide-span, a outra não -- só a que não cabe aparece na lista
    para arrumar ao chão (a que cabe fica para allocate_to_racks)."""
    from warehouse.config import Config
    from warehouse.models import CaixaEspecial, BoxAssignment
    ws = _ws()   # alt_nivel_m=1.6
    baixa = CaixaEspecial(quantidades={"B1": 2}, comp_m=1.0, larg_m=1.0, alt_m=1.5)
    alta = CaixaEspecial(quantidades={"B2": 2}, comp_m=1.0, larg_m=1.0, alt_m=1.8)
    cfg = Config(caixas_especiais={"B1": baixa, "B2": alta}, carrinhos_em_rack=True)
    assignments = [
        BoxAssignment("B1", LoadType.OVERSIZE2, 2, 3),
        BoxAssignment("B2", LoadType.OVERSIZE2, 2, 5),
    ]
    linhas = area.artigos_chao_oversize2(assignments, cfg, [ws])
    skus_no_chao = {l[0] for l in linhas}
    assert skus_no_chao == {"B2"}          # B1 cabe na rack -- não vai ao chão
    linha_b2 = next(l for l in linhas if l[0] == "B2")
    assert linha_b2[1:4] == (1.0, 1.0, 1.8) and linha_b2[5] == 5


def _folha_caixas_especiais(tmp_path, linhas, cabecalho=None):
    """Master data mínimo com a folha «Caixas Especiais» no formato do
    utilizador (uma linha por SKU; coluna extra vazia no fim, como no real)."""
    import openpyxl
    path = tmp_path / "master_teste.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "Peças"
    ws = wb.create_sheet("Caixas Especiais")
    ws.append(cabecalho or ["SKU", "Quantidade", "Comprimento_m", "Largura_m", "Altura_m",
                            "Notas", "Número/ID", "Estação", None])
    for linha in linhas:
        ws.append(linha)
    ws.append([None] * 9)   # linhas vazias no fim da folha não contam
    wb.save(path)
    return path


def test_load_caixas_especiais_agrupa_por_numero_id(tmp_path):
    """Formato da folha real: uma linha por SKU, as linhas com o mesmo
    Número/ID juntam-se num só carrinho, cada SKU com a sua Quantidade. As
    cotas podem vir só na 1ª linha do carrinho. O SKU não leva strip (há
    SKUs reais com espaço no fim que têm de bater com a folha «Peças»)."""
    from warehouse import io_excel
    path = _folha_caixas_especiais(tmp_path, [
        ["A1", 2, 1.4, 1.6, 1.4, "Carrinho de sidebody", 1, "C3"],
        ["A2 ", 2, None, None, None, None, 1, None],
        ["B1", 4, 1, 1, 0.5, "Carrinho de vidros portas", 2.0, "C4"],
    ])
    c1, c2 = io_excel.load_caixas_especiais(path)
    assert (c1.id, c1.estacao, c1.notas) == ("1", "C3", "Carrinho de sidebody")
    assert c1.quantidades == {"A1": 2, "A2 ": 2}
    assert (c1.comp_m, c1.larg_m, c1.alt_m) == (1.4, 1.6, 1.4)
    assert (c2.id, c2.estacao, c2.quantidades) == ("2", "C4", {"B1": 4})
    por_sku = io_excel.caixas_especiais_por_sku([c1, c2])
    assert por_sku["A1"] is por_sku["A2 "] is c1


def test_load_caixas_especiais_sem_numero_id_cada_sku_e_um_carrinho(tmp_path):
    from warehouse import io_excel
    path = _folha_caixas_especiais(tmp_path, [
        ["A1", 2, 1.0, 1.0, 1.0, None, None, "C1"],
        ["A2", 1, 1.0, 1.0, 1.0, None, None, "C1"],
    ])
    caixas = io_excel.load_caixas_especiais(path)
    assert [(c.id, c.skus) for c in caixas] == [("A1", ("A1",)), ("A2", ("A2",))]


def test_load_caixas_especiais_folha_incoerente_da_erro_com_a_linha(tmp_path):
    """Folha presente mas mal preenchida -> ValueError com a linha do Excel,
    nunca [] em silêncio (foi assim que a mudança de formato da folha passou
    despercebida: o leitor antigo ignorava o que não reconhecia)."""
    import pytest
    from warehouse import io_excel
    casos = {
        "Largura_m": [["A1", 2, 1.4, 1.6, 1.4, None, 1, "C3"],
                      ["A2", 2, 1.4, 1.2, 1.4, None, 1, "C3"]],       # 2 tamanhos
        "Estação": [["A1", 2, 1.4, 1.6, 1.4, None, 1, "C3"],
                    ["A2", 2, None, None, None, None, 1, "C4"]],       # 2 estações
        "repetido": [["A1", 2, 1.0, 1.0, 1.0, None, 1, None],
                     ["A1", 1, 1.0, 1.0, 1.0, None, 2, None]],
        "Quantidade": [["A1", "duas", 1.0, 1.0, 1.0, None, 1, None]],
        "em falta": [["A1", 2, None, 1.0, 1.0, None, 1, None]],        # 1ª linha sem cota
    }
    for i, (esperado, linhas) in enumerate(casos.items()):
        sub = tmp_path / str(i)
        sub.mkdir()
        path = _folha_caixas_especiais(sub, linhas)
        with pytest.raises(ValueError, match=esperado) as info:
            io_excel.load_caixas_especiais(path)
        assert "linha" in str(info.value)


def test_load_caixas_especiais_formato_antigo_da_erro(tmp_path):
    """Folha no formato antigo ('SKU:qtd | SKU:qtd' numa célula, sem coluna
    Quantidade): erro claro em vez de devolver [] sem aviso."""
    import pytest
    from warehouse import io_excel
    path = _folha_caixas_especiais(
        tmp_path, [["A1:4", 1.4, 1.6, 1.4, "teste"]],
        cabecalho=["SKU", "Comprimento_m", "Largura_m", "Altura_m", "Notas"])
    with pytest.raises(ValueError, match="Quantidade"):
        io_excel.load_caixas_especiais(path)


def test_caixas_especiais_sheet_ausente(tmp_path):
    """Sem a folha «Caixas Especiais» (master data antigo, ou nunca usado),
    load_caixas_especiais devolve [] -- nenhum SKU tem caixa especial,
    comportamento normal em tudo, sem erro."""
    import openpyxl
    from warehouse import io_excel
    path = tmp_path / "sem_caixas.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "Peças"
    wb.save(path)
    assert io_excel.load_caixas_especiais(path) == []


def test_load_rack_types_le_alturas_niveis_m(tmp_path):
    """Coluna opcional «Alturas_Niveis_m» (comma-separated, índice 0 = base):
    quando presente e não vazia, popula RackType.alturas_niveis_m e
    alt_nivel_m passa a max() dessa lista; célula vazia ou coluna ausente
    mantém o comportamento uniforme de sempre. Contagem errada levanta
    ValueError em vez de gerar uma tipologia incoerente em silêncio."""
    import openpyxl
    from warehouse import io_excel

    path = tmp_path / "racks_teste.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Racks"
    ws.append(["Nome", "Larg_m", "Prof_m", "Alt_Nivel_m", "Niveis", "Aceita",
              "Footprint_m2", "Corredor_m", "Alturas_Niveis_m"])
    ws.append(["Flow Rack", 1.22, 1.22, 0.8, 3, "L, XS", None, 1.2, "0.8,0.3,0.3"])
    ws.append(["Prateleira Leve", 1.8, 0.6, 0.8, 3, "S, M, L, XS", None, 1.2, None])
    wb.save(path)

    racks = io_excel.load_rack_types(path)
    flow = next(r for r in racks if r.nome == "Flow Rack")
    leve = next(r for r in racks if r.nome == "Prateleira Leve")

    assert flow.alturas_niveis_m == (0.8, 0.3, 0.3)
    assert flow.alt_nivel_m == 0.8   # max(alturas_niveis_m)
    assert leve.alturas_niveis_m == ()
    assert leve.alt_nivel_m == 0.8   # célula vazia -- coluna Alt_Nivel_m normal

    ws.append(["Rack Inválida", 1.22, 1.22, 0.8, 3, "L", None, 1.2, "0.8,0.3"])
    wb.save(path)
    try:
        io_excel.load_rack_types(path)
        assert False, "devia ter levantado ValueError (3 níveis, só 2 alturas)"
    except ValueError:
        pass


# ------------------------------------------------ caixa forçada (CAIXA_FORCADA)

def _caixa_l():
    from warehouse.models import BoxType
    return BoxType(nome=LoadType.L, int_comp_m=0.88, int_larg_m=0.53, int_alt_m=0.67,
                   ext_comp_m=0.90, ext_larg_m=0.55, ext_alt_m=0.68, peso_max_kg=30)


def _door_trim(**kw):
    """Geometria real do BEN10.FHD103/104 (door outer trim): 1.054 m não cabe
    na L a direito -- o best-fit põe-na em PALLET."""
    from warehouse.models import Article, Empilhamento
    base = dict(sku="TRIM", descricao="", comp_m=1.0538, larg_m=0.0693, alt_m=0.1093,
                peso_kg=0.0, stock_max_un=10, pecas_por_carro=1, altura_override="y",
                empilhamento=Empilhamento.EMPILHAVEL)
    base.update(kw)
    return Article(**base)


def test_caixa_forcada_com_quantidade_ignora_best_fit_e_geometria():
    """CAIXA_FORCADA=L + PECAS_CAIXA_FORCADA=4: sai (L, 4) mesmo com a
    geometria do motor a dizer 0 na L (as 4 são peças inclinadas numa caixa
    aberta, que o motor não modela). O Passo 0 enche a última caixa com a
    quantidade manual: 10 carros x 1 -> 3 caixas -> 12 peças."""
    from warehouse import boxing
    from warehouse.config import Config
    cfg = Config()
    cat = _catalogo_minimo() + [_caixa_l()]

    assert boxing.choose_load(_door_trim(), cat, cfg)[0] == LoadType.PALLET
    forcado = _door_trim(caixa_forcada="L", pecas_caixa_forcada=4)
    assert boxing.pieces_per_box(forcado, _caixa_l()) == 0
    assert boxing.choose_load(forcado, cat, cfg) == (LoadType.L, 4)

    r = boxing.stock_para_autonomia(forcado, cat, cfg)
    assert (r.load_type, r.pecas_por_unidade_pratico, r.num_unidades, r.stock_ajustado_un) \
        == (LoadType.L, 4, 3, 12)

    asg = boxing.plan_box(forcado, cat, cfg)
    j = boxing.justificar_escolha(forcado, cat, cfg, asg)
    assert "CAIXA_FORCADA=L" in j.criterio and j.fator_limitante.startswith("N/A")


def test_palete_por_sku_conta_a_largura_real_na_viga_e_a_altura():
    """Config.palete_por_sku: um OVERSIZE (1.54 x 1.06, não assenta na palete
    com a tolerância de 0.10) passa a PALLET com 10 peças deitadas, 1 por
    camada. Altura = deck 0.144 + 10 x 0.13; na viga ocupa 1.06 (a carga), não
    os 0.80 da palete; sai 0.17 à frente/atrás e 0.13 para cada lado. Num
    porta-paletes de 2.7 m: 0.80 + 0.80 + 1.06 = 2.66 cabe, mais um 0.80 não."""
    import dataclasses
    from warehouse import boxing
    from warehouse.config import Config
    from warehouse.models import Article, BoxAssignment, Empilhamento
    roof = Article(sku="ROOF", descricao="", comp_m=1.535, larg_m=1.056, alt_m=0.13,
                   peso_kg=0.0, stock_max_un=10, pecas_por_carro=1, altura_override="y",
                   empilhamento=Empilhamento.NAO_EMPILHAVEL)
    front = Article(sku="FRONT", descricao="", comp_m=0.759, larg_m=1.425, alt_m=0.694,
                    peso_kg=0.0, stock_max_un=10, pecas_por_carro=1, altura_override="z",
                    empilhamento=Empilhamento.NAO_EMPILHAVEL)
    cat = [_palete_161()]
    assert boxing.choose_load(roof, cat, Config())[0] == LoadType.OVERSIZE

    cfg = dataclasses.replace(Config(), palete_por_sku={
        "ROOF": {"pecas": 10, "vertical": "z"}, "FRONT": {"pecas": 1, "vertical": "z"}})
    assert boxing.choose_load(roof, cat, cfg) == (LoadType.PALLET, 10)
    r = boxing.stock_para_autonomia(roof, cat, cfg)
    assert (r.load_type, r.pecas_por_unidade_pratico, r.num_unidades, r.stock_ajustado_un) \
        == (LoadType.PALLET, 10, 1, 10)
    assert boxing.altura_real_unidade(roof, _palete_161(), 10, cfg) == 1.444
    # aninhável na cota vertical: deck + cota x (1 + (n - 1) x 0.3)
    roof_aninha = dataclasses.replace(roof, eixo_aninhagem="z")
    assert boxing.altura_real_unidade(roof_aninha, _palete_161(), 10, cfg) == 0.144 + 0.13 * 3.7
    cfg_an = dataclasses.replace(cfg, palete_por_sku={"ROOF": {"pecas": 10, "vertical": "z", "aninha": True}})
    assert boxing.altura_real_unidade(roof, _palete_161(), 10, cfg_an) == 0.144 + 0.13 * 3.7
    assert boxing.base_palete_por_sku(roof, _palete_161(), cfg) == (1.2, 1.056, 0.1675, 0.128)
    assert boxing.base_palete_por_sku(front, _palete_161(), cfg)[:2] == (1.2, 0.8)
    j = boxing.justificar_escolha(roof, cat, cfg, boxing.plan_box(roof, cat, cfg))
    assert "palete_por_sku" in j.criterio and j.altura_real_unidade_m == 1.444

    pos, avisos = racking.allocate_to_racks(
        [BoxAssignment("ROOF", LoadType.PALLET, 10, 1), BoxAssignment("FRONT", LoadType.PALLET, 1, 3)],
        [], [_pp_161()], {LoadType.PALLET: _palete_161()}, [roof, front], 0.0, 4.6, cfg)
    assert avisos == []
    n1 = pos[0].blocos[0].niveis[0].caixas
    assert sorted((c.sku, min(c.comp_m, c.larg_m)) for c in n1) == \
        [("FRONT", 0.8), ("FRONT", 0.8), ("ROOF", 1.056)]
    assert [c.sku for c in pos[0].blocos[0].niveis[1].caixas] == ["FRONT"]


def test_palete_por_sku_orientacao_fixa_nao_roda_no_nivel():
    """SIDE BODY deitado (2026-11-11): palete de 1,2 com a peça a 1,28 na viga.
    Numa emparelhada (2,7 x 2,4) o packer não pode rodá-la (1,2 no vão punha
    os 1,58 m da peça a sair para cima das vizinhas): ficam 2 x 2 com 1,28 no vão."""
    from warehouse.models import NivelRack
    emp = RackType("Rack Porta-Paletes (emparelhada)", larg_m=2.7, prof_m=2.4, alt_nivel_m=1.61,
                   niveis=3, aceita=[LoadType.PALLET, LoadType.OVERSIZE],
                   profundidade_inclui_par=True, alturas_niveis_m=(1.61, 1.61, 0.81))
    nivel = NivelRack(nivel_idx=0, eh_picking=False)
    item = racking._ItemParaColocar(sku="SB", descricao="", load_type=LoadType.PALLET,
                                    comp_m=1.2828, larg_m=1.2, alt_m=1.3855, pecas_nesta_caixa=5,
                                    eh_primeira_do_sku=False, orientacao_fixa=True)
    for _ in range(4):
        sol = racking._empacotar_nivel(nivel, emp, item)
        assert sol is not None
        racking._commit_nivel(nivel, item, sol)
    assert racking._empacotar_nivel(nivel, emp, item) is None
    assert all((c.comp_m, c.larg_m) == (1.2828, 1.2) and c.orientacao_fixa for c in nivel.caixas)


def test_caixa_forcada_quantidade_manual_nao_e_arredondada():
    """A capacidade manual já é uma decisão de manuseio: não leva o
    arredondamento à centena que capacidade_reportada aplica à XS/S/M/MEIA
    (250 ficaria 200)."""
    from warehouse import boxing
    from warehouse.config import Config
    cfg = Config()
    assert boxing.capacidade_reportada(LoadType.S, 250, "X", cfg) == 200
    assert boxing.capacidade_reportada(LoadType.S, 250, "X", cfg, manual=True) == 250
    a = _door_trim(comp_m=0.05, larg_m=0.05, alt_m=0.05, altura_override="",
                   pecas_por_carro=30, caixa_forcada="S", pecas_caixa_forcada=250)
    r = boxing.stock_para_autonomia(a, _catalogo_minimo(), cfg)
    assert (r.pecas_por_unidade_pratico, r.num_unidades, r.stock_ajustado_un) == (250, 2, 500)


def test_caixa_forcada_sem_quantidade_conta_pela_geometria():
    """Só CAIXA_FORCADA (sem quantidade): o tipo vem da folha, a contagem é a
    geometria normal nessa caixa -- aqui PALLET em vez da S do best-fit."""
    from warehouse import boxing
    from warehouse.config import Config
    cfg = Config()
    cat = _catalogo_minimo()
    pequena = _door_trim(comp_m=0.05, larg_m=0.05, alt_m=0.05, altura_override="")
    assert boxing.choose_load(pequena, cat, cfg)[0] == LoadType.S
    forcada = _door_trim(comp_m=0.05, larg_m=0.05, alt_m=0.05, altura_override="",
                         caixa_forcada="PALLET")
    pallet = next(b for b in cat if b.nome == LoadType.PALLET)
    esperado = boxing.pieces_per_box(forcada, pallet, cfg.fator_empilhamento,
                                     cfg.fill_factor_plano, cfg.pallet_tolerancia_lateral_m)
    assert boxing.choose_load(forcada, cat, cfg) == (LoadType.PALLET, esperado)


def test_caixa_forcada_invalida_da_erro_em_vez_de_best_fit():
    """Sem quantidade e sem caber pela geometria, ou com uma caixa que não
    está no catálogo: erro claro, nunca best-fit em silêncio."""
    from warehouse import boxing
    from warehouse.config import Config
    cfg = Config()
    cat = _catalogo_minimo() + [_caixa_l()]
    for art, cat_teste in ((_door_trim(caixa_forcada="L"), cat),
                           (_door_trim(caixa_forcada="L", pecas_caixa_forcada=4),
                            _catalogo_minimo())):
        try:
            boxing.choose_load(art, cat_teste, cfg)
            assert False, "devia ter levantado ValueError"
        except ValueError as e:
            assert "TRIM" in str(e)


def test_load_articles_le_caixa_forcada(tmp_path):
    """Colunas opcionais, lidas por nome: 'l' -> 'L'; vazio -> best-fit
    normal; caixa inexistente ou quantidade sem caixa -> ValueError."""
    import openpyxl
    from warehouse import io_excel
    path = tmp_path / "pecas.xlsx"
    cab = ["SKU", "Descrição", "Comprimento_m", "Largura_m", "Altura_m", "Peso_kg",
           "Stock_máx_un", "Peças_por_carro", "ALTURA_OVERRIDE", "EIXO_ANINHAGEM",
           "Empilhável", "CAIXA_FORCADA", "PECAS_CAIXA_FORCADA"]

    def escrever(*linhas):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Peças"
        ws.append(cab)
        for linha in linhas:
            ws.append(linha)
        wb.save(path)

    peca = ["A", "", 1.05, 0.07, 0.11, None, 0, 1, "y", None, "EMPILHAVEL"]
    escrever(peca + ["l", 4], ["B"] + peca[1:] + [None, None])
    a, b = io_excel.load_articles(path)
    assert (a.caixa_forcada, a.pecas_caixa_forcada) == ("L", 4)
    assert (b.caixa_forcada, b.pecas_caixa_forcada) == ("", 0)

    for extra in (["CAIXOTE", 4], [None, 4], ["L", 2.5]):
        escrever(peca + extra)
        try:
            io_excel.load_articles(path)
            assert False, f"devia ter levantado ValueError para {extra}"
        except ValueError as e:
            assert "SKU A" in str(e)


if __name__ == "__main__":
    test_footprint(); test_floor_area()
    test_zona_area(); test_resumo_area_total()
    test_picking_nivel_pelo_topo(); test_sem_modulo_fantasma_picking()
    test_eixos_possiveis(); test_orientacao_livre_deita_peca()
    test_caixa_rodada_fica_gravada_na_orientacao_colocada()
    test_largura_max_e_teto_reduz_e_cresce_em_profundidade()
    test_pilhas_chao_recebem_coordenadas_sem_sobrepor()
    test_pilhas_chao_tem_corredor_a_cada_2_fiadas()
    test_sem_oversize_chao_nao_gera_pilhas()
    test_oversize_ao_chao_empilha_e_soma_acesso()
    test_oversize_ao_chao_peca_alta_nao_empilha()
    test_oversize_ao_chao_ignora_sem_stock_ou_sem_geometria()
    test_area_ao_chao_nao_conta_duas_vezes_no_resumo()
    test_floor_area_conta_footprint_das_pilhas()
    test_pecas_por_carro_fracionario_pede_picking()
    test_layout_prefere_compacidade_a_area_minima()
    test_largura_max_acima_do_otimo_nao_muda_nada()
    test_profundidade_max_avisa_sem_truncar()
    test_largura_max_menor_que_a_posicao_avisa()
    test_sem_envelope_mantem_comportamento()
    test_limite_modulos_por_substring_do_nome()
    test_limite_modulos_nao_inventa_capacidade()
    test_sem_limite_mantem_comportamento()
    test_limite_modulos_case_insensitive()
    print("smoke OK")

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


def test_pode_deitar():
    """Base fixa / Não deitar travam o eixo vertical; tudo o resto é livre."""
    from warehouse import boxing
    assert not boxing.pode_deitar("Base fixa")
    assert not boxing.pode_deitar("Não deitar")
    assert not boxing.pode_deitar("nao deitar")
    assert boxing.pode_deitar("Livre")
    assert boxing.pode_deitar("")        # default -> livre
    assert boxing.pode_deitar(None)      # em falta -> livre


def test_orientacao_livre_deita_peca():
    """Peça comprida (1.3 m) não assenta deitada na footprint da palete
    (1.2 x 0.8), mas de pé cabe na altura (1.5 m). Com 'Base fixa' (Z medido =
    0.3 m como altura) não cabe -> 0. Com 'Livre' o packer põe a cota de 1.3 m
    na vertical e encaixa muitas peças no plano fino."""
    from warehouse import boxing
    from warehouse.models import Article, BoxType, Empilhamento
    pallet = BoxType(nome=LoadType.PALLET, int_comp_m=1.2, int_larg_m=0.8, int_alt_m=1.5,
                     ext_comp_m=1.2, ext_larg_m=0.8, ext_alt_m=1.644, peso_max_kg=1000.0)
    base = dict(descricao="", comp_m=1.3, larg_m=0.05, alt_m=0.3, peso_kg=0.0,
                stock_max_un=100, pecas_por_carro=1, empilhamento=Empilhamento.EMPILHAVEL)
    travado = Article(sku="T", orientacao="Base fixa", **base)
    livre = Article(sku="L", orientacao="Livre", **base)

    assert boxing.pieces_per_box(travado, pallet) == 0        # 1.3 m não assenta
    assert boxing.pieces_per_box(livre, pallet) == 64         # de pé: 0.05x0.3 x64
    assert boxing.pieces_per_box(livre, pallet) > boxing.pieces_per_box(travado, pallet)


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
                     orientacao="Base fixa", empilhamento=Empilhamento.NAO_EMPILHAVEL)
    lt, ppu = boxing.choose_load(grande, cat, cfg)
    assert lt == LoadType.OVERSIZE, f"esperado OVERSIZE, veio {lt}"


def test_peca_que_assenta_mas_nao_cabe_de_pe_fica_em_palete():
    """Contraponto: se a peça ASSENTA na footprint da palete mas a restrição de
    orientação a impede de ser arrumada, 1/palete continua a ser uma
    aproximação legítima — não deve virar OVERSIZE."""
    from warehouse import boxing
    from warehouse.models import Article, Empilhamento
    from warehouse.config import Config
    cfg = Config()
    cat = _catalogo_minimo()
    # 1.05 x 1.31 x 0.75 -> menores cotas (0.75, 1.05) cabem em 1.20 x 0.80?
    # 0.75 <= 0.80 e 1.05 <= 1.20 -> assenta
    a = Article(sku="OK", descricao="", comp_m=1.05, larg_m=1.31, alt_m=0.75,
                peso_kg=7.85, stock_max_un=50, pecas_por_carro=1,
                orientacao="Base fixa", empilhamento=Empilhamento.NAO_EMPILHAVEL)
    lt, ppu = boxing.choose_load(a, cat, cfg)
    assert lt == LoadType.PALLET and ppu == 1


def test_assenta_na_palete():
    from warehouse import boxing
    from warehouse.models import Article, Empilhamento
    cat = {b.nome: b for b in _catalogo_minimo()}
    P = cat[LoadType.PALLET]
    def art(c, l, a):
        return Article(sku="x", descricao="", comp_m=c, larg_m=l, alt_m=a,
                       peso_kg=1.0, stock_max_un=1, pecas_por_carro=1,
                       empilhamento=Empilhamento.EMPILHAVEL)
    assert boxing._assenta_na_palete(art(1.2, 0.8, 3.0), P)      # de pé, assenta
    assert boxing._assenta_na_palete(art(0.5, 0.5, 0.5), P)
    assert not boxing._assenta_na_palete(art(1.75, 1.37, 0.17), P)
    assert not boxing._assenta_na_palete(art(1.26, 2.08, 0.51), P)


if __name__ == "__main__":
    test_longo_existe(); test_footprint(); test_floor_area()
    test_zona_area(); test_resumo_area_total()
    test_picking_nivel_pelo_topo(); test_sem_modulo_fantasma_picking()
    test_pode_deitar(); test_orientacao_livre_deita_peca()
    test_caixa_rodada_fica_gravada_na_orientacao_colocada()
    print("smoke OK")

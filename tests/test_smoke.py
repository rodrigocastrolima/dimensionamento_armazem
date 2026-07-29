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
    assert boxing._assenta_na_palete(art(0.5, 0.5, 0.5), P)
    assert not boxing._assenta_na_palete(art(1.75, 1.37, 0.17), P)
    assert not boxing._assenta_na_palete(art(1.26, 2.08, 0.51), P)

    # 1.2 x 0.8 x 3.0: a única orientação que assenta no plano (1.2 x 0.8) exige
    # 3.0 m de altura e a palete só tem 1.5 m de altura útil; as outras põem os
    # 3.0 m no plano (transborda 150%). NÃO é paletizável -- a asserção antiga
    # dizia o contrário porque o teste ignorava a altura útil.
    assert not boxing._assenta_na_palete(art(1.2, 0.8, 3.0), P)

    # O que decide na prática é a ALTURA ÚTIL da palete (1.50 m), não a
    # tolerância: uma peça fina e comprida pode ser posta DE PÉ e aí não
    # transborda nada. 1.45 x 1.0 x 0.2 assenta em 0.2 x 1.0 com 1.45 m de
    # altura -- passa mesmo com tolerância zero.
    assert boxing._assenta_na_palete(art(1.45, 1.0, 0.2), P, 0.0)
    # 1.55 já não cabe de pé (> 1.50 m) e deitada põe 1.0 x 1.55 no plano:
    # 1.0 > 0.8, não apoia. Fora em qualquer orientação.
    assert not boxing._assenta_na_palete(art(1.55, 1.0, 0.2), P, 0.25)

    # As peças reais que motivaram o critério: laterais e tejadilho fora,
    # painel dentro (ver Config.pallet_transbordo_max_pct)
    assert not boxing._assenta_na_palete(art(2.465, 1.197, 0.36), P, 0.25)   # SIDE BODY
    assert not boxing._assenta_na_palete(art(1.535, 1.056, 0.129), P, 0.25)  # ROOF
    assert boxing._assenta_na_palete(art(1.314, 1.055, 0.747), P, 0.25)      # REAR PANEL
    # A tolerância NÃO alarga o lado menor (senão era inventar uma palete maior).
    # 0.9 x 0.9 x 0.9: qualquer orientação põe 0.9 x 0.9 no plano e 0.9 > 0.8,
    # logo não apoia -- apesar de 0.9 estar dentro dos 1.5 m de tolerância.
    assert not boxing._assenta_na_palete(art(0.9, 0.9, 0.9), P, 0.25)


def test_cantilever_so_aceita_material_esbelto():
    """Estar marcado 'Comprida' na folha nao chega: a cantilever guarda
    material 1D. Pecas volumosas (altas/largas) tem de cair no best-fit."""
    from warehouse import boxing
    from warehouse.models import Article, Empilhamento
    from warehouse.config import Config
    cfg = Config()

    def art(c, l, a):
        return Article(sku="x", descricao="", comp_m=c, larg_m=l, alt_m=a,
                       peso_kg=1.0, stock_max_un=10, pecas_por_carro=1,
                       geometria="Comprida",
                       empilhamento=Empilhamento.EMPILHAVEL)

    # perfil real: comprido e fino -> cantilever
    assert boxing.cabe_em_cantilever(art(2.47, 0.05, 0.04), cfg)
    # volumoso alto: 1.38 m nao cabe num nivel de 0.6 m
    assert not boxing.cabe_em_cantilever(art(2.26, 1.37, 1.38), cfg)
    # largo/pouco esbelto: e um painel, nao um perfil
    assert not boxing.cabe_em_cantilever(art(1.06, 1.07, 0.21), cfg)
    # curto e volumoso
    assert not boxing.cabe_em_cantilever(art(0.13, 1.53, 1.06), cfg)


def test_volumoso_marcado_comprida_nao_vai_para_longo():
    from warehouse import boxing
    from warehouse.models import Article, Empilhamento
    from warehouse.config import Config
    cfg = Config()
    cat = _catalogo_minimo()
    volumoso = Article(sku="V", descricao="", comp_m=2.26, larg_m=1.37, alt_m=1.38,
                       peso_kg=10.0, stock_max_un=7, pecas_por_carro=1,
                       geometria="Comprida",
                       empilhamento=Empilhamento.NAO_EMPILHAVEL)
    lt, _ = boxing.choose_load(volumoso, cat, cfg)
    assert lt != LoadType.LONGO, "volumoso nao pode ir para cantilever"


def test_aviso_cantilever_lista_reclassificados():
    from warehouse import boxing
    from warehouse.models import Article, Empilhamento
    from warehouse.config import Config
    cfg = Config()
    arts = [
        Article(sku="PERFIL", descricao="", comp_m=2.47, larg_m=0.05, alt_m=0.04,
                peso_kg=1.0, stock_max_un=5, pecas_por_carro=1,
                geometria="Comprida", empilhamento=Empilhamento.EMPILHAVEL),
        Article(sku="VOLUME", descricao="", comp_m=2.26, larg_m=1.37, alt_m=1.38,
                peso_kg=1.0, stock_max_un=5, pecas_por_carro=1,
                geometria="Comprida", empilhamento=Empilhamento.EMPILHAVEL),
    ]
    av = boxing.avisos_cantilever(arts, cfg)
    assert len(av) == 1 and "VOLUME" in av[0] and "PERFIL" not in av[0]


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
    artigos = [("P", 2.26, 1.37, 1.38, 84.4, 4),      # não empilha (1.38 alto)
               ("Q", 1.75, 1.37, 0.17, 100.0, 4)]     # empilha 4
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
    artigos = [("P", 1.0, 1.0, 1.8, 10.0, 30)]
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
    artigos = [("A", 1.75, 1.37, 0.17, 215.8, 4)]
    a, det = area.area_oversize_chao(artigos, 1.8, 1000.0, 0.0)
    assert det[0]["por_pilha"] == 4 and det[0]["limitado_por"] == "peso"
    assert det[0]["pilhas"] == 1
    assert abs(a - 1.75 * 1.37) < 0.01
    # o acesso soma-se por cima
    a45, _ = area.area_oversize_chao(artigos, 1.8, 1000.0, 0.45)
    assert abs(a45 - a * 1.45) < 0.02


def test_oversize_ao_chao_peca_alta_nao_empilha():
    """Peça de 1.37 m de altura com pilha máx. de 1.8 m: só 1 por pilha, logo
    cada unidade ocupa o seu lugar no chão."""
    artigos = [("B", 2.26, 1.37, 1.38, 84.4, 7)]
    a, det = area.area_oversize_chao(artigos, 1.8, 1000.0, 0.0)
    assert det[0]["por_pilha"] == 1 and det[0]["pilhas"] == 7
    assert abs(a - 7 * 2.26 * 1.38) < 0.05


def test_oversize_ao_chao_ignora_sem_stock_ou_sem_geometria():
    artigos = [("Z", 1.0, 1.0, 0.5, 10.0, 0),      # sem stock
               ("Y", 0.0, 1.0, 0.5, 10.0, 4)]      # sem geometria
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
    pl = RackType("Prateleira Leve", larg_m=1.83, prof_m=0.6, alt_nivel_m=0.7, niveis=3)
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
    pl = RackType("Prateleira Leve", larg_m=1.83, prof_m=0.6, alt_nivel_m=0.7, niveis=3)
    assert racking.blocos_empilhados(pl, 2.5, {"prateleira": 2}) == 1   # 2.5/2.1 = 1
    # sem altura definida continua a ser 1 módulo, com ou sem teto
    assert racking.blocos_empilhados(pl, 0.0, {"prateleira": 2}) == 1


def test_sem_limite_mantem_comportamento():
    pl = RackType("Prateleira Leve", larg_m=1.83, prof_m=0.6, alt_nivel_m=0.7, niveis=3)
    for lim in (None, {}):
        assert racking.blocos_empilhados(pl, 10.0, lim) == 4


def test_limite_modulos_case_insensitive():
    r = RackType("PRATELEIRA LEVE 1.83", larg_m=1.83, prof_m=0.6, alt_nivel_m=0.7, niveis=3)
    assert racking.blocos_empilhados(r, 10.0, {"Prateleira": 2}) == 2


# ------------------------------------------------ OVERSIZE em rack wide-span
# Peças que não assentam na palete ficam POUSADAS no nível de uma wide-span
# (bulk rack), sem palete nem caixa -- como o LONGO na cantilever.

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
    """Peça fina (0.17 m) e pesada (215.8 kg): por altura empilharia 9 em 1.55 m
    úteis, mas 1000 kg/nível só permitem 4. É o peso que manda."""
    ws = _ws()
    sem_peso = racking.rack_capacity_oversize(ws, _art("B", 1.75, 1.37, 0.17, 215.8),
                                             peso_max_nivel_kg=0.0, folga_altura_m=0.05)
    com_peso = racking.rack_capacity_oversize(ws, _art("B", 1.75, 1.37, 0.17, 215.8),
                                             peso_max_nivel_kg=1000.0, folga_altura_m=0.05)
    assert sem_peso > com_peso
    assert com_peso == 4 * 4          # 4 por nível x 4 níveis


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


if __name__ == "__main__":
    test_longo_existe(); test_footprint(); test_floor_area()
    test_zona_area(); test_resumo_area_total()
    test_picking_nivel_pelo_topo(); test_sem_modulo_fantasma_picking()
    test_pode_deitar(); test_orientacao_livre_deita_peca()
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

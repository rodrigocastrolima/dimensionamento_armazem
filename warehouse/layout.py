"""Passo 3b — Layout físico: dispõe as PosicaoChao alocadas (Passo 2) em filas
duplas costas-com-costas, com corredores de serviço reais entre filas.

Por omissão não há limite externo: escolhe o nº de posições por fiada que
aproxima um retângulo compacto (minimiza o perímetro do envolvente), tipologia
a tipologia, e empilha as tipologias umas atrás das outras com um corredor de
transição entre elas.

Com `Config.largura_max_m` a largura passa a ser IMPOSTA (as filas enchem até lá
e o bloco cresce em profundidade) e `profundidade_max_m` é um teto de aviso. O nº
de racks continua a vir do stock: o envelope é restrição de forma, não de
capacidade -- se não couber, avisa-se (WarehouseLayout.avisos) em vez de deixar
stock de fora.

Unidades: metros / m².
"""
from __future__ import annotations
import math

from .models import (RackType, PosicaoChao, PosicaoLayout, WarehouseLayout,
                     Zona, ZonaLayout, Corredor, PilhaChao)
from .area import rack_footprint_m2

CORREDOR_DEFAULT_M = 2.5         # usado quando RackType.corredor_m == 0
CORREDOR_TRANSICAO_M = 3.0       # entre o bloco de uma tipologia e a seguinte
GAP_ZONAS_M = 1.0                # espaçamento entre zonas funcionais, na sua fila
GAP_PILHAS_M = 0.3               # folga entre pilhas de peças no chão (manuseio)


def _corredor_m(rack: RackType) -> float:
    return rack.corredor_m if rack.corredor_m > 0 else CORREDOR_DEFAULT_M


def _melhor_num_por_fiada(n: int, larg_m: float, prof_dupla_m: float, corredor_m: float) -> int:
    """Escolhe quantas posições por fiada (frente OU trás) minimizam o
    perímetro do retângulo envolvente da tipologia, testando todas as
    divisões possíveis de n posições em filas duplas de até 2x esse nº."""
    melhor_k, melhor_perimetro = 1, float("inf")
    for k in range(1, n + 1):
        num_filas_duplas = math.ceil(n / (2 * k))
        largura = k * larg_m
        profundidade = num_filas_duplas * prof_dupla_m + max(num_filas_duplas - 1, 0) * corredor_m
        perimetro = 2 * (largura + profundidade)
        if perimetro < melhor_perimetro:
            melhor_perimetro, melhor_k = perimetro, k
    return melhor_k


def _layout_tipologia(rack: RackType, posicoes: list[PosicaoChao],
                      y0: float, k: int | None = None
                      ) -> tuple[list[PosicaoLayout], float, float, list[Corredor]]:
    """Dispõe as posições de UMA tipologia em filas duplas costas-com-costas,
    a partir de y0. k = posições por fiada; se None, otimiza só para esta
    tipologia (perímetro próprio). Devolve (layout, largura_ocupada_m,
    profundidade_ocupada_m, corredores)."""
    n = len(posicoes)
    if n == 0:
        return [], 0.0, 0.0, []

    larg, prof = rack.larg_m, rack.prof_m
    corredor = _corredor_m(rack)
    prof_dupla = 2 * prof

    if k is None:
        k = _melhor_num_por_fiada(n, larg, prof_dupla, corredor)

    resultado: list[PosicaoLayout] = []
    corredores: list[Corredor] = []
    idx = 0
    fila_idx = 0
    y_fila = y0
    largura_max = 0.0
    while idx < n:
        cols_fila = min(k, n - idx)   # largura realmente usada nesta fila dupla
        for lado, y_lado in (("frente", y_fila), ("tras", y_fila + prof)):
            for col in range(k):
                if idx >= n:
                    break
                pos = posicoes[idx]
                x = col * larg
                resultado.append(PosicaoLayout(
                    posicao=pos, x_m=round(x, 4), y_m=round(y_lado, 4),
                    largura_m=larg, profundidade_m=prof,
                    fila_idx=fila_idx, lado=lado,
                ))
                idx += 1
        largura_max = max(largura_max, cols_fila * larg)
        # corredor de serviço logo a seguir a esta fila dupla (exceto o último,
        # removido abaixo). Cobre a largura realmente usada pela fila.
        corredores.append(Corredor(
            x_m=0.0, y_m=round(y_fila + prof_dupla, 4),
            comp_m=round(cols_fila * larg, 4), larg_m=round(corredor, 4)))
        y_fila += prof_dupla + corredor
        fila_idx += 1

    corredores.pop()   # o último corredor (após a última fila) não existe
    profundidade_total = y_fila - corredor - y0  # remove o último corredor não usado
    return resultado, largura_max, profundidade_total, corredores


def _layout_zonas(zonas: list[Zona], y0: float) -> list[ZonaLayout]:
    """Dispõe as zonas funcionais em prateleiras (shelf packing): tamanhos
    variáveis, por isso não dá para replicar o truque de "k por fiada" dos
    racks (todos iguais). Ordena por profundidade decrescente e vai enchendo
    uma fiada até à largura-alvo (~raiz da área total), quebrando para a
    fiada seguinte -- aproxima um retângulo compacto em vez de uma tira única.
    Só dá coordenadas para não sobrepor o bloco de racks na cena 3D -- a área
    das zonas é reportada à parte (area.resumo_area_total), não entra em
    largura_total_m/profundidade_total_m/area_total_m2."""
    if not zonas:
        return []

    area_total = sum(z.area_m2 for z in zonas)
    largura_alvo = max(math.sqrt(area_total), max(z.comp_m for z in zonas))
    ordenadas = sorted(zonas, key=lambda z: z.larg_m, reverse=True)

    resultado = []
    x, y, altura_fiada = 0.0, y0, 0.0
    for zona in ordenadas:
        if x > 0 and x + zona.comp_m > largura_alvo:
            y += altura_fiada + GAP_ZONAS_M
            x, altura_fiada = 0.0, 0.0
        resultado.append(ZonaLayout(
            zona=zona, x_m=round(x, 4), y_m=round(y, 4),
            largura_m=zona.comp_m, profundidade_m=zona.larg_m,
        ))
        x += zona.comp_m + GAP_ZONAS_M
        altura_fiada = max(altura_fiada, zona.larg_m)
    return resultado


def _ks_com_teto(por_tipologia: dict[str, list[PosicaoChao]],
                 rack_types: dict[str, RackType],
                 largura_max_m: float) -> dict[str, int]:
    """k (posições por fiada) por tipologia respeitando um TETO de largura.

    É um teto, não um alvo: parte-se sempre da forma que minimiza a área
    (_larguras_alinhadas) e só se REDUZ o k das tipologias que excedam o limite.
    Dar um teto acima do que o layout escolheria não muda nada -- que é o que
    «largura máxima» tem de significar.

    (Existiu aqui uma versão que enchia as filas ATÉ à largura dada. Com um teto
    de 15 m e prateleiras de 1.83 m metia 8 posições = 14.64 m mesmo quando o
    ótimo livre eram 4 = 7.32 m, ou seja PIORAVA a área ao "respeitar" o limite.
    Um teto que agrava o resultado quando não é violado não é um teto.)"""
    ks = dict(_larguras_alinhadas(por_tipologia, rack_types))
    for nome, lista in por_tipologia.items():
        if not lista:
            continue
        larg = rack_types[nome].larg_m
        if larg <= 0:
            continue
        cabem = int(largura_max_m / larg + 1e-9)
        # cabem == 0 -> nem uma posição cabe no teto: fica 1 e quem chama avisa
        ks[nome] = max(min(ks.get(nome, 1), cabem), 1)
    return ks


def _layout_pilhas_chao(artigos_stock: list[tuple], altura_max_m: float,
                        peso_max_kg: float, x0: float, y0: float,
                        largura_alvo_m: float = 0.0,
                        corredor_m: float = CORREDOR_DEFAULT_M
                        ) -> tuple[list[PilhaChao], list[Corredor]]:
    """Dá coordenadas às pilhas de peças volumosas no chão (block stacking).

    Mesma lógica de contagem que `area.area_oversize_chao` (peça deitada, empilha
    peças do mesmo SKU, vale o menor de altura/peso) -- aqui só se acrescenta a
    posição, para o 3D poder desenhar o que a área já contabiliza.

    Arranjo: fiadas da esquerda para a direita até `largura_alvo_m`, quebrando
    para a fiada seguinte -- como `_layout_zonas`, porque as pilhas também têm
    tamanhos diferentes e não dá para usar o truque do "k por fiada" dos racks.
    **A cada 2 fiadas abre um corredor de serviço**, como nas filas duplas
    costas-com-costas dos racks: sem isso o bloco fica maciço e não há acesso às
    pilhas interiores.

    Devolve (pilhas, corredores) -- os corredores entram na lista do layout para
    a área ser medida e os operadores poderem circular lá.
    """
    if not artigos_stock:
        return [], []

    unidades: list[tuple] = []           # (sku, comp, larg, alt_peca, n, peso)
    for sku, comp_m, larg_m, alt_m, peso_kg, n_un in artigos_stock:
        if n_un <= 0 or min(comp_m, larg_m, alt_m) <= 0:
            continue
        cotas = sorted([comp_m, larg_m, alt_m], reverse=True)
        alt_peca = cotas[2]
        por_altura = max(int(altura_max_m / alt_peca), 1) if altura_max_m > 0 else 1
        por_peso = (max(int(peso_max_kg / peso_kg), 1)
                    if peso_max_kg > 0 and peso_kg > 0 else por_altura)
        por_pilha = max(min(por_altura, por_peso), 1)
        restantes = n_un
        while restantes > 0:
            nesta = min(por_pilha, restantes)
            unidades.append((sku, cotas[0], cotas[1], alt_peca, nesta, nesta * peso_kg))
            restantes -= nesta

    if not unidades:
        return [], []

    if largura_alvo_m <= 0:
        area = sum(c * l for _, c, l, _, _, _ in unidades)
        largura_alvo_m = max(math.sqrt(area), max(c for _, c, _, _, _, _ in unidades))

    # pilhas mais fundas primeiro: fiadas mais regulares (mesmo critério das zonas)
    unidades.sort(key=lambda u: u[2], reverse=True)

    resultado: list[PilhaChao] = []
    corredores: list[Corredor] = []
    x, y, prof_fiada = x0, y0, 0.0
    fiada_idx = 0
    fiada_ini = y0

    def _fecha_fiada(y_atual: float, prof: float) -> float:
        """Avança para a fiada seguinte. A cada 2 fiadas abre um CORREDOR, como
        nos racks: sem isto o bloco de pilhas ficava maciço (só 0.30 m entre
        fiadas) e não havia como ir buscar as peças do meio."""
        nonlocal fiada_idx
        fiada_idx += 1
        if fiada_idx % 2 == 0:
            # par de fiadas completo -> corredor de serviço a seguir
            corredores.append(Corredor(
                x_m=round(x0, 4), y_m=round(y_atual + prof, 4),
                comp_m=round(largura_alvo_m, 4),
                larg_m=round(corredor_m, 4)))
            return y_atual + prof + corredor_m
        return y_atual + prof + GAP_PILHAS_M

    for sku, comp, larg, alt_peca, n, peso in unidades:
        if x > x0 and (x - x0) + comp > largura_alvo_m:
            y = _fecha_fiada(y, prof_fiada)
            x, prof_fiada = x0, 0.0
        resultado.append(PilhaChao(
            sku=sku, descricao="", x_m=round(x, 4), y_m=round(y, 4),
            comp_m=round(comp, 4), larg_m=round(larg, 4),
            alt_peca_m=round(alt_peca, 4), n_pecas=n, peso_kg=round(peso, 1)))
        x += comp + GAP_PILHAS_M
        prof_fiada = max(prof_fiada, larg)
    return resultado, corredores


def _larguras_alinhadas(por_tipologia: dict[str, list[PosicaoChao]],
                        rack_types: dict[str, RackType]) -> dict[str, int]:
    """Escolhe k (posições por fiada) por tipologia de modo a que TODAS as
    tipologias encham filas até uma largura-alvo comum — em vez de cada tipologia
    otimizar o seu próprio retângulo (que deixa cantos vazios ao lado das
    tipologias mais pequenas).

    Critério: **PERÍMETRO mínimo** (bloco mais compacto, próximo do quadrado),
    com a área como desempate.

    Porque não área mínima: a área do envolvente premeia blocos absurdamente
    alongados. Numa base de dados real dava 5.49 x 48.20 m (264.6 m², razão 9:1)
    contra 13.50 x 23.80 m (321.3 m², razão 1.8:1) — a "melhor" forma era uma
    tira de meio metro a mais de largura que um corredor. E a área menor é em
    parte um artefacto: menos filas paralelas = menos corredores transversais
    contados no envolvente, ou seja premeia-se ter MENOS acessos.

    O perímetro mínimo aproxima a planta de um edifício real e encurta as filas,
    o que é percurso de picking a menos. Custa área: nos dados acima, +21%.
    Quem quiser a forma alongada impõe-na com Config.largura_max_m.

    Testa como largura-alvo todos os múltiplos k x larg_m de cada tipologia."""
    tips = [(rack_types[nome], lista) for nome, lista in por_tipologia.items() if lista]
    if not tips:
        return {}

    def _profundidade(rack: RackType, n: int, k: int) -> float:
        filas = math.ceil(n / (2 * k))
        return filas * 2 * rack.prof_m + max(filas - 1, 0) * _corredor_m(rack)

    candidatos = sorted({k * rack.larg_m for rack, lista in tips
                         for k in range(1, len(lista) + 1)})
    melhor_ks: dict[str, int] = {}
    melhor_perimetro, melhor_area = float("inf"), float("inf")

    transicoes = sum(max(_corredor_m(tips[i][0]), _corredor_m(tips[i + 1][0]))
                     for i in range(len(tips) - 1))
    for alvo in candidatos:
        ks = {rack.nome: max(int(alvo / rack.larg_m + 1e-9), 1) for rack, _ in tips}
        largura = max(min(ks[rack.nome], len(lista)) * rack.larg_m for rack, lista in tips)
        profundidade = (sum(_profundidade(rack, len(lista), ks[rack.nome]) for rack, lista in tips)
                        + transicoes)
        area_env = largura * profundidade
        perimetro = 2 * (largura + profundidade)
        if (perimetro < melhor_perimetro - 1e-9
                or (abs(perimetro - melhor_perimetro) <= 1e-9 and area_env < melhor_area)):
            melhor_perimetro, melhor_area, melhor_ks = perimetro, area_env, ks

    return melhor_ks


def gerar_layout(posicoes: list[PosicaoChao],
                 rack_types: dict[str, RackType],
                 zonas: list[Zona] | None = None,
                 largura_max_m: float = 0.0,
                 profundidade_max_m: float = 0.0,
                 oversize_chao: list[tuple] | None = None,
                 chao_altura_max_m: float = 1.8,
                 chao_peso_max_kg: float = 1000.0) -> WarehouseLayout:
    """Gera o layout físico completo: zonas funcionais primeiro (entrada do
    armazém, longe dos racks), depois uma tipologia de rack a seguir à outra
    (eixo Y), cada uma com as suas filas duplas costas-com-costas, separadas
    por um corredor de transição -- entre o bloco de zonas e o de racks, e
    entre tipologias diferentes.

    largura_max_m > 0 é um TETO da largura do bloco: parte-se da forma que
    minimiza a área e só se reduz o nº de posições por fiada das tipologias que
    excedam o limite. Um teto acima do ótimo livre não muda nada.
    profundidade_max_m > 0 é um teto de AVISO: o nº de racks vem do stock, por
    isso a profundidade é consequência e não pode ser truncada sem deixar stock
    de fora. Ambas dizem respeito só a racks + corredores, não às zonas.
    """
    por_tipologia: dict[str, list[PosicaoChao]] = {}
    for pos in posicoes:
        por_tipologia.setdefault(pos.rack_nome, []).append(pos)

    zonas_layout = _layout_zonas(zonas, 0.0) if zonas else []
    y0_racks = 0.0
    if zonas_layout:
        profundidade_zonas = max(zl.y_m + zl.profundidade_m for zl in zonas_layout)
        y0_racks = profundidade_zonas + CORREDOR_TRANSICAO_M

    avisos: list[str] = []
    if largura_max_m > 0:
        # tipologias cuja posição isolada já não cabe na largura pedida: nem com
        # k=1 se respeita o limite, e não se descartam posições -> avisar.
        for nome, lista in por_tipologia.items():
            larg = rack_types[nome].larg_m
            if lista and larg > largura_max_m + 1e-9:
                avisos.append(
                    f"largura maxima {largura_max_m:.2f} m e menor que uma posicao de "
                    f"'{nome}' ({larg:.2f} m): o bloco vai exceder a largura pedida")
        ks_por_tipologia = _ks_com_teto(por_tipologia, rack_types, largura_max_m)
    else:
        ks_por_tipologia = _larguras_alinhadas(por_tipologia, rack_types)

    todas: list[PosicaoLayout] = []
    corredores: list[Corredor] = []
    y_cursor = y0_racks
    largura_total = 0.0
    area_racks = 0.0

    anterior: RackType | None = None
    for rack_nome, lista in por_tipologia.items():
        rack = rack_types[rack_nome]
        if anterior is not None:
            # corredor entre blocos de tipologias: serve os dois lados, logo
            # usa o MAIOR dos corredores das duas (não um valor fixo)
            trans = max(_corredor_m(anterior), _corredor_m(rack))
            corredores.append(Corredor(x_m=0.0, y_m=round(y_cursor, 4),
                                       comp_m=round(largura_total, 4),
                                       larg_m=round(trans, 4)))
            y_cursor += trans
        layout_tipo, largura, profundidade, cors = _layout_tipologia(
            rack, lista, y_cursor, ks_por_tipologia.get(rack_nome))
        todas.extend(layout_tipo)
        corredores.extend(cors)
        largura_total = max(largura_total, largura)
        y_cursor += profundidade
        area_racks += rack_footprint_m2(rack) * len(lista)
        anterior = rack

    # Pilhas no chão como MAIS UMA fiada do bloco: alinhadas à mesma largura e
    # separadas por um corredor de transição, igual a qualquer tipologia. Assim o
    # bloco fica um só retângulo contínuo (um bloco no canvas, não dois) e a área
    # das pilhas entra em largura/profundidade_total_m como a dos racks.
    pilhas: list[PilhaChao] = []
    if oversize_chao:
        if anterior is not None:
            trans = max(_corredor_m(anterior), CORREDOR_TRANSICAO_M)
            corredores.append(Corredor(x_m=0.0, y_m=round(y_cursor, 4),
                                       comp_m=round(largura_total, 4),
                                       larg_m=round(trans, 4)))
            y_cursor += trans
        # corredor das pilhas: as peças aqui são movidas por empilhador/porta-
        # paletes (são volumosas), por isso usa-se a largura de corredor da
        # tipologia mais exigente do bloco, não a de picking manual.
        corredor_pilhas = max((_corredor_m(rack_types[n]) for n in por_tipologia),
                              default=CORREDOR_DEFAULT_M)
        pilhas, cors_pilhas = _layout_pilhas_chao(
            oversize_chao, chao_altura_max_m, chao_peso_max_kg,
            x0=0.0, y0=y_cursor,
            largura_alvo_m=largura_total if largura_total > 0 else 0.0,
            corredor_m=corredor_pilhas)
        if pilhas:
            corredores.extend(cors_pilhas)
            largura_total = max(largura_total,
                                max(p.x_m + p.comp_m for p in pilhas))
            y_cursor = max(p.y_m + p.larg_m for p in pilhas)
            # o footprint das pilhas conta como área ocupada (não é corredor)
            area_racks += sum(p.comp_m * p.larg_m for p in pilhas)

    profundidade_total = max(y_cursor - y0_racks, 0.0)
    area_envolvente = largura_total * profundidade_total
    area_corredores = max(area_envolvente - area_racks, 0.0)

    # Verificação do envelope contra o resultado REAL (não contra a intenção):
    # a largura pode passar por uma posição mais larga que o limite, e a
    # profundidade é livre por construção.
    if largura_max_m > 0 and largura_total > largura_max_m + 1e-6:
        avisos.append(
            f"largura do bloco {largura_total:.2f} m excede o maximo de "
            f"{largura_max_m:.2f} m (+{largura_total - largura_max_m:.2f} m)")
    if profundidade_max_m > 0 and profundidade_total > profundidade_max_m + 1e-6:
        excesso = profundidade_total - profundidade_max_m
        avisos.append(
            f"profundidade do bloco {profundidade_total:.2f} m excede o maximo de "
            f"{profundidade_max_m:.2f} m (+{excesso:.2f} m, "
            f"~{excesso * largura_total:.0f} m2 a mais): o stock exige mais racks do "
            f"que caberia -- alargar o armazem, empilhar mais alto ou reduzir stock")

    return WarehouseLayout(
        posicoes=todas,
        largura_total_m=round(largura_total, 3),
        profundidade_total_m=round(profundidade_total, 3),
        area_total_m2=round(area_envolvente, 3),
        area_corredores_m2=round(area_corredores, 3),
        zonas=zonas_layout,
        corredores=corredores,
        avisos=avisos,
        pilhas_chao=pilhas,
    )

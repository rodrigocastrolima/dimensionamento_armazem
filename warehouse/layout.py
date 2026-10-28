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
import dataclasses
import math

from .models import (RackType, PosicaoChao, PosicaoLayout, WarehouseLayout,
                     Zona, ZonaLayout, Corredor, PilhaChao, EspacoLivre)
from .area import rack_footprint_m2, melhor_pilha_chao
# Racks fantasma (gerar_racks_fantasma) reaproveitam a MESMA abertura de
# blocos/níveis que uma posição real usaria -- nunca reimplementada aqui.
from .racking import _abrir_bloco, blocos_empilhados, niveis_efetivos

CORREDOR_DEFAULT_M = 2.5         # usado quando RackType.corredor_m == 0
CORREDOR_TRANSICAO_M = 3.0       # entre o bloco de uma tipologia e a seguinte
GAP_ZONAS_M = 1.0                # espaçamento entre zonas funcionais, na sua fila
GAP_PILHAS_M = 0.3               # folga entre pilhas de peças no chão (manuseio)


def _corredor_m(rack: RackType) -> float:
    return rack.corredor_m if rack.corredor_m > 0 else CORREDOR_DEFAULT_M


def _e_picking_manual(nome: str, chaves: tuple) -> bool:
    """True se a tipologia `nome` é servida por picking MANUAL (operador a
    pé) -- casa por SUBSTRING (case-insensitive), mesmo padrão de
    `racking.aceita_oversize`/`racking.limite_modulos`."""
    if not chaves:
        return False
    return any(k.lower() in (nome or "").lower() for k in chaves)


def _melhor_num_por_fiada(n: int, larg_m: float, prof_dupla_m: float, corredor_m: float,
                          k_max: int | None = None, par_incluido: bool = False) -> int:
    """Escolhe quantas posições por fiada (frente OU trás) minimizam o
    perímetro do retângulo PRÓPRIO da tipologia (perímetro mínimo, área como
    desempate), testando todas as divisões possíveis de n posições em filas
    duplas de até 2x esse nº (ou só 1x, `par_incluido`, nas tipologias
    sintéticas emparelhadas -- só têm um lado ("único"), não frente+trás; sem
    esta distinção uma tipologia emparelhada era subcontada a metade das
    fiadas que precisa, escolhendo sempre um k pequeno demais). `k_max`, se
    dado, é um TETO (ex.: largura máxima do bloco) -- nunca aumenta o k livre,
    só o pode reduzir."""
    limite = n if k_max is None else max(min(n, k_max), 1)
    divisor = 1 if par_incluido else 2
    melhor_k, melhor_perimetro, melhor_area = 1, float("inf"), float("inf")
    for k in range(1, limite + 1):
        num_filas_duplas = math.ceil(n / (divisor * k))
        largura = k * larg_m
        profundidade = num_filas_duplas * prof_dupla_m + max(num_filas_duplas - 1, 0) * corredor_m
        perimetro = 2 * (largura + profundidade)
        area = largura * profundidade
        if (perimetro < melhor_perimetro - 1e-9
                or (abs(perimetro - melhor_perimetro) <= 1e-9 and area < melhor_area)):
            melhor_perimetro, melhor_area, melhor_k = perimetro, area, k
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

    larg, prof = rack.larg_total_m, rack.prof_m   # larg_total = vão + pilar
    corredor = _corredor_m(rack)
    # Tipologias sintéticas de par emparelhado (racking.com_par_oversize) já
    # TÊM prof_m = profundidade do par -- não se emparelham de novo, senão a
    # profundidade dobrava outra vez. Cada posição ocupa uma fiada única.
    par_incluido = rack.profundidade_inclui_par
    prof_dupla = prof if par_incluido else 2 * prof

    if k is None:
        k = _melhor_num_por_fiada(n, larg, prof_dupla, corredor, par_incluido=par_incluido)

    resultado: list[PosicaoLayout] = []
    corredores: list[Corredor] = []
    idx = 0
    fila_idx = 0
    y_fila = y0
    largura_max = 0.0
    while idx < n:
        cols_fila = min(k, n - idx)   # largura realmente usada nesta fila dupla
        lados = (("unico", y_fila),) if par_incluido else (("frente", y_fila), ("tras", y_fila + prof))
        lados_ocupados = 0
        for lado, y_lado in lados:
            ocupou = False
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
                ocupou = True
            if ocupou:
                lados_ocupados += 1
        largura_max = max(largura_max, cols_fila * larg)
        # profundidade REAL desta fila dupla: "frente" enche sempre primeiro
        # (idx<n garante-o), por isso só "trás" pode ficar por preencher -- na
        # última fila dupla de uma tipologia, quando o que resta cabe todo em
        # "frente". Reservar prof_dupla (2x) nesse caso desenhava uma fiada
        # inteira sem racks lá dentro -- nem ocupada nem marcada como espaço
        # livre (_espacos_livres_por_fiada só vê fila_idx/lado que TÊM posições
        # em `todas`), só chão perdido em branco atrás do bloco.
        prof_fila = prof if par_incluido else prof * lados_ocupados
        # corredor de serviço logo a seguir a esta fila dupla (exceto o último,
        # removido abaixo). Cobre a largura realmente usada pela fila.
        corredores.append(Corredor(
            x_m=0.0, y_m=round(y_fila + prof_fila, 4),
            comp_m=round(cols_fila * larg, 4), larg_m=round(corredor, 4)))
        y_fila += prof_fila + corredor
        fila_idx += 1

    corredores.pop()   # o último corredor (após a última fila) não existe
    profundidade_total = y_fila - corredor - y0  # remove o último corredor não usado
    return resultado, largura_max, profundidade_total, corredores


def _decompor_em_fiadas(rack: RackType, posicoes_locais: list[PosicaoLayout],
                        offset_fila_idx: int = 0) -> list[dict]:
    """Agrupa posições já dispostas (_layout_tipologia) pelas suas fiadas
    (fila_idx), rebasando cada grupo para y=0 local -- vira a lista de itens
    que o shelf packing (_empacotar_tipologias) depois arruma e traduz.
    `offset_fila_idx` evita colisão quando se juntam fiadas de chamadas
    diferentes a _layout_tipologia (cada uma reinicia fila_idx em 0)."""
    por_fiada: dict[int, list[PosicaoLayout]] = {}
    for pl in posicoes_locais:
        por_fiada.setdefault(pl.fila_idx, []).append(pl)

    itens = []
    for fila_idx in sorted(por_fiada):
        grupo = por_fiada[fila_idx]
        y_base = min(pl.y_m for pl in grupo)
        largura_fiada = max(pl.x_m + pl.largura_m for pl in grupo)
        profundidade_fiada = max(pl.y_m + pl.profundidade_m for pl in grupo) - y_base
        rebasadas = [dataclasses.replace(pl, y_m=round(pl.y_m - y_base, 4),
                                        fila_idx=fila_idx + offset_fila_idx) for pl in grupo]
        itens.append({
            "nome": rack.nome, "largura": largura_fiada,
            "profundidade": profundidade_fiada, "corredor_transicao": _corredor_m(rack),
            "posicoes": rebasadas, "corredores": [],
        })
    return itens


def _fiadas_tipologia_k(rack: RackType, posicoes: list[PosicaoChao], k: int) -> list[dict]:
    """Como _fiadas_tipologia, mas com k IMPOSTO em vez de escolhido -- usado
    só para gerar decomposições ALTERNATIVAS (ver _candidatos_decomposicao):
    uma tipologia mais fragmentada (k pequeno) é quase sempre pior sozinha,
    mas os seus pedaços mais pequenos podem encaixar em buracos que a forma
    perímetro-mínimo não encaixava."""
    if not posicoes:
        return []
    locais, _, _, _ = _layout_tipologia(rack, posicoes, 0.0, k)
    return _decompor_em_fiadas(rack, locais)


def _candidatos_decomposicao(rack: RackType, posicoes: list[PosicaoChao],
                             largura_max_m: float = 0.0) -> list[list[dict]]:
    """Decomposições candidatas para UMA tipologia: a melhor sozinha
    (_fiadas_tipologia) mais duas formas extremas -- k=1 (máxima
    fragmentação: cada fiada fica com o menor pedaço possível) e k=n (uma
    única fila, sem par) -- capadas por `largura_max_m` se dado. Usadas por
    _ajustar_decomposicoes para testar se "pior sozinha" encaixa melhor ao
    lado de outra tipologia."""
    n = len(posicoes)
    if n == 0:
        return []
    k_max = None
    if largura_max_m > 0 and rack.larg_total_m > 0:
        k_max = max(int(largura_max_m / rack.larg_total_m + 1e-9), 1)
    candidatos_k = {1, n}
    if k_max is not None:
        candidatos_k = {k for k in candidatos_k if k <= k_max}
        candidatos_k.add(k_max)

    decomposicoes = [_fiadas_tipologia(rack, posicoes, largura_max_m)]
    for k in sorted(candidatos_k):
        decomposicoes.append(_fiadas_tipologia_k(rack, posicoes, k))
    return decomposicoes


def _ajustar_decomposicoes(por_tipologia: dict[str, list[PosicaoChao]],
                           rack_types: dict[str, RackType],
                           largura_max_m: float = 0.0) -> list[dict]:
    """Ajuste fino ENTRE tipologias: cada uma começa na sua forma
    perímetro-mínimo sozinha (`_fiadas_tipologia`), mas testa-se também trocar
    por uma forma mais fragmentada (`_candidatos_decomposicao`) se isso
    reduzir o perímetro GLOBAL do bloco (todas as tipologias juntas) -- às
    vezes uma tipologia "pior sozinha" encaixa nos buracos deixadas por
    outra e o conjunto fica mais compacto do que cada uma a otimizar-se de
    costas voltadas para as restantes.

    Busca gulosa: troca uma tipologia de cada vez, mantendo as outras na
    melhor combinação já encontrada; só aceita a troca se reduzir o
    perímetro. Poucas tipologias e poucos candidatos por tipologia (3) tornam
    isto barato -- não é uma otimização conjunta exaustiva (isso seria
    combinatório), mas apanha os casos onde só UMA tipologia precisa de mudar
    de forma para encaixar nas outras."""
    nomes = [nome for nome, lista in por_tipologia.items() if lista]
    if not nomes:
        return []
    candidatos = {nome: _candidatos_decomposicao(rack_types[nome], por_tipologia[nome], largura_max_m)
                 for nome in nomes}
    escolha = {nome: candidatos[nome][0] for nome in nomes}

    def _perimetro(escolha_atual: dict[str, list[dict]]) -> float:
        chunks = [item for nome in nomes for item in escolha_atual[nome]]
        alvo = _largura_alvo_shelf(chunks, largura_max_m)
        largura, profundidade = _medir_empacotamento(chunks, alvo)
        return 2 * (largura + profundidade)

    melhor_perimetro = _perimetro(escolha)
    for _ in range(2):   # 2ª passagem apanha melhorias que só aparecem depois da 1ª troca
        mudou = False
        for nome in nomes:
            anterior = escolha[nome]
            for opcao in candidatos[nome]:
                if opcao is anterior:
                    continue
                escolha[nome] = opcao
                p = _perimetro(escolha)
                if p < melhor_perimetro - 1e-9:
                    melhor_perimetro, anterior, mudou = p, opcao, True
                else:
                    escolha[nome] = anterior
        if not mudou:
            break

    return [item for nome in nomes for item in escolha[nome]]


def _fiadas_tipologia(rack: RackType, posicoes: list[PosicaoChao],
                      largura_max_m: float = 0.0) -> list[dict]:
    """Decompõe UMA tipologia nas suas fiadas duplas (uma por fiada -- frente
    + trás -- não o retângulo da tipologia inteira): é isto que o shelf
    packing (_empacotar_tipologias) depois arruma, o que permite misturar
    tipologias DIFERENTES fiada a fiada (não só tipologia a tipologia inteira
    com a outra).

    O k (posições por fiada) das fiadas CHEIAS é o perímetro-mínimo da
    tipologia INTEIRA de sempre (_melhor_num_por_fiada, capado por
    `largura_max_m` se dado). A fiada FINAL -- quando sobra menos que uma
    fiada cheia (2k, ou k nas tipologias emparelhadas) -- escolhe o SEU
    PRÓPRIO k, otimizado só para esse resto: herdar o k da maioria distorce
    essa sobra (ex.: restarem 5 posições com k=4 dá sempre "4 à frente, 1
    atrás" -- sozinhas, 5 posições preferem outra forma, nalguns casos até
    "tudo numa só fila, sem par" quando isso reduz mais o perímetro do que
    manter as 2 filas). Nunca piora: é só mais uma forma testada para essa
    sobra, nunca imposta às fiadas cheias."""
    n = len(posicoes)
    if n == 0:
        return []
    larg, prof = rack.larg_total_m, rack.prof_m   # larg_total = vão + pilar
    corredor = _corredor_m(rack)
    par_incluido = rack.profundidade_inclui_par
    prof_dupla = prof if par_incluido else 2 * prof

    k_max = None
    if largura_max_m > 0 and larg > 0:
        k_max = max(int(largura_max_m / larg + 1e-9), 1)
    k = _melhor_num_por_fiada(n, larg, prof_dupla, corredor, k_max, par_incluido)

    por_fiada_cheia = k if par_incluido else 2 * k
    num_cheias = n // por_fiada_cheia
    resto = n - num_cheias * por_fiada_cheia

    itens: list[dict] = []
    if num_cheias > 0:
        locais, _, _, _ = _layout_tipologia(rack, posicoes[:num_cheias * por_fiada_cheia], 0.0, k)
        itens.extend(_decompor_em_fiadas(rack, locais))
    if resto > 0:
        k_resto = _melhor_num_por_fiada(resto, larg, prof_dupla, corredor, k_max, par_incluido)
        locais_resto, _, _, _ = _layout_tipologia(
            rack, posicoes[num_cheias * por_fiada_cheia:], 0.0, k_resto)
        itens.extend(_decompor_em_fiadas(rack, locais_resto, offset_fila_idx=num_cheias))
    return itens


def _empacotar_por_shelf(chunks: list[dict], largura_alvo: float) -> list[list[dict]]:
    """Agrupa as fiadas de todas as tipologias (ver _fiadas_tipologia) em
    'prateleiras': ordena por profundidade decrescente e vai enchendo uma
    prateleira até à largura-alvo, quebrando para a seguinte -- mesma ideia
    de _layout_zonas (shelf packing), mas aqui cada item já traz a sua
    sub-estrutura (uma fiada dupla) pronta, só a traduzir depois. É isto que
    permite tipologias DIFERENTES partilharem a mesma banda horizontal em vez
    de cada uma ocupar sempre uma banda inteira só sua."""
    # First-Fit Decreasing Height (FFDH): por profundidade decrescente, cada
    # item tenta a PRIMEIRA prateleira já aberta onde ainda caiba (não só a
    # última) -- sem isto, uma prateleira que abre com um item largo e fecha
    # sem encher a largura-alvo nunca mais recebia nada, mesmo que um item
    # mais pequeno (de outra tipologia, mais à frente na lista) coubesse
    # perfeitamente na sobra: a mistura só acontece quando a largura-alvo dá
    # mesmo para tal.
    ordenados = sorted(chunks, key=lambda c: c["profundidade"], reverse=True)
    prateleiras: list[list[dict]] = []
    largura_usada: list[float] = []
    for chunk in ordenados:
        for i, usada in enumerate(largura_usada):
            if usada + chunk["largura"] <= largura_alvo + 1e-6:
                prateleiras[i].append(chunk)
                largura_usada[i] += chunk["largura"]
                break
        else:
            prateleiras.append([chunk])
            largura_usada.append(chunk["largura"])
    return prateleiras


def _medir_empacotamento(chunks: list[dict], largura_alvo: float) -> tuple[float, float]:
    """(largura, profundidade) do envolvente para uma largura-alvo candidata --
    usado só na busca de _largura_alvo_shelf. A transição entre prateleiras
    usa aqui o MAIOR corredor entre TODAS (aproximação: a busca só precisa de
    comparar candidatos); a materialização final em _empacotar_tipologias
    recalcula a transição exata por par de prateleiras adjacentes."""
    prateleiras = _empacotar_por_shelf(chunks, largura_alvo)
    largura = max(sum(c["largura"] for c in p) for p in prateleiras)
    profundidade = sum(max(c["profundidade"] for c in p) for p in prateleiras)
    if len(prateleiras) > 1:
        maior_corredor = max(c["corredor_transicao"] for grupo in prateleiras for c in grupo)
        profundidade += (len(prateleiras) - 1) * maior_corredor
    return largura, profundidade


def _largura_alvo_shelf(chunks: list[dict], largura_max_m: float = 0.0) -> float:
    """Largura-alvo comum às prateleiras (shelf packing) do bloco: PERÍMETRO
    mínimo do envolvente resultante (mesma filosofia de perímetro mínimo já
    usada por tipologia em _melhor_num_por_fiada), agora simulando o
    empacotamento em prateleiras de várias tipologias em vez de alinhar cada
    uma sozinha a uma largura comum.

    Testa como candidatos TODAS as somas de subconjuntos das larguras das
    fiadas (limiares onde uma prateleira passa a caber, ou não, mais uma
    fiada de outra tipologia) -- não só as somas cumulativas por profundidade
    decrescente, que perdem combinações como "1 fiada larga + 1 fiada
    estreita" quando não são adjacentes nessa ordem. Nº de fiadas é sempre
    pequeno (poucas tipologias x poucas fiadas cada), por isso 2^n é barato;
    acima de 16 cai para as somas cumulativas (aproximação, evita explosão
    combinatória em casos extremos).

    `largura_max_m` é um TETO: nunca agrava o resultado livre, só o reduz se
    o livre o exceder (mesma regra já validada para largura_max_m)."""
    if not chunks:
        return 0.0
    larguras_individuais = [c["largura"] for c in chunks]
    if len(chunks) <= 16:
        candidatos = {0.0}
        for larg in larguras_individuais:
            candidatos |= {s + larg for s in candidatos}
        candidatos.discard(0.0)
    else:
        candidatos = set(larguras_individuais)
        ordenados = sorted(chunks, key=lambda c: c["profundidade"], reverse=True)
        acumulado = 0.0
        for c in ordenados:
            acumulado += c["largura"]
            candidatos.add(acumulado)

    melhor_alvo = max(larguras_individuais)
    melhor_perimetro, melhor_area = float("inf"), float("inf")
    for alvo in sorted(candidatos):
        largura, profundidade = _medir_empacotamento(chunks, alvo)
        perimetro = 2 * (largura + profundidade)
        area = largura * profundidade
        if (perimetro < melhor_perimetro - 1e-9
                or (abs(perimetro - melhor_perimetro) <= 1e-9 and area < melhor_area)):
            melhor_perimetro, melhor_area, melhor_alvo = perimetro, area, alvo

    if largura_max_m > 0:
        melhor_alvo = min(melhor_alvo, largura_max_m)
    return melhor_alvo


def _bloco_tipologia_unico(rack: RackType, posicoes: list[PosicaoChao],
                           largura_max_m: float = 0.0) -> dict | None:
    """Como `_fiadas_tipologia`, mas devolve UM SÓ chunk em vez de uma lista:
    as fiadas já decompostas (cada uma otimizada tal como sempre, incluindo o
    resto com o seu próprio k) ficam empilhadas DENTRO dele, com os
    corredores entre elas preservados. Serve para o shelf packing global
    (`_empacotar_por_shelf`) nunca poder espalhar esta tipologia por
    prateleiras diferentes (ver Config.picking_manual_racks) -- fica sempre
    um retângulo contíguo, mesmo que esse retângulo DEPOIS partilhe
    prateleira com outra tipologia (isso continua livre)."""
    fiadas = _fiadas_tipologia(rack, posicoes, largura_max_m)
    if not fiadas:
        return None
    corredor = _corredor_m(rack)
    largura = max(f["largura"] for f in fiadas)
    posicoes_bloco: list[PosicaoLayout] = []
    corredores_bloco: list[Corredor] = []
    y = 0.0
    for i, f in enumerate(fiadas):
        for pl in f["posicoes"]:
            posicoes_bloco.append(dataclasses.replace(pl, y_m=round(pl.y_m + y, 4)))
        for c in f["corredores"]:
            corredores_bloco.append(dataclasses.replace(c, y_m=round(c.y_m + y, 4)))
        y += f["profundidade"]
        if i < len(fiadas) - 1:
            corredores_bloco.append(Corredor(x_m=0.0, y_m=round(y, 4),
                                             comp_m=round(largura, 4), larg_m=round(corredor, 4)))
            y += corredor
    return {"nome": rack.nome, "largura": largura, "profundidade": y,
           "corredor_transicao": corredor, "posicoes": posicoes_bloco, "corredores": corredores_bloco}


def _empacotar_tipologias(chunks: list[dict], largura_alvo: float, y0: float
                          ) -> tuple[list[PosicaoLayout], list[Corredor], list[EspacoLivre], float, float]:
    """Materializa o shelf packing: traduz cada fiada (já otimizada sozinha,
    ver _fiadas_tipologia) para a sua posição na prateleira que lhe calhou,
    com um corredor de transição entre prateleiras consecutivas (o MAIOR
    corredor entre as duas, mesmo critério que já se usava entre tipologias).
    Devolve (posicoes, corredores, espacos_livres, largura_total,
    profundidade_total)."""
    if not chunks:
        return [], [], [], 0.0, 0.0

    prateleiras = _empacotar_por_shelf(chunks, largura_alvo)

    resultado: list[PosicaoLayout] = []
    corredores: list[Corredor] = []
    espacos: list[EspacoLivre] = []
    largura_total = 0.0
    y_cursor = y0

    for i, grupo in enumerate(prateleiras):
        x = 0.0
        prof_shelf = max(c["profundidade"] for c in grupo)
        for chunk in grupo:
            for pl in chunk["posicoes"]:
                resultado.append(dataclasses.replace(
                    pl, x_m=round(pl.x_m + x, 4), y_m=round(pl.y_m + y_cursor, 4)))
            for c in chunk["corredores"]:
                corredores.append(dataclasses.replace(
                    c, x_m=round(c.x_m + x, 4), y_m=round(c.y_m + y_cursor, 4)))
            x += chunk["largura"]
        largura_total = max(largura_total, x)

        # espaço livre à direita da última fiada desta prateleira, ao longo
        # de toda a sua altura -- atribuído à tipologia que a fecha, para
        # gerar_racks_fantasma a poder estender para aí (fila_idx negativo =
        # sintético, marca "resto da prateleira", não uma fiada real).
        livre = largura_alvo - x
        if livre > 1e-6:
            ultimo = grupo[-1]
            espacos.append(EspacoLivre(
                rack_nome=ultimo["nome"], fila_idx=-(i + 1), lado="prateleira",
                x_m=round(x, 4), y_m=round(y_cursor, 4),
                comp_m=round(livre, 4), larg_m=round(prof_shelf, 4)))

        y_cursor += prof_shelf
        if i < len(prateleiras) - 1:
            corredor_atual = max(c["corredor_transicao"] for c in grupo)
            corredor_prox = max(c["corredor_transicao"] for c in prateleiras[i + 1])
            trans = max(corredor_atual, corredor_prox)
            corredores.append(Corredor(x_m=0.0, y_m=round(y_cursor, 4),
                                       comp_m=round(largura_total, 4), larg_m=round(trans, 4)))
            y_cursor += trans

    profundidade_total = y_cursor - y0
    return resultado, corredores, espacos, largura_total, profundidade_total


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


def _layout_pilhas_chao(artigos_stock: list[tuple], altura_max_m: float,
                        peso_max_kg: float, x0: float, y0: float,
                        largura_alvo_m: float = 0.0,
                        corredor_m: float = CORREDOR_DEFAULT_M
                        ) -> tuple[list[PilhaChao], list[Corredor]]:
    """Dá coordenadas às pilhas de peças volumosas no chão (block stacking).

    Mesma lógica de contagem que `area.area_oversize_chao` (via o mesmo
    `area.melhor_pilha_chao`: orientação da peça, empilha peças do mesmo SKU,
    vale o menor de altura/peso) -- aqui só se acrescenta a posição, para o 3D
    poder desenhar o que a área já contabiliza.

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
    for sku, comp_m, larg_m, alt_m, peso_kg, n_un, orientacao in artigos_stock:
        if n_un <= 0 or min(comp_m, larg_m, alt_m) <= 0:
            continue
        escolha = melhor_pilha_chao(comp_m, larg_m, alt_m, orientacao, n_un,
                                    altura_max_m, peso_max_kg, peso_kg)
        if escolha is None:
            continue
        plano_a, plano_b, alt_peca, por_pilha, _pilhas, _limitado_por = escolha
        restantes = n_un
        while restantes > 0:
            nesta = min(por_pilha, restantes)
            unidades.append((sku, plano_a, plano_b, alt_peca, nesta, nesta * peso_kg))
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


def gerar_racks_fantasma(espacos_livres: list[EspacoLivre],
                         rack_types: dict[str, RackType],
                         altura_armazem_m: float = 0.0,
                         max_modulos: dict | None = None,
                         altura_picking_m: float = 0.0,
                         empilhamento_por_nivel: tuple | None = None) -> list[PosicaoLayout]:
    """Racks fantasma (preview -- Passo 4): uma posição de chão VAZIA por
    cada slot inteiro que cabe num EspacoLivre, à capacidade física máxima
    (mesmos módulos/níveis que uma posição REAL dessa tipologia teria, via
    racking._abrir_bloco/blocos_empilhados -- reaproveitados sem alteração,
    não reimplementados). Usa sempre a MESMA tipologia do espaço que
    estende -- mantém a disposição em fiadas ("geometria linear"), nunca
    inventa uma tipologia diferente para aproveitar melhor o resto.

    Sem caixas lá dentro: o Passo 4 seguinte (algoritmo de alocação extra)
    é que decide o que lá pôr. Devolve uma lista à parte -- NUNCA deve ser
    juntada a WarehouseLayout.posicoes nem contar para floor_area/
    resumo_area_total (é só um preview, tal como pilhas_chao/
    area_oversize_chao já tratam a armazenagem ao chão à parte)."""
    fantasma: list[PosicaoLayout] = []
    for espaco in espacos_livres:
        rack = rack_types.get(espaco.rack_nome)
        if rack is None or rack.larg_total_m <= 0:
            continue
        k_extra = int(espaco.comp_m / rack.larg_total_m + 1e-9)
        if k_extra <= 0:
            continue
        max_blocos = blocos_empilhados(rack, altura_armazem_m, max_modulos, empilhamento_por_nivel)
        niveis_ov = niveis_efetivos(rack, altura_armazem_m, empilhamento_por_nivel)
        for i in range(k_extra):
            pos = PosicaoChao(rack_nome=rack.nome, indice=i)
            for b in range(max_blocos):
                pos.blocos.append(_abrir_bloco(rack, b, altura_picking_m, niveis_ov))
            fantasma.append(PosicaoLayout(
                posicao=pos,
                x_m=round(espaco.x_m + i * rack.larg_total_m, 4),
                y_m=round(espaco.y_m, 4),
                largura_m=rack.larg_total_m, profundidade_m=espaco.larg_m,
                fila_idx=espaco.fila_idx, lado=espaco.lado,
            ))
    return fantasma


def gerar_fila_manual(rack: RackType, n: int, dupla: bool,
                      altura_armazem_m: float = 0.0,
                      max_modulos: dict | None = None,
                      pool_posicoes_reais: list | None = None,
                      empilhamento_por_nivel: tuple | None = None) -> list[PosicaoLayout]:
    """Fila de racks colocada à mão pelo utilizador no canvas 2D -- "sandbox"
    de capacidade/espaço, para desenhar uma planta antes (ou em vez) de
    deixar o Passo 2 decidir sozinho o nº e a posição dos racks.

    `pool_posicoes_reais` (opcional): lista de `PosicaoChao` JÁ ALOCADAS pelo
    Passo 2 real (`racking.allocate_to_racks`) para ESTA MESMA tipologia --
    quando dada, cada slot desta fila CONSOME uma posição da lista (em vez de
    abrir uma vazia), reaproveitando os seus blocos/níveis/caixas tal como
    o packer 2D os colocou (a geometria de uma caixa é local à posição, não
    precisa de ajuste ao mudar de sítio). É como "emprestar" o conteúdo real
    calculado a partir do stock para a posição física que o utilizador
    escolheu à mão -- sem isto, a fila fica sempre vazia (sem SKU nenhum),
    porque o nº/local dos racks já não vem do Passo 2 aqui. Esgotada a lista
    (mais slots do que posições reais sobra), os slots a mais ficam vazios
    como antes -- é só capacidade extra desenhada, não falta nada.

    Devolve as posições no referencial LOCAL do próprio bloco (origem em
    (0, 0), sem rotação): fica a cargo do chamador (web.py) colocá-las na
    posição/rotação que o bloco tem no canvas -- mesmo padrão do bloco de
    racks principal (rack_by_name + _roda_rect), generalizado a qualquer
    bloco colocado manualmente.

    dupla=True: duas fiadas costas-com-costas (frente + trás), como
    layout._layout_tipologia já faz para as tipologias normais. dupla=False:
    uma única fiada."""
    max_blocos = blocos_empilhados(rack, altura_armazem_m, max_modulos, empilhamento_por_nivel)
    niveis_ov = niveis_efetivos(rack, altura_armazem_m, empilhamento_por_nivel)
    lados = (("frente", 0.0), ("tras", rack.prof_m)) if dupla else (("unico", 0.0),)
    pool = pool_posicoes_reais if pool_posicoes_reais is not None else []
    resultado: list[PosicaoLayout] = []
    for lado, y_lado in lados:
        for i in range(max(n, 0)):
            if pool:
                pos = dataclasses.replace(pool.pop(0), indice=i)
            else:
                pos = PosicaoChao(rack_nome=rack.nome, indice=i)
                for b in range(max_blocos):
                    pos.blocos.append(_abrir_bloco(rack, b, 0.0, niveis_ov))
            resultado.append(PosicaoLayout(
                posicao=pos,
                x_m=round(i * rack.larg_total_m, 4), y_m=round(y_lado, 4),
                largura_m=rack.larg_total_m, profundidade_m=rack.prof_m,
                fila_idx=0, lado=lado,
            ))
    return resultado


def gerar_layout(posicoes: list[PosicaoChao],
                 rack_types: dict[str, RackType],
                 zonas: list[Zona] | None = None,
                 largura_max_m: float = 0.0,
                 profundidade_max_m: float = 0.0,
                 oversize_chao: list[tuple] | None = None,
                 chao_altura_max_m: float = 1.8,
                 chao_peso_max_kg: float = 1000.0,
                 picking_manual_racks: tuple = ("prateleira",)) -> WarehouseLayout:
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

    picking_manual_racks (ver Config.picking_manual_racks): tipologias cujo
    nome contenha uma destas chaves entram no shelf packing como UM SÓ chunk
    atómico (_bloco_tipologia_unico) em vez de fatiadas em fiadas soltas --
    continuam livres para partilhar prateleira com outra tipologia, só nunca
    ficam com as suas próprias posições espalhadas por prateleiras diferentes.
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

    # Cada tipologia decompõe-se nas suas fiadas duplas, cada uma otimizada
    # para o perímetro mínimo da tipologia INTEIRA (capado por largura_max_m
    # se dado), e só depois essas fiadas (de QUALQUER tipologia) são
    # arrumadas em prateleiras (shelf packing, _empacotar_tipologias): uma
    # fiada mais estreita já não força uma banda inteira vazia ao lado -- a
    # fiada de OUTRA tipologia (ou o resto solto da mesma) pode partilhar essa
    # banda. `_ajustar_decomposicoes` vai além disso: testa se alguma
    # tipologia, ao aceitar uma forma pior SOZINHA (mais fragmentada), reduz
    # o perímetro GLOBAL do bloco por encaixar nos buracos deixados pelas
    # outras -- nunca piora (a forma perímetro-mínimo sozinha é sempre uma
    # das opções testadas).
    #
    # Tipologias de picking MANUAL (Config.picking_manual_racks) ficam de
    # fora deste "fatiar em fiadas soltas": entram já como UM SÓ chunk
    # atómico (_bloco_tipologia_unico), nunca fragmentável pelo shelf
    # packing -- continuam livres para partilhar prateleira com outra
    # tipologia (isso é decidido pelo mesmo perímetro-mínimo de sempre), só
    # nunca ficam com as suas próprias posições espalhadas por prateleiras
    # diferentes (o que aconteceria se a última fiada, mais rasa, ficasse
    # "presa" a uma prateleira de outra tipologia mais funda enquanto o
    # resto ficava noutra).
    por_tipologia_resto: dict[str, list[PosicaoChao]] = {}
    chunks: list[dict] = []
    for nome, lista in por_tipologia.items():
        if _e_picking_manual(nome, picking_manual_racks):
            bloco = _bloco_tipologia_unico(rack_types[nome], lista, largura_max_m)
            if bloco:
                chunks.append(bloco)
        else:
            por_tipologia_resto[nome] = lista
    chunks += _ajustar_decomposicoes(por_tipologia_resto, rack_types, largura_max_m)

    largura_alvo = _largura_alvo_shelf(chunks, largura_max_m)
    todas, corredores, espacos_livres, largura_total, profundidade_racks = _empacotar_tipologias(
        chunks, largura_alvo, y0_racks)
    y_cursor = y0_racks + profundidade_racks
    area_racks = sum(rack_footprint_m2(rack_types[nome]) * len(lista)
                     for nome, lista in por_tipologia.items())
    # corredor de transição para as pilhas ao chão (ver abaixo): o maior
    # corredor entre as tipologias que ficaram na ÚLTIMA prateleira do bloco.
    ultimo_corredor = max((c["corredor_transicao"] for c in chunks), default=CORREDOR_TRANSICAO_M)

    # Pilhas no chão como MAIS UMA fiada do bloco: alinhadas à mesma largura e
    # separadas por um corredor de transição, igual a qualquer tipologia. Assim o
    # bloco fica um só retângulo contínuo (um bloco no canvas, não dois) e a área
    # das pilhas entra em largura/profundidade_total_m como a dos racks.
    pilhas: list[PilhaChao] = []
    if oversize_chao:
        if chunks:
            trans = max(ultimo_corredor, CORREDOR_TRANSICAO_M)
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
        espacos_livres=espacos_livres,
        zonas=zonas_layout,
        corredores=corredores,
        avisos=avisos,
        pilhas_chao=pilhas,
    )

"""Passo 1 — Repacking: escolher unidade de carga e contar por artigo.

IN : artigos (geometria + stock máximo), catálogo de unidades (S/M/L + PALLET).
OUT: BoxAssignment por SKU (tipo de carga, peças por unidade, nº de unidades).

Regra de encaminhamento: best-fit entre S/M/L/MEIA/PALLET -- escolhe a caixa
que minimiza o nº de unidades de carga necessárias para o stock máximo. O que
não assenta em nenhuma delas (nem na footprint da palete) vai a OVERSIZE.
"""
from __future__ import annotations
import dataclasses
import math

from .models import (Article, BoxType, LoadType, Empilhamento, BoxAssignment,
                     BoxJustificacao, AutonomiaResultado)
from .config import Config

# Caixas normais, por ordem crescente de tamanho (usada para desempate)
_CAIXAS = [LoadType.XS, LoadType.S, LoadType.M, LoadType.L]

# Tipos de carga que recebem a tolerância lateral de Config.
# pallet_tolerancia_lateral_m no encaixe do plano (ver _pecas_no_envelope).
_TIPOS_COM_TOLERANCIA_PALETE = {LoadType.PALLET, LoadType.MEIA}


def pieces_in_stack(altura_disponivel: float, altura_artigo: float,
                    empilhamento: Empilhamento) -> int:
    """Quantas peças cabem empilhadas RIGIDAMENTE na vertical (sem aninhar).

    NAO_EMPILHAVEL: 1 peça de altura, nunca mais -- não se pode pôr nada em
    cima. EMPILHAVEL: empilha inteiro, sem encaixar -- n = floor(altura_
    disponivel / altura_artigo).

    Só se usa quando o eixo vertical NÃO é o eixo de aninhagem do artigo
    (`Article.eixo_aninhagem`, ver `pecas_no_eixo`) -- se for, usa-se a
    fórmula de aninhagem em vez desta, mesmo que o artigo esteja marcado
    NAO_EMPILHAVEL: aninhar é uma propriedade geométrica FIXA da peça (a
    peça encaixa na anterior nessa cota específica), independente da
    classificação de empilhamento rígido, que só descreve o que acontece
    quando NÃO se está a usar essa cota (ver CLAUDE.md "separar aninhável
    de empilhável")."""
    if altura_artigo <= 0 or altura_artigo > altura_disponivel:
        return 0
    if empilhamento == Empilhamento.NAO_EMPILHAVEL:
        return 1
    return max(math.floor(altura_disponivel / altura_artigo), 1)


def pecas_no_eixo(disponivel: float, dimensao: float, aninhavel: bool,
                  fator_empilhamento: float) -> int:
    """Quantas peças cabem lado a lado num eixo QUALQUER -- vertical ou do
    plano, a mesma fórmula serve para os dois (`_pecas_no_envelope` decide
    qual usar em cada eixo).

    `aninhavel` diz se ESTE eixo específico é o eixo de aninhagem do artigo
    (`Article.eixo_aninhagem`): uma propriedade geométrica FIXA da peça (que
    cota física encaixa na anterior), não da orientação escolhida pelo
    packer -- a mesma peça pode ter esse eixo na vertical numa orientação e
    no plano noutra (ex.: peça "tombada"), mas nunca em mais do que 1 eixo
    ao mesmo tempo.

    aninhavel=False: floor(disponivel/dimensao), encaixe rígido simples.
    aninhavel=True: 1ª peça ocupa a dimensão inteira, cada peça extra
        acrescenta só fator_empilhamento x dimensão (encaixa na anterior):
        total(n) = dimensão x (1 + (n-1) x fator_empilhamento).
        Devolve sempre o MAIOR dos dois (`max`) -- o `max` protege contra um
        fator mal configurado (>= 1) que tornaria a fórmula de aninhagem pior
        do que o simples floor().
    """
    if dimensao <= 0 or dimensao > disponivel:
        return 0
    n_rigido = math.floor(disponivel / dimensao)
    if not aninhavel or fator_empilhamento <= 0:
        return n_rigido
    n_aninhado = math.floor((disponivel / dimensao - 1) / fator_empilhamento) + 1
    return max(n_rigido, n_aninhado)


# Mapeamento partilhado 'x'/'y'/'z' -> índice em `dims = (comp_m, larg_m,
# alt_m)` (mesma ordem de `_NOMES_EIXO`), usado por DUAS colunas independentes
# do master data:
#   ALTURA_OVERRIDE (Article.altura_override): força QUAL cota fica vertical
#     (Z) nas unidades de carga. Vazio = livre, testam-se as 3. Substitui a
#     antiga Orientação_permitida (Livre/Base fixa/Não deitar, binário).
#   EIXO_ANINHAGEM (Article.eixo_aninhagem): diz QUAL cota tem a geometria de
#     encaixe (aninha na peça anterior nessa direção especificamente). Vazio
#     = não aninha. Independente do ALTURA_OVERRIDE -- essa cota pode calhar
#     vertical ou no plano, consoante a orientação escolhida (ver
#     `_pecas_no_envelope`).
_EIXO_POR_LETRA = {"x": 0, "y": 1, "z": 2}


def eixo_forcado(valor: str) -> int | None:
    """Índice do eixo (0=Comprimento, 1=Largura, 2=Altura) que `valor`
    ('x'/'y'/'z') designa, ou None se vazio/não reconhecido. Usada tanto para
    ALTURA_OVERRIDE (`eixos_possiveis`) como para EIXO_ANINHAGEM."""
    return _EIXO_POR_LETRA.get((valor or "").strip().lower())


def eixos_possiveis(altura_override: str) -> range | tuple[int, ...]:
    """Eixos (índices em `dims`/`_NOMES_EIXO`) a testar como vertical: as 3
    escolhas se ALTURA_OVERRIDE estiver vazio/não reconhecido, ou só o eixo
    forçado ('x'/'y'/'z') caso contrário."""
    eixo = eixo_forcado(altura_override)
    return range(3) if eixo is None else (eixo,)


def _plano_e_camadas(article: Article, box: BoxType, iv: int,
                     dims: tuple[float, float, float], fator_empilhamento: float,
                     fill_factor: float, tolerancia_lateral_m: float = 0.0) -> tuple[int, int]:
    """(peças por plano, nº máximo de camadas) no envelope 3D com `dims[iv]`
    como cota vertical e as outras duas no plano. (0, 0) se não couber.

    Por eixo (vertical OU do plano, o mesmo teste para os 3): se for o eixo
    de aninhagem do artigo (`Article.eixo_aninhagem`), usa `pecas_no_eixo`
    com aninhavel=True; senão, rígido -- `pieces_in_stack` (NAO_EMPILHAVEL/
    EMPILHAVEL) no vertical, `pecas_no_eixo` com aninhavel=False no plano.
    O eixo de aninhagem é uma propriedade FIXA da peça, pode calhar no
    vertical nesta orientação (iv) e no plano noutra -- nunca mais do que 1
    eixo ao mesmo tempo (ver CLAUDE.md "separar aninhável de empilhável").

    fill_factor desconta o floor() geométrico do plano (folgas de manuseio/
    forma, encaixe imperfeito) — mín. 1 peça. tolerancia_lateral_m (só
    PALLET/MEIA, ver Config.pallet_tolerancia_lateral_m): soma-se aos DOIS
    lados de CADA cota do plano -- nunca à altura."""
    Dc = box.int_comp_m + 2 * tolerancia_lateral_m
    Dl = box.int_larg_m + 2 * tolerancia_lateral_m
    Da = box.int_alt_m
    eixo_a = eixo_forcado(article.eixo_aninhagem)
    ja, jb = (j for j in range(3) if j != iv)
    vert, a, b = dims[iv], dims[ja], dims[jb]

    n_altura = (pecas_no_eixo(Da, vert, True, fator_empilhamento) if iv == eixo_a
               else pieces_in_stack(Da, vert, article.empilhamento))
    if n_altura == 0:
        return 0, 0

    an_a, an_b = ja == eixo_a, jb == eixo_a
    op1 = (pecas_no_eixo(Dc, a, an_a, fator_empilhamento) *
           pecas_no_eixo(Dl, b, an_b, fator_empilhamento)) if a <= Dc and b <= Dl else 0
    op2 = (pecas_no_eixo(Dc, b, an_b, fator_empilhamento) *
           pecas_no_eixo(Dl, a, an_a, fator_empilhamento)) if b <= Dc and a <= Dl else 0
    por_plano_teorico = max(op1, op2)
    if por_plano_teorico == 0:
        return 0, 0
    return max(math.floor(por_plano_teorico * fill_factor), 1), n_altura


def _pecas_no_envelope(article: Article, box: BoxType, iv: int,
                       dims: tuple[float, float, float], fator_empilhamento: float,
                       fill_factor: float, tolerancia_lateral_m: float = 0.0) -> int:
    """Peças no envelope 3D com `dims[iv]` como cota vertical (plano x
    camadas, ver `_plano_e_camadas`)."""
    por_plano, n_altura = _plano_e_camadas(article, box, iv, dims, fator_empilhamento,
                                           fill_factor, tolerancia_lateral_m)
    return por_plano * n_altura


def pieces_per_box(article: Article, box: BoxType,
                   fator_empilhamento: float = 0.3,
                   fill_factor: float = 1.0,
                   tolerancia_palete_m: float = 0.0) -> int:
    """Quantas unidades do artigo cabem numa unidade de carga (envelope 3D).

    Orientação do artigo (coluna ALTURA_OVERRIDE): 'x'/'y'/'z' força essa cota
    como vertical; vazio testa as 3 escolhas e fica com a que mais peças rende
    (ver `eixos_possiveis`). Em cada escolha: empilhamento/aninhagem por eixo
    (ver `_pecas_no_envelope`) x envelope 2D no plano (2 orientações).
    fill_factor desconta o encaixe geométrico perfeito (folgas de manuseio/
    forma, encaixe-de-gume ao deitar peças finas), aplicado a TODAS as classes.
    tolerancia_palete_m: só entra em PALLET/MEIA (ver
    Config.pallet_tolerancia_lateral_m), ignorada nas outras caixas.
    Corta pelo peso máximo da caixa.
    """
    tol = tolerancia_palete_m if box.nome in _TIPOS_COM_TOLERANCIA_PALETE else 0.0
    dims = (article.comp_m, article.larg_m, article.alt_m)
    por_geometria = max(
        _pecas_no_envelope(article, box, iv, dims, fator_empilhamento, fill_factor, tol)
        for iv in eixos_possiveis(article.altura_override)
    )

    if por_geometria == 0:
        return 0

    if article.peso_kg > 0:
        por_peso = math.floor(box.peso_max_kg / article.peso_kg)
    else:
        por_peso = por_geometria  # peso desconhecido: não cortar

    return min(por_geometria, por_peso)


def altura_real_unidade(article: Article, box: BoxType, pecas: int, cfg: Config) -> float:
    """Altura REAL de uma unidade de carga com `pecas` peças lá dentro: deck
    (`ext_alt_m - int_alt_m`) + altura da carga. Usada para PALLET/MEIA, cuja
    altura nominal do catálogo (`ext_alt_m`) é a de uma palete CHEIA até ao
    topo -- uma palete com pouca carga cabe num nível mais baixo.

    Mesma geometria de `pieces_per_box` (`_plano_e_camadas`, com
    fill_factor_plano e a tolerância lateral da palete). Orientação: a que
    leva mais peças no total (corte por peso incluído); em empate, a que dá a
    carga mais baixa. Camadas = ceil(peças / peças_por_plano), no máximo as
    que cabem no envelope. Altura da carga = camadas x cota vertical, ou, se a
    cota vertical for o eixo de aninhagem, cota x (1 + (camadas - 1) x
    fator_empilhamento) -- o menor dos dois, como o `max` de `pecas_no_eixo`.
    Sem encaixe possível (não devia acontecer numa unidade já escolhida)
    devolve a altura nominal."""
    tol = cfg.pallet_tolerancia_lateral_m if box.nome in _TIPOS_COM_TOLERANCIA_PALETE else 0.0
    deck = max(box.ext_alt_m - box.int_alt_m, 0.0)
    dims = (article.comp_m, article.larg_m, article.alt_m)
    eixo_a = eixo_forcado(article.eixo_aninhagem)
    por_peso = math.floor(box.peso_max_kg / article.peso_kg) if article.peso_kg > 0 else None

    melhor: tuple[tuple[int, float], float] | None = None   # ((-total, carga), carga)
    for iv in eixos_possiveis(article.altura_override):
        por_plano, max_camadas = _plano_e_camadas(article, box, iv, dims, cfg.fator_empilhamento,
                                                  cfg.fill_factor_plano, tol)
        if por_plano == 0:
            continue
        total = por_plano * max_camadas if por_peso is None else min(por_plano * max_camadas, por_peso)
        camadas = min(max(math.ceil(pecas / por_plano), 1), max_camadas)
        carga = camadas * dims[iv]
        if iv == eixo_a:
            carga = min(carga, dims[iv] * (1 + (camadas - 1) * cfg.fator_empilhamento))
        chave = (-total, carga)
        if melhor is None or chave < melhor[0]:
            melhor = (chave, carga)

    if melhor is None:
        return box.ext_alt_m
    return round(deck + melhor[1], 6)


def capacidade_manual(article: Article, load_type: LoadType) -> bool:
    """True se a capacidade de `load_type` para este artigo foi decidida à mão
    no master data (CAIXA_FORCADA + PECAS_CAIXA_FORCADA) -- nesse caso é usada
    tal e qual, sem as regras de capacidade prática (capacidade_reportada)."""
    return article.pecas_caixa_forcada > 0 and article.caixa_forcada == load_type.value


def capacidade_reportada(load_type: LoadType, pecas_por_unidade: int,
                         sku: str, cfg: Config, manual: bool = False) -> int:
    """Peças por caixa a REPORTAR nos resultados (capacidade prática).

    `pecas_por_unidade` é o máximo geométrico à ocupação definida
    (cfg.fill_factor_plano) e continua a ser o valor que dimensiona o armazém
    (nº de caixas -> racks -> área). Esta função só afeta a QUANTIDADE
    reportada dentro de cada caixa, aplicando limites de manuseio real:
    teto por tipo de caixa e arredondamento à centena inferior acima de 100
    peças (ver cfg.capacidade_*). Abaixo de 100 mantém o valor exato --
    arredondar daria 0, que não é uma capacidade.

    manual=True (ver `capacidade_manual`): a quantidade já é uma decisão de
    manuseio real, tomada no master data -- devolve-se sem teto nem
    arredondamento.
    """
    if manual:
        return max(int(pecas_por_unidade), 1)
    override = cfg.capacidade_override_sku.get(sku)
    if override is not None:
        return max(int(override), 1)

    n = pecas_por_unidade
    teto = cfg.capacidade_teto_por_caixa.get(load_type)
    if teto:
        n = min(n, int(teto))
    if load_type in cfg.capacidade_arredonda_centena and n >= 100:
        n = (n // 100) * 100
    return max(n, 1)


def stock_para_autonomia(article: Article, catalogo: list[BoxType],
                         cfg: Config) -> AutonomiaResultado:
    """Passo 0 — decide o stock a usar no dimensionamento (stock_max_un) a
    partir da autonomia mínima em carros (cfg.autonomia_minima_carros), em vez
    de o ler já decidido do master data.

    stock_teorico = ceil(autonomia_minima_carros x pecas_por_carro) -- peças
    para cobrir essa autonomia. Escolhe-se a unidade de carga para ESSE alvo
    (mesmo best-fit de choose_load) e arredonda-se para cima para encher por
    completo a capacidade PRÁTICA reportada (capacidade_reportada: teto por
    caixa + arredondamento à centena) -- não a capacidade geométrica bruta,
    que dava números impraticáveis de manusear. O resultado pode por isso
    exceder a autonomia mínima (não desperdiça a última caixa a meio).

    `cfg.stock_autonomia_override_sku` preserva ajustes manuais que não seguem
    a fórmula (decisão de negócio, prevalece)."""
    stock_teorico = math.ceil(cfg.autonomia_minima_carros * article.pecas_por_carro)
    override = cfg.stock_autonomia_override_sku.get(article.sku)

    if stock_teorico <= 0:
        stock_final = int(override) if override is not None else 0
        return AutonomiaResultado(
            sku=article.sku, stock_atual_un=article.stock_atual_un,
            stock_teorico_un=stock_teorico, load_type=None,
            pecas_por_unidade_pratico=0, num_unidades=0,
            stock_ajustado_un=stock_final, override_manual=override is not None,
        )

    alvo = dataclasses.replace(article, stock_max_un=stock_teorico)
    load_type, ppu_geometrico = choose_load(alvo, catalogo, cfg)
    ppu_pratico = capacidade_reportada(load_type, ppu_geometrico, article.sku, cfg,
                                       manual=capacidade_manual(article, load_type))
    num_unidades = math.ceil(stock_teorico / ppu_pratico) if ppu_pratico else 0
    stock_final = ppu_pratico * num_unidades if ppu_pratico else stock_teorico

    if override is not None:
        stock_final = int(override)

    return AutonomiaResultado(
        sku=article.sku, stock_atual_un=article.stock_atual_un,
        stock_teorico_un=stock_teorico, load_type=load_type,
        pecas_por_unidade_pratico=ppu_pratico, num_unidades=num_unidades,
        stock_ajustado_un=stock_final, override_manual=override is not None,
    )


def aplicar_stock_autonomia(articles: list[Article], catalogo: list[BoxType],
                            cfg: Config) -> tuple[list[Article], list[AutonomiaResultado]]:
    """Aplica `stock_para_autonomia` a todos os artigos: devolve cópias com
    `stock_max_un` já ajustado ao Passo 0 (autonomia mínima), mais o rasto de
    cálculo por SKU para reportar nos resultados (sheet Autonomia)."""
    resultados = [stock_para_autonomia(a, catalogo, cfg) for a in articles]
    idx = {r.sku: r for r in resultados}
    ajustados = [dataclasses.replace(a, stock_max_un=idx[a.sku].stock_ajustado_un)
                for a in articles]
    return ajustados, resultados


def choose_load(article: Article, catalogo: list[BoxType],
                cfg: Config) -> tuple[LoadType, int]:
    """Decide o tipo de carga e devolve (LoadType, peças_por_unidade).

    Best-fit entre S/M/L/MEIA/PALLET: testa todas as candidatas onde caiba pelo
    menos 1 peça e escolhe a que minimiza nº de unidades de carga para o
    stock máximo (ceil(stock_max_un / peças_por_unidade)). Em empate,
    fica com a caixa fisicamente mais pequena (S < M < L < MEIA < PALLET).
    PALLET/MEIA já incluem a tolerância lateral (Config.
    pallet_tolerancia_lateral_m) neste MESMO teste -- não há um fallback à
    parte: se não coube em nenhuma candidata (nem em PALLET/MEIA com
    tolerância), é OVERSIZE, sem exceção.

    `cfg.pallet_ultimo_recurso` (decisão de negócio por cliente, ver
    Config): quando True, a PALLET não compete no best-fit -- só é testada
    (já com a tolerância lateral) se NENHUMA caixa mais pequena couber. Troca
    chão por movimentabilidade (caixas menores são mais fáceis de mover à
    mão/porta-paletes do que uma palete cheia).

    Interceção ANTES do best-fit: SKUs em cfg.caixas_especiais têm um
    equipamento de transporte dedicado (footprint e capacidade FIXOS, não
    geometria) -- ver Config.caixas_especiais/models.CaixaEspecial. Nenhum
    cálculo geométrico é feito para estes SKUs.

    Depois, `cfg.unidade_da_linha` (SIA): o tipo de carga é o que a LINHA
    escolhe (`unidade_da_linha`), exceto SKUs que na linha vão em carrinho.

    A seguir, CAIXA_FORCADA do master data (ver `_caixa_forcada`): o tipo de
    carga vem da folha, não do best-fit.
    """
    especial = cfg.caixas_especiais.get(article.sku)
    if especial is not None:
        return LoadType.OVERSIZE2, especial.quantidades[article.sku]

    if cfg.unidade_da_linha and article.sku not in cfg.skus_carrinho_linha:
        return unidade_da_linha(article, catalogo, cfg)

    if article.caixa_forcada:
        return _caixa_forcada(article, catalogo, cfg)

    catalogo_idx = {b.nome: b for b in catalogo}
    # ordem crescente de tamanho: em empate no nº de unidades ganha a menor
    candidatas = _CAIXAS + [LoadType.MEIA]
    if not cfg.pallet_ultimo_recurso:
        candidatas = candidatas + [LoadType.PALLET]

    melhor: tuple[LoadType, int] | None = None
    melhor_num_unidades = None

    for load_type in candidatas:
        box = catalogo_idx.get(load_type)
        if box is None:
            continue
        ppu = pieces_per_box(article, box, cfg.fator_empilhamento, cfg.fill_factor_plano,
                             cfg.pallet_tolerancia_lateral_m)
        if ppu <= 0:
            continue
        num_unidades = math.ceil(article.stock_max_un / ppu)
        if melhor is None or num_unidades < melhor_num_unidades:
            melhor = (load_type, ppu)
            melhor_num_unidades = num_unidades

    if melhor is not None:
        return melhor

    # Não coube em nenhuma unidade de carga, incluindo PALLET/MEIA já com a
    # tolerância lateral -- OVERSIZE, sem "1/palete" inventado (ver docstring).
    #
    # Com pallet_ultimo_recurso a PALLET ficou de fora da competição: só aqui
    # é tentada (mesmo cálculo, com a tolerância lateral), para nunca perder
    # stock só porque a política do cliente evita paletes quando há alternativa.
    if cfg.pallet_ultimo_recurso:
        pallet = catalogo_idx.get(LoadType.PALLET)
        if pallet is not None:
            ppu = pieces_per_box(article, pallet, cfg.fator_empilhamento,
                                 cfg.fill_factor_plano, cfg.pallet_tolerancia_lateral_m)
            if ppu > 0:
                return LoadType.PALLET, ppu
    return LoadType.OVERSIZE, 1


def unidade_da_linha(article: Article, catalogo: list[BoxType],
                     cfg: Config) -> tuple[LoadType, int]:
    """(LoadType, peças_por_unidade) que o dimensionamento_linha escolhe para
    este SKU no bordo de linha (ver Config.unidade_da_linha) -- reproduz
    bordo_linha.calcular_bordo_linha: Passo 0 para
    cfg.autonomia_minima_carros_linha carros e Passo 1 (choose_load) sobre o
    stock resultante, com as regras da linha (sem pallet_ultimo_recurso, que
    a linha não tem). O mínimo kanban da linha só muda o nº de caixas, não o
    tipo, por isso não entra aqui.

    Peças por unidade = capacidade PRÁTICA (capacidade_reportada: teto por
    caixa, centena, override por SKU ou PECAS_CAIXA_FORCADA), a mesma que o
    bordo de linha usa -- é a quantidade com que o fornecedor entrega, por
    isso é também a que dimensiona o armazém (não o máximo geométrico).
    capacidade_reportada é idempotente, por isso o Passo 0 do armazém, que a
    volta a aplicar, não a altera."""
    cfg_linha = dataclasses.replace(
        cfg, unidade_da_linha=False, pallet_ultimo_recurso=False,
        autonomia_minima_carros=cfg.autonomia_minima_carros_linha)
    stock_linha = stock_para_autonomia(article, catalogo, cfg_linha).stock_ajustado_un
    load_type, ppu = choose_load(dataclasses.replace(article, stock_max_un=stock_linha),
                                 catalogo, cfg_linha)
    if load_type in (LoadType.OVERSIZE, LoadType.OVERSIZE2):
        return load_type, ppu
    return load_type, capacidade_reportada(load_type, ppu, article.sku, cfg,
                                           manual=capacidade_manual(article, load_type))


def _caixa_forcada(article: Article, catalogo: list[BoxType],
                   cfg: Config) -> tuple[LoadType, int]:
    """CAIXA_FORCADA (master data): exceção manual ao best-fit, para quando
    há uma razão que o motor não conhece (ex.: FHD103/104 não podem ir em
    palete e, na caixa L, só cabem inclinadas com a caixa aberta).

    Com PECAS_CAIXA_FORCADA, essa é a capacidade, tal e qual. Sem ela,
    conta-se pela geometria normal (pieces_per_box) dentro da caixa forçada.
    Erro claro, nunca best-fit em silêncio, se a caixa não existir no
    catálogo (o racking não a saberia colocar) ou se, sem quantidade, a
    geometria não lá meter nenhuma peça."""
    load_type = LoadType(article.caixa_forcada)
    box = next((b for b in catalogo if b.nome == load_type), None)
    if box is None:
        raise ValueError(f"SKU {article.sku}: CAIXA_FORCADA={load_type.value} não existe "
                         f"na folha «Caixas»")
    if article.pecas_caixa_forcada > 0:
        return load_type, article.pecas_caixa_forcada
    ppu = pieces_per_box(article, box, cfg.fator_empilhamento, cfg.fill_factor_plano,
                         cfg.pallet_tolerancia_lateral_m)
    if ppu <= 0:
        raise ValueError(f"SKU {article.sku}: CAIXA_FORCADA={load_type.value}, mas pela "
                         f"geometria não cabe nenhuma peça nessa caixa -- indicar "
                         f"PECAS_CAIXA_FORCADA (capacidade decidida à mão)")
    return load_type, ppu


def carrinhos_fisicos(assignments: list[BoxAssignment], cfg: Config) -> dict[str, int]:
    """Nº de carrinhos FÍSICOS de caixa especial (OVERSIZE2) a colocar, por SKU.

    Uma caixa especial (Número/ID da folha) é UM equipamento partilhado por
    vários SKUs, cada um com a sua quantidade POR carrinho. `choose_load`
    devolve por SKU `ceil(stock / quantidade)`, ou seja "quantos carrinhos
    precisava este SKU se andasse sozinho" -- somar isso pelos SKUs do mesmo
    carrinho conta o mesmo equipamento várias vezes (o side body LH e o RH
    vão juntos no MESMO carrinho). O nº real de carrinhos é o do SKU que pede
    mais: `max`. Fica atribuído ao 1º SKU do carrinho; os restantes ficam a 0
    (já estão dentro desses carrinhos). SKUs sem caixa especial não entram."""
    grupos: dict[int, list[BoxAssignment]] = {}
    for a in assignments:
        if a.load_type != LoadType.OVERSIZE2:
            continue
        especial = cfg.caixas_especiais.get(a.sku)
        if especial is not None:
            grupos.setdefault(id(especial), []).append(a)
    fisicos: dict[str, int] = {}
    for lista in grupos.values():
        total = max(a.num_unidades for a in lista)
        for i, a in enumerate(lista):
            fisicos[a.sku] = total if i == 0 else 0
    return fisicos


def plan_box(article: Article, catalogo: list[BoxType],
             cfg: Config) -> BoxAssignment:
    """Escolha de unidade de carga + contagem para o stock máximo (teto)."""
    load_type, ppu = choose_load(article, catalogo, cfg)
    num = math.ceil(article.stock_max_un / ppu) if ppu else 0
    return BoxAssignment(article.sku, load_type, ppu, num)


def plan_all(articles: list[Article], catalogo: list[BoxType],
             cfg: Config) -> list[BoxAssignment]:
    return [plan_box(a, catalogo, cfg) for a in articles]


_NOMES_EIXO = ("Comprimento", "Largura", "Altura")


def justificar_escolha(article: Article, catalogo: list[BoxType], cfg: Config,
                       assignment: BoxAssignment) -> BoxJustificacao:
    """PROVA de que `assignment` é a unidade de carga correta para `article`.

    Não decide nada (a decisão já foi tomada em `choose_load`/`plan_box`):
    reconstrói, para a escolha já feita, os números do cálculo geométrico
    (orientação vencedora, peças/plano, peças/altura, corte por peso) ou o
    critério de admissão (OVERSIZE), para dar rasto auditável a quem olha
    para a sheet 'Detalhe' e pergunta "porque é que está ali?"."""
    catalogo_idx = {b.nome: b for b in catalogo}
    load_type = assignment.load_type
    base = dict(
        sku=article.sku, descricao=article.descricao,
        comp_m=article.comp_m, larg_m=article.larg_m, alt_m=article.alt_m,
        peso_kg=article.peso_kg, load_type=load_type,
        pecas_por_unidade=assignment.pecas_por_unidade,
        stock_max_un=article.stock_max_un, num_unidades=assignment.num_unidades,
    )

    if load_type == LoadType.OVERSIZE2:
        especial = cfg.caixas_especiais.get(article.sku)
        if especial is not None:
            criterio = (
                f"Caixa especial (folha «Caixas Especiais»{f', {especial.notas}' if especial.notas else ''}) "
                f"-- não passa pelo best-fit do catálogo. Vai sempre para um equipamento de "
                f"{especial.comp_m:.2f} x {especial.larg_m:.2f} x {especial.alt_m:.2f} m, "
                f"capacidade fixa de {especial.quantidades[article.sku]} peça(s)/unidade "
                f"(capacidade do EQUIPAMENTO, não calculada por geometria/packer)."
            )
        else:
            criterio = "Caixa especial (folha «Caixas Especiais» sem registo para este SKU -- verificar dados)."
        return BoxJustificacao(
            **base, orientacao_usada="De pé (caixa especial)",
            caixa_int_comp_m=None, caixa_int_larg_m=None, caixa_int_alt_m=None,
            pecas_por_plano_teorico=None, pecas_por_plano=None,
            pecas_por_altura=None, pecas_por_peso=None,
            fator_limitante="N/A (carrinho dedicado OVERSIZE2)", criterio=criterio,
        )

    if load_type == LoadType.OVERSIZE:
        pallet = catalogo_idx.get(LoadType.PALLET)
        if pallet is not None:
            tol = cfg.pallet_tolerancia_lateral_m
            criterio = (
                f"Não assenta em nenhuma unidade de carga do catálogo, incluindo PALLET/MEIA "
                f"com tolerância lateral de {tol:.2f}m por lado ({pallet.int_comp_m:.2f} x "
                f"{pallet.int_larg_m:.2f}m -> {pallet.int_comp_m + 2*tol:.2f} x "
                f"{pallet.int_larg_m + 2*tol:.2f}m efetivo no plano, na(s) orientação(ões) "
                f"permitida(s) por ALTURA_OVERRIDE). "
                f"Vai para armazenagem OVERSIZE dedicada (rack wide-span/emparelhado ou chão)."
            )
        else:
            criterio = "Não assenta em nenhuma unidade de carga do catálogo (sem PALLET definida para testar)."
        return BoxJustificacao(
            **base, orientacao_usada="Pousada (sem palete/caixa)",
            caixa_int_comp_m=None, caixa_int_larg_m=None, caixa_int_alt_m=None,
            pecas_por_plano_teorico=None, pecas_por_plano=None,
            pecas_por_altura=None, pecas_por_peso=None,
            fator_limitante="N/A (armazenagem OVERSIZE)", criterio=criterio,
        )

    # Altura real da unidade (só PALLET/MEIA), com as peças que lá vão de facto
    # -- a capacidade prática, a mesma de «Peças nesta caixa» no racking.
    box = catalogo_idx.get(load_type)
    altura_real = None
    if box is not None and load_type in _TIPOS_COM_TOLERANCIA_PALETE:
        pecas = capacidade_reportada(load_type, assignment.pecas_por_unidade, article.sku, cfg,
                                     manual=capacidade_manual(article, load_type))
        altura_real = altura_real_unidade(article, box, pecas, cfg)
    base["altura_real_unidade_m"] = altura_real

    # Caixa forçada COM quantidade manual: não há geometria a reconstruir (o
    # motor pode até dizer que não cabe nenhuma -- é por isso que é manual).
    if capacidade_manual(article, load_type):
        return BoxJustificacao(
            **base, orientacao_usada="Manual (CAIXA_FORCADA)",
            caixa_int_comp_m=box.int_comp_m if box else None,
            caixa_int_larg_m=box.int_larg_m if box else None,
            caixa_int_alt_m=box.int_alt_m if box else None,
            pecas_por_plano_teorico=None, pecas_por_plano=None,
            pecas_por_altura=None, pecas_por_peso=None,
            fator_limitante="N/A (capacidade manual no master data)",
            criterio=(
                f"Caixa forçada no master data (CAIXA_FORCADA={load_type.value}, "
                f"PECAS_CAIXA_FORCADA={article.pecas_caixa_forcada}) -- não passa pelo "
                f"best-fit nem pelo cálculo geométrico: capacidade decidida à mão, usada "
                f"tal e qual (sem fator de enchimento nem arredondamentos). "
                f"Nº de unidades = ceil({article.stock_max_un} / {assignment.pecas_por_unidade}) "
                f"= {assignment.num_unidades}."
            ),
        )

    # Caixa normal (XS/S/M/L/MEIA/PALLET): reconstrói a orientação vencedora
    # com a MESMA fórmula de pieces_per_box/_pecas_no_envelope, desta vez
    # guardando o detalhe em vez de só o total.
    if box is None:
        return BoxJustificacao(
            **base, orientacao_usada="-", caixa_int_comp_m=None, caixa_int_larg_m=None,
            caixa_int_alt_m=None, pecas_por_plano_teorico=None, pecas_por_plano=None,
            pecas_por_altura=None, pecas_por_peso=None, fator_limitante="N/A",
            criterio=f"Tipo de carga '{load_type}' sem entrada correspondente no catálogo.",
        )

    dims = (article.comp_m, article.larg_m, article.alt_m)
    eixos = eixos_possiveis(article.altura_override)
    tol = cfg.pallet_tolerancia_lateral_m if load_type in _TIPOS_COM_TOLERANCIA_PALETE else 0.0
    Dc, Dl, Da = box.int_comp_m + 2 * tol, box.int_larg_m + 2 * tol, box.int_alt_m
    eixo_a = eixo_forcado(article.eixo_aninhagem)
    melhor = None  # (total, iv, por_plano_teorico, por_plano, n_altura)
    for iv in eixos:
        ja, jb = (j for j in range(3) if j != iv)
        vert, a, b = dims[iv], dims[ja], dims[jb]
        n_altura = (pecas_no_eixo(Da, vert, True, cfg.fator_empilhamento) if iv == eixo_a
                   else pieces_in_stack(Da, vert, article.empilhamento))
        if n_altura == 0:
            continue
        an_a, an_b = ja == eixo_a, jb == eixo_a
        op1 = (pecas_no_eixo(Dc, a, an_a, cfg.fator_empilhamento) *
               pecas_no_eixo(Dl, b, an_b, cfg.fator_empilhamento)) if a <= Dc and b <= Dl else 0
        op2 = (pecas_no_eixo(Dc, b, an_b, cfg.fator_empilhamento) *
               pecas_no_eixo(Dl, a, an_a, cfg.fator_empilhamento)) if b <= Dc and a <= Dl else 0
        por_plano_teorico = max(op1, op2)
        if por_plano_teorico == 0:
            continue
        por_plano = max(math.floor(por_plano_teorico * cfg.fill_factor_plano), 1)
        total = por_plano * n_altura
        if melhor is None or total > melhor[0]:
            melhor = (total, iv, por_plano_teorico, por_plano, n_altura)

    if melhor is None:
        # Defensivo: não deveria acontecer com um assignment já ppu>0.
        return BoxJustificacao(
            **base, orientacao_usada="-",
            caixa_int_comp_m=box.int_comp_m, caixa_int_larg_m=box.int_larg_m,
            caixa_int_alt_m=box.int_alt_m, pecas_por_plano_teorico=None, pecas_por_plano=None,
            pecas_por_altura=None, pecas_por_peso=None, fator_limitante="N/A",
            criterio="Não foi possível reconstruir o encaixe geométrico (verificar dados).",
        )

    por_geometria, iv, por_plano_teorico, por_plano, n_altura = melhor
    orientacao_usada = f"{_NOMES_EIXO[iv]} na vertical ({dims[iv]:.2f}m)"
    if iv == eixo_a:
        orientacao_usada += " -- eixo de aninhagem"

    por_peso = math.floor(box.peso_max_kg / article.peso_kg) if article.peso_kg > 0 else None
    if por_peso is not None and por_peso < por_geometria:
        fator_limitante = f"Peso (corta de {por_geometria} para {por_peso} peças)"
    else:
        fator_limitante = "Geometria (plano x altura)"

    criterio = (
        f"{por_plano_teorico} peças/plano (encaixe geométrico) x {cfg.fill_factor_plano:.0%} "
        f"fator de enchimento = {por_plano} peças/plano; x {n_altura} peças em altura "
        f"({article.empilhamento.value}) = {por_geometria} peças/unidade teórico"
        + (f"; peso ({article.peso_kg:.1f}kg) corta para {por_peso}" if por_peso is not None and por_peso < por_geometria else "")
        + (f"; plano com tolerância de {tol:.2f}m/lado ({Dc:.2f} x {Dl:.2f}m efetivo)" if tol > 0 else "")
        + f". Nº de unidades = ceil({article.stock_max_un} / {assignment.pecas_por_unidade}) = {assignment.num_unidades}."
    )
    if article.caixa_forcada == load_type.value:
        criterio = (f"Caixa forçada no master data (CAIXA_FORCADA={load_type.value}) -- sem "
                    f"best-fit; capacidade pela geometria dessa caixa: " + criterio)

    return BoxJustificacao(
        **base, orientacao_usada=orientacao_usada,
        caixa_int_comp_m=box.int_comp_m, caixa_int_larg_m=box.int_larg_m, caixa_int_alt_m=box.int_alt_m,
        pecas_por_plano_teorico=por_plano_teorico, pecas_por_plano=por_plano,
        pecas_por_altura=n_altura, pecas_por_peso=por_peso,
        fator_limitante=fator_limitante, criterio=criterio,
    )


def justificar_all(articles: list[Article], catalogo: list[BoxType], cfg: Config,
                   assignments: list[BoxAssignment]) -> list[BoxJustificacao]:
    assign_idx = {a.sku: a for a in assignments}
    out = []
    for art in articles:
        assignment = assign_idx.get(art.sku)
        if assignment is None:
            continue
        out.append(justificar_escolha(art, catalogo, cfg, assignment))
    return out

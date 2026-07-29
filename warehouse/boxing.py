"""Passo 1 — Repacking: escolher unidade de carga e contar por artigo.

IN : artigos (geometria + stock máximo), catálogo de unidades (S/M/L + PALLET).
OUT: BoxAssignment por SKU (tipo de carga, peças por unidade, nº de unidades).

Regra de encaminhamento:
  - artigos de geometria em cfg.long_geometrias, EXCETO os marcados como
    painel largo/volumoso (subtipo_longo em cfg.long_subtipos_excluidos)
    -> LONGO (cantilever), sem caixa: pecas_por_unidade = 1,
    num_unidades = stock máximo.
  - restantes (incluindo painéis/volumosos, que não são material 1D de
    cantilever) -> best-fit entre S/M/L/PALLET: escolhe a caixa que
    minimiza o nº de unidades de carga necessárias para o stock máximo.
"""
from __future__ import annotations
import math

from .models import Article, BoxType, LoadType, Empilhamento, BoxAssignment
from .config import Config

# Caixas normais, por ordem crescente de tamanho (usada para desempate)
_CAIXAS = [LoadType.XS, LoadType.S, LoadType.M, LoadType.L]


def pieces_in_stack(altura_disponivel: float, altura_artigo: float,
                    empilhamento: Empilhamento, fator_empilhamento: float) -> int:
    """Quantas peças cabem empilhadas na vertical, conforme a classe de empilhamento.

    NAO_EMPILHAVEL: 1 peça de altura, nunca mais.
    EMPILHAVEL: empilha inteiro, sem encaixar — n = floor(altura_disponivel / altura_artigo).
    ANINHAVEL: 1ª peça ocupa a altura inteira, cada peça extra acrescenta apenas
        fator_empilhamento x altura_artigo (encaixa/aninha na anterior):
        altura_total(n) = altura_artigo x (1 + (n-1) x fator_empilhamento)
    """
    if altura_artigo <= 0 or altura_artigo > altura_disponivel:
        return 0

    if empilhamento == Empilhamento.NAO_EMPILHAVEL:
        return 1

    if empilhamento == Empilhamento.ANINHAVEL and fator_empilhamento > 0:
        n = math.floor((altura_disponivel / altura_artigo - 1) / fator_empilhamento) + 1
        return max(n, 1)

    # EMPILHAVEL (ou ANINHAVEL sem fator definido): altura cheia somada
    n = math.floor(altura_disponivel / altura_artigo)
    return max(n, 1)


# Orientações (coluna Orientação_permitida) que TRAVAM o eixo vertical do
# artigo: a peça não pode ser deitada, logo a altura é sempre a cota Z medida.
# Qualquer outro valor (incl. "Livre", o default) permite reorientar a peça e
# escolher a cota vertical que mais rende.
_ORIENT_TRAVADA = {"não deitar", "nao deitar", "base fixa", "base-fixa"}


def pode_deitar(orientacao: str) -> bool:
    """True se a peça pode ser reorientada (qualquer das 3 cotas como altura)."""
    return (orientacao or "").strip().lower() not in _ORIENT_TRAVADA


def _pecas_no_envelope(article: Article, box: BoxType, vert: float,
                       plano: tuple[float, float], fator_empilhamento: float,
                       fill_factor: float) -> int:
    """Peças no envelope 3D com `vert` como cota vertical e `plano` as duas cotas
    horizontais: empilhamento na vertical (pieces_in_stack) x envelope 2D no
    plano (2 orientações, floor por eixo). fill_factor desconta o floor()
    geométrico (folgas de manuseio/forma, encaixe imperfeito) — mín. 1 peça."""
    Dc, Dl, Da = box.int_comp_m, box.int_larg_m, box.int_alt_m
    n_altura = pieces_in_stack(Da, vert, article.empilhamento, fator_empilhamento)
    if n_altura == 0:
        return 0
    a, b = plano
    op1 = math.floor(Dc / a) * math.floor(Dl / b) if a <= Dc and b <= Dl else 0
    op2 = math.floor(Dc / b) * math.floor(Dl / a) if b <= Dc and a <= Dl else 0
    por_plano_teorico = max(op1, op2)
    if por_plano_teorico == 0:
        return 0
    por_plano = max(math.floor(por_plano_teorico * fill_factor), 1)
    return por_plano * n_altura


def pieces_per_box(article: Article, box: BoxType,
                   fator_empilhamento: float = 0.3,
                   fill_factor: float = 1.0) -> int:
    """Quantas unidades do artigo cabem numa unidade de carga (envelope 3D).

    Orientação do artigo (coluna Orientação_permitida): se permite deitar (tudo
    menos «Base fixa»/«Não deitar»), testam-se as 3 escolhas de cota vertical e
    fica a que mais peças rende — não se força o Z medido como altura. Caso
    contrário a altura é sempre o Z medido (peça de pé). Em cada escolha:
    empilhamento vertical (pieces_in_stack) x envelope 2D no plano (2 orientações).
    fill_factor desconta o encaixe geométrico perfeito (folgas de manuseio/forma,
    encaixe-de-gume ao deitar peças finas), aplicado a TODAS as classes.
    Corta pelo peso máximo da caixa.
    """
    dims = (article.comp_m, article.larg_m, article.alt_m)
    if pode_deitar(article.orientacao):
        por_geometria = max(
            _pecas_no_envelope(article, box, dims[iv],
                               tuple(dims[j] for j in range(3) if j != iv),
                               fator_empilhamento, fill_factor)
            for iv in range(3)
        )
    else:
        por_geometria = _pecas_no_envelope(
            article, box, article.alt_m, (article.comp_m, article.larg_m),
            fator_empilhamento, fill_factor)

    if por_geometria == 0:
        return 0

    if article.peso_kg > 0:
        por_peso = math.floor(box.peso_max_kg / article.peso_kg)
    else:
        por_peso = por_geometria  # peso desconhecido: não cortar

    return min(por_geometria, por_peso)


def cabe_em_cantilever(article: Article, cfg: Config) -> bool:
    """True se o artigo é MESMO material de cantilever.

    Ser "Comprida" na folha não chega: a cantilever guarda material 1D
    (perfis, tubos, vedantes) que assenta atravessado nos braços. Peças
    volumosas -- altas ou largas -- nao cabem no nivel nem se apoiam bem, e
    antes eram aceites na mesma, ficando com capacidade 0 (nao dimensionadas)
    ou a ocupar um nivel inteiro cada.

    Criterio geometrico, com as cotas ordenadas:
      - a MENOR cota tem de caber na altura util do nivel (senao nao entra);
      - a cota MEDIA tem de caber na profundidade do braco (apoio);
      - e a peca tem de ser esbelta: a maior cota bastante maior que a media
        (razao de esbelteza), senao e um volume, nao um perfil.
    """
    d = sorted((article.comp_m, article.larg_m, article.alt_m))
    curta, media, longa = d
    if longa <= 0:
        return False
    if curta > cfg.cantilever_alt_nivel_m + 1e-9:
        return False
    if media > cfg.cantilever_braco_m + 1e-9:
        return False
    return (longa / media) >= cfg.cantilever_esbelteza_min if media > 0 else True


def choose_load(article: Article, catalogo: list[BoxType],
                cfg: Config) -> tuple[LoadType, int]:
    """Decide o tipo de carga e devolve (LoadType, peças_por_unidade).

    1) Geometria LONGO e não é painel/volumoso (subtipo_longo) -> (LONGO, 1).
       Painéis largos e volumosos não são material 1D de cantilever (não se
       apoiam em braços) -> caem para o best-fit normal (passo 2).
    2) Best-fit entre S/M/L/PALLET: testa todas as candidatas onde caiba pelo
       menos 1 peça e escolhe a que minimiza nº de unidades de carga para o
       stock máximo (ceil(stock_max_un / peças_por_unidade)). Em empate,
       fica com a caixa fisicamente mais pequena (S < M < L < PALLET).
       Se não couber em nenhuma -> PALLET (1 peça/palete, mesmo a 0).
    """
    if (article.geometria in cfg.long_geometrias
            and article.subtipo_longo not in cfg.long_subtipos_excluidos
            and cabe_em_cantilever(article, cfg)):
        return LoadType.LONGO, 1

    catalogo_idx = {b.nome: b for b in catalogo}
    # ordem crescente de tamanho: em empate no nº de unidades ganha a menor
    candidatas = _CAIXAS + [LoadType.MEIA, LoadType.PALLET]

    melhor: tuple[LoadType, int] | None = None
    melhor_num_unidades = None

    for load_type in candidatas:
        box = catalogo_idx.get(load_type)
        if box is None:
            continue
        ppu = pieces_per_box(article, box, cfg.fator_empilhamento, cfg.fill_factor_plano)
        if ppu <= 0:
            continue
        num_unidades = math.ceil(article.stock_max_un / ppu)
        if melhor is None or num_unidades < melhor_num_unidades:
            melhor = (load_type, ppu)
            melhor_num_unidades = num_unidades

    if melhor is not None:
        return melhor

    # Não coube em nenhuma unidade de carga. O fallback antigo devolvia
    # (PALLET, 1) sem verificar nada, o que MENTIA em dois sentidos:
    #   - peças maiores que a footprint da palete (em qualquer orientação)
    #     apareciam como "1 por palete", escondendo que são oversize e não
    #     assentam -- subdimensionando a área e sem gerar aviso;
    #   - peças que só não cabem por causa da restrição de orientação ficavam
    #     silenciosamente a 1/palete, sem sinalizar a causa.
    # Agora distingue-se: se a peça ASSENTA na footprint da palete (as duas
    # menores cotas cabem em comp x larg), 1/palete é uma aproximação legítima;
    # caso contrário é OVERSIZE e tem de ser reportado, não inventado.
    pallet = catalogo_idx.get(LoadType.PALLET)
    if pallet is not None and not _assenta_na_palete(
            article, pallet, cfg.pallet_transbordo_max_pct):
        return LoadType.OVERSIZE, 1
    return LoadType.PALLET, 1


def _assenta_na_palete(article: Article, pallet: BoxType,
                       transbordo_max_pct: float = 0.25) -> bool:
    """True se a peça é comportável por uma palete: as duas MENORES cotas cabem
    na footprint E a MAIOR não fica em voo além do tolerado.

    Ignora a restrição de orientação de propósito: a questão aqui é apenas se a
    peça é fisicamente paletizável, não como se arruma.

    O teste do transbordo é essencial e faltava. Sem ele, uma lateral de 2.47 m
    saía como "1 por palete" com 1.27 m a pender de uma palete de 1.20 m -- mais
    do que a palete mede. E uma palete assim não é armazenável em rack: o
    rack_capacity conta-a como ocupando 1.20 m quando na prática colide com as
    vizinhas. Peças acima do limiar são OVERSIZE e vão para armazenagem ao chão
    (ver Config.pallet_transbordo_max_pct).

    O transbordo mede-se sempre no PLANO, nunca na altura: das 3 orientações
    possíveis (cada cota como vertical), basta uma pousar dentro da footprint
    (com a tolerância) para a peça ser paletizável. Uma peça de 1.2 x 0.8 x 3.0 m
    posta de pé não pende para fora, apesar de ter 3 m de cota máxima."""
    c, l, a = article.comp_m, article.larg_m, article.alt_m
    lado_maior = max(pallet.int_comp_m, pallet.int_larg_m)
    lado_menor = min(pallet.int_comp_m, pallet.int_larg_m)
    tol_maior = lado_maior * (1.0 + transbordo_max_pct)

    # As 3 escolhas de cota vertical; em cada uma, (vertical) é a altura e o par
    # (base_menor, base_maior) fica no plano. A peça é paletizável se ALGUMA
    # orientação satisfizer as três condições:
    #   - a cota vertical cabe na ALTURA ÚTIL da palete -- sem isto, aceitava-se
    #     pôr uma lateral de 2.47 m de pé numa palete de 1.50 m de altura útil,
    #     que é o que fazia estas peças escaparem ao critério;
    #   - a cota menor do plano apoia dentro do lado menor (senão pende dos DOIS
    #     lados e não há apoio nenhum);
    #   - a cota maior do plano fica dentro do lado maior + tolerância. A
    #     tolerância aplica-se SÓ a esta: alargá-la a ambas seria inventar uma
    #     palete maior, não tolerar que a peça penda de uma ponta.
    for vertical, (p, q) in ((a, (c, l)), (l, (c, a)), (c, (l, a))):
        if vertical > pallet.int_alt_m + 1e-9:
            continue
        base_maior, base_menor = max(p, q), min(p, q)
        if base_menor <= lado_menor + 1e-9 and base_maior <= tol_maior + 1e-9:
            return True
    return False


def plan_box(article: Article, catalogo: list[BoxType],
             cfg: Config) -> BoxAssignment:
    """Escolha de unidade de carga + contagem para o stock máximo (teto)."""
    load_type, ppu = choose_load(article, catalogo, cfg)
    num = math.ceil(article.stock_max_un / ppu) if ppu else 0
    return BoxAssignment(article.sku, load_type, ppu, num)


def plan_all(articles: list[Article], catalogo: list[BoxType],
             cfg: Config) -> list[BoxAssignment]:
    return [plan_box(a, catalogo, cfg) for a in articles]


def avisos_cantilever(articles: list[Article], cfg: Config) -> list[str]:
    """SKUs marcados «Comprida» que o filtro geométrico recusou na cantilever.

    Não é um erro do cálculo: é classificação da folha que não bate certo com
    a geometria (peças volumosas marcadas como compridas). Ficam no best-fit
    normal, mas convém serem revistos no master data.
    """
    recusados = [
        a for a in articles
        if a.geometria in cfg.long_geometrias
        and a.subtipo_longo not in cfg.long_subtipos_excluidos
        and not cabe_em_cantilever(a, cfg)
    ]
    if not recusados:
        return []
    amostra = ", ".join(f"{a.sku} ({a.comp_m:.2f}x{a.larg_m:.2f}x{a.alt_m:.2f})"
                        for a in recusados[:4])
    return [
        f"{len(recusados)} SKU(s) marcados 'Comprida' NAO sao material de "
        f"cantilever (nao cabem no nivel/braco ou nao sao esbeltos) -- "
        f"encaminhados para o best-fit normal. Rever Geometria/Subtipo_LONGO "
        f"no master data: {amostra}{' ...' if len(recusados) > 4 else ''}"
    ]

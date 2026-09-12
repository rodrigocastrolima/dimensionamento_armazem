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


def capacidade_reportada(load_type: LoadType, pecas_por_unidade: int,
                         sku: str, cfg: Config) -> int:
    """Peças por caixa a REPORTAR nos resultados (capacidade prática).

    `pecas_por_unidade` é o máximo geométrico à ocupação definida
    (cfg.fill_factor_plano) e continua a ser o valor que dimensiona o armazém
    (nº de caixas -> racks -> área). Esta função só afeta a QUANTIDADE
    reportada dentro de cada caixa, aplicando limites de manuseio real:
    teto por tipo de caixa e arredondamento à centena inferior acima de 100
    peças (ver cfg.capacidade_*). Abaixo de 100 mantém o valor exato --
    arredondar daria 0, que não é uma capacidade.
    """
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
    ppu_pratico = capacidade_reportada(load_type, ppu_geometrico, article.sku, cfg)
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

    Best-fit entre S/M/L/PALLET: testa todas as candidatas onde caiba pelo
    menos 1 peça e escolhe a que minimiza nº de unidades de carga para o
    stock máximo (ceil(stock_max_un / peças_por_unidade)). Em empate,
    fica com a caixa fisicamente mais pequena (S < M < L < PALLET).
    Se não couber em nenhuma -> OVERSIZE ou PALLET (ver fallback abaixo).
    """
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

    if load_type == LoadType.OVERSIZE:
        pallet = catalogo_idx.get(LoadType.PALLET)
        if pallet is not None:
            lado_maior = max(pallet.int_comp_m, pallet.int_larg_m)
            lado_menor = min(pallet.int_comp_m, pallet.int_larg_m)
            tol = lado_maior * (1.0 + cfg.pallet_transbordo_max_pct)
            criterio = (
                f"Não assenta em nenhuma unidade de carga do catálogo. Testada a footprint "
                f"da palete ({pallet.int_comp_m:.2f} x {pallet.int_larg_m:.2f} x "
                f"{pallet.int_alt_m:.2f}m): em nenhuma das 3 orientações a cota vertical cabe "
                f"na altura útil e o plano cabe em {lado_menor:.2f} x {tol:.2f}m "
                f"(lado menor x lado maior + transbordo {cfg.pallet_transbordo_max_pct:.0%}). "
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

    # Caixa normal (XS/S/M/L/MEIA/PALLET): reconstrói a orientação vencedora
    # com a MESMA fórmula de pieces_per_box/_pecas_no_envelope, desta vez
    # guardando o detalhe em vez de só o total.
    box = catalogo_idx.get(load_type)
    if box is None:
        return BoxJustificacao(
            **base, orientacao_usada="-", caixa_int_comp_m=None, caixa_int_larg_m=None,
            caixa_int_alt_m=None, pecas_por_plano_teorico=None, pecas_por_plano=None,
            pecas_por_altura=None, pecas_por_peso=None, fator_limitante="N/A",
            criterio=f"Tipo de carga '{load_type}' sem entrada correspondente no catálogo.",
        )

    dims = (article.comp_m, article.larg_m, article.alt_m)
    eixos = range(3) if pode_deitar(article.orientacao) else (2,)  # travada: Z medido = altura
    Dc, Dl, Da = box.int_comp_m, box.int_larg_m, box.int_alt_m
    melhor = None  # (total, iv, por_plano_teorico, por_plano, n_altura)
    for iv in eixos:
        vert = dims[iv]
        a, b = tuple(dims[j] for j in range(3) if j != iv)
        n_altura = pieces_in_stack(Da, vert, article.empilhamento, cfg.fator_empilhamento)
        if n_altura == 0:
            continue
        op1 = math.floor(Dc / a) * math.floor(Dl / b) if a <= Dc and b <= Dl else 0
        op2 = math.floor(Dc / b) * math.floor(Dl / a) if b <= Dc and a <= Dl else 0
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
        + f". Nº de unidades = ceil({article.stock_max_un} / {assignment.pecas_por_unidade}) = {assignment.num_unidades}."
    )

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

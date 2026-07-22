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
            and article.subtipo_longo not in cfg.long_subtipos_excluidos):
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

    # não cabe em nenhuma caixa/palete -> palete a 1 peça (fallback)
    return LoadType.PALLET, 1


def plan_box(article: Article, catalogo: list[BoxType],
             cfg: Config) -> BoxAssignment:
    """Escolha de unidade de carga + contagem para o stock máximo (teto)."""
    load_type, ppu = choose_load(article, catalogo, cfg)
    num = math.ceil(article.stock_max_un / ppu) if ppu else 0
    return BoxAssignment(article.sku, load_type, ppu, num)


def plan_all(articles: list[Article], catalogo: list[BoxType],
             cfg: Config) -> list[BoxAssignment]:
    return [plan_box(a, catalogo, cfg) for a in articles]

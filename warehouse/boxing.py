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
_CAIXAS = [LoadType.S, LoadType.M, LoadType.L]


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


def pieces_per_box(article: Article, box: BoxType,
                   fator_empilhamento: float = 0.3,
                   fill_factor_rigido: float = 1.0) -> int:
    """Quantas unidades do artigo cabem numa unidade de carga (envelope 3D).

    Plano horizontal (comprimento x largura do artigo): fit geométrico simples,
    floor(dimensão_caixa / dimensão_artigo) por eixo, testando as 2 orientações
    do artigo nesse plano. Para artigos RIGIDO, o nº teórico de peças no plano
    é reduzido por fill_factor_rigido (folgas de manuseio/forma irregular —
    o floor() geométrico assume encaixe perfeito, que não é realista).
    Eixo vertical (altura): regra de empilhamento linear via pieces_in_stack.
    Corta pelo peso máximo da caixa.
    """
    comp, larg, alt = article.comp_m, article.larg_m, article.alt_m
    Dc, Dl, Da = box.int_comp_m, box.int_larg_m, box.int_alt_m

    n_altura = pieces_in_stack(Da, alt, article.empilhamento, fator_empilhamento)
    if n_altura == 0:
        return 0

    op1 = math.floor(Dc / comp) * math.floor(Dl / larg) if comp <= Dc and larg <= Dl else 0
    op2 = math.floor(Dc / larg) * math.floor(Dl / comp) if larg <= Dc and comp <= Dl else 0
    por_plano_teorico = max(op1, op2)
    if por_plano_teorico == 0:
        return 0

    if article.classe == "RIGIDO":
        por_plano = max(math.floor(por_plano_teorico * fill_factor_rigido), 1)
    else:
        por_plano = por_plano_teorico

    por_geometria = por_plano * n_altura

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
    candidatas = _CAIXAS + [LoadType.PALLET]

    melhor: tuple[LoadType, int] | None = None
    melhor_num_unidades = None

    for load_type in candidatas:
        box = catalogo_idx.get(load_type)
        if box is None:
            continue
        ppu = pieces_per_box(article, box, cfg.fator_empilhamento, cfg.fill_factor_rigido)
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

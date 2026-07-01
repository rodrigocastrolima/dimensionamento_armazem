"""Passo 2 — Quantos racks para guardar todas as unidades de carga.

IN : BoxAssignment (output do Passo 1), tipologias de rack (dadas).
OUT: RackRequirement por tipologia (resumo) + PosicaoChao detalhada (packer 2D
     real) com módulos/níveis/caixas concretas.

Motor de alocação (allocate_to_racks): packer 2D real por nível (rectpack),
caixas de TODOS os tipos de carga misturadas livremente no mesmo nível/módulo
quando compatível com a tipologia de rack, e nº de módulos empilhados decidido
posição a posição (não há um "blocos_empilhados" global por tipologia).
"""
from __future__ import annotations
import math
from dataclasses import dataclass

from rectpack import newPacker, PackingMode

from .models import (RackType, BoxType, LoadType,
                     BoxAssignment, RackRequirement, Article,
                     PosicaoChao, BlocoRack, NivelRack, CaixaColocada)
from .config import Config


# ----------------------------------------------------------- capacidade base

def rack_capacity(rack: RackType, box: BoxType) -> int:
    """Quantas unidades de carga cabem num rack (envelope 2D por nível).

    Por nível: divide footprint do nível (larg_m x prof_m) pelo footprint
    exterior da caixa (ext_comp_m x ext_larg_m), orientando a caixa da forma
    mais favorável. Valida que a altura exterior da caixa cabe no nível.
    Total = caixas por nível x niveis.
    """
    if box.ext_alt_m > rack.alt_nivel_m:
        return 0

    op1 = math.floor(rack.larg_m / box.ext_comp_m) * math.floor(rack.prof_m / box.ext_larg_m)
    op2 = math.floor(rack.larg_m / box.ext_larg_m) * math.floor(rack.prof_m / box.ext_comp_m)
    por_nivel = max(op1, op2)
    return por_nivel * rack.niveis


def rack_capacity_long(rack: RackType, comp_artigo_m: float) -> int:
    """Capacidade de uma cantilever para material LONGO.

    Itens por braço = floor(comp_braco_m / comp_artigo_m).
    Capacidade total = itens por braço x niveis.
    """
    if rack.comp_braco_m <= 0 or comp_artigo_m <= 0:
        return 0
    itens_por_braco = math.floor(rack.comp_braco_m / comp_artigo_m)
    return itens_por_braco * rack.niveis


def _rack_footprint_m2(rack: RackType) -> float:
    return rack.footprint_m2 if rack.footprint_m2 else rack.larg_m * rack.prof_m


def blocos_empilhados(rack: RackType, altura_armazem_m: float) -> int:
    """Quantos módulos físicos deste RackType cabem empilhados, na mesma
    posição de chão, até à altura do armazém (TETO, não um valor imposto:
    cada posição decide quantos módulos USA, até este máximo).

    altura_armazem_m <= 0 -> sem limite definido: 1 módulo (comportamento
    atual, niveis do Excel já é o total do prumo).
    """
    altura_modulo = rack.alt_nivel_m * rack.niveis
    if altura_armazem_m <= 0 or altura_modulo <= 0:
        return 1
    return max(math.floor(altura_armazem_m / altura_modulo), 1)


def best_rack_for_load(load_type: LoadType, rack_types: list[RackType],
                       catalogo: dict[LoadType, BoxType],
                       comp_max_longo: float = 0.0,
                       altura_armazem_m: float = 0.0) -> tuple[RackType, int, int] | None:
    """Escolhe, entre os racks compatíveis com este tipo de carga, o que tem
    menor footprint de chão por unidade (footprint_rack / capacidade_efetiva).

    Heurística de comparação entre tipologias (apenas para o resumo de
    racks_needed) -- não decide blocos reais usados na alocação, que é feita
    posição a posição em allocate_to_racks. Devolve (rack, capacidade_por_
    modulo, blocos_max) do vencedor, ou None se nenhum rack compatível tiver
    capacidade > 0 para este tipo de carga.
    """
    melhor: tuple[RackType, int, int] | None = None
    melhor_m2_por_unidade = None

    for rack in rack_types:
        if load_type not in rack.aceita:
            continue

        if load_type == LoadType.LONGO:
            cap = rack_capacity_long(rack, comp_max_longo) if rack.comp_braco_m > 0 else 0
        else:
            box = catalogo.get(load_type)
            cap = rack_capacity(rack, box) if box else 0

        if cap <= 0:
            continue

        blocos = blocos_empilhados(rack, altura_armazem_m)
        cap_efetiva = cap * blocos

        m2_por_unidade = _rack_footprint_m2(rack) / cap_efetiva
        if melhor is None or m2_por_unidade < melhor_m2_por_unidade:
            melhor = (rack, cap, blocos)
            melhor_m2_por_unidade = m2_por_unidade

    return melhor


def racks_needed(assignments: list[BoxAssignment],
                 rack_types: list[RackType],
                 catalogo: dict[LoadType, BoxType],
                 cfg: Config,
                 comp_max_longo: float = 0.0,
                 altura_picking_m: float = 0.0) -> tuple[list[RackRequirement], list[str]]:
    """Resumo agregado por tipologia (informativo, usado para a área e o
    sumário da UI). A alocação real e detalhada (posições/blocos/níveis/
    caixas concretas, com mistura de tipos de carga e blocos decididos por
    posição) é feita por allocate_to_racks, que é a fonte da verdade.

    Cada tipo de carga escolhe a tipologia de rack com menor footprint por
    unidade (best_rack_for_load). Os números aqui são uma ESTIMATIVA pela
    capacidade total (ignora picking e partilha de espaço entre tipos de
    carga no mesmo rack); o resumo final apresentado deve idealmente ser
    recalculado a partir do resultado de allocate_to_racks quando disponível.
    """
    totais: dict[LoadType, int] = {}
    for a in assignments:
        totais[a.load_type] = totais.get(a.load_type, 0) + a.num_unidades

    if not totais:
        return [], []

    avisos: list[str] = []
    por_tipologia: dict[str, dict[LoadType, int]] = {}

    for lt, total_unidades in totais.items():
        escolha = best_rack_for_load(lt, rack_types, catalogo, comp_max_longo,
                                     cfg.altura_armazem_m)
        if escolha is None:
            avisos.append(
                f"Sem rack compativel para tipo de carga {lt.value} "
                f"({total_unidades} unidades) -- EXCLUIDO do resultado.")
            continue
        rack, cap, blocos_max = escolha
        num_modulos = math.ceil(total_unidades / cap) if cap > 0 else 0
        por_tipologia.setdefault(rack.nome, {})[lt] = num_modulos

    requirements = []
    for rack_nome, por_tipo in por_tipologia.items():
        num_racks = sum(por_tipo.values())
        rack = next(r for r in rack_types if r.nome == rack_nome)
        blocos_max = blocos_empilhados(rack, cfg.altura_armazem_m)
        posicoes = max(math.ceil(num_racks / blocos_max), 1)
        requirements.append(RackRequirement(
            rack_nome=rack_nome,
            num_racks=num_racks,
            load_types=list(por_tipo.keys()),
            posicoes_chao=posicoes,
            blocos_empilhados=max(math.ceil(num_racks / posicoes), 1),
        ))

    for aviso in avisos:
        print(f"AVISO: {aviso}")

    return requirements, avisos


# ------------------------------------------------------- packer 2D por nível

@dataclass
class _ItemParaColocar:
    sku: str
    descricao: str
    load_type: LoadType
    comp_m: float              # footprint exterior, dimensão 1
    larg_m: float               # footprint exterior, dimensão 2
    alt_m: float
    pecas_nesta_caixa: int
    eh_primeira_do_sku: bool    # True = precisa picking; False = overflow/reserva


def _cabe_no_nivel(nivel: NivelRack, rack: RackType, item: _ItemParaColocar) -> tuple[float, float] | None:
    """Testa (via rectpack, batch com as caixas já lá + a nova) se item cabe
    no nível. Devolve (x_m, y_m) da nova caixa se couber, None caso contrário.

    rectpack não é incremental: para garantir que as caixas já confirmadas
    continuam todas colocadas, corre-se o packer com TODAS (existentes + nova)
    e só se aceita se ele conseguir colocar o lote inteiro.
    """
    if item.alt_m > rack.alt_nivel_m + 1e-9:
        return None

    packer = newPacker(mode=PackingMode.Offline, rotation=True)
    packer.add_bin(rack.larg_m, rack.prof_m)
    for i, c in enumerate(nivel.caixas):
        packer.add_rect(c.comp_m, c.larg_m, rid=("existente", i))
    packer.add_rect(item.comp_m, item.larg_m, rid="novo")
    packer.pack()

    colocados = {}
    for abin in packer:
        for rect in abin:
            colocados[rect.rid] = rect

    if len(colocados) != len(nivel.caixas) + 1 or "novo" not in colocados:
        return None

    novo = colocados["novo"]
    return (novo.x, novo.y)


def _abrir_bloco(rack: RackType, bloco_idx: int, altura_picking_m: float) -> BlocoRack:
    niveis = []
    for n in range(rack.niveis):
        base_nivel = bloco_idx * (rack.alt_nivel_m * rack.niveis) + n * rack.alt_nivel_m
        eh_picking = altura_picking_m > 0 and base_nivel < altura_picking_m
        niveis.append(NivelRack(nivel_idx=n, eh_picking=eh_picking))
    return BlocoRack(bloco_idx=bloco_idx, niveis=niveis)


def _colocar(nivel: NivelRack, x: float, y: float, item: _ItemParaColocar) -> None:
    nivel.caixas.append(CaixaColocada(
        sku=item.sku, descricao=item.descricao, load_type=item.load_type,
        x_m=round(x, 4), y_m=round(y, 4),
        comp_m=item.comp_m, larg_m=item.larg_m, alt_m=item.alt_m,
        pecas_nesta_caixa=item.pecas_nesta_caixa, eh_picking=nivel.eh_picking,
    ))


def _tentar_colocar_em_posicao(pos: PosicaoChao, rack: RackType, item: _ItemParaColocar,
                                altura_picking_m: float, max_blocos: int,
                                preferir_picking: bool, permitir_fallback: bool = True) -> bool:
    """Tenta colocar item na posição pos, abrindo um bloco novo se necessário
    (até max_blocos). Zona alvo: picking se preferir_picking, senão reserva.

    permitir_fallback=True: se a zona alvo não tiver espaço (nem abrindo
    bloco novo), cai para a outra zona -- correto para caixas de overflow
    (reserva), que podem ocupar picking se não houver reserva nenhures.

    permitir_fallback=False: NUNCA cai para a outra zona -- usado para a 1ª
    caixa de um SKU (picking obrigatório): só conta como colocada se entrou
    mesmo em picking; caso contrário falha, para o chamador poder abrir uma
    posição de chão totalmente nova em vez de "desistir" silenciosamente
    para reserva (o que deixaria o SKU sem presença de picking)."""
    niveis_alvo, niveis_outros = [], []
    for bloco in pos.blocos:
        for nivel in bloco.niveis:
            (niveis_alvo if nivel.eh_picking == preferir_picking else niveis_outros).append(nivel)

    for nivel in niveis_alvo:
        xy = _cabe_no_nivel(nivel, rack, item)
        if xy is not None:
            _colocar(nivel, *xy, item)
            return True

    if pos.num_blocos < max_blocos:
        novo_bloco = _abrir_bloco(rack, pos.num_blocos, altura_picking_m)
        pos.blocos.append(novo_bloco)
        for nivel in novo_bloco.niveis:
            if nivel.eh_picking != preferir_picking:
                continue
            xy = _cabe_no_nivel(nivel, rack, item)
            if xy is not None:
                _colocar(nivel, *xy, item)
                return True
        if not permitir_fallback:
            return False
        # bloco novo não deu encaixe na zona preferida -- tentar a outra
        for nivel in novo_bloco.niveis:
            if nivel.eh_picking == preferir_picking:
                continue
            xy = _cabe_no_nivel(nivel, rack, item)
            if xy is not None:
                _colocar(nivel, *xy, item)
                return True

    if not permitir_fallback:
        return False

    for nivel in niveis_outros:
        xy = _cabe_no_nivel(nivel, rack, item)
        if xy is not None:
            _colocar(nivel, *xy, item)
            return True

    return False


def allocate_to_racks(assignments: list[BoxAssignment],
                      requirements: list[RackRequirement],
                      rack_types: list[RackType],
                      catalogo: dict[LoadType, BoxType],
                      articles: list[Article],
                      comp_max_longo: float = 1.0,
                      altura_picking_m: float = 0.0,
                      altura_armazem_m: float = 0.0) -> tuple[list[PosicaoChao], list[str]]:
    """Aloca todas as caixas de todos os tipos de carga, num packer 2D real
    (rectpack) por nível, com mistura livre de tipos de carga no mesmo
    nível/módulo/posição quando a tipologia de rack aceita esses tipos.

    Algoritmo:
    1. Reúne todas as caixas individuais de todos os SKUs/tipos de carga.
       Para cada SKU, marca a 1ª caixa como "precisa picking" (se o modo
       picking estiver ativo) e o resto como overflow/reserva.
    2. Ordena por área da caixa decrescente (first-fit-decreasing): caixas
       maiores primeiro aproveitam melhor o espaço.
    3. Para cada caixa: escolhe a(s) tipologia(s) de rack compatíveis com o
       seu load_type (RackType.aceita). Tenta encaixar numa posição/nível já
       aberto (zona de picking se for a 1ª do SKU, senão reserva,
       preferindo a MESMA posição onde esse SKU já tem presença de picking);
       só abre posição nova quando as existentes esgotaram os blocos
       possíveis (altura_armazem_m).

    Sem modo picking (altura_picking_m <= 0): todos os níveis contam como
    reserva (eh_picking=False em todos) -- o algoritmo funciona igual, só
    não há a obrigação da 1ª caixa ir a zona especial.

    Devolve (posicoes, avisos): posicoes é a lista de PosicaoChao (uma por
    posição de chão física, agrupadas por tipologia), cada uma com os seus
    próprios blocos/níveis/caixas reais; avisos sinaliza SKUs cuja presença
    em picking não foi possível garantir (capacidade de picking esgotada em
    todas as tipologias compatíveis).
    """
    article_idx = {a.sku: a for a in articles}
    rack_by_name = {r.nome: r for r in rack_types}

    candidatos_por_tipo: dict[LoadType, list[RackType]] = {}
    for lt in LoadType:
        compativeis = [r for r in rack_types if lt in r.aceita]
        if lt == LoadType.LONGO:
            compativeis = [r for r in compativeis if r.comp_braco_m > 0]
        else:
            box = catalogo.get(lt)
            compativeis = [r for r in compativeis
                          if box and rack_capacity(r, box) > 0]
        compativeis.sort(key=lambda r: _rack_footprint_m2(r))
        candidatos_por_tipo[lt] = compativeis

    itens: list[_ItemParaColocar] = []
    for a in assignments:
        art = article_idx.get(a.sku)
        desc = art.descricao if art else ""
        if a.load_type == LoadType.LONGO:
            comp_dim = art.comp_m if art else (comp_max_longo if comp_max_longo > 0 else 1.0)
            larg_dim, alt_dim = 0.3, 0.3
        else:
            box = catalogo.get(a.load_type)
            if box is None:
                continue
            comp_dim, larg_dim, alt_dim = box.ext_comp_m, box.ext_larg_m, box.ext_alt_m

        pecas_total = art.stock_max_un if art else 0
        for i in range(a.num_unidades):
            pecas_cheias = i * a.pecas_por_unidade
            pecas_aqui = max(min(a.pecas_por_unidade, pecas_total - pecas_cheias), 0)
            itens.append(_ItemParaColocar(
                sku=a.sku, descricao=desc, load_type=a.load_type,
                comp_m=comp_dim, larg_m=larg_dim, alt_m=alt_dim,
                pecas_nesta_caixa=pecas_aqui,
                eh_primeira_do_sku=(i == 0),
            ))

    # first-fit-decreasing por área (footprint exterior); sort estável
    # preserva a ordem i=0,1,2.. dentro do mesmo SKU (mesma área).
    itens.sort(key=lambda it: it.comp_m * it.larg_m, reverse=True)

    posicoes_por_tipologia: dict[str, list[PosicaoChao]] = {r.nome: [] for r in rack_types}
    sku_posicao: dict[str, tuple[str, int]] = {}   # sku -> (rack_nome, indice) com picking
    avisos_picking: set[str] = set()

    for item in itens:
        candidatos = candidatos_por_tipo.get(item.load_type, [])
        if not candidatos:
            continue

        colocado = False
        preferir = item.eh_primeira_do_sku
        # fallback para a zona oposta só é aceitável para caixas de overflow;
        # a 1ª caixa de um SKU (picking obrigatório) nunca pode "desistir" e
        # cair em reserva sem antes esgotar TODAS as posições possíveis
        # (incluindo abrir posições novas) -- ver passo 3.
        permitir_fallback = not preferir

        # 1) já tem posição de picking conhecida para este SKU -> tentar lá primeiro
        if item.sku in sku_posicao:
            rack_nome, idx = sku_posicao[item.sku]
            rack = rack_by_name[rack_nome]
            if rack in candidatos:
                pos = posicoes_por_tipologia[rack_nome][idx]
                max_blocos = blocos_empilhados(rack, altura_armazem_m)
                colocado = _tentar_colocar_em_posicao(pos, rack, item, altura_picking_m,
                                                      max_blocos, preferir, permitir_fallback)

        # 2) esgotar capacidade já aberta (todas as posições, todas as
        # tipologias compatíveis) antes de abrir posição nova
        if not colocado:
            for rack in candidatos:
                max_blocos = blocos_empilhados(rack, altura_armazem_m)
                for pos in posicoes_por_tipologia[rack.nome]:
                    if _tentar_colocar_em_posicao(pos, rack, item, altura_picking_m,
                                                  max_blocos, preferir, permitir_fallback):
                        colocado = True
                        if item.eh_primeira_do_sku:
                            sku_posicao[item.sku] = (rack.nome, pos.indice)
                        break
                if colocado:
                    break

        # 3) abrir posição(ões) nova(s) -- tenta todas as tipologias
        # compatíveis, uma posição nova de cada vez, até conseguir colocar
        # (a 1ª caixa de um SKU não pode desistir aqui: precisa mesmo de
        # picking, e abrir uma posição nova sempre tem zona de picking
        # vazia, exceto se a própria tipologia não tiver NENHUM nível abaixo
        # de altura_picking_m -- nesse caso é mesmo impossível).
        if not colocado:
            for rack in candidatos:
                max_blocos = blocos_empilhados(rack, altura_armazem_m)
                lista = posicoes_por_tipologia[rack.nome]
                pos = PosicaoChao(rack_nome=rack.nome, indice=len(lista))
                pos.blocos.append(_abrir_bloco(rack, 0, altura_picking_m))
                if _tentar_colocar_em_posicao(pos, rack, item, altura_picking_m,
                                              max_blocos, preferir, permitir_fallback):
                    colocado = True
                    lista.append(pos)
                    if item.eh_primeira_do_sku:
                        sku_posicao[item.sku] = (rack.nome, pos.indice)
                    break

        # 4) última tentativa: se a 1ª caixa de um SKU não coube em picking
        # em lado nenhum (estruturalmente impossível, ex: alt_nivel_m >=
        # altura_picking_m em todas as tipologias compatíveis), aceita
        # reserva mesmo assim -- nunca perder a caixa -- e regista aviso.
        if not colocado and preferir:
            for rack in candidatos:
                max_blocos = blocos_empilhados(rack, altura_armazem_m)
                for pos in posicoes_por_tipologia[rack.nome]:
                    if _tentar_colocar_em_posicao(pos, rack, item, altura_picking_m,
                                                  max_blocos, preferir, permitir_fallback=True):
                        colocado = True
                        break
                if colocado:
                    break
            if not colocado:
                rack = candidatos[0]
                max_blocos = blocos_empilhados(rack, altura_armazem_m)
                lista = posicoes_por_tipologia[rack.nome]
                pos = PosicaoChao(rack_nome=rack.nome, indice=len(lista))
                pos.blocos.append(_abrir_bloco(rack, 0, altura_picking_m))
                colocado = _tentar_colocar_em_posicao(pos, rack, item, altura_picking_m,
                                                      max_blocos, preferir, permitir_fallback=True)
                if colocado:
                    lista.append(pos)
            if colocado:
                avisos_picking.add(item.sku)

        # item não cabe em nada compatível (caixa maior que qualquer rack
        # compatível) -- fica de fora; não deveria acontecer com dados reais.

    avisos: list[str] = []
    if avisos_picking:
        avisos.append(
            f"{len(avisos_picking)} SKU(s) sem zona de picking disponivel "
            f"(capacidade de picking esgotada) -- caixa alocada em reserva.")
        print(f"AVISO: {avisos[-1]}")

    resultado: list[PosicaoChao] = []
    for lista in posicoes_por_tipologia.values():
        resultado.extend(lista)
    return resultado, avisos

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
from .boxing import pieces_in_stack
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


def rack_capacity_long(rack: RackType, comp_artigo_m: float,
                       larg_artigo_m: float = 0.0, alt_artigo_m: float = 0.0,
                       empilhamento=None, fator_empilhamento: float = 0.3,
                       fill_factor: float = 1.0) -> int:
    """Capacidade de uma cantilever para material LONGO, por SKU.

    O nível da cantilever é tratado como um VOLUME de arrumação -- "a própria
    rack é a caixa", sem caixa física:

        por_nivel = floor(braco / comp) x floor(prof / larg) x n_empilhado
        capacidade = por_nivel x niveis

    Antes contava-se 1 peça por braço (floor(braco/comp) x niveis), ignorando
    largura e altura: uma peça de 0.13 m ocupava o mesmo que um perfil de
    2.47 m, e o material nunca era arrumado lado a lado nem empilhado.

    larg/alt a 0 (ou empilhamento None) degradam para o comportamento antigo,
    para nao partir chamadas que so tenham o comprimento.
    """
    if rack.comp_braco_m <= 0 or comp_artigo_m <= 0:
        return 0
    # O material assenta ATRAVESSADO sobre varios bracos: o comprimento da peca
    # corre ao longo da LARGURA do rack (o vao), nao do braco. O comp_braco_m e
    # a profundidade do apoio -- limita a largura do que se pousa, nao o
    # comprimento. Comparar o comprimento da peca com o braco (como se fazia)
    # dava 0 para tudo o que fosse mais largo que 0.6 m.
    vao = rack.larg_m if rack.larg_m > 0 else rack.comp_braco_m
    por_comprimento = math.floor(vao / comp_artigo_m)
    if por_comprimento <= 0:
        return 0

    # lado a lado, na profundidade util do braco
    if larg_artigo_m > 0 and rack.comp_braco_m > 0:
        por_profundidade = math.floor(rack.comp_braco_m / larg_artigo_m)
        if por_profundidade <= 0:
            return 0
    else:
        por_profundidade = 1

    # empilhado, na altura útil do nível
    if alt_artigo_m > 0 and empilhamento is not None:
        n_altura = pieces_in_stack(rack.alt_nivel_m, alt_artigo_m,
                                   empilhamento, fator_empilhamento)
        if n_altura <= 0:
            return 0
    else:
        n_altura = 1

    por_nivel_teorico = por_comprimento * por_profundidade
    por_nivel = max(math.floor(por_nivel_teorico * fill_factor), 1) * n_altura
    return por_nivel * rack.niveis


def _melhor_cantilever(rack_types: list[RackType]) -> RackType | None:
    """Cantilever compatível com LONGO e com braço definido, de menor footprint."""
    cands = [r for r in rack_types
             if LoadType.LONGO in r.aceita and r.comp_braco_m > 0]
    return min(cands, key=_rack_footprint_m2) if cands else None


def _cap_longo_sku(rack: RackType, art: Article | None, cfg: Config) -> int:
    """Capacidade da cantilever para UM SKU concreto (usa as suas 3 cotas)."""
    if art is None or art.comp_m <= 0:
        return 0
    return rack_capacity_long(
        rack, art.comp_m, art.larg_m, art.alt_m,
        art.empilhamento, cfg.fator_empilhamento, cfg.fill_factor_plano)


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
                 altura_picking_m: float = 0.0,
                 articles: list[Article] | None = None) -> tuple[list[RackRequirement], list[str]]:
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

    article_idx = {a.sku: a for a in articles} if articles else {}

    avisos: list[str] = []
    por_tipologia: dict[str, dict[LoadType, int]] = {}

    # LONGO: a capacidade depende das cotas DE CADA SKU, por isso não pode ser
    # estimada com um comprimento único (antes usava-se comp_max_longo, o maior
    # de todos, o que fazia uma peça de 0.13 m ocupar o mesmo que um perfil de
    # 2.47 m). Calcula-se módulo a módulo por SKU e soma-se.
    if LoadType.LONGO in totais:
        rack_c = _melhor_cantilever(rack_types)
        if rack_c is None:
            avisos.append(
                f"Sem cantilever compativel para LONGO "
                f"({totais[LoadType.LONGO]} unidades) -- EXCLUIDO do resultado.")
        else:
            blocos = blocos_empilhados(rack_c, cfg.altura_armazem_m)
            modulos = 0.0
            sem_cap = []
            for a in assignments:
                if a.load_type != LoadType.LONGO:
                    continue
                art = article_idx.get(a.sku) if article_idx else None
                cap = _cap_longo_sku(rack_c, art, cfg)
                if cap <= 0:
                    sem_cap.append(a.sku)
                    continue
                modulos += a.num_unidades / (cap * blocos)
            if sem_cap:
                avisos.append(
                    f"{len(sem_cap)} SKU(s) LONGO sem capacidade na cantilever "
                    f"(nao cabem no braco/nivel): {', '.join(sem_cap[:5])}"
                    f"{' ...' if len(sem_cap) > 5 else ''}")
            if modulos > 0:
                por_tipologia.setdefault(rack_c.nome, {})[LoadType.LONGO] = \
                    max(math.ceil(modulos * blocos), 1)

    for lt, total_unidades in totais.items():
        if lt == LoadType.LONGO:
            continue          # já tratado acima, por SKU
        if lt == LoadType.OVERSIZE:
            # Peças que não assentam em nenhuma unidade de carga do catálogo
            # (nem na footprint da palete). Antes eram contadas como 1/palete,
            # o que inventava capacidade inexistente; agora ficam de fora do
            # dimensionamento e são reportadas para decisão humana.
            avisos.append(
                f"{total_unidades} peca(s) OVERSIZE: nao assentam na footprint "
                f"da palete em nenhuma orientacao -- NAO dimensionadas. "
                f"Requerem solucao dedicada (palete especial/chao/cantilever).")
            continue
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
    num_boxes_in_stack: int = 1  # quantas caixas físicas estão empilhadas neste item


def _empacotar_nivel(nivel: NivelRack, rack: RackType, item: _ItemParaColocar) -> dict | None:
    """Testa (via rectpack, batch com as caixas já lá + a nova) se item cabe no
    nível. Devolve a solução completa {rid -> rect colocado} se couber tudo
    (rid = índice int da caixa existente, ou "novo"); None caso contrário.

    rectpack não é incremental: corre-se o packer com TODAS as caixas
    (existentes + nova) e só se aceita se ele colocar o lote inteiro. Como o
    packer pode REORDENAR e RODAR as caixas (rotation=True), devolve-se a
    solução inteira -- o chamador regrava a footprint de cada caixa (posição E
    dimensão colocadas), para o desenho 3D ficar fiel ao packing real. Sem isto,
    uma caixa rodada pelo packer era gravada na orientação original e desenhada
    a sair da rack / a sobrepor-se às vizinhas.
    """
    if item.alt_m > rack.alt_nivel_m + 1e-9:
        return None

    packer = newPacker(mode=PackingMode.Offline, rotation=True)
    packer.add_bin(rack.larg_m, rack.prof_m)
    for i, c in enumerate(nivel.caixas):
        packer.add_rect(c.comp_m, c.larg_m, rid=i)
    packer.add_rect(item.comp_m, item.larg_m, rid="novo")
    packer.pack()

    colocados = {}
    for abin in packer:
        for rect in abin:
            colocados[rect.rid] = rect

    if len(colocados) != len(nivel.caixas) + 1 or "novo" not in colocados:
        return None

    return colocados


def _abrir_bloco(rack: RackType, bloco_idx: int, altura_picking_m: float) -> BlocoRack:
    niveis = []
    for n in range(rack.niveis):
        base_nivel = bloco_idx * (rack.alt_nivel_m * rack.niveis) + n * rack.alt_nivel_m
        # altura_picking_m é a altura MÁXIMA até onde o operador faz picking, por
        # isso o nível inteiro tem de estar ao alcance: conta o LIMITE SUPERIOR
        # do nível (base + altura do nível), não a base. Ex.: rack de palete com
        # níveis de 1.8 m e picking a 2 m -> só o nível 0 (topo 1.8 <= 2) é de
        # picking; o nível 1 (topo 3.6) não, apesar de a base (1.8) ser < 2.
        topo_nivel = base_nivel + rack.alt_nivel_m
        eh_picking = altura_picking_m > 0 and topo_nivel <= altura_picking_m + 1e-9
        niveis.append(NivelRack(nivel_idx=n, eh_picking=eh_picking))
    return BlocoRack(bloco_idx=bloco_idx, niveis=niveis)


def _commit_nivel(nivel: NivelRack, item: _ItemParaColocar, solucao: dict) -> None:
    """Aplica a solução do rectpack ao nível: cada caixa (existentes + novo) fica
    com a footprint EXACTA como foi colocada -- comp_m/larg_m = largura/altura do
    retângulo colocado, já com a rotação do packer aplicada. As existentes são
    atualizadas in-place (mesma metadata, nova posição/orientação)."""
    novas: list[CaixaColocada] = []
    for i, c in enumerate(nivel.caixas):
        r = solucao[i]
        c.x_m, c.y_m = round(r.x, 4), round(r.y, 4)
        c.comp_m, c.larg_m = round(r.width, 4), round(r.height, 4)
        novas.append(c)
    r = solucao["novo"]
    novas.append(CaixaColocada(
        sku=item.sku, descricao=item.descricao, load_type=item.load_type,
        x_m=round(r.x, 4), y_m=round(r.y, 4),
        comp_m=round(r.width, 4), larg_m=round(r.height, 4), alt_m=item.alt_m,
        pecas_nesta_caixa=item.pecas_nesta_caixa, eh_picking=nivel.eh_picking,
        num_boxes_in_stack=item.num_boxes_in_stack,
    ))
    nivel.caixas = novas


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
        sol = _empacotar_nivel(nivel, rack, item)
        if sol is not None:
            _commit_nivel(nivel, item, sol)
            return True

    if pos.num_blocos < max_blocos:
        novo_bloco = _abrir_bloco(rack, pos.num_blocos, altura_picking_m)
        # O módulo novo só é agarrado à posição se ALGUMA caixa lá entrar --
        # senão ficava um módulo-fantasma vazio a "encher a altura". Isto
        # acontecia sobretudo com caixas de picking: um módulo empilhado
        # (bloco_idx >= 1) não tem níveis de picking, logo a caixa nunca lá
        # cabia, mas o append deixava na mesma o módulo vazio na posição.
        for nivel in novo_bloco.niveis:
            if nivel.eh_picking != preferir_picking:
                continue
            sol = _empacotar_nivel(nivel, rack, item)
            if sol is not None:
                _commit_nivel(nivel, item, sol)
                pos.blocos.append(novo_bloco)
                return True
        # bloco novo não deu encaixe na zona preferida -- tentar a outra,
        # mas só para caixas de overflow (reserva), nunca as de picking
        if permitir_fallback:
            for nivel in novo_bloco.niveis:
                if nivel.eh_picking == preferir_picking:
                    continue
                sol = _empacotar_nivel(nivel, rack, item)
                if sol is not None:
                    _commit_nivel(nivel, item, sol)
                    pos.blocos.append(novo_bloco)
                    return True
        # nada entrou no módulo novo -> descartá-lo (não fica vazio na posição)

    if not permitir_fallback:
        return False

    for nivel in niveis_outros:
        sol = _empacotar_nivel(nivel, rack, item)
        if sol is not None:
            _commit_nivel(nivel, item, sol)
            return True

    return False


def allocate_to_racks(assignments: list[BoxAssignment],
                      requirements: list[RackRequirement],
                      rack_types: list[RackType],
                      catalogo: dict[LoadType, BoxType],
                      articles: list[Article],
                      comp_max_longo: float = 1.0,
                      altura_picking_m: float = 0.0,
                      altura_armazem_m: float = 0.0,
                      cfg: Config | None = None) -> tuple[list[PosicaoChao], list[str]]:
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

    # Pre-calculate stacking capacity per load type
    boxes_per_stack_per_type: dict[LoadType, int] = {}
    for load_type in LoadType:
        if load_type == LoadType.LONGO:
            boxes_per_stack_per_type[load_type] = 1
        else:
            box = catalogo.get(load_type)
            if box:
                box_height = box.ext_alt_m
                # só racks onde a caixa CABE de facto (aceitar não chega: a
                # Prateleira Leve "aceita" L mas não tem profundidade para ela,
                # e o nível baixo dela matava o empilhamento de L nos porta-paletes)
                compatible_racks = [r for r in rack_types
                                    if load_type in r.aceita and rack_capacity(r, box) > 0]
                if compatible_racks:
                    min_level_height = min(r.alt_nivel_m for r in compatible_racks)
                    boxes_per_stack = max(1, int(min_level_height / box_height))
                    boxes_per_stack_per_type[load_type] = boxes_per_stack
                else:
                    boxes_per_stack_per_type[load_type] = 1
            else:
                boxes_per_stack_per_type[load_type] = 1

    _cfg = cfg if cfg is not None else Config()

    itens: list[_ItemParaColocar] = []
    for a in assignments:
        art = article_idx.get(a.sku)
        desc = art.descricao if art else ""

        if a.load_type == LoadType.OVERSIZE:
            continue          # não assenta em nada; já reportado em racks_needed
        if a.load_type == LoadType.LONGO:
            # "A própria rack é a caixa": o item colocado é o FEIXE de peças que
            # cabe num nível da cantilever, não uma peça solta. Antes cada peça
            # era a sua unidade (comp x 0.3 x 0.3), o que enchia a cantilever de
            # itens fictícios e ignorava largura/altura reais.
            comp_dim = art.comp_m if art else (comp_max_longo if comp_max_longo > 0 else 1.0)
            larg_dim = art.larg_m if art and art.larg_m > 0 else 0.3
            alt_dim = art.alt_m if art and art.alt_m > 0 else 0.3
            rack_c = _melhor_cantilever(rack_types)
            cap_nivel = 0
            if rack_c is not None and art is not None:
                cap_nivel = _cap_longo_sku(rack_c, art, _cfg) // max(rack_c.niveis, 1)
            boxes_per_stack = max(cap_nivel, 1)
        else:
            box = catalogo.get(a.load_type)
            if box is None:
                continue
            comp_dim, larg_dim, alt_dim = box.ext_comp_m, box.ext_larg_m, box.ext_alt_m
            boxes_per_stack = boxes_per_stack_per_type.get(a.load_type, 1)

        pecas_total = art.stock_max_un if art else 0
        # modo picking desligado (altura_picking_m <= 0): nenhum nível é de
        # picking, logo não se marca caixa nenhuma como "precisa picking" --
        # senão TODAS falhavam a colocação estrita e geravam aviso falso.
        num_unidades_picking = (math.ceil(art.pecas_por_carro / a.pecas_por_unidade)
                                if art and altura_picking_m > 0 else 0)

        # Create stacks instead of individual boxes
        boxes_allocated = 0
        while boxes_allocated < a.num_unidades:
            boxes_in_stack = min(boxes_per_stack, a.num_unidades - boxes_allocated)
            stack_height = boxes_in_stack * alt_dim

            # Calculate pieces in this stack
            pecas_cheias = boxes_allocated * a.pecas_por_unidade
            pecas_aqui = max(min(boxes_in_stack * a.pecas_por_unidade, pecas_total - pecas_cheias), 0)

            # Determine if this stack contains picking boxes
            eh_primeira_do_sku = boxes_allocated < num_unidades_picking

            itens.append(_ItemParaColocar(
                sku=a.sku, descricao=desc, load_type=a.load_type,
                comp_m=comp_dim, larg_m=larg_dim, alt_m=stack_height,
                pecas_nesta_caixa=pecas_aqui,
                eh_primeira_do_sku=eh_primeira_do_sku,
                num_boxes_in_stack=boxes_in_stack,
            ))
            boxes_allocated += boxes_in_stack

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

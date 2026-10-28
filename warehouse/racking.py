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
import itertools
import math
from dataclasses import dataclass

from rectpack import newPacker, PackingMode

from .models import (RackType, BoxType, LoadType, CaixaEspecial,
                     BoxAssignment, RackRequirement, Article,
                     PosicaoChao, BlocoRack, NivelRack, CaixaColocada)
from .boxing import (altura_real_unidade, capacidade_manual, capacidade_reportada,
                     carrinhos_fisicos, eixos_possiveis)
from .config import Config


# ----------------------------------------------------------- capacidade base

def altura_nivel(rack: RackType, idx: int) -> float:
    """Altura útil do nível `idx` (0 = base). Usa `alturas_niveis_m` quando
    definido (bordo de linha, alturas heterogéneas); senão `alt_nivel_m`
    uniforme (comportamento de sempre)."""
    if rack.alturas_niveis_m:
        return rack.alturas_niveis_m[idx]
    return rack.alt_nivel_m


def altura_ate_niveis(rack: RackType, n: int) -> float:
    """Altura total ocupada pelos primeiros `n` níveis do módulo (offset
    acumulado). Sem `alturas_niveis_m`, reduz a `alt_nivel_m * n`."""
    if rack.alturas_niveis_m:
        return sum(rack.alturas_niveis_m[:n])
    return rack.alt_nivel_m * n


def cotas_fisicas_niveis(rack: RackType, n_niveis: int, cfg: Config) -> tuple[list[float], float]:
    """(cota da base de cada um dos `n_niveis` níveis, altura do módulo) FÍSICAS,
    só para desenhar (3D). Com `alturas_niveis_m` (alturas úteis) o passo de
    cada nível é útil + folga de manuseio + viga (Config.folga_manuseio_m /
    viga_m): 1.61 + 0.10 + 0.09 = 1.80 -> vigas a 0 / 1.8 / 3.6 m. Sem elas
    (uniforme), o passo é `alt_nivel_m`, como sempre."""
    if rack.alturas_niveis_m:
        passos = [altura_nivel(rack, n) + cfg.folga_manuseio_m + cfg.viga_m for n in range(n_niveis)]
    else:
        passos = [rack.alt_nivel_m] * n_niveis
    bases = [round(sum(passos[:n]), 4) for n in range(n_niveis)]
    return bases, round(sum(passos), 4)


def rack_capacity(rack: RackType, box: BoxType) -> int:
    """Quantas unidades de carga cabem num rack (envelope 2D por nível).

    Por nível: divide footprint do nível (larg_m x prof_m) pelo footprint
    exterior da caixa (ext_comp_m x ext_larg_m), orientando a caixa da forma
    mais favorável. Valida que a altura exterior da caixa cabe no nível --
    com `alturas_niveis_m` definido (nível a nível, heterogéneo), só conta os
    níveis onde a caixa cabe; sem isso, todos os níveis têm a mesma altura
    (alt_nivel_m) e o resultado é idêntico ao de sempre (todos ou nenhum).
    Total = caixas por nível x nº de níveis compatíveis.
    """
    op1 = math.floor(rack.larg_m / box.ext_comp_m) * math.floor(rack.prof_m / box.ext_larg_m)
    op2 = math.floor(rack.larg_m / box.ext_larg_m) * math.floor(rack.prof_m / box.ext_comp_m)
    por_nivel = max(op1, op2)
    if por_nivel <= 0:
        return 0

    n_compat = sum(1 for n in range(rack.niveis)
                   if box.ext_alt_m <= altura_nivel(rack, n) + 1e-9)
    return por_nivel * n_compat


def _oversize_plano_altura(rack: RackType, art: Article,
                           peso_max_nivel_kg: float = 0.0,
                           folga_altura_m: float = 0.0
                           ) -> tuple[int, int, float, float, float]:
    """(por_plano, por_altura, plano_a, plano_b, altura_peca) para UM nível de
    OVERSIZE: quantas colunas (peças) cabem lado a lado no plano, quantas peças
    empilha CADA coluna na vertical (já cortado pelo peso), e as dimensões da
    orientação escolhida (plano_a x plano_b no chão, altura_peca na vertical).
    Ver rack_capacity_oversize para o modelo completo -- extraído para
    partilhar exatamente este cálculo (incluindo a orientação) com
    allocate_to_racks, que usa plano_a/plano_b/altura_peca para desenhar o
    item no packer 2D com a MESMA orientação usada na estimativa de
    capacidade (ver nota no bug de boxes_per_stack, corrigido nesta função).

    Orientação (ver `boxing.eixos_possiveis`): se ALTURA_OVERRIDE estiver
    vazio (livre), testam-se as 3 escolhas de cota vertical e fica a que
    MAXIMIZA a capacidade do nível (por_plano x por_altura) -- não se força
    sempre a menor cota como altura. "Painéis não se põem ao alto" é o caso
    comum, mas a maioria dos SKUs fica livre; forçar sempre a maior footprint
    desperdiçava capacidade nesses SKUs. 'x'/'y'/'z' força essa cota como
    altura, como em `boxing.pieces_per_box`."""
    if art.comp_m <= 0 or art.larg_m <= 0 or art.alt_m <= 0:
        return 0, 0, 0.0, 0.0, 0.0

    dims = (art.comp_m, art.larg_m, art.alt_m)
    eixos = eixos_possiveis(art.altura_override)

    melhor_plano, melhor_altura, melhor_cap = 0, 0, 0
    melhor_plano_a, melhor_plano_b, melhor_vertical = 0.0, 0.0, 0.0
    for iv in eixos:
        vertical = dims[iv]
        plano_a, plano_b = (dims[j] for j in range(3) if j != iv)
        if vertical <= 0:
            continue

        # no plano: as duas cotas restantes, na orientação que render mais
        por_plano = 0
        for base_c, base_l in ((plano_a, plano_b), (plano_b, plano_a)):
            if base_c <= rack.larg_m and base_l <= rack.prof_m:
                por_plano = max(por_plano,
                                math.floor(rack.larg_m / base_c) * math.floor(rack.prof_m / base_l))
        if por_plano <= 0:
            continue                      # não assenta no nível nesta orientação

        # empilhamento em altura, dentro do nível. Com alturas_niveis_m a
        # altura do nível já é ÚTIL (folga de manuseio descontada na folha
        # «Racks»), por isso não se desconta a folga OVERSIZE uma 2.ª vez --
        # senão o SIDE BODY (1.582 m de pé) não cabia nos 1.61 m.
        folga = 0.0 if rack.alturas_niveis_m else folga_altura_m
        util = max(rack.alt_nivel_m - folga, 0.0)
        if vertical > util:
            continue                      # a própria peça, de pé nesta orientação, não cabe no nível
        por_altura = max(math.floor(util / vertical), 1)

        # corte por peso do nível
        if peso_max_nivel_kg > 0 and art.peso_kg > 0:
            cabem_peso = math.floor(peso_max_nivel_kg / (art.peso_kg * por_plano)) \
                if art.peso_kg * por_plano > 0 else por_altura
            por_altura = max(min(por_altura, cabem_peso), 1)

        cap = por_plano * por_altura
        if cap > melhor_cap:
            melhor_cap, melhor_plano, melhor_altura = cap, por_plano, por_altura
            melhor_plano_a, melhor_plano_b, melhor_vertical = plano_a, plano_b, vertical

    return melhor_plano, melhor_altura, melhor_plano_a, melhor_plano_b, melhor_vertical


def rack_capacity_oversize(rack: RackType, art: Article,
                           peso_max_nivel_kg: float = 0.0,
                           folga_altura_m: float = 0.0) -> int:
    """Capacidade de uma wide-span (bulk rack) para UM SKU OVERSIZE.

    "A própria rack é a superfície": a peça fica POUSADA no nível, sem palete
    nem caixa. Por isso a capacidade não vem de unidades de carga --
    vem do footprint do nível mais o empilhamento de peças iguais:

        por_nivel = (peças no plano) x (peças empilhadas em altura)
        capacidade = por_nivel x niveis

    A orientação da peça decide qual cota fica vertical (ver `_oversize_plano_
    altura`/`boxing.eixos_possiveis`): deitada por omissão (menor cota =
    altura), mas testam-se as 3 escolhas quando `ALTURA_OVERRIDE` está vazio
    (livre), ficando a que maximiza peças por nível. Testam-se sempre as duas
    orientações no plano.

    Dois limites independentes ao empilhamento, e vale o MENOR:
      - altura útil do nível (menos a folga de manuseio);
      - peso máximo do nível -- nas peças reais é este que morde primeiro (uma
        peça de 216 kg empilharia 8x por altura mas só 4x por peso).
    """
    por_plano, por_altura, *_ = _oversize_plano_altura(rack, art, peso_max_nivel_kg, folga_altura_m)
    if por_plano <= 0:
        return 0
    return por_plano * por_altura * rack.niveis


def aceita_oversize(rack: RackType, chaves: tuple | None) -> bool:
    """True se esta tipologia é uma wide-span destinada a OVERSIZE.

    Casa por SUBSTRING do nome (case-insensitive), como o limite de módulos: a
    folha «Racks» é editada à mão e o nome exato varia. Preferiu-se isto a
    inventar uma coluna nova -- basta chamar a tipologia "Rack Wide-Span"."""
    if not chaves:
        return False
    nome = (rack.nome or "").lower()
    return any(k.lower() in nome for k in chaves)


def _melhor_oversize(rack_types: list[RackType], chaves: tuple | None) -> RackType | None:
    """Wide-span de menor footprint entre as que aceitam OVERSIZE."""
    cands = [r for r in rack_types if aceita_oversize(r, chaves)]
    return min(cands, key=_rack_footprint_m2) if cands else None


def com_par_oversize(rack_types: list[RackType], cfg: Config) -> list[RackType]:
    """Acrescenta, se existir uma tipologia elegível (Config.oversize_par_racks),
    uma tipologia SINTÉTICA "<nome base> (emparelhada)" com profundidade
    dobrada -- representa um PAR dessa tipologia costas-com-costas (a
    configuração de densidade máxima que layout.py já usa) como UMA superfície
    contínua para peças OVERSIZE. Nunca escrita no Excel -- derivada em memória
    a cada corrida, e devolvida numa lista NOVA (não muta rack_types).

    Partilha o STOCK NORMAL da tipologia base (aceita = base.aceita + OVERSIZE):
    o packer 2D por nível (rectpack) já mistura livremente tipos de carga
    compatíveis no mesmo nível, sem nunca sobrepor footprints -- por isso uma
    palete normal pode ocupar o espaço que uma peça OVERSIZE não enche num
    nível já aberto, sem duplicar estrutura. `allocate_to_racks` tenta sempre
    as posições já abertas de menor footprint primeiro (ver ordenação de
    `candidatos_por_tipo`), por isso o stock normal só usa a emparelhada
    (footprint maior) quando já está aberta e tem espaço livre -- nunca abre
    uma emparelhada nova só para paletes.

    NOTA (decisão tomada durante a implementação): a alternativa considerada
    -- reaproveitar níveis vazios de um PAR já aberto de posições normais --
    não é possível aqui: a adjacência física (que posição fica "frente"/"trás"
    da mesma fiada) só é decidida no layout.py (Passo 3b), depois deste passo
    já ter corrido. Não há como saber, durante a alocação, que duas posições
    normais vão ficar costas-com-costas."""
    # exclui sintéticas já existentes: o NOME delas ("<base> (emparelhada)")
    # também casa por substring com a chave da base -- sem isto, chamar esta
    # função 2x (ou já ter uma sintética na lista) tentaria emparelhar a
    # emparelhada.
    cands = [r for r in rack_types
             if not r.profundidade_inclui_par and aceita_oversize(r, cfg.oversize_par_racks)]
    if not cands:
        return rack_types
    base = min(cands, key=_rack_footprint_m2)
    sintetico = RackType(
        nome=f"{base.nome} (emparelhada)",
        larg_m=base.larg_m, prof_m=base.prof_m * 2,
        alt_nivel_m=base.alt_nivel_m, niveis=base.niveis,
        aceita=[*base.aceita, LoadType.OVERSIZE], corredor_m=cfg.oversize_par_corredor_m,
        profundidade_inclui_par=True,
        alturas_niveis_m=base.alturas_niveis_m,
        pilar_m=base.pilar_m,
    )
    return [*rack_types, sintetico]


def _melhor_oversize_ativo(rack_types: list[RackType], cfg: Config) -> RackType | None:
    """Prioridade para dimensionar OVERSIZE em rack: 1) wide-span dedicada
    (Config.oversize_racks) se a folha «Racks» já tiver uma; 2) par emparelhado
    sintético (ver com_par_oversize). None = nenhuma das duas -- cai para o
    chão (Config.oversize_ao_chao).

    A sintética identifica-se pela flag `profundidade_inclui_par`, NÃO por
    substring do nome: o nome dela ("<base> (emparelhada)") também contém a
    chave de Config.oversize_par_racks, por isso casar por nome apanhava a
    rack BASE (footprint menor, 2.7x1.2) em vez da sintética (2.7x2.4)."""
    wide_span = _melhor_oversize(rack_types, cfg.oversize_racks)
    if wide_span is not None:
        return wide_span
    pares = [r for r in rack_types if r.profundidade_inclui_par]
    return min(pares, key=_rack_footprint_m2) if pares else None


def carrinho_cabe_na_rack_oversize(rack_types: list[RackType], cfg: Config,
                                   alt_carrinho_m: float) -> RackType | None:
    """Rack (wide-span dedicada OU par emparelhado -- mesma prioridade de
    `_melhor_oversize_ativo`) onde um carrinho OVERSIZE2 de altura
    `alt_carrinho_m` (ver models.CaixaEspecial.alt_m -- cada caixa especial
    tem a sua própria, não é um valor global) cabe no NÍVEL DE CHÃO
    (`alt_carrinho_m <= alt_nivel_m` dessa rack). None = não há rack
    OVERSIZE ativa, ou a altura não cabe no nível -- nesses casos o
    carrinho cai para uma fiada própria ao chão (ver area.
    artigos_chao_oversize2), tal como o OVERSIZE ao chão cai quando não há
    wide-span."""
    if not cfg.carrinhos_em_rack:
        return None
    rack_o = _melhor_oversize_ativo(rack_types, cfg)
    if rack_o is None or alt_carrinho_m > rack_o.alt_nivel_m + 1e-9:
        return None
    return rack_o


def _rack_footprint_m2(rack: RackType) -> float:
    return rack.area_chao_m2


def limite_modulos(rack: RackType, max_por_tipologia: dict | None) -> int | None:
    """Máximo de módulos empilhados imposto a esta tipologia por configuração
    (Config.max_modulos_por_tipologia), ou None se não houver.

    A chave é uma SUBSTRING do nome da tipologia, case-insensitive: a folha
    «Racks» é editada à mão e "Prateleira Leve" pode aparecer com variações de
    grafia; casar por substring é mais robusto que exigir o nome exato."""
    if not max_por_tipologia:
        return None
    nome = (rack.nome or "").lower()
    limites = [v for k, v in max_por_tipologia.items() if k.lower() in nome]
    return min(limites) if limites else None


def _nome_bate(rack: RackType, chaves: tuple | None) -> bool:
    """Substring do nome da tipologia (case-insensitive) -- mesmo padrão de
    `aceita_oversize`/`limite_modulos`, generalizado para outros usos
    (ver `empilhamento_por_nivel`)."""
    if not chaves:
        return False
    nome = (rack.nome or "").lower()
    return any(k.lower() in nome for k in chaves)


# Config fields whose value is a substring-match key against RackType.nome
# (ver `_nome_bate`/`aceita_oversize`): nome do campo -> como extrair as
# chaves desse campo (a maioria já é uma tuple; max_modulos_por_tipologia é
# um dict, as chaves de negócio são as SUAS chaves, não os valores).
_CAMPOS_SUBSTRING_RACK = {
    "oversize_racks": lambda cfg: cfg.oversize_racks,
    "oversize_par_racks": lambda cfg: cfg.oversize_par_racks,
    "max_modulos_por_tipologia": lambda cfg: tuple(cfg.max_modulos_por_tipologia),
    "empilhamento_por_nivel": lambda cfg: cfg.empilhamento_por_nivel,
    "picking_manual_racks": lambda cfg: cfg.picking_manual_racks,
    "pilar_rack_m": lambda cfg: tuple(cfg.pilar_rack_m),
}


def verificar_chaves_substring_sem_correspondencia(
        rack_types: list[RackType], cfg: "Config") -> list[str]:
    """Deteta o modo de falha mais perigoso das chaves de substring do cliente
    (`Config.oversize_racks`/`oversize_par_racks`/`max_modulos_por_tipologia`/
    `empilhamento_por_nivel`/`picking_manual_racks`): uma chave CONFIGURADA
    que não casa NENHUMA tipologia real da folha «Racks» deste cliente.

    Sem este aviso, esse caso é indistinguível de um sucesso limpo -- o
    pipeline continua a correr e a produzir uma área plausível, só que a
    regra de negócio correspondente (OVERSIZE em rack dedicado, par
    emparelhado, limite de módulos, empilhamento nível-a-nível, ou picking
    manual nunca fragmentado) fica silenciosamente inativa, tipicamente
    porque o nome da tipologia mudou na folha «Racks» ou a chave foi copiada
    de outro cliente sem ajustar. Chamar ANTES de `com_par_oversize` (que
    acrescenta uma tipologia sintética cujo nome também casaria a própria
    chave, mascarando exatamente o caso que este aviso existe para apanhar).

    Uma chave vazia (`()`/`{}`, cliente que não usa a regra) nunca gera
    aviso -- só uma chave configurada e sem correspondência é sinal de
    problema."""
    nomes = [r.nome for r in rack_types]
    avisos: list[str] = []
    for campo, extrator in _CAMPOS_SUBSTRING_RACK.items():
        chaves = extrator(cfg)
        if not chaves:
            continue
        if any(_nome_bate(r, chaves) for r in rack_types):
            continue
        lista_nomes = ", ".join(f"'{n}'" for n in nomes) if nomes else "(nenhuma tipologia na folha «Racks»)"
        avisos.append(
            f"Config.{campo}={list(chaves)} (cliente '{cfg.client_name or '?'}') não "
            f"corresponde a NENHUMA tipologia da folha «Racks» -- {lista_nomes}. "
            f"Esta regra de negócio está configurada mas NUNCA vai disparar: "
            f"verifica se o nome da tipologia mudou no Excel, ou corrige "
            f"'{campo}' em dados/clients/{cfg.client_name or '<cliente>'}.json."
        )
    return avisos


def blocos_empilhados(rack: RackType, altura_armazem_m: float,
                      max_por_tipologia: dict | None = None,
                      empilhamento_por_nivel: tuple | None = None) -> int:
    """Quantos módulos físicos deste RackType cabem empilhados, na mesma
    posição de chão, até à altura do armazém (TETO, não um valor imposto:
    cada posição decide quantos módulos USA, até este máximo).

    altura_armazem_m <= 0 -> sem limite definido: 1 módulo (comportamento
    atual, niveis do Excel já é o total do prumo).

    max_por_tipologia (Config.max_modulos_por_tipologia) trava tipologias que não
    devem crescer em altura mesmo que o pé-direito o permita -- ex.: prateleira
    leve servida por operador a pé. Quando o limite morde, o stock que subiria
    passa a abrir MAIS posições de chão (ver _tentar_colocar_em_posicao: uma
    posição só empilha enquanto num_blocos < max_blocos), o que é o efeito
    pretendido: espalhar ao lado em vez de subir.

    `empilhamento_por_nivel` (Config.empilhamento_por_nivel): tipologias cujo
    nome bate são tratadas ao contrário -- NUNCA empilham módulos inteiros
    (devolve sempre 1), o próprio módulo é que se ajusta ao pé-direito
    disponível nível a nível (ver `niveis_efetivos`). Um módulo de Porta-
    Paletes de 7.2 m não cabe todo num armazém de 5 m -- módulo a módulo isso
    dava 0 módulos (corrigido para 1, ultrapassando o teto); nível a nível dá
    1 módulo mais baixo (2 dos 4 níveis), que cabe mesmo."""
    if _nome_bate(rack, empilhamento_por_nivel):
        return 1
    altura_modulo = altura_ate_niveis(rack, rack.niveis)
    if altura_armazem_m <= 0 or altura_modulo <= 0:
        cabem = 1
    else:
        cabem = max(math.floor(altura_armazem_m / altura_modulo), 1)
    teto = limite_modulos(rack, max_por_tipologia)
    return min(cabem, teto) if teto is not None else cabem


def niveis_efetivos(rack: RackType, altura_armazem_m: float,
                    empilhamento_por_nivel: tuple | None = None) -> int:
    """Nº de níveis do ÚNICO módulo de uma tipologia de empilhamento NÍVEL A
    NÍVEL (ver `blocos_empilhados`) -- nunca mais do que `rack.niveis` (o
    desenho do catálogo, não se inventam níveis que a folha «Racks» não deu),
    nunca menos de 1. Tipologias fora de `empilhamento_por_nivel` devolvem
    sempre `rack.niveis` (módulo completo -- é `_abrir_bloco` sem override,
    comportamento de sempre)."""
    if altura_armazem_m <= 0 or rack.alt_nivel_m <= 0:
        return rack.niveis
    if not _nome_bate(rack, empilhamento_por_nivel):
        return rack.niveis
    if rack.alturas_niveis_m:
        # alturas heterogéneas: conta os níveis que cabem em altura acumulada
        # (ex.: 1.8 + 1.8 + 1.0 cabem em 4.6 m), não floor(pé-direito / maior nível)
        cabem, acumulado = 0, 0.0
        for n in range(rack.niveis):
            acumulado += altura_nivel(rack, n)
            if acumulado > altura_armazem_m + 1e-9:
                break
            cabem += 1
        return min(max(cabem, 1), rack.niveis)
    cabem = max(math.floor(altura_armazem_m / rack.alt_nivel_m), 1)
    return min(cabem, rack.niveis)


def best_rack_for_load(load_type: LoadType, rack_types: list[RackType],
                       catalogo: dict[LoadType, BoxType],
                       altura_armazem_m: float = 0.0,
                       max_modulos: dict | None = None,
                       empilhamento_por_nivel: tuple | None = None) -> tuple[RackType, int, int] | None:
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

        box = catalogo.get(load_type)
        cap = rack_capacity(rack, box) if box else 0

        if cap <= 0:
            continue

        blocos = blocos_empilhados(rack, altura_armazem_m, max_modulos, empilhamento_por_nivel)
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

    for lt, total_unidades in totais.items():
        if lt == LoadType.OVERSIZE2:
            # Cada caixa especial (models.CaixaEspecial, ver Config.
            # caixas_especiais) tem a sua PRÓPRIA capacidade/dimensões --
            # agrupa os assignments deste tipo de carga por caixa especial
            # (SKUs que partilham o mesmo registo -- mesmo objeto Python,
            # via caixas_especiais_por_sku -- partilham o mesmo cálculo).
            # Cada grupo fica SEMPRE ao solo (nunca sobe de nível), mas
            # dentro do FOOTPRINT da mesma rack usada para OVERSIZE (wide-
            # span/emparelhada) quando existe e o carrinho cabe no nível de
            # chão -- carrinho_cabe_na_rack_oversize. Sem isso, cai para uma
            # fiada própria ao chão (ver area.artigos_chao_oversize2).
            por_caixa: dict[int, tuple[CaixaEspecial, list[BoxAssignment]]] = {}
            for a in assignments:
                if a.load_type != LoadType.OVERSIZE2:
                    continue
                especial = cfg.caixas_especiais.get(a.sku)
                if especial is None:
                    continue
                chave = id(especial)
                if chave not in por_caixa:
                    por_caixa[chave] = (especial, [])
                por_caixa[chave][1].append(a)

            for especial, lista in por_caixa.values():
                total_grupo = max(a.num_unidades for a in lista)   # carrinho partilhado: ver boxing.carrinhos_fisicos
                rotulo = especial.notas or ", ".join(especial.skus)
                rack_c = carrinho_cabe_na_rack_oversize(rack_types, cfg, especial.alt_m)
                if rack_c is None:
                    avisos.append(
                        f"{total_grupo} unidade(s) de caixa especial '{rotulo}' "
                        f"({len(lista)} SKU) -- sem rack wide-span/emparelhada onde "
                        f"caibam no nível de chão -- arrumadas EM FIADA PRÓPRIA ao "
                        f"chão, ver 'Area ao chao' no resumo.")
                    continue
                # Estimativa: cada MÓDULO da rack contribui só o seu nível de
                # chão para carrinhos (os níveis acima continuam livres para
                # OVERSIZE/outras cargas -- ver allocate_to_racks) -- por
                # isso a capacidade aqui NÃO multiplica por rack_c.niveis,
                # ao contrário de rack_capacity/rack_capacity_oversize.
                cap_nivel0 = math.floor(rack_c.larg_m / especial.comp_m) * \
                    math.floor(rack_c.prof_m / especial.larg_m)
                if cap_nivel0 <= 0:
                    avisos.append(
                        f"{total_grupo} unidade(s) de caixa especial '{rotulo}' não "
                        f"cabem no plano do nível de chão da '{rack_c.nome}' "
                        f"({rack_c.larg_m:.2f} x {rack_c.prof_m:.2f} m) -- arrumadas "
                        f"EM FIADA PRÓPRIA ao chão.")
                    continue
                modulos_c = max(math.ceil(total_grupo / cap_nivel0), 1)
                por_tipologia.setdefault(rack_c.nome, {})
                por_tipologia[rack_c.nome][lt] = por_tipologia[rack_c.nome].get(lt, 0) + modulos_c
            continue
        if lt == LoadType.OVERSIZE:
            # Peças que não assentam em nenhuma unidade de carga do catálogo.
            # Se houver uma wide-span na folha «Racks» (Config.oversize_racks) ou
            # um par emparelhado sintético (Config.oversize_par_racks, ver
            # com_par_oversize/_melhor_oversize_ativo), são dimensionadas nela --
            # pousadas no nível, sem palete. Senão, ficam só em aviso
            # (comportamento anterior): nunca se inventa capacidade contando-as
            # como 1/palete.
            rack_o = _melhor_oversize_ativo(rack_types, cfg)
            if rack_o is None:
                if cfg.oversize_ao_chao:
                    # Sem tipologia wide-span: arruma-se EM PILHA NO CHÃO. A área
                    # é calculada aqui (area.area_oversize_chao) e reportada à
                    # parte -- não há posições de rack, logo não entra em
                    # requirements nem no layout de filas.
                    n_sku = sum(1 for a in assignments
                                if a.load_type == LoadType.OVERSIZE)
                    avisos.append(
                        f"{total_unidades} peca(s) OVERSIZE ({n_sku} SKU) arrumadas "
                        f"AO CHAO em pilha (sem rack) -- ver 'Area ao chao' no resumo. "
                        f"Para as pôr em rack, acrescente à folha «Racks» uma "
                        f"tipologia com 'wide' ou 'bulk' no nome.")
                else:
                    avisos.append(
                        f"{total_unidades} peca(s) OVERSIZE: nao assentam na footprint "
                        f"da palete em nenhuma orientacao -- NAO dimensionadas. "
                        f"Requerem solucao dedicada (wide-span/chao). Para as dimensionar, "
                        f"acrescente à folha «Racks» uma tipologia com 'wide' ou 'bulk' no nome.")
                continue
            blocos_o = blocos_empilhados(rack_o, cfg.altura_armazem_m,
                                        cfg.max_modulos_por_tipologia, cfg.empilhamento_por_nivel)
            modulos_o = 0.0
            sem_cap_o = []
            for a in assignments:
                if a.load_type != LoadType.OVERSIZE:
                    continue
                art = (article_idx or {}).get(a.sku)
                cap = rack_capacity_oversize(
                    rack_o, art, cfg.oversize_peso_max_nivel_kg,
                    cfg.oversize_folga_altura_m) if art else 0
                if cap <= 0:
                    sem_cap_o.append(a.sku)
                    continue
                modulos_o += a.num_unidades / cap
            if sem_cap_o:
                avisos.append(
                    f"{len(sem_cap_o)} SKU(s) OVERSIZE nao assentam no nivel da "
                    f"'{rack_o.nome}' ({rack_o.larg_m:.2f} x {rack_o.prof_m:.2f} m) "
                    f"-- EXCLUIDOS: {', '.join(sem_cap_o[:8])}"
                    + (" ..." if len(sem_cap_o) > 8 else ""))
            if modulos_o <= 0:
                continue
            num_racks_o = max(math.ceil(modulos_o), 1)
            por_tipologia.setdefault(rack_o.nome, {})[lt] = num_racks_o
            continue
        escolha = best_rack_for_load(lt, rack_types, catalogo, cfg.altura_armazem_m,
                                     empilhamento_por_nivel=cfg.empilhamento_por_nivel)
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
        blocos_max = blocos_empilhados(rack, cfg.altura_armazem_m,
                                       cfg.max_modulos_por_tipologia, cfg.empilhamento_por_nivel)
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
    if item.alt_m > altura_nivel(rack, nivel.nivel_idx) + 1e-9:
        return None

    dims = [(c.comp_m, c.larg_m) for c in nivel.caixas] + [(item.comp_m, item.larg_m)]
    ids: list = list(range(len(nivel.caixas))) + ["novo"]

    # 1.º tenta uma arrumação ORDENADA: todas as caixas com a mesma footprint
    # ficam com a MESMA orientação (grelha alinhada). Só se isso não couber se
    # recorre ao packer livre abaixo. O packer com rotação livre fecha o lote
    # com o que encontrar e mistura orientações no mesmo nível (ex.: 3 meias-
    # paletes de 0,6 x 0,8 e 2 rodadas a 0,8 x 0,6, com 0,4 m de profundidade
    # vazia à frente das primeiras): frentes desalinhadas e 5 meias por nível
    # quando a grelha 3 x 2 leva 6.
    colocados = _empacotar_alinhado(rack, dims, ids)
    if colocados is not None:
        return colocados

    packer = newPacker(mode=PackingMode.Offline, rotation=True)
    packer.add_bin(rack.larg_m, rack.prof_m)
    for rid, (w, h) in zip(ids, dims):
        packer.add_rect(w, h, rid=rid)
    packer.pack()

    colocados = {}
    for abin in packer:
        for rect in abin:
            colocados[rect.rid] = rect

    if len(colocados) != len(nivel.caixas) + 1 or "novo" not in colocados:
        return None

    return colocados


_MAX_FORMAS_ALINHADO = 4   # 2^4 = 16 combinações de orientação, no máximo


def _empacotar_alinhado(rack: RackType, dims: list[tuple[float, float]], ids: list):
    """Arrumação sem rotação livre: cada footprint distinta (lado curto x lado
    longo) recebe UMA orientação fixa, igual para todas as caixas dessa
    footprint. Testa as combinações de orientação por ordem (lado longo ao
    longo da profundidade primeiro, como a palete no rack) e devolve a 1.ª
    onde o lote inteiro cabe; None se nenhuma couber ou houver formas demais."""
    if sum(w * h for w, h in dims) > rack.larg_m * rack.prof_m + 1e-9:
        return None
    formas = sorted({tuple(sorted((round(w, 4), round(h, 4)))) for w, h in dims})
    if len(formas) > _MAX_FORMAS_ALINHADO:
        return None

    for escolha in itertools.product((0, 1), repeat=len(formas)):
        ori = dict(zip(formas, escolha))
        packer = newPacker(mode=PackingMode.Offline, rotation=False)
        packer.add_bin(rack.larg_m, rack.prof_m)
        for rid, (w, h) in zip(ids, dims):
            curto, longo = sorted((w, h))
            forma = tuple(sorted((round(w, 4), round(h, 4))))
            # 0: lado longo na profundidade (y); 1: lado longo no vão (x)
            packer.add_rect(*((curto, longo) if ori[forma] == 0 else (longo, curto)), rid=rid)
        packer.pack()
        colocados = {}
        for abin in packer:
            for rect in abin:
                colocados[rect.rid] = rect
        if len(colocados) == len(dims):
            return colocados
    return None


def _abrir_bloco(rack: RackType, bloco_idx: int, altura_picking_m: float,
                 niveis_override: int | None = None) -> BlocoRack:
    """`niveis_override`: nº de níveis a construir NESTE módulo, em vez de
    `rack.niveis` -- usado pelas tipologias de empilhamento nível a nível
    (ver `niveis_efetivos`), cujo único módulo pode ter menos níveis do que o
    catálogo prevê, quando o pé-direito não dá para todos. Sem override
    (comportamento de sempre), o módulo continua completo."""
    n_niveis = niveis_override if niveis_override is not None else rack.niveis
    altura_modulo = altura_ate_niveis(rack, rack.niveis)
    niveis = []
    for n in range(n_niveis):
        base_nivel = bloco_idx * altura_modulo + altura_ate_niveis(rack, n)
        # altura_picking_m é a altura MÁXIMA até onde o operador faz picking, por
        # isso o nível inteiro tem de estar ao alcance: conta o LIMITE SUPERIOR
        # do nível (base + altura do nível), não a base. Ex.: rack de palete com
        # níveis de 1.8 m e picking a 2 m -> só o nível 0 (topo 1.8 <= 2) é de
        # picking; o nível 1 (topo 3.6) não, apesar de a base (1.8) ser < 2.
        topo_nivel = base_nivel + altura_nivel(rack, n)
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


def _tentar_colocar_carrinho(pos: PosicaoChao, rack: RackType, item: _ItemParaColocar) -> bool:
    """Coloca um carrinho OVERSIZE2 -- SEMPRE no NÍVEL 0 do BLOCO 0 desta
    posição (nunca sobe de nível, nem abre bloco novo além do 0): é um
    carrinho ao solo, não uma caixa que sobe pela rack. Só tenta esse único
    nível; se não couber, falha (o chamador tenta outra posição ou abre uma
    nova -- sempre com bloco 0 recém-aberto, nunca um bloco > 0)."""
    if not pos.blocos or not pos.blocos[0].niveis:
        return False
    nivel0 = pos.blocos[0].niveis[0]
    sol = _empacotar_nivel(nivel0, rack, item)
    if sol is None:
        return False
    _commit_nivel(nivel0, item, sol)
    return True


def _tentar_colocar_em_posicao(pos: PosicaoChao, rack: RackType, item: _ItemParaColocar,
                                altura_picking_m: float, max_blocos: int,
                                preferir_picking: bool, permitir_fallback: bool = True,
                                niveis_override: int | None = None) -> bool:
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
        novo_bloco = _abrir_bloco(rack, pos.num_blocos, altura_picking_m, niveis_override)
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
    # cfg é opcional nesta função: resolve-se uma vez aqui, em vez de a cada uso
    _cfg = cfg if cfg is not None else Config()
    max_modulos = _cfg.max_modulos_por_tipologia
    # Tipologias de empilhamento NÍVEL A NÍVEL (ver blocos_empilhados/
    # niveis_efetivos): o único módulo delas usa este nº de níveis em vez de
    # rack.niveis -- calculado uma vez aqui (não muda durante a alocação,
    # só depende da tipologia + pé-direito) e passado a _abrir_bloco em cada
    # posição nova, para o packer nunca ver mais níveis do que os que cabem
    # fisicamente (e por isso abrir mais posições em vez de perder stock).
    niveis_ov_por_rack = {
        r.nome: niveis_efetivos(r, altura_armazem_m, _cfg.empilhamento_por_nivel)
        for r in rack_types
    }

    candidatos_por_tipo: dict[LoadType, list[RackType]] = {}
    for lt in LoadType:
        compativeis = [r for r in rack_types if lt in r.aceita]
        if lt in (LoadType.OVERSIZE, LoadType.OVERSIZE2):
            # Não há BoxType para OVERSIZE/OVERSIZE2 (não vão a caixa): a
            # compatibilidade vem do NOME da tipologia, não do catálogo. Aceita-se
            # também quem declare OVERSIZE na coluna «Aceita» da folha «Racks».
            # OVERSIZE2 partilha a MESMA rack de OVERSIZE (carrinho ao solo
            # dentro do footprint da wide-span/emparelhada) -- ver
            # carrinho_cabe_na_rack_oversize/_tentar_colocar_carrinho abaixo.
            compativeis = [r for r in rack_types
                           if aceita_oversize(r, _cfg.oversize_racks)
                           or LoadType.OVERSIZE in r.aceita]
        else:
            box = catalogo.get(lt)
            compativeis = [r for r in compativeis
                          if box and rack_capacity(r, box) > 0]
        compativeis.sort(key=lambda r: _rack_footprint_m2(r))
        candidatos_por_tipo[lt] = compativeis

    # Pre-calculate stacking capacity per load type
    boxes_per_stack_per_type: dict[LoadType, int] = {}
    for load_type in LoadType:
        box = catalogo.get(load_type)
        if box:
            box_height = box.ext_alt_m
            # só racks onde a caixa CABE de facto (aceitar não chega: a
            # Prateleira Leve "aceita" L mas não tem profundidade para ela,
            # e o nível baixo dela matava o empilhamento de L nos porta-paletes)
            compatible_racks = [r for r in rack_types
                                if load_type in r.aceita and rack_capacity(r, box) > 0]
            # Alturas de TODOS os níveis (de todos os racks compatíveis) onde
            # a caixa efetivamente cabe -- não só um valor por rack: com
            # alturas_niveis_m heterogéneo, níveis diferentes do MESMO rack
            # podem ter alturas diferentes. Sem alturas_niveis_m, isto reduz
            # a "alt_nivel_m repetido niveis vezes" == comportamento de sempre.
            alturas_compat = [altura_nivel(r, n)
                              for r in compatible_racks
                              for n in range(r.niveis)
                              if altura_nivel(r, n) + 1e-9 >= box_height]
            if alturas_compat:
                min_level_height = min(alturas_compat)
                boxes_per_stack = max(1, int(min_level_height / box_height))
                boxes_per_stack_per_type[load_type] = boxes_per_stack
            else:
                boxes_per_stack_per_type[load_type] = 1
        else:
            boxes_per_stack_per_type[load_type] = 1

    itens: list[_ItemParaColocar] = []
    fisicos = carrinhos_fisicos(assignments, _cfg)
    for a in assignments:
        art = article_idx.get(a.sku)
        desc = art.descricao if art else ""

        if a.load_type == LoadType.OVERSIZE:
            # Wide-span OU par emparelhado (ver _melhor_oversize_ativo): a peça
            # fica POUSADA no nível, sem palete nem caixa -- o "item" é o
            # conjunto que cabe num nível. As dimensões do item vêm da MESMA
            # orientação escolhida por _oversize_plano_altura (deitada por
            # omissão, de pé quando a orientação permite e rende mais
            # capacidade) -- tem de ser a mesma usada na estimativa de
            # capacidade, senão o packer real coloca a peça maior do que o
            # nível "sabe" que cabe.
            rack_o = _melhor_oversize_ativo(rack_types, _cfg)
            if rack_o is None or art is None:
                continue      # sem wide-span/par na folha: fica em aviso (racks_needed)
            # boxes_per_stack tem de ser SÓ o empilhamento vertical de UMA
            # coluna (por_altura) -- o "lado a lado" (por_plano) é resolvido
            # pelo próprio packer 2D ao colocar várias stacks, tal como as
            # outras cargas. Usar cap_total (que já inclui por_plano) aqui
            # inflava a altura da stack por um fator de por_plano: uma peça
            # com por_plano=2 e por_altura=11 ficava com UMA stack de 22 peças
            # de altura em vez de 2 stacks de 11 -- quase sempre mais alta que
            # o nível, falhando a colocação para TODA a peça, em silêncio
            # (bug confirmado: 0 de 40 unidades colocadas num caso de teste).
            por_plano, por_altura, plano_a, plano_b, altura_peca = _oversize_plano_altura(
                rack_o, art, _cfg.oversize_peso_max_nivel_kg,
                _cfg.oversize_folga_altura_m)
            if por_plano <= 0:
                continue      # não assenta no nível: já reportado em racks_needed
            comp_dim, larg_dim, alt_dim = plano_a, plano_b, altura_peca
            boxes_per_stack = max(por_altura, 1)
        elif a.load_type == LoadType.OVERSIZE2:
            # Caixa especial (models.CaixaEspecial): footprint/altura FIXOS
            # (não geometria da peça), próprios de CADA registo -- não um
            # valor global. Só entra aqui (para ser colocada dentro do
            # footprint da rack OVERSIZE) quando essa rack existe e a caixa
            # cabe no seu nível de chão -- ver carrinho_cabe_na_rack_oversize.
            # Caso contrário fica de fora (continue): main.py/web.py já não a
            # adicionam aos artigos_chao normais, tratando-a à parte como
            # fiada própria ao chão (area.artigos_chao_oversize2).
            especial = _cfg.caixas_especiais.get(a.sku)
            if especial is None:
                continue
            rack_c = carrinho_cabe_na_rack_oversize(rack_types, _cfg, especial.alt_m)
            if rack_c is None:
                continue
            comp_dim, larg_dim, alt_dim = especial.comp_m, especial.larg_m, especial.alt_m
            boxes_per_stack = 1   # carrinho nunca empilha -- sempre 1 por posição
        else:
            box = catalogo.get(a.load_type)
            if box is None:
                continue
            comp_dim, larg_dim, alt_dim = box.ext_comp_m, box.ext_larg_m, box.ext_alt_m
            if a.load_type in (LoadType.PALLET, LoadType.MEIA) and art is not None:
                # Altura REAL (deck + carga das peças que lá vão), não a nominal
                # de uma palete cheia: uma palete baixa cabe num nível mais baixo
                # (_empacotar_nivel testa a altura nível a nível). O filtro de
                # tipologias (candidatos_por_tipo/rack_capacity) e o
                # boxes_per_stack continuam com a nominal.
                pecas_unidade = capacidade_reportada(
                    a.load_type, a.pecas_por_unidade, a.sku, _cfg,
                    manual=capacidade_manual(art, a.load_type))
                alt_dim = altura_real_unidade(art, box, pecas_unidade, _cfg)
            boxes_per_stack = boxes_per_stack_per_type.get(a.load_type, 1)

        # modo picking desligado (altura_picking_m <= 0): nenhum nível é de
        # picking, logo não se marca caixa nenhuma como "precisa picking" --
        # senão TODAS falhavam a colocação estrita e geravam aviso falso.
        num_unidades_picking = (math.ceil(art.pecas_por_carro / a.pecas_por_unidade)
                                if art and altura_picking_m > 0 else 0)

        # Create stacks instead of individual boxes
        boxes_allocated = 0
        n_unidades = fisicos[a.sku] if a.load_type == LoadType.OVERSIZE2 else a.num_unidades
        while boxes_allocated < n_unidades:
            boxes_in_stack = min(boxes_per_stack, n_unidades - boxes_allocated)
            stack_height = boxes_in_stack * alt_dim

            # Peças nesta caixa/stack: SEMPRE a capacidade cheia da caixa
            # (boxes_in_stack x peças_por_unidade), nunca o stock restante.
            # `pecas_por_unidade` já vem de pieces_per_box com o fator de
            # ocupação aplicado (cfg.fill_factor_plano), logo é o MÁXIMO que a
            # caixa leva a essa ocupação. Aplica-se a todos os SKUs, incluindo
            # os que só precisam de 1 caixa: a pergunta que esta coluna responde
            # é "quanto cabe de facto no armazém dimensionado", não "quanto
            # stock a master data pede". O total por SKU pode assim ficar bem
            # acima do stock_max_un (peça pequena numa caixa grande cabe aos
            # milhares) -- é esperado, não é bug: o espaço é o mesmo, muda só a
            # quantidade armazenada.
            pecas_aqui = boxes_in_stack * capacidade_reportada(
                a.load_type, a.pecas_por_unidade, a.sku, _cfg,
                manual=art is not None and capacidade_manual(art, a.load_type))

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

    # first-fit-decreasing por área (footprint exterior) e, na mesma área, as
    # mais ALTAS primeiro: as paletes altas ocupam os níveis onde só elas cabem
    # e as baixas enchem depois o nível de cima, em vez de lhes roubarem os
    # lugares de baixo (sem isto o resultado oscilava ±1-2 módulos só com a
    # ordem dos SKUs). Sort estável: preserva a ordem i=0,1,2.. dentro do
    # mesmo SKU (mesma área e altura).
    itens.sort(key=lambda it: (it.comp_m * it.larg_m, it.alt_m), reverse=True)

    posicoes_por_tipologia: dict[str, list[PosicaoChao]] = {r.nome: [] for r in rack_types}
    sku_posicao: dict[str, tuple[str, int]] = {}   # sku -> (rack_nome, indice) com picking
    avisos_picking: set[str] = set()
    nao_colocados: dict[str, int] = {}   # sku -> unidades que não couberam em lado nenhum

    for item in itens:
        candidatos = candidatos_por_tipo.get(item.load_type, [])
        if not candidatos:
            nao_colocados[item.sku] = nao_colocados.get(item.sku, 0) + item.num_boxes_in_stack
            continue

        if item.load_type == LoadType.OVERSIZE2:
            # Carrinho: SEMPRE nível 0 / bloco 0 -- nunca a lógica de
            # picking/reserva nem a de empilhar blocos de _tentar_colocar_
            # em_posicao (essa serve caixas que sobem na rack; um carrinho
            # nunca sobe). Tenta posições já abertas primeiro, só abre nova
            # se nenhuma tiver espaço no seu nível de chão.
            colocado = False
            for rack in candidatos:
                for pos in posicoes_por_tipologia[rack.nome]:
                    if _tentar_colocar_carrinho(pos, rack, item):
                        colocado = True
                        break
                if colocado:
                    break
            if not colocado:
                for rack in candidatos:
                    lista = posicoes_por_tipologia[rack.nome]
                    pos = PosicaoChao(rack_nome=rack.nome, indice=len(lista))
                    pos.blocos.append(_abrir_bloco(rack, 0, altura_picking_m,
                                                   niveis_ov_por_rack[rack.nome]))
                    if _tentar_colocar_carrinho(pos, rack, item):
                        colocado = True
                        lista.append(pos)
                        break
            # Não coube em nada (footprint do carrinho maior que o nível de
            # chão de todas as candidatas): fica de fora -- já reportado em
            # racks_needed/carrinho_cabe_na_rack_oversize, main.py trata como
            # fiada própria ao chão.
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
                max_blocos = blocos_empilhados(rack, altura_armazem_m, max_modulos, _cfg.empilhamento_por_nivel)
                colocado = _tentar_colocar_em_posicao(pos, rack, item, altura_picking_m,
                                                      max_blocos, preferir, permitir_fallback,
                                                      niveis_override=niveis_ov_por_rack[rack.nome])

        # 2) esgotar capacidade já aberta (todas as posições, todas as
        # tipologias compatíveis) antes de abrir posição nova
        if not colocado:
            for rack in candidatos:
                max_blocos = blocos_empilhados(rack, altura_armazem_m, max_modulos, _cfg.empilhamento_por_nivel)
                for pos in posicoes_por_tipologia[rack.nome]:
                    if _tentar_colocar_em_posicao(pos, rack, item, altura_picking_m,
                                                  max_blocos, preferir, permitir_fallback,
                                                  niveis_override=niveis_ov_por_rack[rack.nome]):
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
                max_blocos = blocos_empilhados(rack, altura_armazem_m, max_modulos, _cfg.empilhamento_por_nivel)
                lista = posicoes_por_tipologia[rack.nome]
                pos = PosicaoChao(rack_nome=rack.nome, indice=len(lista))
                pos.blocos.append(_abrir_bloco(rack, 0, altura_picking_m, niveis_ov_por_rack[rack.nome]))
                if _tentar_colocar_em_posicao(pos, rack, item, altura_picking_m,
                                              max_blocos, preferir, permitir_fallback,
                                              niveis_override=niveis_ov_por_rack[rack.nome]):
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
                max_blocos = blocos_empilhados(rack, altura_armazem_m, max_modulos, _cfg.empilhamento_por_nivel)
                for pos in posicoes_por_tipologia[rack.nome]:
                    if _tentar_colocar_em_posicao(pos, rack, item, altura_picking_m,
                                                  max_blocos, preferir, permitir_fallback=True,
                                                  niveis_override=niveis_ov_por_rack[rack.nome]):
                        colocado = True
                        break
                if colocado:
                    break
            if not colocado:
                rack = candidatos[0]
                max_blocos = blocos_empilhados(rack, altura_armazem_m, max_modulos, _cfg.empilhamento_por_nivel)
                lista = posicoes_por_tipologia[rack.nome]
                pos = PosicaoChao(rack_nome=rack.nome, indice=len(lista))
                pos.blocos.append(_abrir_bloco(rack, 0, altura_picking_m, niveis_ov_por_rack[rack.nome]))
                colocado = _tentar_colocar_em_posicao(pos, rack, item, altura_picking_m,
                                                      max_blocos, preferir, permitir_fallback=True,
                                                      niveis_override=niveis_ov_por_rack[rack.nome])
                if colocado:
                    lista.append(pos)
            if colocado:
                avisos_picking.add(item.sku)

        # item não cabe em nada compatível (caixa/stack maior que qualquer
        # nível compatível) -- fica de fora, mas nunca em silêncio (aviso abaixo).
        if not colocado:
            nao_colocados[item.sku] = nao_colocados.get(item.sku, 0) + item.num_boxes_in_stack

    avisos: list[str] = []
    if avisos_picking:
        avisos.append(
            f"{len(avisos_picking)} SKU(s) sem zona de picking disponivel "
            f"(capacidade de picking esgotada) -- caixa alocada em reserva.")
        print(f"AVISO: {avisos[-1]}")
    if nao_colocados:
        lista = ", ".join(f"{sku} ({n})" for sku, n in list(nao_colocados.items())[:8])
        avisos.append(
            f"{sum(nao_colocados.values())} unidade(s) de {len(nao_colocados)} SKU(s) NAO "
            f"colocadas -- não cabem em nenhum nível das tipologias compatíveis: {lista}"
            + (" ..." if len(nao_colocados) > 8 else ""))
        print(f"AVISO: {avisos[-1]}")

    resultado: list[PosicaoChao] = []
    for lista in posicoes_por_tipologia.values():
        resultado.extend(lista)
    return resultado, avisos


# ------------------------------------------------------------------ ocupação

def capacidade_por_posicao(rack: RackType, cfg: Config) -> dict:
    """Capacidade de UMA posição de chão desta tipologia, com a configuração
    atual (pé-direito, tetos de empilhamento, empilhamento nível a nível).

    Existe para o front end poder contar a capacidade de uma fila de racks
    SANDBOX (colocada à mão no canvas, ver layout.gerar_fila_manual) sem
    reimplementar `blocos_empilhados`/`niveis_efetivos` em JavaScript: o nº de
    níveis de uma posição NÃO é `rack.niveis` do catálogo, depende de
    altura_armazem_m + Config.max_modulos_por_tipologia +
    Config.empilhamento_por_nivel. Duplicar essa regra no cliente era garantir
    que um dia divergia da do servidor.

    A área/volume de um nível são os do packer 2D real (ver _cabe_no_nivel:
    `packer.add_bin(rack.larg_m, rack.prof_m)`), não uma aproximação."""
    blocos = blocos_empilhados(rack, cfg.altura_armazem_m,
                               cfg.max_modulos_por_tipologia,
                               cfg.empilhamento_por_nivel)
    niveis = niveis_efetivos(rack, cfg.altura_armazem_m, cfg.empilhamento_por_nivel)
    area_nivel = rack.larg_m * rack.prof_m
    return {
        "blocos_por_posicao": blocos,
        "niveis_por_posicao": niveis,
        "niveis_por_posicao_total": blocos * niveis,
        "area_nivel_m2": round(area_nivel, 4),
        "volume_nivel_m3": round(area_nivel * rack.alt_nivel_m, 4),
    }


def resumo_ocupacao(posicoes: list[PosicaoChao], rack_by_name: dict) -> dict:
    """Quanto do espaço de armazenagem CONSTRUÍDO está de facto usado.

    Três granularidades, porque sozinha nenhuma é honesta:
    - `niveis`: um nível conta como ocupado se tiver ≥1 caixa. É o número que
      um gestor de armazém reconhece, mas é otimista -- um nível com uma caixa
      conta igual a um cheio.
    - `area`: soma da footprint das caixas colocadas / soma de
      (rack.larg_m x rack.prof_m) por nível. É a mesma área que o packer 2D usa
      como bin, por isso 100% aqui significa mesmo "não cabe lá mais nada".
    - `volume`: idem em 3D, contra rack.alt_nivel_m. `CaixaColocada.alt_m` JÁ é
      a altura do stack inteiro (ver _empacotar_em_stacks, stack_height) --
      multiplicar por num_boxes_in_stack contaria a dobrar.

    Só olha para o que existe: não conta racks fantasma (preview) nem filas
    sandbox (o front end acrescenta essas ao denominador, ver
    capacidade_por_posicao).

    `por_nivel` (por tipologia): para cada índice de nível dentro do módulo
    (1 = base), quantos níveis desses existem, quantos têm carga e quantas
    unidades lá estão (`num_boxes_in_stack`; num OVERSIZE são peças)."""
    por_tipologia: dict[str, dict] = {}
    por_nivel: dict[str, dict[int, dict]] = {}
    for pos in posicoes:
        rack = rack_by_name.get(pos.rack_nome)
        if rack is None:
            continue
        t = por_tipologia.setdefault(pos.rack_nome, {
            "rack_nome": pos.rack_nome, "posicoes": 0, "posicoes_ocupadas": 0,
            "niveis": 0, "niveis_ocupados": 0,
            "area_total_m2": 0.0, "area_usada_m2": 0.0,
            "volume_total_m3": 0.0, "volume_usado_m3": 0.0,
            "unidades_carga": 0,
        })
        area_nivel = rack.larg_m * rack.prof_m
        t["posicoes"] += 1
        tem_caixa = False
        for bloco in pos.blocos:
            for nivel in bloco.niveis:
                n = por_nivel.setdefault(pos.rack_nome, {}).setdefault(nivel.nivel_idx, {
                    "nivel": nivel.nivel_idx + 1, "niveis": 0, "niveis_ocupados": 0,
                    "unidades_carga": 0})
                n["niveis"] += 1
                t["niveis"] += 1
                t["area_total_m2"] += area_nivel
                t["volume_total_m3"] += area_nivel * rack.alt_nivel_m
                if nivel.caixas:
                    t["niveis_ocupados"] += 1
                    n["niveis_ocupados"] += 1
                    tem_caixa = True
                for c in nivel.caixas:
                    t["unidades_carga"] += c.num_boxes_in_stack
                    n["unidades_carga"] += c.num_boxes_in_stack
                    t["area_usada_m2"] += c.comp_m * c.larg_m
                    t["volume_usado_m3"] += c.comp_m * c.larg_m * c.alt_m
        if tem_caixa:
            t["posicoes_ocupadas"] += 1

    total = {"rack_nome": "TOTAL", "posicoes": 0, "posicoes_ocupadas": 0,
             "niveis": 0, "niveis_ocupados": 0,
             "area_total_m2": 0.0, "area_usada_m2": 0.0,
             "volume_total_m3": 0.0, "volume_usado_m3": 0.0, "unidades_carga": 0}
    for t in por_tipologia.values():
        for k, v in t.items():
            if k != "rack_nome":
                total[k] += v
    for nome, t in por_tipologia.items():
        t["por_nivel"] = [por_nivel[nome][i] for i in sorted(por_nivel.get(nome, {}))]

    def _arredonda(d: dict) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items()}

    return {
        "por_tipologia": [_arredonda(t) for t in por_tipologia.values()],
        "total": _arredonda(total),
    }

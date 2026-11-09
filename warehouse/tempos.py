"""Tempos de picking no armazém (Passo 5c).

Substitui os tempos FIXOS de picking do livro do comboio («Arm Picking +
Preparação»: 2,5 min por caixa, 3 min por palete, 3 min pela 1.ª peça de cada
referência de um carrinho, 2 min por pick de kit -- deslocação incluída) por um
tempo que depende de ONDE está a peça no arranjo:

    tempo de um pick = manuseio + 2 × percurso ÷ velocidade + extra de altura

* percurso: caminho mais curto numa grelha do arranjo (racks e obstáculos são
  paredes, corredores e zonas são chão), do ponto de carga até à frente da
  posição; ida e volta, uma viagem por unidade (como o livro: "por caixa");
* velocidade: pelo equipamento (Config.picking_velocidade_m_s);
* extra de altura: +30 s («Arm Picking», linha 56) se o topo do nível passar de
  Config.picking_altura_limite_m;
* manuseio: CALIBRADO para que, no arranjo de referência, a média de cada tipo de
  pick seja a do livro (2,5 / 3 / 3 / 2 min). O livro não tem velocidades nem
  tempos medidos -- a calibração diz "o livro tem razão em média"; o que o modelo
  acrescenta é a diferença entre uma posição perto e uma longe, e entre dois
  arranjos (a calibração fica gravada e não se refaz sozinha).

A preparação de cada volta/viagem do bulk usa as fórmulas do livro («7b Voltas»
BB/BC, «Bulk» AB) com a parte de picking trocada por esta; o Gantt do armazém
reproduz as regras da folha «Gantt» (1 trem em avanço, margem de 85 % em branco
a seguir a cada preparação, bulk depois da última volta regular, o almoço faz de
margem antes das caixas do bulk). Com os tempos do livro, tudo isto tem de dar os
números do livro -- ``validar_contra_livro``.
"""
from __future__ import annotations

import heapq
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .procura import SEMANAL

INF = float("inf")
GRUPO_CAIXA, GRUPO_PALETE, GRUPO_PECA, GRUPO_KIT = "caixa", "palete", "peça de carrinho", "kit"
GRUPOS = (GRUPO_CAIXA, GRUPO_PALETE, GRUPO_PECA, GRUPO_KIT)
A_PE = "a pé"

ASSUNCOES_TEMPO = (
    "Uma viagem por unidade (ida e volta do ponto de carga à posição), como o livro: «por caixa».",
    "Percurso = caminho mais curto numa grelha de Config.picking_grelha_m: racks e obstáculos são paredes, "
    "corredores e zonas são chão (as zonas não bloqueiam). Chega-se a uma posição pelos lados compridos.",
    "Equipamento: caixas na prateleira -> order-picker; paletes -> empilhador; peças de carrinho e de kit -> "
    "a pé (Config.picking_equipamento). Velocidades em Config.picking_velocidade_m_s (ASSUNÇÃO, não há "
    "medição).",
    "Extra de altura: +30 s (livro, «Arm Picking + Preparação», linha 56) se o topo do nível passar de "
    "Config.picking_altura_limite_m. Não há velocidade de elevação: os 30 s do livro já são esse tempo.",
    "Manuseio calibrado: no arranjo de referência a média de cada tipo de pick (só referências do PFEP) é a "
    "do livro. Fica gravado (Config.tempos_calibracao); outros arranjos comparam-se com esse.",
    "O resto da preparação (esvaziar, lista, organizar carruagens, verificar, desengatar carrinhos, peças a "
    "mais da mesma palete) é o do livro, sem mudança.",
    "Consumíveis: o livro não os tem. Em reposição semanal (Config.consumiveis_reposicao) a preparação de "
    "cada reposição = unidades × (esvaziar + picking pela localização) + lista e verificação de uma volta "
    "de caixas (C88); não entra no Gantt diário, verifica-se se cabe na sobra do dia (com a margem de "
    "85 %). Em «voltas» entram nas voltas de caixas/paletes e aparecem como «dos quais consumíveis».",
    "Com mais carros por dia do que o livro: carruagens só das cheias (sem vazios) e viagens do bulk iguais "
    "às do livro -- aproximação.",
)


# ----------------------------------------------------------------- percursos
@dataclass
class Grelha:
    x0: float
    y0: float
    passo: float
    nx: int
    ny: int
    livre: bytearray
    folga: float = 0.0          # metade da passagem mínima: o chão a menos disto de uma rack não conta

    def celula(self, x: float, y: float) -> tuple[int, int]:
        return (min(max(int((x - self.x0) / self.passo), 0), self.nx - 1),
                min(max(int((y - self.y0) / self.passo), 0), self.ny - 1))

    def centro(self, i: int, j: int) -> tuple[float, float]:
        return self.x0 + (i + 0.5) * self.passo, self.y0 + (j + 0.5) * self.passo

    def e_livre(self, i: int, j: int) -> bool:
        return 0 <= i < self.nx and 0 <= j < self.ny and bool(self.livre[j * self.nx + i])


def _aabb_rodado(x, y, w, h, rot) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) do retângulo rodado em torno do centro (como o canvas)."""
    r = math.radians(float(rot or 0))
    cx, cy = x + w / 2, y + h / 2
    pts = [(cx + dx * math.cos(r) - dy * math.sin(r), cy + dx * math.sin(r) + dy * math.cos(r))
           for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def construir_grelha(posicoes: list, obstaculos: list, pontos: list, passo: float = 0.25,
                     margem: float = 2.0, folga: float = 0.0) -> Grelha:
    """Grelha do arranjo: livre = chão; bloqueado = footprint de uma rack ou de
    um obstáculo (AABB, se rodado) ALARGADO de `folga` -- assim uma passagem
    mais estreita do que 2 × folga (Config.picking_passagem_min_m) fica fechada:
    sem isto um caminho passava por uma fresta de 25 cm entre filas pousadas à
    mão. Cobre tudo + `margem` à volta."""
    rects = [(p.x_m - folga, p.y_m - folga, p.x_m + p.larg_m + folga, p.y_m + p.prof_m + folga)
             for p in posicoes]
    for o in obstaculos:
        a, b, c, d = _aabb_rodado(float(o["x"]), float(o["y"]), float(o["w"]), float(o["h"]), o.get("rot", 0))
        rects.append((a - folga, b - folga, c + folga, d + folga))
    xs = [r[0] for r in rects] + [r[2] for r in rects] + [p[0] for p in pontos]
    ys = [r[1] for r in rects] + [r[3] for r in rects] + [p[1] for p in pontos]
    x0, y0 = min(xs) - margem, min(ys) - margem
    nx = int(math.ceil((max(xs) + margem - x0) / passo))
    ny = int(math.ceil((max(ys) + margem - y0) / passo))
    livre = bytearray(b"\x01") * (nx * ny)
    for a, b, c, d in rects:
        i0, i1 = int((a - x0) / passo), int(math.ceil((c - x0) / passo))
        j0, j1 = int((b - y0) / passo), int(math.ceil((d - y0) / passo))
        for j in range(max(j0, 0), min(j1, ny)):
            cy = y0 + (j + 0.5) * passo
            if not (b < cy < d):
                continue
            for i in range(max(i0, 0), min(i1, nx)):
                cx = x0 + (i + 0.5) * passo
                if a < cx < c:
                    livre[j * nx + i] = 0
    return Grelha(x0, y0, passo, nx, ny, livre, folga)


def distancias(g: Grelha, origem: tuple[float, float]) -> list:
    """Dijkstra a 8 vizinhos (diagonal só se as duas ortogonais forem livres --
    não se corta a esquina de uma rack). Devolve a distância em metros a cada
    célula (INF = inalcançável). Origem numa célula bloqueada -> a livre mais perto."""
    i, j = g.celula(*origem)
    if not g.e_livre(i, j):
        livres = [(abs(a - i) + abs(b - j), a, b) for b in range(g.ny) for a in range(g.nx) if g.e_livre(a, b)]
        if not livres:
            return [INF] * (g.nx * g.ny)
        _, i, j = min(livres)
    d = [INF] * (g.nx * g.ny)
    d[j * g.nx + i] = 0.0
    fila = [(0.0, i, j)]
    diag = math.sqrt(2) * g.passo
    while fila:
        dist, a, b = heapq.heappop(fila)
        if dist > d[b * g.nx + a]:
            continue
        for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            na, nb = a + da, b + db
            if not g.e_livre(na, nb):
                continue
            if da and db and not (g.e_livre(a + da, b) and g.e_livre(a, b + db)):
                continue
            nd = dist + (diag if da and db else g.passo)
            k = nb * g.nx + na
            if nd < d[k]:
                d[k] = nd
                heapq.heappush(fila, (nd, na, nb))
    return d


def distancia_acesso(g: Grelha, d: list, pos) -> float:
    """Distância até à frente de uma posição: a célula livre mais perto ao
    longo dos LADOS COMPRIDOS do footprint (não se tira uma caixa pela
    cabeceira de uma estante)."""
    x, y, w, h = pos.x_m, pos.y_m, pos.larg_m, pos.prof_m
    meio = g.folga + g.passo / 2
    if w >= h:
        n = max(int(w / g.passo), 1)
        pontos = [(x + (k + 0.5) * w / n, yy) for k in range(n) for yy in (y - meio, y + h + meio)]
    else:
        n = max(int(h / g.passo), 1)
        pontos = [(xx, y + (k + 0.5) * h / n) for k in range(n) for xx in (x - meio, x + w + meio)]
    melhor = INF
    for px, py in pontos:
        i, j = g.celula(px, py)
        if g.e_livre(i, j):
            melhor = min(melhor, d[j * g.nx + i])
    return melhor


def distancias_acesso(posicoes: list, obstaculos: list, ponto: tuple, passo: float = 0.25,
                      folga: float = 0.0) -> tuple[dict, list]:
    """posição -> metros do ponto de carga à frente dela, pelo chão. Posições
    sem acesso (fechadas por racks/obstáculos) ficam com a distância em planta
    (|dx| + |dy|) e um aviso."""
    if not posicoes:
        return {}, []
    g = construir_grelha(posicoes, obstaculos, [ponto], passo, folga=folga)
    d = distancias(g, ponto)
    out, sem = {}, []
    for p in posicoes:
        v = distancia_acesso(g, d, p)
        if v == INF:
            cx, cy = p.centro
            v = abs(cx - ponto[0]) + abs(cy - ponto[1])
            sem.append(p.endereco())
        out[p.id] = v
    avisos = []
    if sem:
        avisos.append(f"{len(sem)} posição(ões) sem acesso pelo chão (fechadas por racks/obstáculos) -- "
                      f"distância em planta usada: {', '.join(sem[:5])}" + (" …" if len(sem) > 5 else ""))
    return out, avisos


# ----------------------------------------------------------------- custo de uma visita
def _bate(nome: str, chaves) -> bool:
    n = nome.lower()
    return any(str(k).lower() in n for k in chaves)


def equipamento_da_rack(rack, cfg) -> str:
    for chave, equip in cfg.picking_equipamento.items():
        if _bate(rack.nome, (chave,)):
            return equip
    return A_PE


def extra_altura_min(rack, nivel_idx: int, cfg) -> float:
    from .racking import altura_nivel, cotas_fisicas_niveis
    bases, _ = cotas_fisicas_niveis(rack, nivel_idx + 1, cfg)
    topo = bases[nivel_idx] + altura_nivel(rack, nivel_idx)
    return cfg.picking_altura_extra_min if topo > cfg.picking_altura_limite_m + 1e-9 else 0.0


def custo_visita_min(dist_m: float, rack, nivel_idx: int, cfg, equipamento: str | None = None) -> float:
    """Parte do tempo de um pick que depende da localização: ida e volta +
    extra de altura (min). O manuseio (constante por tipo) fica de fora --
    não muda a ordem entre posições."""
    v = cfg.picking_velocidade_m_s[equipamento or equipamento_da_rack(rack, cfg)]
    return 2.0 * dist_m / v / 60.0 + extra_altura_min(rack, nivel_idx, cfg)


# ----------------------------------------------------------------- livro
@dataclass
class TemposLivro:
    """Tempos e referências do livro do comboio (só valores)."""
    caixa: float            # Parâmetros C84: esvaziar + picking + carregar, por caixa
    caixa_picking: float    # «Arm Picking» G56: picking da caixa (deslocação incluída)
    palete: float           # C89
    palete_picking: float   # G70
    carrinho: float         # C90: desengatar/engatar, por carrinho
    peca_carrinho: float    # C91: 1.ª peça de cada referência (inclui ir à palete)
    peca_extra: float       # C103: peças seguintes da mesma palete
    caixa_kit: float        # C92: caixa de kit (desengatar + engatar)
    kit_pequena: float      # C86
    kit_grande: float       # C93
    carr_caixas: float      # C87: organizar, por carruagem
    fixo_caixas: float      # C88: lista + verificar, por volta
    carr_paletes: float     # C94
    fixo_paletes: float     # C95
    ocupacao: float         # C101: 85 %
    tempo_dia: float        # C11
    pausas: tuple           # min de TRABALHO em que começa cada pausa («Cenário» G54:G56)
    partidas: dict          # "Caixas"/"Paletes" -> [min]
    # referências para validar
    prep_7b: dict = field(default_factory=dict)       # ("Caixas"/"Paletes", nº da volta) -> min
    carr_7b: dict = field(default_factory=dict)       # idem -> carruagens
    viagens_bulk: list = field(default_factory=list)  # [{tipo, carruagens, prep}]
    gantt_sobra: float = 0.0
    gantt_atrasadas: int = 0
    # cronograma do dia (horas de relógio, comboio)
    t_volta: dict = field(default_factory=dict)       # (ciclo, nº) -> «t volta máx.» do livro (min)
    dolly_min: float = 0.0                            # dollys C -> P1 no fim do dia («7c Relatório» E29)
    dolly_rel: float = 0.0                            # dollys só depois das saídas das estações C («Gantt» E77)
    bulk_hora_min: float = 0.0                        # nenhuma viagem do bulk antes disto («Parâmetros» C104, 225 = 13:00)
    bulk_caixas_primeiro: bool = True                 # armazém prepara as caixas do bulk antes das paletes (C106)
    voltas_horario: bool = True                       # voltas regulares no horário do livro todos os dias
    gantt_mizu_sobra: float | None = None             # «Gantt» C9 (sobra do mizu) -- validação
    gantt_viagens: dict = field(default_factory=dict) # nome -> início (min) das voltas, viagens e dollys («Gantt» O32:O61, K67:K77)
    pausas_dur: tuple = ()                            # duração de cada pausa (min de relógio)
    turno_inicio_min: float = 480.0                   # 08:00
    turno_fim_min: float = 1020.0                     # 17:00


def carregar_tempos_livro(caminho: Path) -> TemposLivro:
    import warnings
    import openpyxl
    warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
    wb = openpyxl.load_workbook(caminho, data_only=True, read_only=True)
    par = wb["Parâmetros"]
    num = lambda ws, ref: float(ws[ref].value or 0.0)   # noqa: E731

    arm = wb["Arm Picking + Preparação"]
    pick = {}
    for row in arm.iter_rows(min_row=50, max_row=80, values_only=True):
        fase, tarefa, tempo = row[1], str(row[2] or ""), row[6]
        if fase and "(b)" in str(fase) and isinstance(tempo, (int, float)):
            if tarefa.startswith("Picking da caixa"):
                pick.setdefault("caixa", float(tempo))
            elif tarefa.startswith("Picking da palete"):
                pick.setdefault("palete", float(tempo))
    if set(pick) != {"caixa", "palete"}:
        raise ValueError("«Arm Picking + Preparação»: não encontrei as linhas «Picking da caixa…» e "
                         "«Picking da palete…» (coluna G).")

    cen = wb["Cenário"]
    linhas_pausa = [r for r in (54, 55, 56) if isinstance(cen[f"G{r}"].value, (int, float))]
    pausas = tuple(float(cen[f"G{r}"].value) for r in linhas_pausa)
    pausas_dur = tuple(float(cen[f"E{r}"].value or 0.0) for r in linhas_pausa)
    partidas = {"Caixas": [], "Paletes": []}
    t_volta = {}
    for r in range(60, 102):
        t, tipo = cen[f"F{r}"].value, cen[f"B{r}"].value
        if tipo in partidas and isinstance(t, (int, float)):
            partidas[tipo].append(float(t))
            if isinstance(cen[f"C{r}"].value, (int, float)):
                t_volta[(tipo, int(cen[f"C{r}"].value))] = float(cen[f"I{r}"].value or 0.0)

    def minutos(v, omissao):
        return v.hour * 60 + v.minute if hasattr(v, "hour") else omissao

    tl = TemposLivro(
        caixa=num(par, "C84"), caixa_picking=pick["caixa"], palete=num(par, "C89"), palete_picking=pick["palete"],
        carrinho=num(par, "C90"), peca_carrinho=num(par, "C91"), peca_extra=num(par, "C103"),
        caixa_kit=num(par, "C92"), kit_pequena=num(par, "C86"), kit_grande=num(par, "C93"),
        carr_caixas=num(par, "C87"), fixo_caixas=num(par, "C88"), carr_paletes=num(par, "C94"),
        fixo_paletes=num(par, "C95"), ocupacao=num(par, "C101") or 1.0, tempo_dia=num(par, "C11"),
        pausas=pausas, partidas={k: sorted(v) for k, v in partidas.items()},
        t_volta=t_volta, pausas_dur=pausas_dur, turno_inicio_min=minutos(cen["C53"].value, 480.0),
        turno_fim_min=minutos(cen["D53"].value, 1020.0))
    try:
        tl.dolly_min = float(wb["7c Relatório"]["E29"].value or 0.0)
    except (KeyError, TypeError, ValueError):
        tl.dolly_min = 0.0

    v7 = wb["7b Voltas"]
    for row in v7.iter_rows(min_row=9, max_row=308, values_only=True):
        # B volta nº, P carruagens, BB prep (caixas); W volta nº, AK carruagens, BC prep (paletes)
        if isinstance(row[1], (int, float)):
            tl.prep_7b[("Caixas", int(row[1]))] = float(row[53] or 0)
            tl.carr_7b[("Caixas", int(row[1]))] = int(row[15] or 0)
        if isinstance(row[22], (int, float)):
            tl.prep_7b[("Paletes", int(row[22]))] = float(row[54] or 0)
            tl.carr_7b[("Paletes", int(row[22]))] = int(row[36] or 0)
    bu = wb["Bulk"]
    for row in bu.iter_rows(min_row=268, max_row=277, values_only=True):
        if row[4] == 1:   # E: existe
            # B nome, C tipo, F parte depois de, H postos, V carruagens, AA tempo de mizu, AB preparação, AE forçada
            tl.viagens_bulk.append({"nome": row[1], "tipo": row[2], "rel": float(row[5] or 0),
                                    "postos": str(row[7] or ""), "carruagens": int(row[21] or 0),
                                    "dur": float(row[26] or 0), "prep": float(row[27] or 0),
                                    "forcada": float(row[30]) if isinstance(row[30], (int, float)) else None})
    ga = wb["Gantt"]
    tl.gantt_sobra = num(ga, "C7")
    tl.gantt_atrasadas = int(ga["C14"].value or 0)
    if isinstance(ga["C9"].value, (int, float)):
        tl.gantt_mizu_sobra = float(ga["C9"].value)
    for r in range(32, 62):              # voltas regulares: C tipo, D nº, O partida real
        tipo, n, o = ga[f"C{r}"].value, ga[f"D{r}"].value, ga[f"O{r}"].value
        if tipo in ("Caixas", "Paletes") and isinstance(n, (int, float)) and isinstance(o, (int, float)):
            tl.gantt_viagens[f"Volta {tipo} {int(n)}"] = float(o)
    for r in range(67, 78):
        nome, ini = ga[f"C{r}"].value, ga[f"K{r}"].value
        if nome and isinstance(ini, (int, float)):
            tl.gantt_viagens["Dollys" if r == 77 else str(nome)] = float(ini)
    if isinstance(ga["E77"].value, (int, float)):
        tl.dolly_rel = float(ga["E77"].value)   # = máx. das «Saídas C» com «Sim»
    if isinstance(par["C104"].value, (int, float)):
        tl.bulk_hora_min = float(par["C104"].value)
    if isinstance(par["C106"].value, str):
        tl.bulk_caixas_primeiro = par["C106"].value.strip().lower().startswith("caixas")
    return tl


# ----------------------------------------------------------------- picks -> tempos
def grupo_do_pick(pick) -> str:
    if pick.origem.startswith("Carrinho"):
        return GRUPO_PECA
    if pick.origem.startswith("Kit"):
        return GRUPO_KIT
    return GRUPO_CAIXA if pick.unidade in ("XS", "S", "M", "L") else GRUPO_PALETE


def tempo_livro(grupo: str, tl: TemposLivro, kit_grande: bool = False) -> float:
    return {GRUPO_CAIXA: tl.caixa_picking, GRUPO_PALETE: tl.palete_picking, GRUPO_PECA: tl.peca_carrinho,
            GRUPO_KIT: tl.kit_grande if kit_grande else tl.kit_pequena}[grupo]


def deslocacao_por_sku(res_aloc, dist: dict, cfg) -> dict:
    """(sku, grupo) -> custo médio de uma visita (ida e volta + altura, min),
    pesado pelas unidades em cada posição (com posições fixas e FIFO cada
    unidade é tirada uma vez)."""
    acc = defaultdict(lambda: [0.0, 0])
    for c in res_aloc.colocacoes:
        d = dist.get(c.posicao.id, 0.0)
        for g in GRUPOS:
            equip = A_PE if g in (GRUPO_PECA, GRUPO_KIT) else None
            a = acc[(c.sku, g)]
            a[0] += c.n_unidades * custo_visita_min(d, c.posicao.rack, c.nivel_idx, cfg, equip)
            a[1] += c.n_unidades
    return {k: s / n for k, (s, n) in acc.items() if n}


def _peso(pick, grupo) -> float:
    """Quantas visitas (deslocações) este pick representa."""
    return float(pick.unidades)


def calibrar(picks: list, desl: dict, tl: TemposLivro) -> tuple[dict, list]:
    """Deslocação média de REFERÊNCIA por grupo (só picks de referências do
    PFEP -- o livro não tem consumíveis), já completada com o manuseio
    (``completar_calibracao``). Só a deslocação de referência é que conta: se
    um tempo do livro mudar, o manuseio acompanha sem recalibrar."""
    acc = defaultdict(lambda: [0.0, 0.0])
    for k in picks:
        if k.origem == "Consumível":
            continue
        g = grupo_do_pick(k)
        if (k.sku, g) in desl:
            a = acc[g]
            a[0] += _peso(k, g) * desl[(k.sku, g)]
            a[1] += _peso(k, g)
    ref = {g: {"deslocacao_media_min": round(acc[g][0] / acc[g][1], 4) if acc[g][1] else 0.0} for g in GRUPOS}
    return completar_calibracao(ref, tl)


def completar_calibracao(ref: dict, tl: TemposLivro) -> tuple[dict, list]:
    """Manuseio = tempo do livro − deslocação de referência (nunca < 0)."""
    cal, avisos = {}, []
    for g in GRUPOS:
        media = float(ref.get(g, {}).get("deslocacao_media_min", 0.0))
        livro = tempo_livro(g, tl)
        m = livro - media
        if m < 0:
            avisos.append(f"Calibração «{g}»: a deslocação média de referência ({media:.2f} min) já passa o "
                          f"tempo do livro ({livro:.2f} min) -- manuseio posto a 0: o livro subestima a "
                          f"deslocação.")
            m = 0.0
        cal[g] = {"livro_min": livro, "deslocacao_media_min": round(media, 4), "manuseio_min": round(m, 4)}
    return cal, avisos


def tempo_pick(pick, desl: dict, cal: dict, tl: TemposLivro) -> tuple[float, bool]:
    """(min por visita, localizado?). SKU sem localização -> tempo do livro."""
    g = grupo_do_pick(pick)
    if (pick.sku, g) not in desl:
        return tempo_livro(g, tl), False
    return cal[g]["manuseio_min"] + desl[(pick.sku, g)], True


# ----------------------------------------------------------------- preparação
CAPACIDADE_CARRUAGEM = {"B": {"XS": 60, "S": 24}, "D": {"M": 8, "L": 4}, "P": {"MEIA": 2, "PALLET": 1}}


def _carruagens_so_cheias(contagem: dict) -> int:
    total = 0
    for caps in CAPACIDADE_CARRUAGEM.values():
        f = sum(contagem.get(u, 0) / c for u, c in caps.items())
        total += math.ceil(f - 1e-9) if f > 0 else 0
    return total


@dataclass
class Prep:
    dia: int
    ciclo: str
    volta: int
    partida: float
    livro: float             # fórmula do livro, tempos fixos, só referências do PFEP
    modelo: float            # com o picking pela localização (e os consumíveis)
    consumiveis: float       # parte do modelo que é de consumíveis
    picks: int
    sem_localizacao: int = 0

    @property
    def sem_consumiveis(self) -> float:
        """Modelo só com as referências do livro: separa o efeito da
        localização do efeito dos consumíveis (que o livro não tem)."""
        return self.modelo - self.consumiveis


def preparacoes(res_proc, desl: dict, cal: dict, tl: TemposLivro, carros_base: bool) -> tuple[list, list]:
    """Preparação de cada volta e de cada dia de bulk (livro e modelo)."""
    por = defaultdict(list)
    for k in res_proc.picks:
        por[(k.dia, k.ciclo, k.volta)].append(k)
    voltas_dia = {c: len(tl.partidas[c]) for c in ("Caixas", "Paletes")}
    fixo_bulk_tipo = {t: sum((tl.fixo_caixas + v["carruagens"] * tl.carr_caixas) if t == "Caixas"
                             else (tl.fixo_paletes + v["carruagens"] * tl.carr_paletes)
                             for v in tl.viagens_bulk if v["tipo"] == t) for t in ("Caixas", "Paletes")}
    out, avisos = [], []
    dias = res_proc.dias
    for d in range(1, dias + 1):
        for ciclo in ("Caixas", "Paletes"):
            for k, partida in enumerate(tl.partidas[ciclo], 1):
                out.append(_prep_volta(d, ciclo, k, partida, por.get((d, ciclo, k), []), desl, cal, tl,
                                       carros_base, voltas_dia))
        for tipo in ("Paletes", "Caixas"):
            out.append(_prep_bulk(d, tipo, por.get((d, "Bulk", 0), []), desl, cal, tl, fixo_bulk_tipo[tipo]))
        if por.get((d, SEMANAL, 0)):
            out.append(_prep_semanal(d, por[(d, SEMANAL, 0)], desl, cal, tl))
    if not carros_base:
        avisos.append("Carros por dia diferentes do livro: carruagens só das cheias e viagens do bulk iguais às "
                      "do livro (aproximação).")
    return out, avisos


def _contas(picks, desl, cal, tl):
    """Somas comuns: unidades, tempo de picking (livro e modelo), carrinhos."""
    c = defaultdict(float)
    unidades = defaultdict(int)
    carrinhos = defaultdict(int)
    kits = defaultdict(int)
    sem = 0
    for k in picks:
        g = grupo_do_pick(k)
        cons = k.origem == "Consumível"
        t_mod, loc = tempo_pick(k, desl, cal, tl)
        sem += 0 if loc else k.unidades
        if g in (GRUPO_CAIXA, GRUPO_PALETE):
            if not cons:
                unidades[k.unidade] += k.unidades
            c[f"n_{g}" + ("_cons" if cons else "")] += k.unidades
            c[f"pick_{g}_livro"] += 0 if cons else k.unidades * tempo_livro(g, tl)
            c[f"pick_{g}_modelo" + ("_cons" if cons else "")] += k.unidades * t_mod
        elif g == GRUPO_PECA:
            carrinhos[k.origem] = max(carrinhos[k.origem], k.unidades)
            c["primeiras"] += k.unidades
            c["extra"] += max(k.pecas - k.unidades, 0)
            c["pick_peca_livro"] += k.unidades * tl.peca_carrinho
            c["pick_peca_modelo"] += k.unidades * t_mod
        else:
            kits[k.origem] = max(kits[k.origem], k.unidades)
            c["kit_picks"] += k.unidades
            c["pick_kit_livro"] += k.unidades * tl.kit_pequena
            c["pick_kit_modelo"] += k.unidades * t_mod
    c["carrinhos"] = sum(carrinhos.values())
    c["kits"] = sum(kits.values())
    return c, unidades, sem


def _prep_volta(d, ciclo, k, partida, picks, desl, cal, tl, carros_base, voltas_dia) -> Prep:
    c, unidades, sem = _contas(picks, desl, cal, tl)
    n_global = (d - 1) * voltas_dia[ciclo] + k
    carr = tl.carr_7b.get((ciclo, n_global)) if carros_base else None
    if carr is None:
        carr = _carruagens_so_cheias(unidades) + int(c["carrinhos"])
    if ciclo == "Caixas":
        resto = tl.caixa - tl.caixa_picking                  # esvaziar + carregar
        fixo = carr * tl.carr_caixas + tl.fixo_caixas
        livro = c["n_caixa"] * tl.caixa + fixo
        cons = c["n_caixa_cons"] * resto + c["pick_caixa_modelo_cons"]
        modelo = c["n_caixa"] * resto + c["pick_caixa_modelo"] + fixo + cons
    else:
        resto = tl.palete - tl.palete_picking
        fixo = (c["carrinhos"] * tl.carrinho + c["extra"] * tl.peca_extra
                + carr * tl.carr_paletes + tl.fixo_paletes)
        livro = c["n_palete"] * tl.palete + c["pick_peca_livro"] + fixo
        cons = c["n_palete_cons"] * resto + c["pick_palete_modelo_cons"]
        modelo = c["n_palete"] * resto + c["pick_palete_modelo"] + c["pick_peca_modelo"] + fixo + cons
    return Prep(d, ciclo, k, partida, livro, modelo, cons, len(picks), sem)


def _prep_bulk(d, tipo, picks_bulk, desl, cal, tl, fixo_viagens) -> Prep:
    """Bulk de um dia, separado em viagens de paletes (paletes, carrinhos) e de
    caixas (caixas, kits) -- o Gantt põe uma margem entre as duas."""
    if tipo == "Paletes":
        picks = [k for k in picks_bulk if grupo_do_pick(k) in (GRUPO_PALETE, GRUPO_PECA)]
    else:
        picks = [k for k in picks_bulk if grupo_do_pick(k) in (GRUPO_CAIXA, GRUPO_KIT)]
    c, _, sem = _contas(picks, desl, cal, tl)
    if tipo == "Caixas":
        resto = tl.caixa - tl.caixa_picking
        fixo = fixo_viagens + c["kits"] * tl.caixa_kit
        livro = c["n_caixa"] * tl.caixa + c["pick_kit_livro"] + fixo
        cons = c["n_caixa_cons"] * resto + c["pick_caixa_modelo_cons"]
        modelo = c["n_caixa"] * resto + c["pick_caixa_modelo"] + c["pick_kit_modelo"] + fixo + cons
    else:
        resto = tl.palete - tl.palete_picking
        fixo = fixo_viagens + c["carrinhos"] * tl.carrinho + c["extra"] * tl.peca_extra
        livro = c["n_palete"] * tl.palete + c["pick_peca_livro"] + fixo
        cons = c["n_palete_cons"] * resto + c["pick_palete_modelo_cons"]
        modelo = c["n_palete"] * resto + c["pick_palete_modelo"] + c["pick_peca_modelo"] + fixo + cons
    return Prep(d, "Bulk " + tipo.lower(), 0, tl.tempo_dia, livro, modelo, cons, len(picks), sem)


def _prep_semanal(d, picks, desl, cal, tl) -> Prep:
    """Reposição semanal dos consumíveis: esvaziar + picking pela localização,
    por unidade, + lista e verificação (C88). Toda ela é consumíveis."""
    c, _, sem = _contas(picks, desl, cal, tl)
    t = (c["n_caixa_cons"] * (tl.caixa - tl.caixa_picking) + c["pick_caixa_modelo_cons"]
         + c["n_palete_cons"] * (tl.palete - tl.palete_picking) + c["pick_palete_modelo_cons"] + tl.fixo_caixas)
    return Prep(d, SEMANAL, 0, 0.0, 0.0, t, t, len(picks), sem)


# ----------------------------------------------------------------- Gantt
@dataclass
class Gantt:
    fim_min: float           # fim do trabalho do armazém (min de trabalho), com a margem final
    sobra_min: float
    atrasadas: int
    atraso_max_min: float
    linhas: list             # [(nome, início, fim, partida planeada)]
    fim_comboio_min: float = 0.0
    comboio: dict = field(default_factory=dict)   # nome -> início (min) das tarefas do comboio


@dataclass
class Tarefa:
    """Uma tarefa do dia (cronograma): do operador do armazém ou do comboio."""
    recurso: str                 # "Armazém" / "Comboio"
    tipo: str                    # preparação / margem / volta / viagem / dollys / reposição
    nome: str
    ini: float                   # min de TRABALHO desde o início do turno
    fim: float
    partida_planeada: float | None = None
    atraso: float = 0.0
    chave: str = ""              # liga aos picks: "Caixas|2", "Bulk|Paletes 1", "Reposição semanal|0"
    nota: str = ""


def _viagens_por_tipo(tl: TemposLivro, tipo: str, total: float) -> list:
    """Viagens do bulk de um tipo, com a preparação do modelo repartida pela
    proporção da do livro (AB). Sem viagens no livro: uma só, com tudo."""
    vs = [v for v in tl.viagens_bulk if v.get("tipo") == tipo]
    soma = sum(v.get("prep", 0.0) for v in vs)
    if not vs or soma <= 0:
        return [{"nome": f"{tipo} (bulk)", "tipo": tipo, "prep_mod": total, "rel": 0.0, "dur": 0.0,
                 "forcada": None, "postos": ""}] if total > 0 else []
    return [dict(v, prep_mod=total * v.get("prep", 0.0) / soma) for v in vs]


def _encaixar_comboio(tl: TemposLivro, ini: float, dur: float, pronto: float | None = None,
                      prep: float = 0.0) -> float:
    """Uma tarefa do comboio NUNCA se parte numa pausa e só fica antes dela COM
    FOLGA -- regra do livro «D2 auto» («Parâmetros» C107; «Gantt» Y33 e X67:X77),
    igual para voltas regulares, viagens do bulk e dollys: fica antes da próxima
    pausa só se início + duração ÷ 85 % acabar antes dela, mesmo com a
    preparação atrasada pela margem de 85 % (início = máx.(início, pronto +
    preparação × (1/85 % − 1))); senão parte no fim da pausa (em min de
    trabalho, a pausa é um instante). Repete-se para a pausa seguinte."""
    occ = tl.ocupacao or 1.0
    m = 1 / occ - 1
    while True:
        p = next((p for p in sorted(tl.pausas) if p > ini + 1e-9), None)
        if p is None:
            return ini
        ref = max(ini, (pronto + prep * m) if pronto is not None else ini)
        if ref + dur / occ <= p + 1e-9:
            return ini
        ini = p


def _dia(pior: dict, tl: TemposLivro, nome_volta=None, caixas_primeiro: bool | None = None,
         horario: bool | None = None) -> tuple[list, dict]:
    """O dia do armazém e do comboio, ENCADEADO (decisão do utilizador,
    2026-11-05 -- "quando acaba uma operação + margem, inicia a próxima"),
    a partir da preparação de cada volta ((ciclo, nº) -> min;
    ("Bulk paletes"/"Bulk caixas", 0)).

    * Sem horas fixas: as voltas regulares seguem a ordem do livro (C P C P),
      mas cada uma parte quando o seu trem está pronto e o comboio está livre.
      A 1.ª do dia foi preparada na véspera e parte no início do turno.
    * Armazém: 1 trem em avanço (a preparação de uma volta só começa quando a
      anterior parte) e margem em branco de preparação × (1/85 % − 1) a seguir
      a cada preparação; depois das regulares, o bulk em dois blocos (D2: caixas
      primeiro, `caixas_primeiro`), margem entre eles (se uma pausa cair dentro
      dela, a pausa faz de margem) e depois do último; por fim a 1.ª volta de
      amanhã com a sua margem.
    * Comboio: margem também, a próxima tarefa só começa depois de
      início + duração ÷ 85 % da anterior (D2, coluna Y); as viagens do bulk vão
      pela ordem em que ficam prontas e o(s) posto(s) acabam («Bulk» F), sem
      partidas forçadas (D2); dollys no fim. NENHUMA tarefa do comboio
      atravessa uma pausa nem fica antes dela sem folga (``_encaixar_comboio``).
      Uma viagem do bulk ou os dollys podem ir num intervalo entre voltas
      regulares se lá couberem sem as atrasar («Gantt» V67:V77); os dollys só
      depois das saídas das estações C (`tl.dolly_rel`).
    * Horário das voltas regulares (regra do utilizador, 2026-11-05: "o mesmo
      horário serve para todos os dias, calculado para o pior dos 30 dias"): a
      volta não parte antes da hora gravada no livro («Cenário» E63/E82/E83,
      `tl.partidas`, calculada pelo livro o mais cedo possível no pior dia) --
      O = máx.(horário, pronto, comboio livre + 85 %). Num dia mais pesado do
      que esse sai depois: atraso. `horario` (omissão `tl.voltas_horario`).
    * Bulk: nenhuma viagem antes de `tl.bulk_hora_min` («Parâmetros» C104, 13:00);
      ordem no armazém `tl.bulk_caixas_primeiro` (C106)."""
    if horario is None:
        horario = tl.voltas_horario
    if caixas_primeiro is None:
        caixas_primeiro = tl.bulk_caixas_primeiro
    nome_volta = nome_volta or (lambda c, k: f"{c} {k}")
    occ = tl.ocupacao
    m = 1 / occ - 1
    seq = [(c, k) for _, c, k in sorted((t, c, k) for c in ("Caixas", "Paletes")
                                         for k, t in enumerate(tl.partidas[c], 1))]
    tar = []
    if not seq:
        return tar, {"fim": 0.0, "sobra": tl.tempo_dia, "atrasadas": 0, "atraso_max": 0.0, "fim_comboio": 0.0,
                     "fim_trabalho": 0.0}
    livre_arm, livre_miz, partida_ant = 0.0, 0.0, 0.0
    janelas, regresso_ant = [], 0.0          # intervalos do comboio entre voltas regulares
    atrasadas, atraso_max = 0, 0.0
    for i, (c, k) in enumerate(seq):
        p = pior.get((c, k), 0.0)
        if i == 0:
            pronto = 0.0                        # preparado na véspera
        else:
            ini = max(livre_arm, partida_ant)  # 1 trem em avanço
            tar.append(Tarefa("Armazém", "preparação", f"Preparar {nome_volta(c, k)}", ini, ini + p,
                              chave=f"{c}|{k}"))
            if p * m > 0:
                tar.append(Tarefa("Armazém", "margem", "Margem (85 %)", ini + p, ini + p + p * m))
            livre_arm = ini + p + p * m
            pronto = ini + p
        dur = tl.t_volta.get((c, k), 0.0)
        cedo = max(pronto, livre_miz)
        s = _encaixar_comboio(tl, cedo, dur, pronto, p)
        planeada = tl.partidas[c][k - 1] if horario and k - 1 < len(tl.partidas[c]) else None
        atraso = 0.0
        if planeada is not None:
            atraso = max(0.0, s - planeada)
            s = max(s, planeada)
        if atraso > 0.05:
            atrasadas += 1
            atraso_max = max(atraso_max, atraso)
        nota = ("trem preparado na véspera" if i == 0 else
                ("atrasada: " + ("o trem só ficou pronto depois do horário" if pronto >= livre_miz
                                  else "esperou pelo comboio")) if atraso > 0.05
                else "no horário" if planeada is not None
                else "depois da pausa (antes dela não cabia com a folga de 85 %)" if s > cedo + 0.05
                else "esperou pelo comboio" if livre_miz > pronto + 0.05 else "")
        tar.append(Tarefa("Comboio", "volta", f"Volta {c} {k}", s, s + dur, partida_planeada=planeada,
                          atraso=atraso if atraso > 0.05 else 0.0, chave=f"{c}|{k}", nota=nota))
        janelas.append((regresso_ant, s))
        regresso_ant = s + dur
        livre_miz = s + dur / occ
        partida_ant = s
    janelas.append((regresso_ant, float("inf")))

    def no_intervalo(j: float, dur: float) -> float:
        """1.º intervalo do comboio entre voltas regulares onde a tarefa cabe."""
        for a, b in janelas:
            s = max(a, j)
            if s + dur <= b + 1e-9:
                return s
        return max(j, janelas[-1][0])

    # bulk: dois blocos, margem entre eles e depois do último
    t = livre_arm
    prontas, viagens = {}, []
    ordem = ("Caixas", "Paletes") if caixas_primeiro else ("Paletes", "Caixas")
    for bi, tipo in enumerate(ordem):
        total = pior.get((f"Bulk {tipo.lower()}", 0), 0.0)
        for v in _viagens_por_tipo(tl, tipo, total):
            tar.append(Tarefa("Armazém", "preparação", f"Preparar bulk — {v['nome']}", t, t + v["prep_mod"],
                              chave=f"Bulk|{v['nome']}"))
            t += v["prep_mod"]
            prontas[v["nome"]] = t
            viagens.append(v)
        margem_fim = t + total * m
        pausa = (next((x for x in tl.pausas if t - 1e-9 <= x <= margem_fim + 1e-9), None) if bi == 0 else None)
        t2 = pausa if pausa is not None else margem_fim
        if t2 > t + 1e-9:
            tar.append(Tarefa("Armazém", "margem", "Margem (85 %)" + (" — a pausa faz de margem" if pausa else ""),
                              t, t2))
        t = t2
    c0, k0 = seq[0]
    p0 = pior.get((c0, k0), 0.0)
    tar.append(Tarefa("Armazém", "preparação", f"Preparar {c0} {k0} de amanhã", t, t + p0, chave=f"Amanhã|{c0}|{k0}"))
    if p0 * m > 0:
        tar.append(Tarefa("Armazém", "margem", "Margem (85 %)", t + p0, t + p0 + p0 * m))
    fim_trabalho = t + p0
    fim = t + p0 + p0 * m

    # viagens do bulk pela ordem em que ficam disponíveis (pronta e posto pronto,
    # «Bulk» AC), cada uma 85 % depois da anterior; depois os dollys
    seguinte = 0.0                    # «Gantt» Y: início + duração ÷ 85 % da viagem anterior
    for v in sorted(viagens, key=lambda v: (max(prontas[v["nome"]], v["rel"]), v["nome"])):
        pronta = prontas[v["nome"]]
        j = no_intervalo(max(pronta, v["rel"], seguinte, tl.bulk_hora_min), v["dur"])
        ini = _encaixar_comboio(tl, j, v["dur"], pronta, v["prep_mod"])
        nota = f"postos {v['postos']}" if v["postos"] else ""
        if ini > j + 0.05:
            nota += ("; " if nota else "") + "depois da pausa (antes dela não cabia com a folga de 85 %)"
        tar.append(Tarefa("Comboio", "viagem", f"Viagem do bulk — {v['nome']}", ini, ini + v["dur"],
                          chave=f"Bulk|{v['nome']}", nota=nota))
        seguinte = ini + v["dur"] / occ
    if tl.dolly_min > 0:
        j = no_intervalo(max(tl.dolly_rel, seguinte), tl.dolly_min)
        ini = _encaixar_comboio(tl, j, tl.dolly_min, 0.0, 0.0)
        tar.append(Tarefa("Comboio", "dollys", "Dollys C → P1 (à mão)", ini, ini + tl.dolly_min,
                          nota="depois das saídas das estações C" if tl.dolly_rel > 0 else ""))
    fim_comboio = max([x.fim for x in tar if x.recurso == "Comboio"] + [0.0])
    return tar, {"fim": fim, "sobra": tl.tempo_dia - fim, "atrasadas": atrasadas, "atraso_max": atraso_max,
                 "fim_comboio": fim_comboio, "fim_trabalho": fim_trabalho}


def gantt(preps: list, tl: TemposLivro, campo: str = "modelo", horario: bool | None = None) -> Gantt:
    """Gantt do armazém com a PIOR preparação de cada volta do dia nos dias
    dados (como a folha «Gantt» do livro); regras em ``_dia`` (`horario`: as
    voltas regulares não partem antes da hora do livro -- só para validar)."""
    pior, dia_pior = defaultdict(float), {}
    for p in preps:
        k, v = (p.ciclo, p.volta), getattr(p, campo)
        if k not in dia_pior or v > pior[k]:
            pior[k], dia_pior[k] = v, p.dia
    varios_dias = len({p.dia for p in preps}) > 1

    def nome(c, k):
        return f"{c} {k}" + (f" (pior: dia {dia_pior[(c, k)]})" if varios_dias and (c, k) in dia_pior else "")

    tar, r = _dia(pior, tl, nome, None, horario)
    linhas = [(x.nome.replace("Preparar ", "").replace("bulk — ", "Bulk — "), x.ini, x.fim, x.partida_planeada)
              for x in tar if x.recurso == "Armazém" and x.tipo == "preparação"]
    comboio = {x.nome.replace("Viagem do bulk — ", "").replace("Dollys C → P1 (à mão)", "Dollys"): x.ini
               for x in tar if x.recurso == "Comboio"}
    return Gantt(r["fim"], r["sobra"], r["atrasadas"], r["atraso_max"], linhas, r["fim_comboio"], comboio)


def gantt_por_dia(preps: list, tl: TemposLivro, campo: str = "modelo") -> list:
    """O Gantt de CADA dia (não a pior volta de cada posição do dia, que junta
    picos de dias diferentes): as preparações desse dia e, no fim, a da 1.ª
    volta do dia seguinte. -> [(dia, Gantt)]."""
    sched = sorted((t, c, k) for c in ("Caixas", "Paletes") for k, t in enumerate(tl.partidas[c], 1))
    if not sched:
        return []
    primeira = (sched[0][1], sched[0][2])
    por_dia = defaultdict(list)
    for p in preps:
        if p.ciclo != SEMANAL:
            por_dia[p.dia].append(p)
    out = []
    for d in sorted(por_dia):
        amanha = por_dia.get(d + 1, por_dia[d])
        lst = ([p for p in por_dia[d] if (p.ciclo, p.volta) != primeira]
               + [p for p in amanha if (p.ciclo, p.volta) == primeira])
        out.append((d, gantt(lst, tl, campo)))
    return out


# ----------------------------------------------------------------- resultado
@dataclass
class ResultadoTempos:
    preps: list
    calibracao: dict
    gantt_livro: Gantt
    gantt_modelo: Gantt
    tempo_por_posicao: dict          # pid -> min/dia de picking (deslocação + altura + manuseio)
    validacao: list
    avisos: list
    gantt_sem_consumiveis: Gantt | None = None
    semanal: dict | None = None      # reposição semanal dos consumíveis (se houver)
    por_dia: list = field(default_factory=list)   # Gantt de cada dia (livro e modelo)
    dias: int = 0                    # horizonte simulado
    # para o cronograma de um dia (``cronograma``)
    tl: object = None
    picks_por_dia: dict = field(default_factory=dict)
    desl: dict = field(default_factory=dict)
    enderecos: dict = field(default_factory=dict)  # sku -> endereços no arranjo
    posicoes_pick: dict = field(default_factory=dict)  # id(pick) -> [posição visitada por unidade]
    percursos: object = None         # Percursos do arranjo (preparações do bulk num só percurso)


def avaliar(res_proc, res_aloc, dist: dict, tl: TemposLivro, cfg, calibracao: dict | None,
            carros_base: bool, res_proc_validacao=None) -> ResultadoTempos:
    """`res_proc`: a simulação do horizonte (Config.simulacao_dias). A
    validação contra o livro usa `res_proc_validacao` (os 30 dias do livro),
    se dado -- o livro só tem 30 dias, e o Gantt dele é a pior volta nesses 30."""
    desl = deslocacao_por_sku(res_aloc, dist, cfg)
    if calibracao is None:
        calibracao, avisos = calibrar(res_proc.picks, desl, tl)
    else:
        calibracao, avisos = completar_calibracao(calibracao, tl)
    preps, av = preparacoes(res_proc, desl, calibracao, tl, carros_base)
    avisos += av
    sem = sum(p.sem_localizacao for p in preps)
    if sem:
        avisos.append(f"{sem} unidade(s) tiradas de SKUs sem localização no arranjo: contadas com o tempo "
                      f"do livro.")
    # minutos por dia em cada posição: cada visita reparte-se pelas posições do SKU
    por_sku = defaultdict(list)
    for c in res_aloc.colocacoes:
        por_sku[c.sku].append(c)
    tempo_pos = defaultdict(float)
    for k in res_proc.picks:
        cols = por_sku.get(k.sku)
        if not cols:
            continue
        g = grupo_do_pick(k)
        tot = sum(c.n_unidades for c in cols)
        equip = A_PE if g in (GRUPO_PECA, GRUPO_KIT) else None
        for c in cols:
            t = calibracao[g]["manuseio_min"] + custo_visita_min(dist.get(c.posicao.id, 0.0), c.posicao.rack,
                                                                 c.nivel_idx, cfg, equip)
            tempo_pos[c.posicao.id] += k.unidades * t * c.n_unidades / tot / res_proc.dias
    g_mod = gantt(preps, tl, "modelo")
    livro_dia = dict(gantt_por_dia(preps, tl, "livro"))
    semanal_dia = {p.dia: p.modelo for p in preps if p.ciclo == SEMANAL}
    por_dia = []
    for d, g in gantt_por_dia(preps, tl, "modelo"):
        s = semanal_dia.get(d, 0.0)
        cabe_s = s / tl.ocupacao <= g.sobra_min + 1e-9 if s else True
        comboio_cabe = g.fim_comboio_min <= tl.tempo_dia + 1e-9
        por_dia.append({"dia": d, "sobra_livro_min": round(livro_dia[d].sobra_min, 2),
                        "atrasadas_livro": livro_dia[d].atrasadas, "sobra_modelo_min": round(g.sobra_min, 2),
                        "atrasadas_modelo": g.atrasadas, "atraso_max_min": round(g.atraso_max_min, 2),
                        "fim_comboio_min": round(g.fim_comboio_min, 2), "comboio_cabe": comboio_cabe,
                        "reposicao_semanal_min": round(s, 2), "semanal_cabe": cabe_s,
                        "encaixa": g.sobra_min >= 0 and comboio_cabe and cabe_s})
    semanal = None
    if semanal_dia:
        vals = list(semanal_dia.values())
        nao = [x["dia"] for x in por_dia if x["reposicao_semanal_min"]
               and x["reposicao_semanal_min"] / tl.ocupacao > x["sobra_modelo_min"] + 1e-9]
        semanal = {"reposicoes": len(vals), "media_min": round(sum(vals) / len(vals), 2),
                   "pior_min": round(max(vals), 2), "pior_com_margem_min": round(max(vals) / tl.ocupacao, 2),
                   "nao_cabem": len(nao), "dias_que_nao_cabem": nao[:20], "cabe": not nao}
        if nao:
            avisos.append(f"A reposição semanal dos consumíveis não cabe na sobra do dia em {len(nao)} de "
                          f"{len(vals)} semanas (dias {', '.join(map(str, nao[:8]))}).")
    val_preps = preps
    if res_proc_validacao is not None:
        val_preps, _ = preparacoes(res_proc_validacao, desl, calibracao, tl, carros_base)
    rt = ResultadoTempos(preps, calibracao, gantt(preps, tl, "livro"), g_mod,
                         dict(tempo_pos), validar_contra_livro(val_preps, tl, carros_base), avisos,
                         gantt(preps, tl, "sem_consumiveis"), semanal, por_dia, res_proc.dias)
    rt.tl, rt.desl = tl, desl
    for k in res_proc.picks:
        rt.picks_por_dia.setdefault(k.dia, []).append(k)
    # que posição visita cada pick: as unidades do SKU por custo, tiradas por
    # ordem (FIFO) -- para o mapa de movimentos de um dia / de uma operação
    unidades_sku = defaultdict(list)
    for c in sorted(res_aloc.colocacoes, key=lambda c: (c.custo, c.posicao.id, c.nivel_idx)):
        unidades_sku[c.sku].extend([c.posicao.id] * max(c.n_unidades, 1))
    tiradas = defaultdict(int)
    for d in sorted(rt.picks_por_dia):
        for k in rt.picks_por_dia[d]:
            lst = unidades_sku.get(k.sku)
            if not lst:
                continue
            n = max(int(k.unidades), 1)
            rt.posicoes_pick[id(k)] = [lst[(tiradas[k.sku] + u) % len(lst)] for u in range(n)]
            tiradas[k.sku] += n
    for c in res_aloc.colocacoes:
        lst = rt.enderecos.setdefault(c.sku, [])
        if c.endereco not in lst:
            lst.append(c.endereco)
    return rt


def validar_contra_livro(preps: list, tl: TemposLivro, carros_base: bool) -> list:
    """Com os tempos do livro, a preparação tem de bater com a «7b Voltas»
    (BB/BC), com a folha «Bulk» (AB) e o Gantt com a sobra do livro."""
    from .procura import Verificacao
    if not carros_base:
        return [Verificacao("Validação dos tempos contra o livro", True,
                            "Não aplicável: carros por dia diferentes do livro.")]
    out = []
    voltas_dia = {c: len(tl.partidas[c]) for c in ("Caixas", "Paletes")}
    for ciclo in ("Caixas", "Paletes"):
        lst = [p for p in preps if p.ciclo == ciclo]
        maus = [p for p in lst if abs(p.livro - tl.prep_7b.get((ciclo, (p.dia - 1) * voltas_dia[ciclo] + p.volta),
                                                                 -1)) > 1e-6]
        out.append(Verificacao(f"Preparação das voltas de {ciclo.lower()} = «7b Voltas»", not maus,
                               f"{len(lst) - len(maus)} de {len(lst)} voltas iguais"
                               + (f"; ex. dia {maus[0].dia} volta {maus[0].volta}: {maus[0].livro:.2f} vs "
                                  f"{tl.prep_7b.get((ciclo, (maus[0].dia - 1) * voltas_dia[ciclo] + maus[0].volta))}"
                                  if maus else "")))
    esperado = sum(v["prep"] for v in tl.viagens_bulk)
    dias = sorted({p.dia for p in preps})
    maus = [d for d in dias if abs(sum(p.livro for p in preps if p.dia == d and p.ciclo.startswith("Bulk"))
                                   - esperado) > 1e-6]
    out.append(Verificacao("Preparação do bulk por dia = «Bulk» (AB)", not maus,
                           f"livro {esperado:.2f} min/dia" + (f"; diferem os dias {maus[:5]}" if maus else
                                                               f"; igual nos {len(dias)} dias")))
    # Gantt: a pior volta de cada posição nos dias do livro, com os tempos do
    # livro e as voltas regulares à hora gravada no livro (o «D2 auto» calcula-a
    # por repetição) -- tem de dar a folha «Gantt»: sobra do armazém (C7), fim
    # do mizu (C9) e o início de cada viagem do bulk e dos dollys (K67:K77)
    dias_livro = max((n for (c, n) in tl.prep_7b if c == "Caixas"), default=0) // max(voltas_dia["Caixas"], 1)
    if len(dias) != dias_livro:
        out.append(Verificacao("Gantt do dia = folha «Gantt»", True,
                               f"Não aplicável: {len(dias)} dias, o livro usa a pior volta de {dias_livro} dias."))
        return out
    g = gantt(preps, tl, "livro", horario=True)
    out.append(Verificacao("Gantt: sobra do armazém no fim do dia = «Gantt» C7",
                           abs(g.sobra_min - tl.gantt_sobra) < 0.01,
                           f"{g.sobra_min:.2f} min vs livro {tl.gantt_sobra:.2f}"))
    if tl.gantt_mizu_sobra is not None:
        sobra_mizu = tl.tempo_dia - g.fim_comboio_min
        out.append(Verificacao("Gantt: sobra do mizu no fim do dia = «Gantt» C9",
                               abs(sobra_mizu - tl.gantt_mizu_sobra) < 0.01,
                               f"{sobra_mizu:.2f} min vs livro {tl.gantt_mizu_sobra:.2f}"))
    if tl.gantt_viagens:
        dif = {n: (g.comboio.get(n), t) for n, t in tl.gantt_viagens.items()
               if g.comboio.get(n) is None or abs(g.comboio[n] - t) > 0.01}
        out.append(Verificacao("Gantt: partida das voltas regulares, das viagens do bulk e dos dollys = "
                               "«Gantt» O32:O61 e K67:K77", not dif,
                               f"{len(tl.gantt_viagens) - len(dif)} de {len(tl.gantt_viagens)} iguais"
                               + "".join(f"; {n}: {a if a is None else round(a, 2)} vs {b:.2f}"
                                         for n, (a, b) in list(dif.items())[:4])))
    return out


# ----------------------------------------------------------------- calibração gravada
def carregar_calibracao(caminho: Path, chave: str | None = None) -> dict | None:
    """A calibração gravada, se a `chave` (o que ela depende) for a mesma."""
    caminho = Path(caminho)
    if not caminho.exists():
        return None
    d = json.loads(caminho.read_text(encoding="utf-8"))
    cal = d.get("grupos")
    if chave is not None and d.get("chave") != chave:
        return None
    return cal if cal and all(g in cal for g in GRUPOS) else None


def guardar_calibracao(caminho: Path, cal: dict, contexto: dict, chave: str | None = None) -> None:
    Path(caminho).write_text(json.dumps({"chave": chave, "grupos": cal, "contexto": contexto},
                                        ensure_ascii=False, indent=1), encoding="utf-8")


# ----------------------------------------------------------------- cronograma de um dia
def _relogio(tl: TemposLivro, t: float, depois: bool = True) -> float:
    """min de TRABALHO -> min de RELÓGIO desde o início do turno. Num instante
    em que começa uma pausa, `depois` diz de que lado se fica: uma tarefa que
    COMEÇA aí começa depois da pausa, uma que ACABA aí acaba antes dela."""
    return t + sum(d for ini, d in zip(tl.pausas, tl.pausas_dur)
                   if (ini <= t + 1e-9 if depois else ini < t - 1e-9))


def _hora(tl: TemposLivro, t: float, depois: bool = True) -> str:
    m = int(round(tl.turno_inicio_min + _relogio(tl, t, depois)))
    return f"{m // 60:02d}:{m % 60:02d}"


def _segmentos(tl: TemposLivro, ini: float, fim: float) -> list:
    """Intervalo de trabalho -> pedaços em min de relógio, cortados nas pausas."""
    cortes = [ini] + [p for p in sorted(tl.pausas) if ini + 1e-9 < p < fim - 1e-9] + [fim]
    seg = [[_relogio(tl, a, True), _relogio(tl, b, False)] for a, b in zip(cortes, cortes[1:])]
    return [[round(x, 2), round(y, 2)] for x, y in seg if y > x + 1e-6]


def _pausas_relogio(tl: TemposLivro) -> list:
    return [[round(_relogio(tl, p, False), 2), round(_relogio(tl, p, False) + d, 2)]
            for p, d in zip(tl.pausas, tl.pausas_dur)]


def cronograma(rt, dia: int) -> dict:
    """O que o operador do armazém e o comboio fazem num dia, a que horas, e
    que picks entram em cada preparação. Mesmas regras do Gantt (``_dia``),
    com as preparações do PRÓPRIO dia (e a 1.ª volta do dia seguinte)."""
    tl = rt.tl
    preps = [p for p in rt.preps if p.dia in (dia, dia + 1)]
    sched = sorted((t, c, k) for c in ("Caixas", "Paletes") for k, t in enumerate(tl.partidas[c], 1))
    c0, k0 = sched[0][1], sched[0][2]
    pior = {}
    amanha = dia + 1 if any(p.dia == dia + 1 for p in preps) else dia
    for p in preps:
        if p.ciclo == SEMANAL:
            continue
        if (p.ciclo, p.volta) == (c0, k0):
            if p.dia == amanha:
                pior[(c0, k0)] = p.modelo
        elif p.dia == dia:
            pior[(p.ciclo, p.volta)] = p.modelo
    tar, r = _dia(pior, tl)
    semanal = next((p for p in preps if p.dia == dia and p.ciclo == SEMANAL), None)
    if semanal:
        ini = r["fim"]
        tar.append(Tarefa("Armazém", "reposição", "Reposição semanal dos consumíveis", ini, ini + semanal.modelo,
                          chave=f"{SEMANAL}|0", nota="na sobra do dia" if ini + semanal.modelo <= tl.tempo_dia
                          else "passa o fim do turno"))

    # picks de cada tarefa
    picks = rt.picks_por_dia.get(dia, [])
    amanha_picks = rt.picks_por_dia.get(amanha, [])
    por_chave = defaultdict(list)
    viagens = {"Paletes": [v for v in tl.viagens_bulk if v.get("tipo") == "Paletes"],
               "Caixas": [v for v in tl.viagens_bulk if v.get("tipo") == "Caixas"]}

    def chave_bulk(k):
        tipo = "Paletes" if grupo_do_pick(k) in (GRUPO_PALETE, GRUPO_PECA) else "Caixas"
        vs = viagens[tipo]
        if not vs:
            return f"Bulk|{tipo} (bulk)"
        v = next((v for v in vs if k.posto and k.posto in [x.strip() for x in str(v.get("postos", "")).split(",")]),
                 vs[0])
        return f"Bulk|{v['nome']}"

    for k in picks:
        if k.ciclo == "Bulk":
            por_chave[chave_bulk(k)].append(k)
        elif k.ciclo == SEMANAL:
            por_chave[f"{SEMANAL}|0"].append(k)
        else:   # inclui a 1.ª volta de hoje (preparada ontem): os picks vão na volta do comboio
            por_chave[f"{k.ciclo}|{k.volta}"].append(k)
    for k in amanha_picks:
        if (k.ciclo, k.volta) == (c0, k0):
            por_chave[f"Amanhã|{c0}|{k0}"].append(k)

    def detalhe(k):
        t, _ = tempo_pick(k, rt.desl, rt.calibracao, tl)
        return {"sku": k.sku, "descricao": k.descricao, "unidade": k.unidade, "n": k.unidades, "pecas": k.pecas,
                "posto": k.posto, "origem": k.origem, "enderecos": rt.enderecos.get(k.sku, [])[:4],
                "tempo_min": round(t * k.unidades, 2)}

    def movimentos(lst):
        cnt = defaultdict(int)
        for k in lst:
            for pid in rt.posicoes_pick.get(id(k), []):
                cnt[pid] += 1
        return dict(cnt)

    out = []
    for x in sorted(tar, key=lambda x: (x.recurso, x.ini)):
        lst = por_chave.get(x.chave, []) if x.tipo in ("preparação", "reposição") or x.recurso == "Comboio" else []
        out.append({"recurso": x.recurso, "tipo": x.tipo, "nome": x.nome, "ini": round(x.ini, 2),
                    "fim": round(x.fim, 2), "ini_h": _hora(tl, x.ini), "fim_h": _hora(tl, x.fim, False),
                    "partida_h": _hora(tl, x.partida_planeada) if x.partida_planeada is not None else None,
                    "atraso_min": round(x.atraso, 2), "nota": x.nota, "chave": x.chave,
                    "segmentos": _segmentos(tl, x.ini, x.fim), "n_picks": len(lst),
                    "unidades": sum(k.unidades for k in lst),
                    "movimentos": movimentos(lst) if x.recurso == "Armazém" else {}})
        # o bulk de CAIXAS prepara-se de uma vez, com o carrinho: um percurso só pelas
        # posições, não uma ida e volta por caixa. As paletes não: o empilhador leva
        # uma de cada vez, por isso aí as idas e voltas são o percurso real.
        if rt.percursos is not None and x.recurso == "Armazém" and x.chave.startswith("Bulk|Caixas") \
                and out[-1]["movimentos"]:
            out[-1]["percurso"] = rt.percursos.volta(out[-1]["movimentos"])
    d = next((x for x in rt.por_dia if x["dia"] == dia), {})
    fim_turno = tl.turno_fim_min - tl.turno_inicio_min
    dia_mov = defaultdict(int)
    for t in out:
        for pid, n in t["movimentos"].items():
            dia_mov[pid] += n
    return {"dia": dia, "tarefas": out, "movimentos_dia": dict(dia_mov),
            "picks": {c: [detalhe(k) for k in sorted(v, key=lambda k: (k.posto, k.sku))] for c, v in por_chave.items()},
            "indicadores": {"sobra_min": round(r["sobra"], 2), "atrasadas": r["atrasadas"],
                            "atraso_max_min": round(r["atraso_max"], 2),
                            "fim_armazem_h": _hora(tl, r["fim_trabalho"], False),
                            "fim_comboio_h": _hora(tl, r["fim_comboio"], False), "encaixa": d.get("encaixa"),
                            "comboio_cabe": d.get("comboio_cabe"), "semanal_cabe": d.get("semanal_cabe")},
            "turno": {"inicio_h": _hora(tl, 0.0), "duracao_relogio_min": fim_turno,
                      "pausas": _pausas_relogio(tl)}}


# ----------------------------------------------------------------- percursos e caminhos
def _simplificar(cels: list, g: Grelha) -> list:
    """Polilinha só com os pontos onde a direção muda."""
    if not cels:
        return []
    pts = [g.centro(*cels[0])]
    for a, b, c in zip(cels, cels[1:], cels[2:]):
        if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
            pts.append(g.centro(*b))
    if len(cels) > 1:
        pts.append(g.centro(*cels[-1]))
    return [[round(x, 3), round(y, 3)] for x, y in pts]


class Percursos:
    """Percurso de uma preparação feita DE UMA VEZ (o bulk: decisão do
    utilizador, 2026-11-05): o operador sai do destino do picking, passa pela
    frente de cada posição da tarefa e volta -- um percurso fechado, pelo chão
    (a mesma grelha, passagem mínima e acessos dos tempos), na ordem do vizinho
    mais perto melhorada com 2-opt. Compara-se com as idas e voltas, uma por
    caixa (o modelo dos tempos, calibrado ao livro, que NÃO muda)."""

    def __init__(self, posicoes: list, obstaculos: list, ponto: tuple, passo: float = 0.25,
                 folga: float = 0.0):
        self.g = construir_grelha(posicoes, obstaculos, [ponto], passo, folga=folga)
        self.d0 = distancias(self.g, ponto)
        self.c0 = min(((i, j) for j in range(self.g.ny) for i in range(self.g.nx) if self.d0[j * self.g.nx + i] == 0.0),
                      default=self.g.celula(*ponto))
        self.acesso = {p.id: _celula_acesso(self.g, self.d0, p) for p in posicoes}
        self._dist, self._voltas = {}, {}

    def _de(self, cel) -> list:
        if cel not in self._dist:
            self._dist[cel] = distancias(self.g, self.g.centro(*cel))
        return self._dist[cel]

    def ida_e_volta_m(self, pid: str) -> float:
        c = self.acesso.get(pid)
        return 2.0 * self.d0[c[1] * self.g.nx + c[0]] if c else 0.0

    def volta(self, movimentos: dict) -> dict:
        """`movimentos`: pid -> nº de caixas tiradas lá. -> {metros, idas_voltas_m,
        paragens, pontos (polilinha fechada), ordem (pids)}."""
        pids = sorted(p for p in movimentos if self.acesso.get(p))
        chave = tuple(pids)
        if chave not in self._voltas:
            self._voltas[chave] = self._calcular(pids)
        r = dict(self._voltas[chave])
        r["idas_voltas_m"] = round(sum(n * self.ida_e_volta_m(p) for p, n in movimentos.items()), 1)
        return r

    def _calcular(self, pids: list) -> dict:
        g = self.g
        nos = [self.c0]
        dono = [None]
        for p in pids:            # várias posições com a mesma célula de acesso = uma paragem
            c = self.acesso[p]
            if c not in nos:
                nos.append(c)
                dono.append(p)
        n = len(nos)
        D = [[self._de(nos[a])[nos[b][1] * g.nx + nos[b][0]] for b in range(n)] for a in range(n)]
        ordem, falta = [0], set(range(1, n))
        while falta:
            k = min(falta, key=lambda b: (D[ordem[-1]][b], b))
            ordem.append(k)
            falta.remove(k)
        ordem.append(0)
        melhorou = True
        while melhorou and n > 3:
            melhorou = False
            for i in range(1, len(ordem) - 2):
                for j in range(i + 1, len(ordem) - 1):
                    a, b, c, d = ordem[i - 1], ordem[i], ordem[j], ordem[j + 1]
                    if D[a][c] + D[b][d] < D[a][b] + D[c][d] - 1e-9:
                        ordem[i:j + 1] = reversed(ordem[i:j + 1])
                        melhorou = True
        metros = sum(D[a][b] for a, b in zip(ordem, ordem[1:]))
        cels = [nos[ordem[0]]]
        for a, b in zip(ordem, ordem[1:]):
            troco = _descida(g, self._de(nos[b]), nos[a])
            cels += troco[1:]
        return {"metros": round(metros, 1), "paragens": n - 1, "pontos": _simplificar(cels, g),
                "ordem": [dono[k] for k in ordem[1:-1]]}


def _celula_acesso(g: Grelha, d: list, pos):
    """A célula livre (alcançável) mais perto do ponto de carga ao longo dos
    lados compridos da posição, a `g.folga` + meia célula da rack."""
    x, y, w, h = pos.x_m, pos.y_m, pos.larg_m, pos.prof_m
    off = g.folga + g.passo / 2
    if w >= h:
        n = max(int(w / g.passo), 1)
        pts = [(x + (k + 0.5) * w / n, yy) for k in range(n) for yy in (y - off, y + h + off)]
    else:
        n = max(int(h / g.passo), 1)
        pts = [(xx, y + (k + 0.5) * h / n) for k in range(n) for xx in (x - off, x + w + off)]
    cels = [g.celula(px, py) for px, py in pts]
    cels = [(i, j) for i, j in cels if g.e_livre(i, j) and d[j * g.nx + i] < INF]
    return min(cels, key=lambda c: d[c[1] * g.nx + c[0]]) if cels else None


def _descida(g: Grelha, d: list, ij) -> list:
    """Da célula `ij` até ao ponto de carga, pelo gradiente das distâncias --
    com a MESMA regra de vizinhança de ``distancias`` (diagonal só se as duas
    ortogonais forem livres: nunca se corta a esquina de uma rack)."""
    i, j = ij
    out = [(i, j)]
    for _ in range(g.nx * g.ny):
        atual = d[j * g.nx + i]
        if atual <= 0:
            break
        melhor = None
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            a, b = i + di, j + dj
            if not g.e_livre(a, b) or d[b * g.nx + a] >= atual - 1e-9:
                continue
            if di and dj and not (g.e_livre(i + di, j) and g.e_livre(i, j + dj)):
                continue
            if melhor is None or d[b * g.nx + a] < d[melhor[1] * g.nx + melhor[0]]:
                melhor = (a, b)
        if melhor is None:
            break
        i, j = melhor
        out.append((i, j))
    return out


def rotas(posicoes: list, obstaculos: list, ponto: tuple, visitas: dict, passo: float = 0.25,
          folga: float = 0.0) -> tuple[list, dict]:
    """Caminhos de picking no arranjo, os mesmos dos tempos: para cada posição,
    da frente dela ao ponto de carga pelo caminho mais curto, com a passagem
    mínima (`folga` de cada lado das racks).
    -> (calor do chão: [[x, y, lado, passagens/dia]] por célula, de todas as
    idas e voltas com `visitas` (pid -> visitas/dia);
        caminhos: pid -> polilinha [[x, y], ...] da posição ao ponto de carga)."""
    if not posicoes:
        return [], {}
    g = construir_grelha(posicoes, obstaculos, [ponto], passo, folga=folga)
    d = distancias(g, ponto)
    calor = defaultdict(float)
    caminhos = {}
    for p in posicoes:
        ij = _celula_acesso(g, d, p)
        if ij is None:
            continue
        cels = _descida(g, d, ij)
        v = visitas.get(p.id, 0.0)
        if v > 0:
            for c in cels:
                calor[c] += 2.0 * v              # ida e volta
        # polilinha: só os pontos onde muda a direção
        pts = [g.centro(*cels[0])]
        for a, b, c in zip(cels, cels[1:], cels[2:]):
            if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
                pts.append(g.centro(*b))
        if len(cels) > 1:
            pts.append(g.centro(*cels[-1]))
        caminhos[p.id] = [[round(x, 3), round(y, 3)] for x, y in pts]
    lado = g.passo
    return ([[round(g.x0 + a * lado, 3), round(g.y0 + b * lado, 3), round(lado, 3), round(v, 3)]
             for (a, b), v in calor.items()], caminhos)

"""Simulação operacional de 1 ano do armazém (2026-11-19).

Pergunta da reunião de 2026-11-18: manter posições FIXAS por componente deixa
posições boas vazias enquanto não chega o lote seguinte? E separar as
prateleiras por tipo de caixa custa espaço/tempo? Compara 3 modos de arrumação
× 2 arrumações das prateleiras = 6 cenários, no MESMO arranjo físico:

* ``fixo``      -- cada SKU tem os seus sítios (a alocação da app), reservados
                   mesmo vazios. Os SKUs de peças têm face + reserva (a app).
* ``dinamico``  -- nada é fixo: cada unidade que chega vai para o sítio livre
                   que a sua classe de rotação permite (os que mais saem por
                   metro de prateleira ficam com os sítios mais perto). Picking
                   FIFO onde estiver.
* ``picking``   -- posição de picking + reserva: cada SKU tem UMA faixa fixa
                   nos melhores sítios (a «posição de picking»); o resto do
                   stock fica em reserva dinâmica no espaço que sobra. O
                   operador de logística interna repõe a posição de picking a
                   partir da reserva (realocações).

× ``misto`` (níveis com caixas de vários tipos, como hoje) ou ``separado``
(cada nível de prateleira só leva um tipo de caixa; nos dinâmicos um nível
vazio pode mudar de tipo).

O que se mede (minutos/dia, só a parte que depende do sítio -- como o custo
ponderado da alocação; o manuseio do picking é igual em todos):
* picking para a linha: Σ visitas × custo da visita do nível (ida e volta +
  extra de altura) -- o mesmo ``custo_visita_min`` da alocação;
* arrumação dos lotes que chegam (operador de logística): viagens × custo do
  nível + manuseio por unidade;
* realocações reserva -> posição de picking: cais -> reserva -> face -> cais
  pelo chão + extra de altura nos dois níveis + manuseio;
* espaço: largura de prateleira / lugares de palete ocupados (o fixo conta o
  reservado), faltas na posição de picking, unidades sem lugar, ruturas.

Abastecimento (o cliente ainda não tem lotes -- PRESSUPOSTOS, ver ``Pressupostos``):
(R, Q) por SKU, revisão diária; prazo de entrega com atrasos aleatórios; o lote
é tal que o pico de stock = o stock dimensionado (10 carros), salvo
``cobertura_lote_dias``. Produção constante (Config.carros_por_dia).

Excluídas (iguais em todos os cenários): as OVERSIZE e as posições em par.

Uso: python -m warehouse.simulacao_ano --arranjo dados/arranjo_utilizador_2026-11-18.json
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from . import alocacao as A
from . import tempos as T
from .config import Config
from .racking import altura_nivel

CAIXAS = ("XS", "S", "M", "L")
MODOS = ("fixo", "dinamico", "picking", "hibrido")
NOME_MODO = {"fixo": "Posições fixas", "dinamico": "Dinâmico (por classe)",
             "picking": "Posição de picking + reserva", "hibrido": "Picking fixo só nas peças, resto dinâmico"}


@dataclass
class Pressupostos:
    dias: int = 250                    # dias úteis medidos (1 ano)
    aquecimento: int = 40              # dias antes de medir (stock e lotes desfasados)
    sementes: int = 3
    prazo_dias: int = 5                # prazo de entrega do fornecedor (dias úteis)
    atrasos: tuple = ((-1, 0.05), (0, 0.75), (1, 0.12), (2, 0.08))   # (dias, prob.)
    stock_seg_dias: float = 2.0        # stock de segurança (dias de consumo)
    cobertura_lote_dias: float | None = None   # None = lote enche o stock dimensionado
    # unidades por viagem do operador de logística (empilhador: 1 palete)
    cap_viagem: dict = field(default_factory=lambda: {"PALLET": 1, "MEIA": 1, "L": 2, "M": 4, "S": 6, "XS": 10})
    # manuseio de uma unidade numa arrumação/realocação (pegar + pousar), min
    manuseio_min: dict = field(default_factory=lambda: {"PALLET": 1.0, "MEIA": 1.0, "L": 0.2, "M": 0.2,
                                                        "S": 0.15, "XS": 0.15})
    folga_classe: float = 0.05         # dinâmico: tolerância da fronteira da classe
    # lotes que chegam no mesmo dia: arruma primeiro quem mais sai (fronteira mais
    # perto); False = pela ordem das encomendas (pedido do utilizador, 2026-11-19)
    prioridade_chegadas: bool = True


# ------------------------------------------------------------------ modelo físico
@dataclass(eq=False)
class Cel:
    key: tuple
    pid: str
    rack: object
    idx: int
    L: float
    P: float
    g: float
    H: float
    custo: float
    dist: float
    extra: float
    v: float
    familia: str
    q: float = 0.0                       # percentil de custo na família (dinâmico)
    tipo: str | None = None              # separado: tipo de caixa do nível
    tipo_fixo: bool = False
    lanes: list = field(default_factory=list)
    usado: float = 0.0                   # Σ larguras das faixas

    @property
    def limite(self) -> float:
        return self.L + self.g


@dataclass(eq=False)
class Lane:
    sku: str
    cel: Cel
    x: float
    w: float
    cap: int
    fixa: bool
    face: bool = False
    units: deque = field(default_factory=deque)


@dataclass
class Sku:
    sku: str
    tipo: str
    dims: tuple
    fixa_orient: bool
    unit_h: float
    hmax: int
    s_max: int
    racks: set
    u: float = 0.0
    v: float = 0.0
    pecas: bool = False
    pecas_tot: int = 0                   # peças em stock (para as embalagens/dia dos SKUs de peças)


@dataclass
class Modelo:
    cels: list               # [Cel] do pool (sem OVERSIZE / pares)
    skus: dict               # sku -> Sku
    fixas: dict              # sku -> [(cel.key, x, w, cap, face)] da alocação
    posicoes: dict           # pid -> PosicaoFisica
    obstaculos: list
    ponto: tuple
    excluidos: list
    nao_alocados: int
    custo_ponderado: float


def _so_um_tipo(orig):
    """Packer da alocação com «um nível de prateleira = um tipo de caixa»."""
    def f(nivel, rack, item):
        t = item.load_type.value
        if t in CAIXAS and any(c.load_type.value != t for c in nivel.caixas):
            return None
        return orig(nivel, rack, item)
    return f


def alocar(cfg, arranjo, pasta: Path, separado: bool):
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "estado.json").unlink(missing_ok=True)
    c = dataclasses.replace(cfg, alocacao_estado=pasta / "estado.json", tempos_calibracao=pasta / "cal.json")
    orig = A._empacotar_faixas
    if separado:
        A._empacotar_faixas = _so_um_tipo(orig)
    try:
        res, taxas = A.run(c, arranjo, out=pasta / "alocacao.xlsx")
    finally:
        A._empacotar_faixas = orig
    from . import data_access, io_excel
    local = io_excel.com_carrinhos_da_linha(dataclasses.replace(cfg, altura_armazem_m=arranjo.altura_armazem_m))
    return res, taxas, {a.sku: a for a in data_access.load_articles(local)}


def extrair(res, taxas, cfg, artigos: dict) -> Modelo:
    especiais = set(res.pares)
    cels, skus, fixas, excl = [], {}, defaultdict(list), set()
    faces = {(c.posicao.id, c.nivel_idx, c.sku) for c in res.colocacoes if c.face}
    pecas = {c.sku for c in res.colocacoes if c.face}
    for c in res.celulas:
        rack, idx, pid = c.pos.rack, c.nivel.nivel_idx, c.pos.id
        over = any(cx.load_type.value.startswith("OVERSIZE") for cx in c.nivel.caixas)
        for cx in c.nivel.caixas:
            if cx.load_type.value.startswith("OVERSIZE"):
                excl.add(cx.sku)
        if pid in especiais or c.area_livre < -0.5 or over:
            continue
        g = getattr(rack, "folga_lateral_m", 0.0) or 0.0
        cel = Cel((pid, idx), pid, rack, idx, rack.larg_m, rack.prof_m, g, altura_nivel(rack, idx), c.custo,
                  res.dist.get(pid, 0.0), T.extra_altura_min(rack, idx, cfg),
                  cfg.picking_velocidade_m_s[T.equipamento_da_rack(rack, cfg)],
                  "prateleira" if "prateleira" in rack.nome.lower() else "palete")
        cels.append(cel)
        faixas = {}
        for cx in c.nivel.caixas:
            t = cx.load_type.value
            n = max(1, cx.num_boxes_in_stack)
            s = skus.get(cx.sku)
            if s is None:
                s = skus[cx.sku] = Sku(cx.sku, t, (cx.comp_m, cx.larg_m), bool(cx.orientacao_fixa),
                                       cx.alt_m / n, n, 0, set())
            s.hmax = max(s.hmax, n)
            s.unit_h = min(s.unit_h, cx.alt_m / n)
            s.s_max += n
            s.pecas_tot += cx.pecas_nesta_caixa or 0
            s.racks.add(rack.nome)
            k = (cx.sku, round(cx.x_m, 3))
            if k not in faixas:
                faixas[k] = [cx.x_m, cx.comp_m + g, 0]
            faixas[k][2] += n
        vistas = set()
        for (sku, _), (x, w, cap) in faixas.items():
            face = (pid, idx, sku) in faces and sku not in vistas
            vistas.add(sku)
            fixas[sku].append((cel.key, x, w, cap, face))
    for s in skus.values():
        t = taxas.get(s.sku)
        s.u, s.v = (t.unidades_dia, t.visitas_dia) if t else (0.0, 0.0)
        s.pecas = s.sku in pecas
        # SKUs de peças: a Taxa conta carrinhos/kits por dia (visitas), não embalagens gastas --
        # embalagens/dia = peças por carro × carros/dia / peças por embalagem (master data)
        art = artigos.get(s.sku)
        if s.pecas and art is not None and s.pecas_tot > 0 and getattr(art, "pecas_por_carro", 0):
            s.u = art.pecas_por_carro * cfg.carros_por_dia / (s.pecas_tot / s.s_max)
    # percentil de custo por família (largura acumulada por ordem de custo)
    por_fam = defaultdict(list)
    for c in cels:
        por_fam[c.familia].append(c)
    for lst in por_fam.values():
        lst.sort(key=lambda c: (c.custo, c.pid, c.idx))
        tot = sum(c.L for c in lst) or 1.0
        acc = 0.0
        for c in lst:
            c.q = acc / tot
            acc += c.L
    return Modelo(cels, skus, dict(fixas), {p.id: p for p in res.posicoes}, res.planta.get("obstaculos", []),
                  res.ponto_carga, sorted(excl), sum(n.n_unidades for n in res.nao_alocados),
                  sum(c.visitas_dia * c.custo for c in res.colocacoes if c.sku not in excl))


class Distancias:
    """Metros pelo chão entre duas posições (Dijkstra da grelha da app, em cache)."""

    def __init__(self, m: Modelo, cfg):
        self.m, self.cfg = m, cfg
        self.g = T.construir_grelha(list(m.posicoes.values()), m.obstaculos, [m.ponto], cfg.picking_grelha_m,
                                    folga=cfg.picking_passagem_min_m / 2)
        self.cache, self.dj = {}, {}

    def __call__(self, a: str, b: str) -> float:
        if a == b:
            return 0.0
        k = (a, b) if a < b else (b, a)
        if k not in self.cache:
            pa, pb = self.m.posicoes[k[0]], self.m.posicoes[k[1]]
            if k[0] not in self.dj:
                self.dj[k[0]] = T.distancias(self.g, pa.centro)
            d = self.dj[k[0]]
            v = T.distancia_acesso(self.g, d, pb)
            if v == T.INF:
                v = abs(pa.centro[0] - pb.centro[0]) + abs(pa.centro[1] - pb.centro[1])
            self.cache[k] = v
        return self.cache[k]


# ------------------------------------------------------------------ simulação
class Sim:
    def __init__(self, m: Modelo, modo: str, separado: bool, fixas: dict, pr: Pressupostos, seed: int,
                 dist: Distancias, zona: set | None = None):
        """`zona`: posições (pid) da zona dinâmica -- os sítios novos abrem-se primeiro
        lá; fora dela só se não houver lugar (conta em «fora_zona»). None = todas."""
        self.m, self.modo, self.sep, self.pr, self.dist = m, modo, separado, pr, dist
        self.zona = zona
        self.rng = random.Random(seed)
        self.cels = {c.key: dataclasses.replace(c, lanes=[], usado=0.0, tipo=None, tipo_fixo=False) for c in m.cels}
        self.skus = {s: x for s, x in m.skus.items() if s not in m.excluidos}
        self.lanes = defaultdict(list)       # sku -> [Lane]
        self.face = {}                       # sku -> Lane
        self.geo_cache = {}
        self.met = defaultdict(float)
        self.vis_pos = defaultdict(float)     # pid -> visitas de picking no ano medido (mapa de calor)
        self.medir = False
        self._ordens(fixas)
        self._politica()

    # --- abastecimento
    def _ordens(self, fixas):
        pr = self.pr
        self.R, self.Q, self.acc, self.encomendas = {}, {}, {}, defaultdict(list)
        for s, x in self.skus.items():
            ss = min(math.ceil(pr.stock_seg_dias * x.u), max(x.s_max - 1, 0)) if x.u > 0 else 0
            q = max(1, x.s_max - ss)
            if pr.cobertura_lote_dias and x.u > 0:
                q = min(q, max(1, math.ceil(x.u * pr.cobertura_lote_dias)))
            self.Q[s] = q
            # encomenda no fim do dia d, chega no início de d + prazo: consomem-se prazo - 1 dias
            self.R[s] = ss + math.floor(x.u * (pr.prazo_dias - 1)) if x.u > 0 else -1
            self.acc[s] = self.rng.random()
        self.fixas = fixas

    def _prazo(self) -> int:
        r, a = self.rng.random(), 0.0
        for d, p in self.pr.atrasos:
            a += p
            if r <= a:
                return max(1, self.pr.prazo_dias + d)
        return self.pr.prazo_dias

    # --- geometria
    def geo(self, x: Sku, c: Cel):
        k = (x.sku, c.rack.nome, c.idx)
        if k not in self.geo_cache:
            w, h = x.dims
            opts = [(w, h)] if x.fixa_orient else [tuple(sorted((w, h))), tuple(sorted((w, h), reverse=True))]
            best = None
            hc = min(x.hmax, int((c.H + 1e-9) // x.unit_h)) if x.unit_h > 0 else x.hmax
            for cw, ch in opts:
                if cw > c.L + 1e-9 or ch > c.P + 1e-9 or hc < 1:
                    continue
                kk = max(1, int((c.P + 1e-9) // ch))
                cand = (cw + c.g, -kk * hc)
                if best is None or cand < best:
                    best = cand
            self.geo_cache[k] = None if best is None else (best[0], -best[1])
        return self.geo_cache[k]

    def x_livre(self, c: Cel, w: float, tipo: str):
        if c.rack.nome not in self._racks_ok or c.limite - c.usado < w - 1e-9:
            return None
        if self.sep and tipo in CAIXAS:
            if c.tipo is not None and c.tipo != tipo:
                return None
            if c.tipo is None and c.lanes:
                return None
            n = int((c.limite + 1e-9) // w)
            ocup = [(l.x, l.x + l.w) for l in c.lanes]
            for i in range(n):
                a = i * w
                if all(b0 >= a + w - 1e-9 or b1 <= a + 1e-9 for b0, b1 in ocup):
                    return a
            return None
        x = 0.0
        for a, b in sorted((l.x, l.x + l.w) for l in c.lanes):
            if a - x >= w - 1e-9:
                return x
            x = max(x, b)
        return x if c.limite - x >= w - 1e-9 else None

    def nova_lane(self, x: Sku, c: Cel, fixa=False, face=False, cap=None, xpos=None, w=None):
        if w is None:
            gw = self.geo(x, c)
            if gw is None:
                return None
            w, cap0 = gw
            cap = cap or cap0
        if xpos is None:
            self._racks_ok = x.racks
            xpos = self.x_livre(c, w, x.tipo)
            if xpos is None:
                return None
        ln = Lane(x.sku, c, xpos, w, cap, fixa, face)
        c.lanes.append(ln)
        c.usado += w
        if self.sep and x.tipo in CAIXAS and c.tipo is None:
            c.tipo = x.tipo
        self.lanes[x.sku].append(ln)
        if face:
            self.face[x.sku] = ln
        return ln

    def tirar_lane(self, ln: Lane):
        c = ln.cel
        c.lanes.remove(ln)
        c.usado -= ln.w
        self.lanes[ln.sku].remove(ln)
        if self.sep and not c.lanes and not c.tipo_fixo:
            c.tipo = None

    # --- políticas
    def _politica(self):
        m = self.m
        por_custo = self._na_zona(sorted(self.cels.values(), key=lambda c: (c.custo, c.pid, c.idx)))
        self.ordem_perto = por_custo
        if self.modo == "fixo":
            for s, lst in self.fixas.items():
                if s not in self.skus:
                    continue
                x = self.skus[s]
                for key, xpos, w, cap, face in sorted(lst, key=lambda t: self.cels[t[0]].custo if t[0] in self.cels else 1e9):
                    c = self.cels.get(key)
                    if c is None:
                        continue
                    self.nova_lane(x, c, fixa=True, face=face and x.pecas, cap=cap, xpos=xpos, w=w)
                    if self.sep and x.tipo in CAIXAS:
                        c.tipo, c.tipo_fixo = x.tipo, True
        if self.modo in ("picking", "hibrido"):
            # uma faixa fixa por SKU nos melhores sítios: por visitas/dia por metro de faixa
            # (híbrido: só os SKUs de peças -- os outros ficam dinâmicos por classe)
            def dens(x):
                c0 = next((c for c in por_custo if c.rack.nome in x.racks and self.geo(x, c)), None)
                w = self.geo(x, c0)[0] if c0 else 1.0
                return x.v / w
            com_face = [x for x in self.skus.values() if self.modo == "picking" or x.pecas]
            for x in sorted(com_face, key=lambda x: (-dens(x), x.sku)):
                for c in por_custo:
                    if c.rack.nome not in x.racks:
                        continue
                    gw = self.geo(x, c)
                    if gw is None:
                        continue
                    ln = self.nova_lane(x, c, fixa=True, face=True, cap=min(gw[1], x.s_max))
                    if ln:
                        break
        if self.modo in ("dinamico", "hibrido") and self.zona:
            # percentil de custo DENTRO da zona (as fronteiras das classes repartem a zona)
            por_fam_z = defaultdict(list)
            for c in self.cels.values():
                if c.pid in self.zona:
                    por_fam_z[c.familia].append(c)
                else:
                    c.q = 1.0 + c.q
            for lst in por_fam_z.values():
                lst.sort(key=lambda c: (c.custo, c.pid, c.idx))
                tot, acc = sum(c.L for c in lst) or 1.0, 0.0
                for c in lst:
                    c.q = acc / tot
                    acc += c.L
        if self.modo in ("dinamico", "hibrido"):
            # classe: fronteira = posição do SKU na ordem visitas por metro de faixa ocupada (média)
            self.alvo = {}
            por_fam = defaultdict(list)
            for x in self.skus.values():
                c0 = next((c for c in por_custo if c.rack.nome in x.racks and self.geo(x, c)), None)
                if c0 is None:
                    continue
                w, cap = self.geo(x, c0)
                medio = self.Q[x.sku] / 2 + max(x.s_max - self.Q[x.sku], 0)
                esp = math.ceil(max(medio, 1) / cap) * w
                por_fam[c0.familia].append((x.v / esp if esp else 0.0, x.sku, esp))
            cap_fam = defaultdict(float)
            for c in self.cels.values():
                if not self.zona or c.pid in self.zona:      # com zona, as classes repartem a zona
                    cap_fam[c.familia] += c.L
            for fam, lst in por_fam.items():
                lst.sort(key=lambda t: (-t[0], t[1]))
                tot = sum(e for _, _, e in lst)
                escala = min(1.0, tot / cap_fam[fam]) if cap_fam[fam] else 1.0
                acc = 0.0
                for _, s, e in lst:
                    self.alvo[s] = acc / tot * escala if tot else 0.0
                    acc += e
            self.ordem_classe = {}

    def ordem_para(self, x: Sku):
        if self.modo not in ("dinamico", "hibrido"):
            return self._na_zona(self.ordem_perto)
        if x.sku not in self.ordem_classe:
            alvo = self.alvo.get(x.sku, 0.0) - self.pr.folga_classe
            self.ordem_classe[x.sku] = self._na_zona(sorted(
                self.cels.values(), key=lambda c: (0, c.custo) if c.q >= alvo else (1, -c.q)))
        return self.ordem_classe[x.sku]

    def _na_zona(self, ordem):
        if not self.zona:
            return ordem
        return [c for c in ordem if c.pid in self.zona] + [c for c in ordem if c.pid not in self.zona]

    def colocar(self, x: Sku, dia: int):
        """Uma unidade que chega -> a faixa onde fica."""
        lst = self.lanes[x.sku]
        f = self.face.get(x.sku)
        if f is not None and len(f.units) < f.cap:
            ln = f
        else:
            cands = [l for l in lst if len(l.units) < l.cap and l is not f]
            if self.modo == "fixo":
                cands.sort(key=lambda l: (not l.fixa, l.cel.custo))
            else:
                cands.sort(key=lambda l: l.cel.custo)
            ln = cands[0] if cands else None
            if ln is None:
                for c in self.ordem_para(x):
                    ln = self.nova_lane(x, c)
                    if ln:
                        break
                if ln is not None and self.modo == "fixo":
                    self.conta("sem_lugar_fixo", 1)
                if ln is not None and self.zona and ln.cel.pid not in self.zona:
                    self.conta("fora_zona", 1)
        if ln is None:
            self.conta("sem_lugar", 1)
            return None
        ln.units.append(dia)
        return ln

    # --- operação
    def conta(self, k, v):
        if self.medir:
            self.met[k] += v

    def stock(self, s):
        return sum(len(l.units) for l in self.lanes[s])

    def mais_antiga(self, s, excluir=None):
        best = None
        for l in self.lanes[s]:
            if l is excluir or not l.units:
                continue
            if best is None or l.units[0] < best.units[0]:
                best = l
        return best

    def tirar(self, ln: Lane):
        ln.units.popleft()
        if not ln.units and not ln.fixa:
            self.tirar_lane(ln)

    def de_onde(self, s):
        """Faixa de onde sai a próxima unidade e se foi uma falta na posição de picking."""
        f = self.face.get(s)
        if f is not None and f.units:
            return f, False
        ln = self.mais_antiga(s)
        return ln, (f is not None and ln is not None)

    def repor_face(self, s, ate=None):
        f = self.face.get(s)
        if f is None:
            return
        x = self.skus[s]
        cap_v = self.pr.cap_viagem.get(x.tipo, 1)
        alvo = f.cap if ate is None else min(f.cap, ate)
        while len(f.units) < alvo:
            orig = self.mais_antiga(s, excluir=f)
            if orig is None:
                return
            n = min(alvo - len(f.units), len(orig.units), cap_v)
            c0, c1 = orig.cel, f.cel
            t = ((c0.dist + self.dist(c0.pid, c1.pid) + c1.dist) / c0.v / 60.0 + c0.extra + c1.extra
                 + n * self.pr.manuseio_min.get(x.tipo, 0.2))
            self.conta("realoc_min", t)
            self.conta("realoc_viagens", 1)
            self.conta("realoc_unid", n)
            for _ in range(n):
                f.units.append(orig.units[0])
                self.tirar(orig)

    def dia(self, d: int):
        pr = self.pr
        # 1) chegadas -> arrumação
        chegadas = self.encomendas.pop(d, [])
        if pr.prioridade_chegadas and getattr(self, "alvo", None):
            chegadas.sort(key=lambda t: self.alvo.get(t[0], 1.0))      # quem mais sai primeiro
        for s, q in chegadas:
            x = self.skus[s]
            por_cel = defaultdict(int)
            for _ in range(q):
                ln = self.colocar(x, d)
                if ln:
                    por_cel[ln.cel.key] += 1
            cap_v = pr.cap_viagem.get(x.tipo, 1)
            for key, n in por_cel.items():
                c = self.cels[key]
                self.conta("arrum_min", math.ceil(n / cap_v) * c.custo + n * pr.manuseio_min.get(x.tipo, 0.2))
        # 2) picking
        for s, x in self.skus.items():
            if x.u <= 0:
                continue
            self.acc[s] += x.u
            n = int(self.acc[s])
            self.acc[s] -= n
            if x.pecas:
                ln, falta = self.de_onde(s)
                if ln is None:
                    self.conta("ruturas", x.v)
                else:
                    self.conta("picking_min", x.v * ln.cel.custo)
                    self.conta("visitas", x.v)
                    if self.medir:
                        self.vis_pos[ln.cel.pid] += x.v
                    self.conta("faltas_face", x.v if falta else 0)
                for _ in range(n):
                    ln, _ = self.de_onde(s)
                    if ln is None:
                        break
                    self.tirar(ln)
                    if self.modo != "dinamico":
                        f = self.face.get(s)
                        if f is not None and not f.units:
                            self.repor_face(s)
            else:
                fv = min(1.0, x.v / x.u)
                for _ in range(n):
                    ln, falta = self.de_onde(s)
                    if ln is None:
                        self.conta("ruturas", fv)
                        break
                    self.conta("picking_min", fv * ln.cel.custo)
                    self.conta("visitas", fv)
                    if self.medir:
                        self.vis_pos[ln.cel.pid] += fv
                    self.conta("faltas_face", fv if falta else 0)
                    self.tirar(ln)
        # 3) fim do dia: repor posições de picking (operador de logística) e encomendar
        if self.modo == "picking":
            for s, x in self.skus.items():
                f = self.face.get(s)
                if f is not None and not x.pecas and len(f.units) < min(f.cap, max(1, math.ceil(x.u))):
                    self.repor_face(s)
        for s, x in self.skus.items():
            if self.R[s] < 0:
                continue
            self._pend[s] = [(c, q) for c, q in self._pend[s] if c > d]
            ip = self.stock(s) + sum(q for _, q in self._pend[s])
            while ip <= self.R[s]:
                chega = d + self._prazo()
                self.encomendas[chega].append((s, self.Q[s]))
                self._pend[s].append((chega, self.Q[s]))
                ip += self.Q[s]
        # 4) espaço
        if self.medir:
            for fam in ("prateleira", "palete"):
                u = sum(c.usado for c in self.cels.values() if c.familia == fam)
                self.met[f"esp_{fam}_soma"] += u
                self.met[f"esp_{fam}_max"] = max(self.met[f"esp_{fam}_max"], u)
            pos = {c.pid for c in self.cels.values() if c.lanes}
            for fam in ("prateleira", "palete"):
                n = len({c.pid for c in self.cels.values() if c.lanes and c.familia == fam})
                self.met[f"pos_{fam}_soma"] += n
                self.met[f"pos_{fam}_max"] = max(self.met[f"pos_{fam}_max"], n)
            self.met["pos_soma"] += len(pos)
            self.met["pos_max"] = max(self.met["pos_max"], len(pos))
            self.pos_usadas |= pos
            if d % 5 == 0:
                sk = [len({l.cel.key for l in self.lanes[s] if l.units}) for s in self.skus if self.stock(s)]
                self.met["disp_soma"] += sum(sk) / max(len(sk), 1)
                self.met["disp_n"] += 1

    def correr(self) -> dict:
        pr = self.pr
        self._pend = defaultdict(list)
        self.pos_usadas = set()
        # stock inicial desfasado no ciclo de cada SKU (e encomendas em curso)
        for s, x in self.skus.items():
            if x.u <= 0:
                n0 = x.s_max
            else:
                ss = max(self.R[s] - math.floor(x.u * (pr.prazo_dias - 1)), 0)
                n0 = ss + self.rng.randint(0, self.Q[s])
            for _ in range(min(n0, x.s_max)):
                self.colocar(x, -self.rng.randint(1, 30))
        for d in range(pr.aquecimento + pr.dias):
            self.medir = d >= pr.aquecimento
            self.dia(d)
        nd = pr.dias
        cap = {fam: sum(c.L for c in self.cels.values() if c.familia == fam) for fam in ("prateleira", "palete")}
        met = self.met
        out = {
            "picking_min_dia": met["picking_min"] / nd,
            "arrumacao_min_dia": met["arrum_min"] / nd,
            "realocacao_min_dia": met["realoc_min"] / nd,
            "realocacoes_dia": met["realoc_viagens"] / nd,
            "unid_realocadas_dia": met["realoc_unid"] / nd,
            "visitas_dia": met["visitas"] / nd,
            "faltas_face_pct": 100 * met["faltas_face"] / met["visitas"] if met["visitas"] else 0.0,
            "ruturas_ano": met["ruturas"],
            "sem_lugar_fixo_ano": met["sem_lugar_fixo"],
            "sem_lugar_ano": met["sem_lugar"],
            "fora_zona_ano": met["fora_zona"],
            "pos_media": met["pos_soma"] / nd,
            "pos_max": met["pos_max"],
            "pos_nunca_usadas": len({c.pid for c in self.cels.values()} - self.pos_usadas),
            "sitios_por_sku": met["disp_soma"] / met["disp_n"] if met["disp_n"] else 0.0,
        }
        for fam in ("prateleira", "palete"):
            out[f"ocup_{fam}_media_pct"] = 100 * met[f"esp_{fam}_soma"] / nd / cap[fam] if cap[fam] else 0.0
            out[f"ocup_{fam}_pico_pct"] = 100 * met[f"esp_{fam}_max"] / cap[fam] if cap[fam] else 0.0
        for fam in ("prateleira", "palete"):
            out[f"pos_{fam}_media"] = met[f"pos_{fam}_soma"] / nd
            out[f"pos_{fam}_max"] = met[f"pos_{fam}_max"]
        out["logistica_min_dia"] = out["arrumacao_min_dia"] + out["realocacao_min_dia"]
        out["total_min_dia"] = out["picking_min_dia"] + out["logistica_min_dia"]
        return out


def correr_cenarios(m_misto: Modelo, m_sep: Modelo, cfg, pr: Pressupostos) -> dict:
    dist = Distancias(m_misto, cfg)
    out = {}
    for sep, m in ((False, m_misto), (True, m_sep)):
        for modo in MODOS:
            runs = []
            for k in range(pr.sementes):
                # o fixo separado usa a alocação separada; os outros só precisam do físico
                sim = Sim(m_misto if modo != "fixo" else m, modo, sep, (m if modo == "fixo" else m_misto).fixas,
                          pr, 1000 + k, dist)
                runs.append(sim.correr())
            med = {k: sum(r[k] for r in runs) / len(runs) for k in runs[0]}
            out[f"{modo}|{'separado' if sep else 'misto'}"] = med
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Simulação de 1 ano: posições fixas / dinâmicas / picking + reserva")
    ap.add_argument("--client", default="sia")
    ap.add_argument("--arranjo", type=Path, required=True, help="arranjo guardado na app (JSON)")
    ap.add_argument("--pasta", type=Path, default=Path("dados/cenarios/simulacao_ano"))
    ap.add_argument("--dias", type=int, default=250)
    ap.add_argument("--sementes", type=int, default=3)
    ap.add_argument("--prazo", type=int, default=5)
    ap.add_argument("--ss-dias", type=float, default=2.0)
    ap.add_argument("--cobertura-lote", type=float, default=None,
                    help="dias de consumo por lote (omissão: o lote enche o stock dimensionado)")
    a = ap.parse_args()
    from .web import ArrangeRequest
    cfg = Config.for_client(a.client)
    raw = json.loads(a.arranjo.read_text(encoding="utf-8"))
    arr = ArrangeRequest(**{k: v for k, v in raw.items() if k in ArrangeRequest.model_fields})
    t0 = time.time()
    res_m, taxas, arts = alocar(cfg, arr, a.pasta / "misto", False)
    res_s, taxas_s, _ = alocar(cfg, arr, a.pasta / "separado", True)
    m_m, m_s = extrair(res_m, taxas, cfg, arts), extrair(res_s, taxas_s, cfg, arts)
    mist = sum(1 for c in res_s.celulas if len({cx.load_type.value for cx in c.nivel.caixas} & set(CAIXAS)) > 1)
    print(f"alocações: {time.time() - t0:.0f} s | separado: {m_s.nao_alocados} unidades sem lugar, "
          f"{mist} níveis ainda mistos | excluídos (OVERSIZE): {m_m.excluidos}")
    print(f"custo ponderado da app (sem OVERSIZE): misto {m_m.custo_ponderado:.1f} min/dia, "
          f"separado {m_s.custo_ponderado:.1f} min/dia")
    pr = Pressupostos(dias=a.dias, sementes=a.sementes, prazo_dias=a.prazo, stock_seg_dias=a.ss_dias,
                      cobertura_lote_dias=a.cobertura_lote)
    out = correr_cenarios(m_m, m_s, cfg, pr)
    print(f"simulação: {time.time() - t0:.0f} s")
    a.pasta.mkdir(parents=True, exist_ok=True)
    (a.pasta / "resultados.json").write_text(json.dumps({"pressupostos": dataclasses.asdict(pr), "cenarios": out,
                                                         "app": {"misto": m_m.custo_ponderado,
                                                                 "separado": m_s.custo_ponderado,
                                                                 "separado_sem_lugar": m_s.nao_alocados}},
                                                        ensure_ascii=False, indent=1), encoding="utf-8")
    cols = ["picking_min_dia", "arrumacao_min_dia", "realocacao_min_dia", "total_min_dia", "realocacoes_dia",
            "faltas_face_pct", "ocup_prateleira_media_pct", "ocup_prateleira_pico_pct", "ocup_palete_media_pct",
            "ocup_palete_pico_pct", "pos_max", "pos_nunca_usadas", "sitios_por_sku", "sem_lugar_fixo_ano",
            "sem_lugar_ano", "ruturas_ano"]
    print("cenário".ljust(20) + "".join(c[:12].rjust(13) for c in cols))
    for k, r in out.items():
        print(k.ljust(20) + "".join(f"{r[c]:13.1f}" for c in cols))


if __name__ == "__main__":
    main()

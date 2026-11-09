"""Alocação dos componentes às racks do ARRANJO (Passo 5b).

Pega nas racks que estão no canvas num dado momento (bloco de racks gerado
e/ou filas manuais do sandbox -- nunca gera layout novo) e diz, para cada
unidade de armazenagem do stock de 10 carros, em que posição e nível fica.
A ordem vem da frequência de picking (``procura.taxas_por_sku``): quem sai mais
vezes por m² ocupado fica mais perto do ponto de carga do comboio e mais
baixo. As regras estão em ``REGRAS`` (vão também para o Excel de saída) --
quem as quiser mudar muda-as aqui e nesse texto, nunca só num dos dois.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .models import CaixaColocada, LoadType, NivelRack, RackType
from .racking import _commit_nivel, _empacotar_nivel, altura_nivel, cotas_fisicas_niveis

# Versão das REGRAS de alocação: mudar quando a lógica muda (o modo incremental
# deixa de valer -- alocação de raiz até se guardar um estado novo).
ALGORITMO_VERSAO = "2026-11-09.1"
CONTIGUIDADE_MODULOS = 2     # 1.ª etapa: a unidade seguinte de um SKU tenta até ±2 módulos na mesma fila

REGRAS = (
    "Só as racks que estão no arranjo nesse momento (bloco de racks gerado e filas manuais do sandbox): "
    "não se gera nem se muda layout nenhum.",
    "Mesmas unidades do dimensionamento (racking.preparar_itens): unidade herdada da linha, stock da "
    "autonomia mínima, caixas empilhadas como lá, altura REAL das paletes e meias-paletes -- no 3.º "
    "nível do porta-paletes (0,81 m úteis) só entra o que lá cabe de facto.",
    "Cada unidade só entra numa tipologia que a aceite (folha «Racks», «Aceita») e num nível onde caiba "
    "em altura e em planta (o mesmo packer 2D por nível do dimensionamento).",
    "Posições FIXAS: cada SKU tem as suas posições; não há stock partilhado nem aleatório.",
    "Custo de um nível = minutos de uma visita: ida e volta pelo chão (caminho mais curto na grelha do "
    "arranjo, do ponto de carga à frente da posição) ÷ velocidade do equipamento da tipologia + 30 s se "
    "o topo do nível passar de 1,6 m (tempos.custo_visita_min).",
    "1.ª etapa -- arrumação: dentro de cada zona as unidades entram pela FREQUÊNCIA (visitas por dia de "
    "cada unidade guardada, decrescente; em empate, área decrescente), cada uma no primeiro nível livre "
    "por distância onde caiba -- o mais frequente fica mais perto. Se assim alguma coisa ficar de fora, "
    "repete-se por área decrescente (como no dimensionamento, que garante que cabe o que lá caberia) e "
    "fica a que deixa menos de fora.",
    "2.ª etapa -- frequência: troca-se o conteúdo de níveis inteiros entre níveis da mesma tipologia e "
    "da mesma altura (os de mais visitas por dia vão para os mais baratos) e depois trocam-se unidades "
    "com a mesma forma (ex.: caixa XS por caixa XS, palete por palete) -- a de mais visitas por dia "
    "fica na posição mais barata onde caiba em altura; por fim trocam-se posições inteiras iguais duas a "
    "duas sempre que isso baixa Σ visitas × custo sem partir um SKU nem misturar zonas. Nenhuma troca tira "
    "nada do arranjo.",
    "Contiguidade: as unidades de um SKU ficam juntas -- na 1.ª etapa a unidade seguinte tenta primeiro o "
    "módulo onde o SKU já está, depois os vizinhos (o do lado na mesma fila ou o da frente no mesmo "
    "corredor, depois a 2 módulos), e só depois o resto do arranjo; na troca de unidades fica na mesma "
    "posição ou num vizinho se lá houver lugar até Config.alocacao_tolerancia_contiguo_min mais caro do "
    "que o lugar mais barato; na troca de posições inteiras os módulos seguidos de um SKU mudam-se em "
    "bloco.",
    "Zona de bulk (Config.alocacao_zona_bulk): o que vai no bulk (sai todos os dias, preparado de uma vez "
    "num percurso) arruma-se PRIMEIRO, nas posições mais perto do destino do picking; depois o que vai nas "
    "voltas do comboio; depois os consumíveis (reposição semanal); por fim o que não tem picking. As trocas "
    "da 2.ª etapa não misturam zonas: posições inteiras ordenam-se por zona e só depois por visitas, e as "
    "unidades só se trocam com unidades da mesma zona. Dentro de cada zona, o mais frequente fica nos "
    "níveis mais baratos.",
    "OVERSIZE em racks normais (Config.alocacao_oversize_em_pares): numa fila DUPLA de porta-paletes, "
    "o mesmo módulo da frente e de trás (costas com costas) funciona como uma emparelhada -- uma "
    "posição «frente+trás» só para OVERSIZE -- desde que as duas faces tenham corredor. As OVERSIZE "
    "arrumam-se primeiro; um nível ocupado no par fica reservado nos dois módulos reais e vice-versa.",
    "Fixações (Config.alocacao_fixacoes, sku -> endereço) entram primeiro e não se trocam. Se há um "
    "estado guardado (Config.alocacao_estado) com a MESMA lógica (ALGORITMO_VERSAO + parâmetros das "
    "regras), cada SKU fica onde estava, se ainda couber, e também não se troca -- incremental "
    "automático, para não se mudar o armazém todo a cada alteração da BOM. Com a lógica mudada, de raiz.",
    "Nada fica de fora em silêncio: o que não cabe vai para «Não alocados», com o motivo.",
)


# ----------------------------------------------------------------- modelos
@dataclass
class PosicaoFisica:
    """Uma posição de chão do arranjo (já com a rotação e a normalização do
    canvas aplicadas -- as mesmas coordenadas do 3D)."""
    id: str             # estável enquanto a fila não mudar: "<bloco>|<lado>|<índice>"
    fila: str           # nome legível (nome do bloco no canvas + frente/trás)
    modulo: int         # 1-based, ao longo da fila
    rack: RackType
    n_niveis: int
    x_m: float
    y_m: float
    larg_m: float
    prof_m: float

    @property
    def centro(self) -> tuple[float, float]:
        return self.x_m + self.larg_m / 2, self.y_m + self.prof_m / 2

    def endereco(self, nivel_idx: int | None = None) -> str:
        e = f"{self.fila} · M{self.modulo:02d}"
        return e if nivel_idx is None else f"{e} · N{nivel_idx + 1}"


@dataclass
class _Celula:
    pos: PosicaoFisica
    nivel: NivelRack
    base_m: float
    custo: float
    area_livre: float
    falhas: set = field(default_factory=set)   # formas que já não couberam (o nível só enche)

    @property
    def chave(self) -> tuple[str, int]:
        return self.pos.id, self.nivel.nivel_idx


@dataclass
class Colocacao:
    sku: str
    descricao: str
    unidade: str
    n_unidades: int          # unidades físicas neste stack
    pecas: int
    posicao: PosicaoFisica
    nivel_idx: int
    custo: float
    visitas_dia: float       # parte das visitas do SKU que cabe a este stack

    @property
    def endereco(self) -> str:
        return self.posicao.endereco(self.nivel_idx)


@dataclass
class NaoAlocado:
    sku: str
    descricao: str
    unidade: str
    n_unidades: int
    motivo: str


@dataclass
class ResultadoAlocacao:
    posicoes: list
    celulas: list               # [_Celula], com os níveis já preenchidos
    colocacoes: list
    nao_alocados: list
    classes: dict               # sku -> "A"/"B"/"C"/"—"
    ponto_carga: tuple
    mudancas: list = field(default_factory=list)    # SKUs que mudaram de sítio (incremental)
    avisos: list = field(default_factory=list)
    tempos: object = None       # tempos.ResultadoTempos (preenchido por run)
    estado: dict = field(default_factory=dict)       # modo (incremental / de raiz), alterações à BOM, pode guardar
    estado_ctx: dict = field(default_factory=dict)   # BOM e assinatura da lógica desta corrida (para guardar)
    planta: dict = field(default_factory=dict)   # zonas/obstáculos/Cubo do render (para a UI desenhar)
    pares: dict = field(default_factory=dict)    # pid «frente+trás» -> (pid frente, pid trás)

    @property
    def custo_ponderado(self) -> float:
        """Σ visitas/dia × custo do nível: o que a 2.ª etapa minimiza (min de deslocação por dia)."""
        return sum(c.visitas_dia * c.custo for c in self.colocacoes)

    def visitas_por_posicao(self) -> dict:
        out = defaultdict(float)
        for c in self.colocacoes:
            out[c.posicao.id] += c.visitas_dia
        return dict(out)


# ----------------------------------------------------------------- arranjo
def posicoes_do_render(itens: list[dict], rack_by_name: dict) -> list[PosicaoFisica]:
    """Posições físicas a partir da resposta do /api/render-layout («layout» +
    «racks_manuais», anotadas com bloco_id/bloco_nome). Agrupa por fila (bloco
    + lado) e numera os módulos ao longo do eixo comprido da fila."""
    grupos = defaultdict(list)
    for it in itens:
        rack = rack_by_name.get(it.get("rack_nome"))
        if rack is None:
            continue
        bid = it.get("bloco_id") or "racking"
        lado = it.get("lado") or "unico"
        fila = it.get("fila_idx", 0) if bid == "racking" else 0
        grupos[(bid, fila, lado)].append((it, rack))
    out = []
    for (bid, fila_idx, lado), lst in sorted(grupos.items(), key=lambda kv: str(kv[0])):
        nome = lst[0][0].get("bloco_nome") or bid
        if bid == "racking":
            nome = f"{nome} F{fila_idx + 1}"
        if lado in ("frente", "tras"):
            nome += " frente" if lado == "frente" else " trás"
        xs, ys = [it["x"] for it, _ in lst], [it["y"] for it, _ in lst]
        eixo = "x" if max(xs) - min(xs) >= max(ys) - min(ys) else "y"
        lst.sort(key=lambda t: (t[0][eixo], t[0]["x"], t[0]["y"]))
        for m, (it, rack) in enumerate(lst, 1):
            pid = (f"racking|{it['rack_nome']}|{it['indice']}" if bid == "racking"
                   else f"{bid}|{lado}|{it['indice']}")
            out.append(PosicaoFisica(pid, nome, m, rack, int(it.get("niveis") or rack.niveis),
                                     float(it["x"]), float(it["y"]), float(it["larg_m"]), float(it["prof_m"])))
    return out


def ponto_de_carga(zonas: list[dict], chave: str = "comboio") -> tuple[float, float] | None:
    """Centro da zona com o nome `chave` (igual, sem maiúsculas nem espaços nas
    pontas) ou, se não houver, da 1.ª que o contenha -- «espera» sozinho
    apanhava «Espera para Armazenagem» antes da «ZONA DE ESPERA». O canvas roda
    as zonas em torno do centro, por isso o centro não depende da rotação."""
    def centro(z):
        return (float(z["x"]) + float(z["larg_m"]) / 2, float(z["y"]) + float(z["prof_m"]) / 2)
    alvo = chave.strip().lower()
    for z in zonas:
        if str(z.get("nome", "")).strip().lower() == alvo:
            return centro(z)
    for z in zonas:
        if alvo in str(z.get("nome", "")).lower():
            return centro(z)
    return None


# ----------------------------------------------------------------- alocação
def classes_abc(visitas: dict, corte_a: float = 0.80, corte_b: float = 0.95) -> dict:
    """A = SKUs que fazem os primeiros 80 % das visitas, B até 95 %, C o resto
    com saídas, «—» sem saídas no regime simulado."""
    total = sum(v for v in visitas.values() if v > 0)
    out, acum = {}, 0.0
    for s, v in sorted(visitas.items(), key=lambda kv: (-kv[1], kv[0])):
        if v <= 0 or total <= 0:
            out[s] = "—"
            continue
        out[s] = "A" if acum < corte_a * total - 1e-12 else "B" if acum < corte_b * total - 1e-12 else "C"
        acum += v
    return out


def _resolver_fixacao(valor: str, posicoes: list[PosicaoFisica]) -> list[tuple[str, int | None]]:
    """«<fila> · M03» ou «<fila> · M03 · N2» (o endereço do Excel) ou o id da
    posição -> [(id, nível ou None)]."""
    valor = valor.strip()
    for p in posicoes:
        if valor == p.id or valor == p.endereco():
            return [(p.id, None)]
        for n in range(p.n_niveis):
            if valor == p.endereco(n):
                return [(p.id, n)]
    return []


def _atribuir_por_altura(prioridade: list, alt_conteudo: list, slots_por_custo: list, alt_slot: list,
                         sku_c: list | None = None, pos_s: list | None = None,
                         custo_s: list | None = None, tolerancia: float = 0.0,
                         vizinhos: dict | None = None) -> dict:
    """Atribuição gulosa conteúdo -> slot: cada conteúdo (por ordem de
    prioridade) fica no slot mais barato onde caiba em altura, SEM tornar
    impossível arrumar os que faltam. Os slots só têm poucas alturas
    distintas (ex.: 1,61 / 0,81 m), por isso a viabilidade é uma condição de
    contagem por classe de altura (Hall, conjuntos encaixados): para cada
    classe j, os conteúdos que precisam de uma classe ≥ j não podem ser mais
    do que os slots livres de classe ≥ j.

    Contiguidade (com `sku_c`/`pos_s`/`custo_s`): se o SKU já tem unidades
    numa posição e lá houver um slot livre até `tolerancia` mais caro do que
    o mais barato, fica lá -- sem isto, slots quase com o mesmo custo em filas
    diferentes espalhavam as 10 paletes de um SKU por 3 posições de 2 filas.
    Com `vizinhos` (``vizinhanca``), se não houver lugar na mesma posição,
    tenta um módulo vizinho (ao lado na mesma fila ou em frente no mesmo
    corredor, depois a 2), com a mesma tolerância (2026-11-05: um SKU de
    caixas aparecia em módulos de filas diferentes). Devolve {slot: conteúdo}."""
    classes = sorted(set(alt_slot))

    def classe(h):
        for j, a in enumerate(classes):
            if h <= a + 1e-9:
                return j
        raise ValueError("conteúdo mais alto do que qualquer slot")   # a 1.ª etapa nunca o deixa acontecer

    cls_c = [classe(h) for h in alt_conteudo]
    cls_s = [classes.index(a) for a in alt_slot]
    precisa, tem = [0] * len(classes), [0] * len(classes)
    for j in cls_c:
        precisa[j] += 1
    for j in cls_s:
        tem[j] += 1

    def viavel():
        p = t = 0
        for j in range(len(classes) - 1, -1, -1):
            p, t = p + precisa[j], t + tem[j]
            if p > t:
                return False
        return True

    livres = list(slots_por_custo)
    out = {}
    usadas = defaultdict(set)          # sku -> posições onde já ficou

    def afastamento(sku, si):
        """0 = mesma posição; 1, 2 = módulo vizinho; None = longe."""
        if pos_s[si] in usadas[sku]:
            return 0
        if not vizinhos:
            return None
        v = vizinhos.get(pos_s[si], {})
        return min((v[u] for u in usadas[sku] if u in v), default=None)

    for ci in prioridade:
        precisa[cls_c[ci]] -= 1
        sku = sku_c[ci] if sku_c else None
        escolhido, custo0, melhor = None, None, None
        for k, si in enumerate(livres):
            if cls_s[si] < cls_c[ci]:
                continue
            if escolhido is not None and custo_s[si] > custo0 + tolerancia + 1e-9:
                break
            tem[cls_s[si]] -= 1
            ok = viavel()
            tem[cls_s[si]] += 1
            if not ok:
                continue
            if escolhido is None:
                escolhido, custo0 = k, (custo_s[si] if custo_s else 0.0)
                if sku is None or not usadas[sku]:
                    break
            g = afastamento(sku, si)
            if g is not None and (melhor is None or g < melhor[0]):
                melhor = (g, k)
                if g == 0:
                    break
        if escolhido is None:
            continue
        if melhor is not None:
            escolhido = melhor[1]
        si = livres.pop(escolhido)
        tem[cls_s[si]] -= 1
        out[si] = ci
        if sku is not None:
            usadas[sku].add(pos_s[si])
    return out


def posicoes_em_par(posicoes: list, obstaculos: list, ponto: tuple, cfg) -> tuple[list, dict, dict]:
    """OVERSIZE em racks normais (decisão do utilizador, 2026-11-09): numa fila
    DUPLA de porta-paletes (Config.oversize_par_racks), cada par de módulos
    costas com costas (frente + trás, sobrepostos ao longo da fila) funciona
    como uma emparelhada -- desde que as DUAS faces tenham corredor (acesso
    pelo chão a cada uma, com a passagem mínima). Devolve as posições virtuais
    «frente+trás» (só aceitam OVERSIZE), {pid par: (pid frente, pid trás)} e
    as tipologias sintéticas usadas ({nome: RackType})."""
    if not getattr(cfg, "alocacao_oversize_em_pares", True):
        return [], {}, {}
    from . import racking, tempos

    def base(fila: str):
        for lado in (" frente", " trás"):
            if fila.endswith(lado):
                return fila[: -len(lado)]
        return None

    eleg = [p for p in posicoes if not p.rack.profundidade_inclui_par
            and racking.aceita_oversize(p.rack, cfg.oversize_par_racks) and base(p.fila)]
    frentes = [p for p in eleg if p.fila.endswith(" frente")]
    tras = [p for p in eleg if p.fila.endswith(" trás")]
    if not frentes or not tras:
        return [], {}, {}
    g = tempos.construir_grelha(posicoes, obstaculos, [ponto], cfg.picking_grelha_m,
                                folga=cfg.picking_passagem_min_m / 2)
    d = tempos.distancias(g, ponto)
    acesso = {}

    def tem_corredor(p):
        if p.id not in acesso:
            acesso[p.id] = tempos._celula_acesso(g, d, p) is not None
        return acesso[p.id]

    def costas(a, b) -> bool:
        if (a.larg_m >= a.prof_m) != (b.larg_m >= b.prof_m):
            return False
        if a.larg_m >= a.prof_m:
            sobre = min(a.x_m + a.larg_m, b.x_m + b.larg_m) - max(a.x_m, b.x_m)
            folga = max(b.y_m - (a.y_m + a.prof_m), a.y_m - (b.y_m + b.prof_m))
            comp = min(a.larg_m, b.larg_m)
        else:
            sobre = min(a.y_m + a.prof_m, b.y_m + b.prof_m) - max(a.y_m, b.y_m)
            folga = max(b.x_m - (a.x_m + a.larg_m), a.x_m - (b.x_m + b.larg_m))
            comp = min(a.prof_m, b.prof_m)
        return sobre >= 0.9 * comp and abs(folga) <= 0.05

    racks, out, pares, usadas = {}, [], {}, set()
    for f in sorted(frentes, key=lambda p: (p.fila, p.modulo)):
        t = next((t for t in tras if t.id not in usadas and base(t.fila) == base(f.fila)
                  and t.rack.nome == f.rack.nome and costas(f, t)), None)
        if t is None or not (tem_corredor(f) and tem_corredor(t)):
            continue
        usadas.add(t.id)
        rp = racks.get(f.rack.nome)
        if rp is None:
            rp = racks[f.rack.nome] = dataclasses.replace(
                f.rack, nome=f"{f.rack.nome} (par frente+trás)", prof_m=f.rack.prof_m * 2,
                aceita=[LoadType.OVERSIZE], footprint_m2=0.0, profundidade_inclui_par=True)
        x0, y0 = min(f.x_m, t.x_m), min(f.y_m, t.y_m)
        x1, y1 = max(f.x_m + f.larg_m, t.x_m + t.larg_m), max(f.y_m + f.prof_m, t.y_m + t.prof_m)
        pid = f"par|{f.id}|{t.id}"
        out.append(PosicaoFisica(pid, f"{base(f.fila)} frente+trás", f.modulo, rp, min(f.n_niveis, t.n_niveis),
                                 x0, y0, x1 - x0, y1 - y0))
        pares[pid] = (f.id, t.id)
    return out, pares, racks


def com_pares(posicoes: list, dist: dict, candidatos: dict, obstaculos: list, ponto: tuple, cfg):
    """Acrescenta as posições «frente+trás» (``posicoes_em_par``) às posições,
    às distâncias (a da face mais perto) e aos candidatos das OVERSIZE (depois
    das emparelhadas dedicadas, se houver). -> (posicoes, dist, candidatos, pares)."""
    extra, pares, racks = posicoes_em_par(posicoes, obstaculos, ponto, cfg)
    if not extra:
        return posicoes, dist, candidatos, {}
    dist = dict(dist)
    for pid, (f, t) in pares.items():
        dist[pid] = min(dist.get(f, 0.0), dist.get(t, 0.0))
    candidatos = {**candidatos, LoadType.OVERSIZE: [*candidatos.get(LoadType.OVERSIZE, []), *racks.values()]}
    return posicoes + extra, dist, candidatos, pares


ZONA_BULK, ZONA_VOLTAS, ZONA_SEMANAL, ZONA_SEM_PICKING = 0, 1, 2, 3


def zona_do_sku(taxa) -> int:
    """Zona de arrumação pelo ciclo de reposição (procura.Taxa.ciclos): bulk ->
    voltas do comboio -> reposição semanal -> sem picking."""
    if taxa is None:
        return ZONA_SEM_PICKING
    ciclos = set(taxa.ciclos)
    if "Bulk" in ciclos:
        return ZONA_BULK
    if ciclos & {"Caixas", "Paletes"}:
        return ZONA_VOLTAS
    return ZONA_SEMANAL if ciclos else ZONA_SEM_PICKING


def _frente_a_frente(a: PosicaoFisica, b: PosicaoFisica) -> bool:
    """Dois módulos de filas diferentes, virados um para o outro do mesmo
    corredor: sobrepõem-se ao longo da fila (≥ metade do menor) e entre as
    frentes há um corredor (0,5 a 4 m) -- costas com costas (0 m) não conta."""
    if (a.larg_m >= a.prof_m) != (b.larg_m >= b.prof_m):
        return False
    if a.larg_m >= a.prof_m:      # filas ao longo de x
        sobre = min(a.x_m + a.larg_m, b.x_m + b.larg_m) - max(a.x_m, b.x_m)
        folga = max(b.y_m - (a.y_m + a.prof_m), a.y_m - (b.y_m + b.prof_m))
        comp = min(a.larg_m, b.larg_m)
    else:
        sobre = min(a.y_m + a.prof_m, b.y_m + b.prof_m) - max(a.y_m, b.y_m)
        folga = max(b.x_m - (a.x_m + a.larg_m), a.x_m - (b.x_m + b.larg_m))
        comp = min(a.prof_m, b.prof_m)
    return sobre >= 0.5 * comp - 1e-9 and 0.5 - 1e-9 <= folga <= 4.0 + 1e-9


def vizinhanca(posicoes: list[PosicaoFisica]) -> dict:
    """pid -> {pid vizinho: afastamento}. 1 = módulo ao lado na mesma fila ou o
    módulo em frente do mesmo corredor (a mesma paragem do operador); 2 = dois
    módulos ao lado, ou o do lado do que está em frente. Só entre posições da
    mesma tipologia (uma prateleira e um porta-paletes nunca guardam a mesma
    unidade) e fisicamente perto (o nº de módulo de uma fila de um bloco pode
    saltar de uma ponta à outra)."""
    viz = defaultdict(dict)

    def liga(a, b, n):
        if a.id == b.id or a.rack.nome != b.rack.nome:
            return
        if n < viz[a.id].get(b.id, 99):
            viz[a.id][b.id] = viz[b.id][a.id] = n

    por_fila = defaultdict(dict)
    for p in posicoes:
        por_fila[p.fila][p.modulo] = p
    for mods in por_fila.values():
        for m, p in mods.items():
            for g in range(1, CONTIGUIDADE_MODULOS + 1):
                q = mods.get(m + g)
                if q is None:
                    continue
                (ax, ay), (bx, by) = p.centro, q.centro
                if abs(ax - bx) + abs(ay - by) <= (g + 0.5) * max(p.larg_m, p.prof_m, q.larg_m, q.prof_m):
                    liga(p, q, g)
    pmap = {p.id: p for p in posicoes}
    lado = {pid: {q for q, n in d.items() if n == 1} for pid, d in viz.items()}
    for i, a in enumerate(posicoes):
        for b in posicoes[i + 1:]:
            if a.fila != b.fila and a.rack.nome == b.rack.nome and _frente_a_frente(a, b):
                liga(a, b, 1)
                for q in lado.get(b.id, ()):
                    liga(a, pmap[q], 2)
                for q in lado.get(a.id, ()):
                    liga(b, pmap[q], 2)
    return viz


def _ligado(ps: set, viz: dict) -> bool:
    """As posições de um SKU formam um só bloco (vizinhança: ao lado ou em frente)."""
    if len(ps) < 2:
        return True
    ini = next(iter(ps))
    vist, pilha = {ini}, [ini]
    while pilha:
        x = pilha.pop()
        for q in viz.get(x, {}):
            if q in ps and q not in vist:
                vist.add(q)
                pilha.append(q)
    return vist == ps


def _trocas_aos_pares(por_posicao: dict, parte, viz: dict, zona, bloqueada) -> int:
    """2.ª etapa (c): troca o conteúdo de DUAS posições inteiras iguais
    (mesma tipologia e nº de níveis) sempre que isso baixa Σ visitas × custo,
    sem partir nenhum SKU (``_ligado``) e sem misturar zonas (só entre posições
    com a mesma zona de topo, ou com uma vazia). Repete até não haver ganho.
    Sem isto, as posições ficavam com o que a 1.ª etapa (por área) lá pôs: a
    troca em bloco (a1) quase nunca se aplica porque a contiguidade liga as
    posições umas às outras -- e uma posição mais longe ficava com mais visitas
    do que a de perto (2026-11-05). -> nº de trocas."""
    grupos = defaultdict(list)
    for pid, cels in por_posicao.items():
        if not any(bloqueada(c) for c in cels):
            p = cels[0].pos
            grupos[(p.rack.nome, p.n_niveis)].append(pid)
    niveis = {pid: sorted(cels, key=lambda c: c.nivel.nivel_idx) for pid, cels in por_posicao.items()}
    sku_pos = defaultdict(set)
    for pid, cels in por_posicao.items():
        for c in cels:
            for cx in c.nivel.caixas:
                sku_pos[cx.sku].add(pid)

    def vis(pid):
        return [sum(parte(cx.sku, cx.num_boxes_in_stack) for cx in c.nivel.caixas) for c in niveis[pid]]

    def skus(pid):
        return {cx.sku for c in niveis[pid] for cx in c.nivel.caixas}

    def zona_topo(pid):
        return min((zona(s) for s in skus(pid)), default=None) if zona else 0

    def mesma_zona(p, q):
        zp, zq = zona_topo(p), zona_topo(q)
        return zp is None or zq is None or zp == zq

    def blocos(ps) -> list:
        """Componentes ligadas (pela vizinhança) das posições de um SKU."""
        ps, out, vist = set(ps), [], set()
        for x in sorted(ps):
            if x in vist:
                continue
            comp, pilha = {x}, [x]
            vist.add(x)
            while pilha:
                y = pilha.pop()
                for z in viz.get(y, {}):
                    if z in ps and z not in vist:
                        vist.add(z)
                        comp.add(z)
                        pilha.append(z)
            out.append(comp)
        return out

    def depois(s, p, q):
        if s in skus(p) and s in skus(q):
            return set(sku_pos[s])
        return {q if x == p else p if x == q else x for x in sku_pos[s]}

    def delta(p, q):
        vp, vq = vis(p), vis(q)
        cp, cq = [c.custo for c in niveis[p]], [c.custo for c in niveis[q]]
        return sum(a * y + b * x - a * x - b * y for a, b, x, y in zip(vp, vq, cp, cq))

    def nao_parte(p, q, exceto=None):
        """Nenhum SKU de p ou q fica em mais blocos do que estava."""
        return all(len(blocos(depois(s, p, q))) <= len(blocos(sku_pos[s]))
                   for s in (skus(p) | skus(q)) - {exceto})

    def trocar(p, q):
        sp, sq = skus(p), skus(q)
        for a, b in zip(niveis[p], niveis[q]):
            (a.nivel.caixas, b.nivel.caixas) = (b.nivel.caixas, a.nivel.caixas)
            (a.area_livre, b.area_livre) = (b.area_livre, a.area_livre)
            (a.falhas, b.falhas) = (b.falhas, a.falhas)
        for s in sp ^ sq:
            sku_pos[s] = {q if x == p else p if x == q else x for x in sku_pos[s]}

    grupo_de = {pid: k for k, pids in grupos.items() for pid in pids}
    trocas = 0
    # reparação: um SKU que a 1.ª etapa partiu (os vizinhos já estavam cheios
    # quando chegou a vez da unidade seguinte) junta-se trocando a posição solta
    # com uma igual ao lado do resto dele -- mesmo que custe um pouco mais
    for s in sorted(sku_pos):
        for _ in range(len(sku_pos[s])):
            bs = blocos(sku_pos[s])
            if len(bs) <= 1:
                break
            ancora = max(bs, key=lambda b: (len(b), sorted(b)))
            melhor = None
            for p in sorted(sku_pos[s] - ancora):
                for q in grupos.get(grupo_de.get(p), []):
                    if q in sku_pos[s] or not any(a in viz.get(q, {}) for a in ancora) or not mesma_zona(p, q):
                        continue
                    if len(blocos(depois(s, p, q))) >= len(bs) or not nao_parte(p, q, s):
                        continue
                    d = delta(p, q)
                    if melhor is None or d < melhor[0]:
                        melhor = (d, p, q)
            if melhor is None:
                break
            trocar(melhor[1], melhor[2])
            trocas += 1
    # melhoria: só trocas que baixam Σ visitas × custo e não partem ninguém
    for _ in range(50):
        melhorou = False
        for pids in grupos.values():
            for i, p in enumerate(pids):
                for q in pids[i + 1:]:
                    if not any(vis(p)) and not any(vis(q)):
                        continue
                    if delta(p, q) > -1e-6 or not mesma_zona(p, q) or not nao_parte(p, q):
                        continue
                    trocar(p, q)
                    trocas += 1
                    melhorou = True
        if not melhorou:
            break
    return trocas


def _trocar_posicoes(grupo: list, parte, distancia, por_posicao: dict, zona=None) -> None:
    """2.ª etapa (a1): o conteúdo de posições INTEIRAS entre posições iguais --
    o de mais visitas por posição para a mais perto. Um SKU que ocupa vários
    módulos seguidos da mesma fila move-se em BLOCO para outros tantos
    módulos seguidos de uma fila (sem isto, trocar posição a posição levava
    os módulos de um SKU para filas diferentes -- 2026-11-05). Um conjunto de
    posições ligado por SKUs que não forma módulos seguidos de uma fila, ou
    que tem o SKU também fora do grupo, fica onde está."""
    ids = {cels[0].pos.id: cels for cels in grupo}
    sku_pos = defaultdict(set)
    for pid, cels in por_posicao.items():
        for c in cels:
            for cx in c.nivel.caixas:
                sku_pos[cx.sku].add(pid)
    # componentes ligadas por SKUs partilhados (union-find)
    pai = {pid: pid for pid in ids}

    def raiz(x):
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x
    fixos = set()
    for s, ps in sku_pos.items():
        if len(ps) < 2:
            continue
        if not ps <= ids.keys():
            fixos |= ps & ids.keys()
            continue
        ps = sorted(ps)
        for q in ps[1:]:
            pai[raiz(q)] = raiz(ps[0])
    comp = defaultdict(list)
    for pid in ids:
        comp[raiz(pid)].append(pid)
    blocos = []
    for membros in comp.values():
        if any(m in fixos for m in membros):
            fixos |= set(membros)
            continue
        pos = sorted((ids[m][0].pos for m in membros), key=lambda p: p.modulo)
        seguidos = len({p.fila for p in pos}) == 1 and pos[-1].modulo - pos[0].modulo == len(pos) - 1
        if not seguidos:
            fixos |= set(membros)
            continue
        visitas = sum(parte(cx.sku, cx.num_boxes_in_stack) for p in pos for c in ids[p.id] for cx in c.nivel.caixas)
        z = min((zona(cx.sku) for p in pos for c in ids[p.id] for cx in c.nivel.caixas),
                default=ZONA_SEM_PICKING) if zona else 0
        blocos.append(([p.id for p in pos], visitas / len(pos), z))
    livres = [cels[0].pos for cels in grupo if cels[0].pos.id not in fixos]
    por_fila = defaultdict(dict)
    for p in livres:
        por_fila[p.fila][p.modulo] = p

    def corridas(k, ocupadas):
        out = []
        for f, mods in por_fila.items():
            for m0 in mods:
                run = [mods.get(m0 + i) for i in range(k)]
                if all(r is not None and r.id not in ocupadas for r in run):
                    out.append((sum(distancia(r) for r in run) / k, run[0].id, run))
        return sorted(out, key=lambda t: (t[0], t[1]))

    def cabem(restantes, ocupadas) -> bool:
        """Os blocos de vários módulos que faltam ainda têm módulos seguidos livres?"""
        occ = set(ocupadas)
        for membros, *_ in sorted(restantes, key=lambda b: (-len(b[0]), b[0][0])):
            cs = corridas(len(membros), occ)
            if not cs:
                return False
            occ |= {r.id for r in cs[0][2]}
        return True

    # por zona (bulk primeiro) e visitas por posição (as de mais para as mais
    # perto), mas um bloco só fica num sítio se os blocos de vários módulos que
    # faltam ainda couberem
    ordem = sorted(blocos, key=lambda b: (b[2], -b[1], b[0][0]))
    if not cabem([b for b in ordem if len(b[0]) > 1], set()):
        return                       # sem lugar para os blocos: o grupo fica como estava
    destino, ocupadas = {}, set()
    for i, (membros, *_) in enumerate(ordem):
        restantes = [b for b in ordem[i + 1:] if len(b[0]) > 1]
        escolhido = None
        for _, _, run in corridas(len(membros), ocupadas):
            occ = ocupadas | {r.id for r in run}
            if not restantes or cabem(restantes, occ):
                escolhido = run
                break
        if escolhido is None:
            return                   # não devia acontecer (cabem() verificou); o grupo fica como estava
        for m, r in zip(membros, escolhido):
            destino[m] = r.id
            ocupadas.add(r.id)
    conteudo = {m: [(c.nivel.caixas, c.area_livre, c.falhas) for c in ids[m]] for m in destino}
    for m, d in destino.items():
        for c, (cxs, livre, falhas) in zip(ids[d], conteudo[m]):
            c.nivel.caixas, c.area_livre, c.falhas = cxs, livre, falhas


def alocar(posicoes: list[PosicaoFisica], itens: list, candidatos_por_tipo: dict, taxas: dict,
           ponto: tuple[float, float], cfg, fixacoes: dict | None = None,
           anterior: dict | None = None, otimizar: bool = True,
           dist: dict | None = None, pares: dict | None = None) -> ResultadoAlocacao:
    """Alocação (ver ``_alocar``): 1.ª etapa por frequência; se deixar alguma
    unidade de fora, repete por área (FFD, como o dimensionamento) e fica a
    que deixa menos unidades de fora (2026-11-05: com a ordem por área uma
    posição mais longe ficava com mais visitas do que a de perto)."""
    args = (posicoes, itens, candidatos_por_tipo, taxas, ponto, cfg, fixacoes, anterior, otimizar, dist, pares)
    r = _alocar(*args, por_frequencia=True)
    if r.nao_alocados:
        r2 = _alocar(*args, por_frequencia=False)
        fora = lambda x: sum(n.n_unidades for n in x.nao_alocados)
        if fora(r2) < fora(r):
            r2.avisos.append(f"A arrumação por frequência deixava {fora(r)} unidade(s) de fora; usada a "
                             f"arrumação por área (deixa {fora(r2)}).")
            return r2
    return r


def _alocar(posicoes: list[PosicaoFisica], itens: list, candidatos_por_tipo: dict, taxas: dict,
            ponto: tuple[float, float], cfg, fixacoes: dict | None = None,
            anterior: dict | None = None, otimizar: bool = True,
            dist: dict | None = None, pares: dict | None = None,
            por_frequencia: bool = True) -> ResultadoAlocacao:
    """`itens`: racking.preparar_itens (stacks). `taxas`: sku -> procura.Taxa.
    `fixacoes`: sku -> endereço/id (ver _resolver_fixacao). `anterior`: sku ->
    [(id da posição, nível)] da última alocação guardada (modo incremental).
    Ver REGRAS: 1.ª etapa viável (área decrescente, níveis por custo), 2.ª etapa
    trocas que só melhoram a frequência e nunca tiram nada do arranjo
    (`otimizar=False` salta a 2.ª etapa -- só para medir o que ela ganha).
    `dist`: posição -> metros do ponto de carga pelo chão (tempos.distancias_acesso);
    sem ele, a distância em planta |dx| + |dy| (só para testes)."""
    from .tempos import custo_visita_min
    fixacoes, anterior = fixacoes or {}, anterior or {}
    avisos = []
    compat = {lt: {r.nome for r in rs} for lt, rs in candidatos_por_tipo.items()}
    nomes_no_arranjo = {p.rack.nome for p in posicoes}

    def distancia(pos) -> float:
        if dist is not None and pos.id in dist:
            return dist[pos.id]
        cx, cy = pos.centro
        return abs(cx - ponto[0]) + abs(cy - ponto[1])

    celulas = []
    for pos in posicoes:
        bases, _ = cotas_fisicas_niveis(pos.rack, pos.n_niveis, cfg)
        d = distancia(pos)
        for n in range(pos.n_niveis):
            celulas.append(_Celula(pos, NivelRack(nivel_idx=n, eh_picking=False), bases[n],
                                   custo_visita_min(d, pos.rack, n, cfg),
                                   pos.rack.larg_m * pos.rack.prof_m))
    celulas.sort(key=lambda c: (c.custo, c.pos.id, c.nivel.nivel_idx))
    por_chave = {c.chave: c for c in celulas}
    viz = vizinhanca(posicoes)
    # OVERSIZE em par (frente+trás): o mesmo nível não pode ter as duas coisas
    pares = pares or {}
    ligadas = defaultdict(list)
    for par, (pf, pt) in pares.items():
        for (pid, n) in [k for k in por_chave if k[0] == par]:
            for outra in ((pf, n), (pt, n)):
                if outra in por_chave:
                    ligadas[(par, n)].append(outra)
                    ligadas[outra].append((par, n))
    reservadas = set()

    def reservar(c):
        for k in ligadas.get(c.chave, ()):
            por_chave[k].area_livre = -1.0      # nada mais lá cabe
            reservadas.add(k)
    por_posicao = defaultdict(list)
    for c in celulas:
        por_posicao[c.pos.id].append(c)

    por_sku = defaultdict(list)
    for it in itens:
        por_sku[it.sku].append(it)
    unidades = {s: sum(i.num_boxes_in_stack for i in lst) for s, lst in por_sku.items()}
    vis = {s: (taxas[s].visitas_dia if s in taxas else 0.0) for s in por_sku}
    classes = classes_abc(vis)
    zonas = bool(getattr(cfg, "alocacao_zona_bulk", False))

    def zona_de(sku: str) -> int:
        return zona_do_sku(taxas.get(sku))

    def parte(sku: str, n: int) -> float:
        return vis.get(sku, 0.0) * n / unidades[sku] if unidades.get(sku) else 0.0

    pref = {}
    for s, v in fixacoes.items():
        alvo = _resolver_fixacao(str(v), posicoes)
        if not alvo:
            avisos.append(f"Fixação de {s} ignorada: «{v}» não é nenhuma posição do arranjo.")
        elif s not in por_sku:
            avisos.append(f"Fixação de {s} ignorada: o SKU não tem stock a arrumar.")
        else:
            pref[s] = [c for pid, n in alvo for c in por_posicao[pid] if n is None or c.nivel.nivel_idx == n]

    # 1.ª etapa = o packer do dimensionamento, só com as posições por distância:
    # tipologias por footprint crescente (uma palete tenta o porta-paletes antes
    # do par emparelhado, o único sítio das OVERSIZE), e dentro de cada uma
    # posição a posição, níveis de baixo para cima. Como as posições vazias de
    # uma tipologia são todas iguais, isto dá a MESMA arrumação do
    # dimensionamento (só muda que posição física leva o quê): se o arranjo
    # tiver as posições do dimensionamento, cabe tudo. Por custo puro (níveis
    # de posições diferentes intercalados) ficavam de fora 8 a 30 stacks
    # OVERSIZE no bloco dimensionado.
    por_rack = defaultdict(list)
    for c in sorted(celulas, key=lambda c: (distancia(c.pos), c.pos.id, c.nivel.nivel_idx)):
        por_rack[c.pos.rack.nome].append(c)
    ordem_celulas = {lt: [c for r in rs for c in por_rack.get(r.nome, [])]
                     for lt, rs in candidatos_por_tipo.items()}
    # contiguidade na 1.ª etapa: as células de cada tipo de carga por posição
    por_modulo = {lt: defaultdict(list) for lt in ordem_celulas}
    for lt, cs in ordem_celulas.items():
        for c in cs:
            por_modulo[lt][c.pos.id].append(c)
    onde = defaultdict(set)          # sku -> {posição} onde já tem unidades

    def junto(it) -> list:
        """Onde tentar primeiro uma unidade de um SKU que já tem lugar: os
        módulos onde ele já está, depois os vizinhos (``vizinhanca``: ao lado
        na mesma fila ou em frente no mesmo corredor, depois a 2), cada um com
        os níveis por custo. Sem isto a unidade seguinte ia para o buraco livre
        mais perto, muitas vezes noutra fila (2026-11-05)."""
        ja = onde.get(it.sku)
        if not ja:
            return []
        idx = por_modulo.get(it.load_type, {})
        nivel = {pid: 0 for pid in ja}
        for pid in ja:
            for q, n in viz.get(pid, {}).items():
                nivel[q] = min(nivel.get(q, 99), n)
        out = []
        for pid in sorted(nivel, key=lambda q: (nivel[q], distancia(idx[q][0].pos) if idx.get(q) else 0.0, q)):
            out += idx.get(pid, [])
        return out

    def colocar(it, tentar=None):
        if tentar is None:
            tentar = junto(it) + ordem_celulas.get(it.load_type, [])
        nomes = compat.get(it.load_type, set())
        forma = (round(it.comp_m, 3), round(it.larg_m, 3), round(it.alt_m, 3))
        a_it = it.comp_m * it.larg_m
        for c in tentar:
            if c.pos.rack.nome not in nomes or forma in c.falhas or c.area_livre < a_it - 1e-9:
                continue
            sol = _empacotar_nivel(c.nivel, c.pos.rack, it)
            if sol is None:
                c.falhas.add(forma)
                continue
            _commit_nivel(c.nivel, it, sol)
            c.area_livre -= a_it
            onde[it.sku].add(c.pos.id)
            reservar(c)
            return c
        return None

    def motivo(it) -> str:
        nomes = compat.get(it.load_type, set())
        if not nomes:
            return "nenhuma tipologia aceita esta unidade"
        if not nomes & nomes_no_arranjo:
            return f"o arranjo não tem nenhuma rack que a aceite ({', '.join(sorted(nomes))})"
        return "sem espaço livre (em planta ou em altura) nas racks compatíveis do arranjo"

    def ffd(i):   # mesma ordem do dimensionamento: área, depois altura, decrescentes
        return (i.comp_m * i.larg_m, i.alt_m)

    def repor(it, reg) -> bool:
        """Repõe uma unidade com a geometria guardada (modo incremental), se
        ainda couber: rack compatível, altura do nível, dentro do nível, sem
        sobrepor o que já lá está."""
        pid, n, x, y, comp, larg = reg[:6]
        c = por_chave.get((pid, int(n)))
        if c is None or c.pos.rack.nome not in compat.get(it.load_type, set()):
            return False
        if it.alt_m > altura_nivel(c.pos.rack, c.nivel.nivel_idx) + 1e-9:
            return False
        if sorted((round(comp, 3), round(larg, 3))) != sorted((round(it.comp_m, 3), round(it.larg_m, 3))):
            return False
        if x < -1e-6 or y < -1e-6 or x + comp > c.pos.rack.larg_m + 1e-6 or y + larg > c.pos.rack.prof_m + 1e-6:
            return False
        for b in c.nivel.caixas:
            if x < b.x_m + b.comp_m - 1e-6 and b.x_m < x + comp - 1e-6 and \
                    y < b.y_m + b.larg_m - 1e-6 and b.y_m < y + larg - 1e-6:
                return False
        cx = CaixaColocada(sku=it.sku, descricao=it.descricao, load_type=it.load_type, x_m=x, y_m=y,
                           comp_m=comp, larg_m=larg, alt_m=it.alt_m, pecas_nesta_caixa=it.pecas_nesta_caixa,
                           eh_picking=False, num_boxes_in_stack=it.num_boxes_in_stack)
        if c.chave in reservadas:
            return False
        c.nivel.caixas.append(cx)
        c.area_livre -= comp * larg
        bloqueadas.add(id(cx))
        onde[it.sku].add(c.pos.id)
        reservar(c)
        return True

    # 1.ª etapa: fixações, depois o estado anterior (geometria exata), depois o
    # resto por área decrescente
    bloqueadas: set = set()        # id() das CaixaColocada que a 2.ª etapa não mexe
    pendentes = []
    for s in sorted(pref):
        falhou = False
        for it in sorted(por_sku[s], key=ffd, reverse=True):
            c = colocar(it, pref[s])
            if c is None:
                pendentes.append(it)
                falhou = True
            else:
                bloqueadas.add(id(c.nivel.caixas[-1]))
        if falhou:
            avisos.append(f"Fixação de {s} só em parte: não coube tudo em «{fixacoes[s]}».")
    repostos = set()
    formato_antigo = False
    for s in sorted(anterior):
        if s not in por_sku or s in pref:
            continue
        livres = sorted(por_sku[s], key=ffd, reverse=True)
        for reg in anterior[s]:
            if len(reg) < 6:
                formato_antigo = True
                continue
            # a unidade do SKU com a mesma forma (e, se houver, o mesmo nº de caixas)
            cands = [i for i in livres if sorted((round(i.comp_m, 3), round(i.larg_m, 3)))
                     == sorted((round(reg[4], 3), round(reg[5], 3)))]
            cands.sort(key=lambda i: i.num_boxes_in_stack != (reg[6] if len(reg) > 6 else i.num_boxes_in_stack))
            for i in cands:
                if repor(i, reg):
                    livres.remove(i)
                    break
        pendentes += livres
        repostos.add(s)
    if formato_antigo:
        avisos.append("Estado da alocação no formato antigo (só posição e nível): ignorado -- volta a guardar.")
    resto = [it for s, lst in por_sku.items() if s not in pref and s not in repostos for it in lst] + pendentes
    resto.sort(key=ffd, reverse=True)
    if zonas:     # zona de bulk: bulk primeiro (mais perto), depois voltas, consumíveis, sem picking
        resto.sort(key=lambda it: zona_de(it.sku))       # estável: dentro da zona fica a ordem FFD
    if por_frequencia:   # dentro da zona: o mais frequente (por unidade guardada) primeiro, perto
        resto.sort(key=lambda it: (zona_de(it.sku) if zonas else 0,
                                   -vis.get(it.sku, 0.0) / max(unidades.get(it.sku, 1), 1)))
    if pares:            # as OVERSIZE primeiro: só cabem num par se os dois módulos ainda estiverem livres
        resto.sort(key=lambda it: it.load_type != LoadType.OVERSIZE)
    nao = []
    for it in resto:
        if colocar(it) is None:
            nao.append(NaoAlocado(it.sku, it.descricao, it.load_type.value, it.num_boxes_in_stack, motivo(it)))

    def visitas_celula(c):
        return sum(parte(cx.sku, cx.num_boxes_in_stack) for cx in c.nivel.caixas)

    # posições que não entram nas trocas de posições/níveis: os pares e os
    # módulos com um nível reservado por um par (a reserva é física)
    pos_fixas = set(pares) | {k[0] for k in reservadas}

    def bloqueada(c):
        return c.pos.id in pos_fixas or any(id(cx) in bloqueadas for cx in c.nivel.caixas)

    # 2.ª etapa (a1): posições INTEIRAS (todos os níveis juntos) entre posições
    # iguais (mesma tipologia e nº de níveis) -- a de mais visitas para a mais
    # perto. Trocar níveis soltos ganhava um pouco mais (14,4 % contra 13,5 %
    # no bloco dimensionado) mas separava os níveis de um SKU que ocupava
    # vários níveis da mesma posição (57 SKUs em mais de 1 posição, até 6);
    # posições inteiras levam-no junto (22 SKUs).
    grupos = defaultdict(list)
    for pid, cels in (por_posicao.items() if otimizar else []):
        if not any(bloqueada(c) for c in cels):
            p = cels[0].pos
            grupos[(p.rack.nome, p.n_niveis)].append(sorted(cels, key=lambda c: c.nivel.nivel_idx))
    for grupo in grupos.values():
        _trocar_posicoes(grupo, parte, distancia, por_posicao, zona_de if zonas else None)

    # 2.ª etapa (a2): dentro de cada posição, níveis com a mesma altura útil --
    # o de mais visitas no mais baixo (mais barato)
    for cels in (por_posicao.values() if otimizar else []):
        por_altura = defaultdict(list)
        for c in cels:
            if not bloqueada(c):
                por_altura[round(altura_nivel(c.pos.rack, c.nivel.nivel_idx), 3)].append(c)
        for grupo in por_altura.values():
            conteudos = sorted(((c.nivel.caixas, c.area_livre, c.falhas, visitas_celula(c)) for c in grupo),
                               key=lambda t: -t[3])
            for c, (cxs, livre, falhas, _) in zip(sorted(grupo, key=lambda c: c.custo), conteudos):
                c.nivel.caixas, c.area_livre, c.falhas = cxs, livre, falhas

    # 2.ª etapa (b): unidades com a mesma forma -- a de mais visitas no slot mais barato
    slots = defaultdict(list)
    for c in celulas if otimizar else []:
        h = altura_nivel(c.pos.rack, c.nivel.nivel_idx)
        for cx in c.nivel.caixas:
            if id(cx) not in bloqueadas:
                forma = (cx.load_type, tuple(sorted((round(cx.comp_m, 3), round(cx.larg_m, 3)))),
                         zona_de(cx.sku) if zonas else 0)
                slots[forma].append((c, cx, h))
    for lst in slots.values():
        if len(lst) < 2:
            continue
        conteudo = [(cx.sku, cx.descricao, cx.alt_m, cx.pecas_nesta_caixa, cx.num_boxes_in_stack)
                    for _, cx, _ in lst]
        prioridade = sorted(range(len(conteudo)),
                            key=lambda i: (-parte(conteudo[i][0], conteudo[i][4]), conteudo[i][0]))
        por_custo = sorted(range(len(lst)), key=lambda i: (lst[i][0].custo, lst[i][0].pos.id,
                                                           lst[i][0].nivel.nivel_idx, lst[i][1].x_m,
                                                           lst[i][1].y_m))
        atrib = _atribuir_por_altura(prioridade, [t[2] for t in conteudo], por_custo, [t[2] for t in lst],
                                     sku_c=[t[0] for t in conteudo], pos_s=[t[0].pos.id for t in lst],
                                     custo_s=[t[0].custo for t in lst],
                                     tolerancia=cfg.alocacao_tolerancia_contiguo_min,
                                     vizinhos=viz)
        if len(atrib) != len(lst):      # não devia acontecer (a 1.ª etapa é viável): fica como estava
            avisos.append("Troca de unidades da mesma forma não aplicada a um grupo (inviável em altura).")
            continue
        for si, ci in atrib.items():
            cx = lst[si][1]
            cx.sku, cx.descricao, cx.alt_m, cx.pecas_nesta_caixa, cx.num_boxes_in_stack = conteudo[ci]

    # 2.ª etapa (c): posições inteiras duas a duas, depois de tudo o resto
    if otimizar:
        _trocas_aos_pares(por_posicao, parte, viz, zona_de if zonas else None, bloqueada)

    colocacoes = [Colocacao(cx.sku, cx.descricao, cx.load_type.value, cx.num_boxes_in_stack,
                            cx.pecas_nesta_caixa, c.pos, c.nivel.nivel_idx, c.custo,
                            parte(cx.sku, cx.num_boxes_in_stack))
                  for c in celulas for cx in c.nivel.caixas]
    mudancas = []
    for s, regs in anterior.items():
        agora = sorted({(c.posicao.id, c.nivel_idx) for c in colocacoes if c.sku == s})
        if s in por_sku and agora != sorted({(r[0], int(r[1])) for r in regs}):
            mudancas.append(s)
    res = ResultadoAlocacao(posicoes, celulas, colocacoes, nao, classes, tuple(ponto), mudancas, avisos)
    res.pares = dict(pares)
    return res


# ----------------------------------------------------------------- estado
def carregar_json(caminho: Path) -> dict:
    caminho = Path(caminho)
    if not caminho.exists():
        return {}
    return json.loads(caminho.read_text(encoding="utf-8"))


def assinatura_logica(cfg) -> str:
    """O que, se mudar, invalida o estado guardado: a versão das regras e os
    parâmetros que decidem onde fica cada coisa."""
    chave = {"versao": ALGORITMO_VERSAO, "vel": cfg.picking_velocidade_m_s, "equip": cfg.picking_equipamento,
             "alt_lim": cfg.picking_altura_limite_m, "alt_extra": cfg.picking_altura_extra_min,
             "tol": cfg.alocacao_tolerancia_contiguo_min, "grelha": cfg.picking_grelha_m,
             "passagem": cfg.picking_passagem_min_m, "destino": cfg.picking_zona_destino,
             "zona_bulk": getattr(cfg, "alocacao_zona_bulk", False),
             "oversize_pares": getattr(cfg, "alocacao_oversize_em_pares", False),
             "cons": [cfg.consumiveis_reposicao, cfg.consumiveis_periodo_dias]}
    return hashlib.sha1(json.dumps(chave, sort_keys=True, default=str).encode()).hexdigest()[:12]


def impressao_bom(assignments, artigos) -> dict:
    """sku -> [unidade, peças por unidade, nº de unidades, peças por carro]."""
    ppc = {a.sku: a.pecas_por_carro for a in artigos}
    return {a.sku: [a.load_type.value, float(a.pecas_por_unidade), int(a.num_unidades), float(ppc.get(a.sku, 0))]
            for a in assignments}


def comparar_bom(antes: dict, agora: dict) -> dict:
    novos = sorted(set(agora) - set(antes))
    removidos = sorted(set(antes) - set(agora))
    alterados = sorted(s for s in set(agora) & set(antes) if list(antes[s]) != list(agora[s]))
    return {"novos": novos, "removidos": removidos, "alterados": alterados,
            "n": len(novos) + len(removidos) + len(alterados)}


def estado_da_alocacao(res: ResultadoAlocacao) -> dict:
    """sku -> [[id da posição, nível, x, y, comp, larg, nº de caixas], ...]: a
    geometria EXATA de cada unidade no nível. O modo incremental repõe-na tal
    e qual (só verifica que ainda cabe) -- guardar só a posição e o nível e
    voltar a empacotar mudava 50 SKUs sem nada ter mudado (um nível cheio
    não volta a fechar com outra ordem de entrada)."""
    out = defaultdict(list)
    for c in res.celulas:
        for cx in c.nivel.caixas:
            out[cx.sku].append([c.pos.id, c.nivel.nivel_idx, round(cx.x_m, 4), round(cx.y_m, 4),
                                round(cx.comp_m, 4), round(cx.larg_m, 4), cx.num_boxes_in_stack])
    return {s: sorted(v) for s, v in sorted(out.items())}


def guardar_estado(caminho: Path, res: ResultadoAlocacao) -> None:
    """Grava a geometria de cada unidade + a BOM e a lógica com que foi feita
    (`res.estado_ctx`) -- é com elas que a próxima corrida decide se é
    incremental e se há alterações à BOM."""
    dados = {"versao": 2, "logica": res.estado_ctx.get("logica"), "bom": res.estado_ctx.get("bom", {}),
             "skus": estado_da_alocacao(res)}
    Path(caminho).write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")


# ----------------------------------------------------------------- Excel
def write_alocacao(caminho: Path, res: ResultadoAlocacao, taxas: dict, contexto: dict) -> None:
    import openpyxl
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    neg, fundo = Font(bold=True), PatternFill("solid", fgColor="DDE5F0")

    def folha(nome, cab, linhas, larguras):
        ws = wb.create_sheet(nome)
        ws.append(cab)
        for c in ws[1]:
            c.font, c.fill = neg, fundo
        for l in linhas:
            ws.append(l)
        ws.freeze_panes = "A2"
        for i, w in enumerate(larguras, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        return ws

    ws = wb.active
    ws.title = "Leia-me"
    ws.append(["Alocação dos componentes às racks do arranjo"])
    ws["A1"].font = Font(bold=True, size=13)
    n_un = sum(c.n_unidades for c in res.colocacoes)
    n_nao = sum(n.n_unidades for n in res.nao_alocados)
    for l in ([], *[[k, v] for k, v in contexto.items()],
              ["Ponto de carga (x, y no arranjo, m)", f"{res.ponto_carga[0]:.2f}, {res.ponto_carga[1]:.2f}"],
              ["Posições no arranjo", len(res.posicoes)],
              ["Unidades alocadas", n_un], ["Unidades NÃO alocadas", n_nao],
              ["SKUs que mudaram de sítio (incremental)", len(res.mudancas)],
              [], ["Regras"], *[[f"{i}.", r] for i, r in enumerate(REGRAS, 1)]):
        ws.append(l)
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 130

    def ciclos(sku):    # Bulk / Caixas / Paletes / Reposição semanal -- para filtrar bulk e voltas
        return ", ".join(taxas[sku].ciclos) if sku in taxas else ""

    folha("Localizações", ["SKU", "Descrição", "Ciclos", "Unidade", "Nº de unidades", "Peças", "Endereço", "Fila",
                           "Módulo", "Nível", "Tipologia", "x (m)", "y (m)", "Classe", "Visitas/dia (parte)"],
          [[c.sku, c.descricao, ciclos(c.sku), c.unidade, c.n_unidades, c.pecas, c.endereco, c.posicao.fila,
            c.posicao.modulo, c.nivel_idx + 1, c.posicao.rack.nome, round(c.posicao.centro[0], 2),
            round(c.posicao.centro[1], 2), res.classes.get(c.sku, "—"), round(c.visitas_dia, 4)]
           for c in sorted(res.colocacoes, key=lambda c: (c.posicao.fila, c.posicao.modulo, c.nivel_idx, c.sku))],
          [40, 36, 18, 9, 10, 8, 46, 34, 8, 6, 30, 7, 7, 7, 12])

    por_sku = defaultdict(list)
    for c in res.colocacoes:
        por_sku[c.sku].append(c)
    linhas = []
    for s, lst in sorted(por_sku.items(), key=lambda kv: (-(taxas[kv[0]].visitas_dia if kv[0] in taxas else 0), kv[0])):
        t = taxas.get(s)
        linhas.append([s, lst[0].descricao, res.classes.get(s, "—"), ", ".join(sorted({c.unidade for c in lst})),
                       sum(c.n_unidades for c in lst), sum(c.pecas for c in lst),
                       round(t.visitas_dia, 4) if t else 0, round(t.unidades_dia, 4) if t else 0,
                       ", ".join(t.ciclos) if t else "", len({c.posicao.id for c in lst}),
                       " | ".join(sorted({c.endereco for c in lst}))])
    folha("Por SKU", ["SKU", "Descrição", "Classe", "Unidade", "Nº de unidades", "Peças", "Visitas/dia",
                      "Unidades/dia", "Ciclos", "Nº de posições", "Endereços"],
          linhas, [40, 36, 7, 9, 10, 8, 11, 12, 18, 10, 90])

    vis_pos = res.visitas_por_posicao()
    min_pos = res.tempos.tempo_por_posicao if res.tempos else {}
    por_pos = defaultdict(list)
    for c in res.celulas:
        por_pos[c.pos.id].append(c)
    linhas = []
    for p in sorted(res.posicoes, key=lambda p: (p.fila, p.modulo)):
        cel = sorted(por_pos[p.id], key=lambda c: c.nivel.nivel_idx)
        area_niv = p.rack.larg_m * p.rack.prof_m
        ocup = [1 - c.area_livre / area_niv if area_niv else 0 for c in cel]
        linhas.append([p.endereco(), p.fila, p.modulo, p.rack.nome, p.n_niveis,
                       sum(1 for o in ocup if o > 1e-9), round(sum(ocup) / len(ocup), 3) if ocup else 0,
                       len({c.sku for c in res.colocacoes if c.posicao.id == p.id}),
                       round(vis_pos.get(p.id, 0.0), 4), round(min_pos.get(p.id, 0.0), 2),
                       round(p.centro[0], 2), round(p.centro[1], 2)])
    wsp = folha("Por posição", ["Posição", "Fila", "Módulo", "Tipologia", "Níveis", "Níveis com carga",
                                "Ocupação média em planta", "SKUs", "Visitas/dia", "Picking (min/dia)",
                                "x (m)", "y (m)"],
                linhas, [46, 34, 8, 30, 7, 10, 12, 7, 11, 12, 7, 7])
    for col in ("I", "J") if linhas else ():
        wsp.conditional_formatting.add(f"{col}2:{col}{len(linhas) + 1}",
                                       ColorScaleRule(start_type="min", start_color="FFFFFF",
                                                      mid_type="percentile", mid_value=50, mid_color="FFD966",
                                                      end_type="max", end_color="E06666"))
    folha("Não alocados", ["SKU", "Descrição", "Unidade", "Nº de unidades", "Motivo"],
          [[n.sku, n.descricao, n.unidade, n.n_unidades, n.motivo] for n in res.nao_alocados]
          or [["(nenhum)"]], [40, 36, 9, 10, 90])
    avisos = res.avisos + (res.tempos.avisos if res.tempos else [])
    if res.tempos:
        _folhas_tempos(wb, folha, res.tempos)
    folha("Avisos", ["Aviso"], [[a] for a in avisos] or [["(nenhum)"]], [140])
    wb.save(caminho)


def _folhas_tempos(wb, folha, rt) -> None:
    """Fase 3: «Tempos» (resumo, Gantt, calibração, validação, assunções) e
    «Tempos por volta» (cada volta e cada dia de bulk, livro vs modelo)."""
    from openpyxl.styles import Font
    from .tempos import ASSUNCOES_TEMPO
    ws = wb.create_sheet("Tempos")
    neg = Font(bold=True)

    def bloco(titulo, cab, linhas):
        ws.append([titulo])
        ws.cell(ws.max_row, 1).font = Font(bold=True, size=12)
        ws.append(cab)
        for c in ws[ws.max_row]:
            c.font = neg
        for l in linhas:
            ws.append(l)
        ws.append([])

    gs = [("Livro (tempos fixos)", rt.gantt_livro), ("Modelo (pela localização)", rt.gantt_modelo)]
    if rt.gantt_sem_consumiveis is not None:
        gs.append(("Modelo sem consumíveis", rt.gantt_sem_consumiveis))
    bloco("Gantt do armazém (pior volta de cada posição do dia, regras da folha «Gantt»)",
          ["Caso", "Sobra no fim do dia (min)", "Fim do armazém (min de trabalho)", "Voltas atrasadas",
           "Atraso máximo (min)", "Encaixa?"],
          [[n, round(g.sobra_min, 2), round(g.fim_min, 2), g.atrasadas, round(g.atraso_max_min, 2),
            "Sim" if g.sobra_min >= 0 and g.atrasadas == 0 else "NÃO"] for n, g in gs])
    med = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    for p in rt.preps:
        m = med[p.ciclo]
        m[0] += p.livro
        m[1] += p.modelo
        m[2] += p.consumiveis
        m[3] += 1
    if rt.por_dia:
        nao = [d for d in rt.por_dia if not d["encaixa"]]
        piores = sorted(rt.por_dia, key=lambda d: d["sobra_modelo_min"])[:10]
        bloco(f"Dia a dia ({len(rt.por_dia)} dias): {len(nao)} dia(s) não encaixam -- os 10 piores",
              ["Dia", "Sobra livro (min)", "Sobra modelo (min)", "Voltas atrasadas (modelo)", "Atraso máx. (min)",
               "Encaixa?"],
              [[d["dia"], d["sobra_livro_min"], d["sobra_modelo_min"], d["atrasadas_modelo"], d["atraso_max_min"],
                "Sim" if d["encaixa"] else "NÃO"] for d in piores])
    bloco("Preparação média (min por volta; bulk: por dia)",
          ["Ciclo", "Livro", "Modelo", "dos quais consumíveis", "Diferença"],
          [[c, round(l / n, 2), round(mo / n, 2), round(co / n, 2), round((mo - l) / n, 2)]
           for c, (l, mo, co, n) in med.items()])
    if rt.semanal:
        s = rt.semanal
        bloco("Reposição semanal dos consumíveis (fora do comboio; cabe na sobra do próprio dia?)",
              ["Reposições", "Média (min)", "Pior (min)", "Pior com a margem de 85 % (min)",
               "Semanas em que não cabe", "Cabe sempre?"],
              [[s["reposicoes"], s["media_min"], s["pior_min"], s["pior_com_margem_min"], s["nao_cabem"],
                "Sim" if s["cabe"] else "NÃO"]])
    bloco("Calibração (manuseio = tempo do livro − deslocação média de referência)",
          ["Tipo de pick", "Livro (min)", "Deslocação média de referência (min)", "Manuseio (min)"],
          [[g, c["livro_min"], c["deslocacao_media_min"], c["manuseio_min"]] for g, c in rt.calibracao.items()])
    bloco("Validação contra o livro (com os tempos do livro)", ["Verificação", "Estado", "Detalhe"],
          [[v.nome, "OK" if v.ok else "FALHA", v.detalhe] for v in rt.validacao])
    bloco("Gantt do modelo, tarefa a tarefa", ["Tarefa", "Início (min)", "Fim (min)", "Partida planeada (min)"],
          [[n, round(a, 2), round(b, 2), "" if t is None else t] for n, a, b, t in rt.gantt_modelo.linhas])
    bloco("Assunções", ["#", "Assunção"], [[i, a] for i, a in enumerate(ASSUNCOES_TEMPO, 1)])
    for col, w in zip("ABCDEF", (52, 22, 30, 16, 18, 10)):
        ws.column_dimensions[col].width = w

    folha("Gantt por dia", ["Dia", "Sobra livro (min)", "Atrasadas livro", "Sobra modelo (min)",
                            "Atrasadas modelo", "Atraso máx. (min)", "Reposição semanal (min)", "Encaixa?"],
          [[d["dia"], d["sobra_livro_min"], d["atrasadas_livro"], d["sobra_modelo_min"], d["atrasadas_modelo"],
            d["atraso_max_min"], d["reposicao_semanal_min"] or "", "Sim" if d["encaixa"] else "NÃO"]
           for d in rt.por_dia], [6, 14, 12, 15, 13, 14, 18, 9])
    folha("Tempos por volta", ["Dia", "Ciclo", "Volta", "Partida (min)", "Livro (min)", "Modelo (min)",
                               "dos quais consumíveis", "Diferença (min)", "Picks"],
          [[p.dia, p.ciclo, p.volta or "", p.partida, round(p.livro, 2), round(p.modelo, 2),
            round(p.consumiveis, 2), round(p.modelo - p.livro, 2), p.picks] for p in rt.preps],
          [6, 14, 7, 12, 11, 12, 14, 13, 7])


# ----------------------------------------------------------------- pipeline
def calibracao_automatica(cfg, local, arranjo, assignments, artigos, catalogo, rack_types, candidatos, itens,
                          taxas, res_proc, tl) -> tuple[dict, str]:
    """Calibração dos tempos no ARMAZÉM DIMENSIONADO (o bloco de racks e as zonas
    como o «Gerar racks» os põe -- web.blocos_canvas_por_omissao): a referência
    é sempre a mesma e reproduzível, e todos os arranjos se comparam com ela.
    Refaz-se sozinha quando muda alguma coisa de que depende (BOM, lógica,
    tempos do livro, racks, zonas, pé-direito, carros); senão lê-se do ficheiro."""
    from . import area, data_access, layout as layout_mod, racking, tempos
    from .web import ArrangeRequest, blocos_canvas_por_omissao, render_layout
    zonas = data_access.load_zones(local)
    chave = hashlib.sha1(json.dumps({
        "logica": assinatura_logica(cfg), "bom": impressao_bom(assignments, artigos),
        "livro": [tl.caixa_picking, tl.palete_picking, tl.peca_carrinho, tl.kit_pequena, tl.kit_grande],
        "arranjo": [arranjo.altura_armazem_m, arranjo.largura_max_m, arranjo.profundidade_max_m,
                    arranjo.carros_alvo], "carros": res_proc.carros_por_dia,
        "racks": [[r.nome, r.larg_m, r.prof_m, r.niveis, list(r.alturas_niveis_m), [x.value for x in r.aceita]]
                  for r in rack_types],
        "zonas": [[z.nome, z.comp_m, z.larg_m] for z in zonas]}, sort_keys=True, default=str).encode()).hexdigest()[:16]
    guardada = tempos.carregar_calibracao(cfg.tempos_calibracao, chave)
    if guardada is not None:
        return guardada, "lida"
    cpt = {b.nome: b for b in catalogo}
    req, _ = racking.racks_needed(assignments, rack_types, cpt, local, 0.0, artigos)
    posd, _ = racking.allocate_to_racks(assignments, req, rack_types, cpt, artigos, 0.0, local.altura_armazem_m, local)
    rack_by_name = {r.nome: r for r in rack_types}
    artigos_chao = area.artigos_chao_oversize2(assignments, local, rack_types)
    wl = layout_mod.gerar_layout(posd, rack_by_name, zonas, arranjo.largura_max_m, arranjo.profundidade_max_m,
                                 artigos_chao, local.oversize_chao_altura_max_m, local.oversize_chao_peso_max_kg,
                                 local.picking_manual_racks)
    ref = arranjo.model_copy(update={"blocks": blocos_canvas_por_omissao(wl, zonas)})
    render = render_layout(ref)
    posicoes = posicoes_do_render(render["layout"] + render["racks_manuais"], rack_by_name)
    ponto = ponto_de_carga(render["zonas_layout"], cfg.picking_zona_destino) or \
        ponto_de_carga(render["zonas_layout"], "comboio") or (0.0, 0.0)
    dist, _ = tempos.distancias_acesso(posicoes, render.get("obstaculos_layout", []), ponto, cfg.picking_grelha_m,
                                       cfg.picking_passagem_min_m / 2)
    posicoes, dist, candidatos, pares = com_pares(posicoes, dist, candidatos, render.get("obstaculos_layout", []),
                                                  ponto, cfg)
    res_ref = alocar(posicoes, itens, candidatos, taxas, ponto, local, dist=dist, pares=pares)
    cal, _ = tempos.calibrar(res_proc.picks, tempos.deslocacao_por_sku(res_ref, dist, local), tl)
    tempos.guardar_calibracao(cfg.tempos_calibracao, cal, {"referencia": "armazém dimensionado (Gerar racks)",
                                                          "posicoes": len(posicoes)}, chave)
    return cal, "refeita"


def run(cfg, arranjo, altura_armazem_m: float | None = None, carros_por_dia: int | None = None,
        ponto: tuple | None = None, out: Path | None = None, dias: int | None = None,
        guardar: bool = False) -> tuple[ResultadoAlocacao, dict]:
    """`arranjo`: o pedido do «Renderizar 3D» (web.ArrangeRequest -- blocos do
    canvas + pé-direito, envelope e carros_alvo, que mudam o bloco de racks)
    ou só a lista de blocos. -> alocação + Excel. `ponto`: (x, y) do ponto de
    carga; omissão = centro da zona com «comboio» no nome.

    Sem opções -- tudo automático:
    * incremental se houver estado guardado com a MESMA lógica; de raiz se não
      houver estado (e esta alocação passa a sê-lo) ou se a lógica mudou;
    * `res.estado["pode_guardar"]` só quando há alterações à BOM (ou à lógica)
      face ao estado guardado -- guarda-se com `guardar_estado` (UI: botão;
      CLI: `guardar=True` / --guardar);
    * calibração dos tempos no armazém dimensionado (``calibracao_automatica``).
    `dias`: só para testes (omissão: Config.simulacao_dias)."""
    from . import boxing, data_access, io_excel, procura, racking, tempos
    from .cenario_autonomia import aplicar_autonomia
    from .web import ArrangeRequest, render_layout

    if not isinstance(arranjo, ArrangeRequest):
        arranjo = ArrangeRequest(blocks=list(arranjo), altura_armazem_m=altura_armazem_m or 0.0)
    elif altura_armazem_m is not None:
        arranjo = arranjo.model_copy(update={"altura_armazem_m": altura_armazem_m})
    altura_armazem_m = arranjo.altura_armazem_m

    carros = int(carros_por_dia or cfg.carros_por_dia)
    p = procura.carregar_pressupostos(Path(cfg.pressupostos_picking))
    mapa, consumiveis = procura.consumiveis_e_mapa(cfg, p)
    taxas = procura.taxas_por_sku(p, carros, consumiveis, mapa, cfg.consumiveis_reposicao,
                                  cfg.consumiveis_periodo_dias)

    # as MESMAS unidades que o render-layout dimensiona para este arranjo
    local = io_excel.com_carrinhos_da_linha(dataclasses.replace(cfg, altura_armazem_m=altura_armazem_m))
    catalogo = data_access.load_catalog(local)
    artigos, _ = boxing.aplicar_stock_autonomia(data_access.load_articles(local), catalogo, local)
    if arranjo.carros_alvo > 0:
        artigos = aplicar_autonomia(artigos, arranjo.carros_alvo)
    assignments = boxing.plan_all(artigos, catalogo, local)
    rack_types = racking.com_par_oversize(data_access.load_rack_types(local), local)
    candidatos, itens = racking.preparar_itens(assignments, rack_types, {b.nome: b for b in catalogo},
                                               artigos, 0.0, local)

    render = render_layout(arranjo)
    posicoes = posicoes_do_render(render["layout"] + render["racks_manuais"], {r.nome: r for r in rack_types})
    avisos = []
    if ponto is None:
        ponto = ponto_de_carga(render["zonas_layout"], cfg.picking_zona_destino)
        if ponto is None:
            ponto = ponto_de_carga(render["zonas_layout"], "comboio")
            avisos.append(f"Sem a zona «{cfg.picking_zona_destino}» no arranjo: o picking vai para "
                          + ("a zona do comboio." if ponto else "(0, 0)."))
            ponto = ponto or (0.0, 0.0)
    dist, av_dist = tempos.distancias_acesso(posicoes, render.get("obstaculos_layout", []), ponto,
                                             cfg.picking_grelha_m, cfg.picking_passagem_min_m / 2)
    # incremental automático
    logica = assinatura_logica(cfg)
    bom = impressao_bom(assignments, artigos)
    guardado = carregar_json(cfg.alocacao_estado)
    tem_estado = guardado.get("versao") == 2 and "skus" in guardado
    mesma_logica = tem_estado and guardado.get("logica") == logica
    anterior = guardado["skus"] if mesma_logica else {}
    posicoes, dist, candidatos, pares = com_pares(posicoes, dist, candidatos, render.get("obstaculos_layout", []),
                                                  ponto, cfg)
    res = alocar(posicoes, itens, candidatos, taxas, ponto, local,
                 carregar_json(cfg.alocacao_fixacoes), anterior, dist=dist, pares=pares)
    res.avisos[:0] = avisos + av_dist
    res.estado_ctx = {"bom": bom, "logica": logica}
    diff = comparar_bom(guardado.get("bom", {}), bom) if tem_estado else {"novos": [], "removidos": [],
                                                                          "alterados": [], "n": 0}
    if not tem_estado:
        motivo = ("sem estado guardado: esta alocação passou a ser o estado de referência" if not guardado
                  else "estado guardado num formato antigo: substituído por esta alocação")
        guardar_estado(cfg.alocacao_estado, res)
    elif not mesma_logica:
        motivo = "a lógica de alocação mudou desde o estado guardado: alocação de raiz"
    else:
        motivo = (f"incremental: {len(res.mudancas)} SKU(s) mudaram de sítio"
                  + (f"; a BOM tem {diff['n']} alteração(ões) face ao estado guardado" if diff["n"] else ""))
    res.estado = {"modo": "incremental" if mesma_logica else "de raiz", "motivo": motivo, "bom": diff,
                  "logica_mudou": tem_estado and not mesma_logica, "guardado": not tem_estado,
                  "pode_guardar": tem_estado and (diff["n"] > 0 or not mesma_logica),
                  "mudancas": len(res.mudancas)}
    res.planta = {"zonas": render.get("zonas_layout", []), "obstaculos": render.get("obstaculos_layout", []),
                  "cubo": render.get("cubo"), "largura_m": render.get("largura_total_m", 0.0),
                  "profundidade_m": render.get("profundidade_total_m", 0.0)}
    # calor do chão: todas as idas e voltas de picking (visitas/dia de todos os ciclos)
    res.planta["percursos"], res.planta["caminhos"] = tempos.rotas(
        posicoes, render.get("obstaculos_layout", []), ponto, res.visitas_por_posicao(), cfg.picking_grelha_m,
        cfg.picking_passagem_min_m / 2)
    res.planta["destino"] = cfg.picking_zona_destino

    # fase 3: tempos de picking com esta alocação, nos dias simulados
    res_proc = procura.simular(p, carros, dias or cfg.simulacao_dias, consumiveis, mapa,
                               cfg.consumiveis_reposicao, cfg.consumiveis_periodo_dias)
    tl = tempos.carregar_tempos_livro(Path(cfg.pressupostos_picking))
    tl.voltas_horario = cfg.voltas_horario_do_livro   # bulk: hora mínima e ordem vêm do livro (C104, C106)
    cal, como = calibracao_automatica(cfg, local, arranjo, assignments, artigos, catalogo, rack_types,
                                      candidatos, itens, taxas, res_proc, tl)
    base = carros == int(p.carros_por_dia_base)
    # validação contra o livro: os 30 dias DELE, à parte do horizonte
    res_val = procura.simular(p, carros, p.dias_simulados, consumiveis, mapa, cfg.consumiveis_reposicao,
                              cfg.consumiveis_periodo_dias) if base else None
    rt = tempos.avaliar(res_proc, res, dist, tl, local, cal, base, res_val)
    rt.percursos = tempos.Percursos(posicoes, render.get("obstaculos_layout", []), ponto, cfg.picking_grelha_m,
                                    cfg.picking_passagem_min_m / 2)
    res.estado["calibracao"] = como
    res.tempos = rt

    contexto = {"Carros por dia": carros, "Pé-direito útil (m)": altura_armazem_m or "(sem limite)",
                "Modo": f"{res.estado['modo']} -- {res.estado['motivo']}",
                "Calibração dos tempos": f"no armazém dimensionado ({como})",
                "Pressupostos de picking": str(cfg.pressupostos_picking),
                "Velocidades (m/s)": ", ".join(f"{k} {v:g}" for k, v in cfg.picking_velocidade_m_s.items()),
                "Extra de altura": f"+{cfg.picking_altura_extra_min * 60:g} s acima de "
                                   f"{cfg.picking_altura_limite_m:g} m",
                "Tolerância de contiguidade (min)": cfg.alocacao_tolerancia_contiguo_min,
                "Deslocação ponderada (Σ visitas/dia × custo da visita, min/dia)": round(res.custo_ponderado, 1),
                "Horizonte simulado (dias úteis)": f"{res_proc.dias} (Config.simulacao_dias; a validação "
                                                   f"contra o livro usa os {p.dias_simulados} dele)",
                "Consumíveis": (f"reposição semanal à parte, de {cfg.consumiveis_periodo_dias} em "
                                f"{cfg.consumiveis_periodo_dias} dias úteis"
                                if cfg.consumiveis_reposicao == procura.MODO_SEMANAL
                                else "nas voltas do comboio")}
    write_alocacao(Path(out or cfg.output_alocacao), res, taxas, contexto)
    if guardar and res.estado.get("pode_guardar"):
        guardar_estado(cfg.alocacao_estado, res)
        res.estado.update(pode_guardar=False, guardado=True)
    return res, taxas


def para_json(res: ResultadoAlocacao, taxas: dict) -> dict:
    """Resposta do /api/alocacao: o que a UI precisa para o heatmap (visitas
    por posição e por nível) e para a vista de frente de cada rack (caixas
    por nível, com x/y/cotas no referencial do nível)."""
    vis_pos = res.visitas_por_posicao()
    vis_nivel = defaultdict(float)
    for co in res.colocacoes:
        vis_nivel[(co.posicao.id, co.nivel_idx)] += co.visitas_dia
    por_pos = defaultdict(list)
    for c in res.celulas:
        por_pos[c.pos.id].append(c)
    posicoes = []
    for p in res.posicoes:
        niveis = []
        for c in sorted(por_pos[p.id], key=lambda c: c.nivel.nivel_idx):
            niveis.append({
                "nivel": c.nivel.nivel_idx + 1, "base_m": c.base_m,
                "altura_util_m": altura_nivel(p.rack, c.nivel.nivel_idx), "custo": round(c.custo, 2),
                "visitas_dia": round(vis_nivel[c.chave], 4),
                "caixas": [{"sku": cx.sku, "descricao": cx.descricao, "unidade": cx.load_type.value,
                            "x_m": cx.x_m, "y_m": cx.y_m, "comp_m": cx.comp_m, "larg_m": cx.larg_m,
                            "alt_m": cx.alt_m, "n_unidades": cx.num_boxes_in_stack,
                            "pecas": cx.pecas_nesta_caixa, "classe": res.classes.get(cx.sku, "—")}
                           for cx in c.nivel.caixas]})
        posicoes.append({"id": p.id, "par": p.id in res.pares, "endereco": p.endereco(), "fila": p.fila,
                         "modulo": p.modulo,
                         "rack_nome": p.rack.nome, "larg_nivel_m": p.rack.larg_m, "prof_nivel_m": p.rack.prof_m,
                         "x": p.x_m, "y": p.y_m, "larg_m": p.larg_m, "prof_m": p.prof_m,
                         "visitas_dia": round(vis_pos.get(p.id, 0.0), 4), "niveis": niveis})
    rt = res.tempos
    min_pos = rt.tempo_por_posicao if rt else {}
    for p in posicoes:
        p["picking_min_dia"] = round(min_pos.get(p["id"], 0.0), 2)
    # OVERSIZE num par frente+trás: os níveis que ocupam aparecem nos DOIS
    # módulos reais (sem isto a vista de frente e o mapa mostravam-nos vazios,
    # 2026-11-09); as visitas e o picking do par contam metade em cada um
    por_id = {p["id"]: p for p in posicoes}
    for par, (pf, pt) in res.pares.items():
        jp = por_id.get(par)
        if not jp or not any(n["caixas"] for n in jp["niveis"]):
            continue
        for real in (pf, pt):
            jr = por_id.get(real)
            if not jr:
                continue
            for nr, npar in zip(jr["niveis"], jp["niveis"]):
                if npar["caixas"]:
                    nr["par"] = {"id": par, "endereco": jp["endereco"], "caixas": npar["caixas"],
                                 "visitas_dia": npar["visitas_dia"], "prof_m": jp["prof_nivel_m"]}
            jr["visitas_par_dia"] = round(jp["visitas_dia"] / 2, 4)
            jr["visitas_dia"] = round(jr["visitas_dia"] + jp["visitas_dia"] / 2, 4)
            jr["picking_min_dia"] = round(jr["picking_min_dia"] + jp["picking_min_dia"] / 2, 2)
    tempos = None
    if rt:
        def g(x):
            return None if x is None else {"sobra_min": round(x.sobra_min, 2), "fim_min": round(x.fim_min, 2),
                                           "atrasadas": x.atrasadas, "atraso_max_min": round(x.atraso_max_min, 2),
                                           "linhas": [[n, round(a, 2), round(b, 2), t] for n, a, b, t in x.linhas]}
        tempos = {"gantt_livro": g(rt.gantt_livro), "gantt_modelo": g(rt.gantt_modelo),
                  "gantt_sem_consumiveis": g(rt.gantt_sem_consumiveis), "calibracao": rt.calibracao,
                  "reposicao_semanal": rt.semanal, "dias": rt.dias, "por_dia": rt.por_dia,
                  "validacao": [dataclasses.asdict(v) for v in rt.validacao], "avisos": rt.avisos}
    return {
        "ponto_carga": list(res.ponto_carga),
        "estado": res.estado,
        "planta": res.planta,
        "tempos": tempos,
        "posicoes": posicoes,
        "nao_alocados": [dataclasses.asdict(n) for n in res.nao_alocados],
        "mudancas": res.mudancas,
        "avisos": res.avisos,
        "resumo": {
            "posicoes": len(res.posicoes),
            "unidades_alocadas": sum(c.n_unidades for c in res.colocacoes),
            "unidades_nao_alocadas": sum(n.n_unidades for n in res.nao_alocados),
            "custo_ponderado": round(res.custo_ponderado, 1),
            "classes": {k: sum(1 for v in res.classes.values() if v == k) for k in ("A", "B", "C", "—")},
        },
        "regras": list(REGRAS),
    }


def main() -> None:
    from .config import Config
    ap = argparse.ArgumentParser(description="Alocação dos componentes às racks de um arranjo")
    ap.add_argument("arranjo", type=Path, help="arranjo_layout.json («Guardar arranjo» no Arranjar Layout)")
    ap.add_argument("--client", default=os.environ.get("WAREHOUSE_CLIENT", "sia"))
    ap.add_argument("--altura", type=float, default=0.0, help="pé-direito útil (m), como no formulário")
    ap.add_argument("--carros", type=int, help="carros por dia (omissão: Config.carros_por_dia)")
    ap.add_argument("--guardar", action="store_true",
                    help="aceitar esta alocação como estado (só quando há alterações à BOM ou à lógica)")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    cfg = Config.for_client(a.client)
    blocos = json.loads(a.arranjo.read_text(encoding="utf-8"))["blocks"]
    res, _ = run(cfg, blocos, a.altura, a.carros, out=a.out, guardar=a.guardar)
    print(f"Estado: {res.estado['modo']} -- {res.estado['motivo']}"
          + (" (há alterações: --guardar para as aceitar)" if res.estado.get("pode_guardar") else ""))
    rt = res.tempos
    for nome, g in (("livro", rt.gantt_livro), ("modelo", rt.gantt_modelo)):
        print(f"Gantt do armazém, pior volta em {rt.dias} dias ({nome}): sobra {g.sobra_min:.1f} min, "
              f"{g.atrasadas} volta(s) atrasada(s)")
    nao = [d for d in rt.por_dia if not d["encaixa"]]
    print(f"Dia a dia: {len(nao)} de {len(rt.por_dia)} dias não encaixam")
    for v in rt.validacao:
        print(f"  [{'OK' if v.ok else 'FALHA'}] {v.nome}: {v.detalhe}")
    for av in rt.avisos:
        print(f"  AVISO: {av}")
    n_un = sum(c.n_unidades for c in res.colocacoes)
    n_nao = sum(n.n_unidades for n in res.nao_alocados)
    print(f"{len(res.posicoes)} posições no arranjo; {n_un} unidades alocadas, {n_nao} por alocar "
          f"-> {a.out or cfg.output_alocacao}")
    for av in res.avisos:
        print(f"  AVISO: {av}")


if __name__ == "__main__":
    main()

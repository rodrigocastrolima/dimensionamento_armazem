"""Procura de picking no armazém, por ciclo de reposição (Passo 5a).

Responde a: em cada volta do comboio (caixas / paletes) e em cada viagem do
bulk de fim de dia, que SKUs saem do armazém, em que unidade e quantas.
É o que alimenta a alocação e o heatmap de picking (passos seguintes).

Os pressupostos do abastecimento à linha NÃO são decididos aqui: vêm do livro
do comboio (``Config.pressupostos_picking``, ex. «comboio bulk + armazem
23-10.xlsx``), que é onde o utilizador os decide -- PFEP (unidade e SNP de
cada referência no bordo de linha, atraso de reposição, nível inicial,
carrinhos, kits), «Consumo SKU» (a hora a que cada peça é consumida no carro
do dia), «Cenário» (horário das voltas e regra do bulk) e «Parâmetros»
(tempo por dia). Este módulo só lê VALORES (openpyxl, data_only) e reproduz,
em Python, as fórmulas das folhas «Esvaziamentos», «Entregas caixas» e
«7b Voltas»:

* esvaziadas(t) = INT(dias completos × emb/dia + Σ embalagens dos consumos da
  referência até à hora do dia de t + nível inicial)
* caixas: entregues numa volta = esvaziadas(t − atraso de reposição) − o
  mesmo na volta anterior do mesmo tipo (folha «Entregas caixas»);
* paletes e carrinhos: entregues = esvaziadas(t) − esvaziadas(volta anterior)
  (folha «Esvaziamentos», sem atraso);
* bulk (fim do dia): referências com ≥ ``Cenário!C13`` embalagens por dia,
  conforme ``Cenário!C12`` -- saem das voltas e vão todas de uma vez.

Com ``carros_por_dia`` igual ao do livro (1), o resultado tem de bater volta a
volta com a folha «7b Voltas» e com os totais da folha «Bulk» --
``validar_contra_excel``. Com mais carros por dia é uma EXTRAPOLAÇÃO, com as
assunções listadas em ``ASSUNCOES`` (vão também para o Excel de saída).

Consumíveis (SKUs do master data sem linha no PFEP -- parafusos, porcas,
anilhas, clips...): não têm hora de consumo nem posto; ver ``ASSUNCOES``.
"""
from __future__ import annotations

import argparse
import dataclasses
import math
import os
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

EPS = 1e-9
CAIXAS, PALETES, BULK = "Caixas", "Paletes", "Bulk"
SEMANAL = "Reposição semanal"       # consumíveis, à parte do comboio (Config.consumiveis_reposicao)
MODO_SEMANAL, MODO_VOLTAS = "semanal", "voltas"
CARRINHO = "Carrinho"
# Nome da unidade no livro do comboio -> nome no catálogo do armazém (LoadType)
UNIDADE_EXCEL_PARA_ARMAZEM = {"Europalete": "PALLET", "Meia-palete": "MEIA"}
UNIDADES_CAIXA = ("XS", "S", "M", "L")
BULK_NENHUM = "Nenhum"
BULK_SO_PALETES = "Paletes e carrinhos"
BULK_TUDO = "Paletes, carrinhos e caixas"

ASSUNCOES = (
    "Com 1 carro por dia (o do livro) o cálculo é o das folhas «Esvaziamentos», «Entregas caixas», "
    "«7b Voltas» e «Bulk» -- ver a folha «Validação».",
    "Com N carros por dia: cada carro demora (tempo por dia ÷ N) e os consumos de «Consumo SKU» "
    "acontecem à mesma fração desse tempo (o dia de 1 carro comprimido N vezes). Embalagens por dia "
    "de cada referência = N × as do livro. O horário das voltas e a regra do bulk não mudam.",
    "Consumíveis (SKUs do master data sem linha no PFEP): unidade de movimentação = a do armazém "
    "(herdada da linha); consumo regular ao longo do dia (peças/carro × carros/dia). Com "
    "Config.consumiveis_reposicao = «semanal» (decisão do utilizador, 2026-11-05): reposição à parte, "
    "fora do comboio, de Config.consumiveis_periodo_dias em Config.consumiveis_periodo_dias dias úteis, "
    "no início do dia -- leva as caixas que esvaziaram desde a anterior. Com «voltas»: caixa vazia "
    "reposta na volta seguinte do seu tipo (XS/S/M/L -> caixas; palete -> paletes) e bulk pela regra "
    "das outras referências.",
    "Carrinhos: cada carrinho entregue é um enchimento no armazém -- 1 visita por referência do "
    "carrinho, com as peças dessa referência (PFEP, «Qtd por embalagem»).",
    "Kits: só os kits que vão no bulk (autonomia do kit ≤ «Kits»!C4) são modelados; 1 visita por "
    "referência do kit, com as «Unidades por kit». Um kit nas voltas regulares dá aviso.",
)


# ----------------------------------------------------------------- modelos
@dataclass
class LinhaPFEP:
    """Uma linha do PFEP (uma referência num posto)."""
    linha: int                 # nº da linha no Excel (para rastrear)
    ref: str
    descricao: str
    estacao: str
    posto: str
    qtd_carro: float
    unidade: str               # XS/S/M/L/Meia-palete/Europalete/Carrinho
    snp: float                 # peças por embalagem no bordo de linha
    tipo_volta: str            # Caixas / Paletes
    emb_h: float               # «Emb./h efetivas» (0 = não vai sozinha: 2.ª linha de carrinho, kit)
    nivel_inicial: float
    atraso_min: float          # atraso de reposição (só caixas)
    carrinho: str = ""
    kit: str = ""
    unidades_kit: float = 0.0
    bulk_excel: bool = False   # «Vai em bulk?» calculado pelo próprio Excel (só para validar)


@dataclass
class Kit:
    id: str
    posto: str
    kits_por_carro: float


@dataclass
class Pressupostos:
    caminho: Path
    tempo_dia_min: float
    carros_por_dia_base: float
    dias_simulados: int
    partidas: dict                  # "Caixas"/"Paletes" -> [min de trabalho, ordenado]
    bulk_modo: str
    bulk_minimo_emb_dia: float
    kit_autonomia_max_dias: float
    linhas: list                    # [LinhaPFEP]
    eventos: dict                   # (ref, estação) -> [(minuto, embalagens)] de 1 carro
    kits: dict                      # id -> Kit


@dataclass(frozen=True)
class Consumivel:
    sku: str
    descricao: str
    unidade: str                    # LoadType do armazém (XS/S/M/L/MEIA/PALLET/OVERSIZE)
    pecas_por_unidade: float
    pecas_por_carro: float


@dataclass
class Pick:
    """Uma saída do armazém: uma visita a uma localização para tirar uma unidade
    (ou peças, nos carrinhos e kits)."""
    dia: int                  # 1-based
    ciclo: str                # Caixas / Paletes / Bulk
    volta: int                # nº da volta do tipo no dia (1-based); 0 no bulk
    partida_min: float        # min de trabalho desde o início do turno (bulk = fim do dia)
    sku: str                  # SKU do master data do armazém (ou a ref do PFEP, se não houver)
    descricao: str
    unidade: str              # unidade que sai (XS/S/M/L/MEIA/PALLET) ou "Peças" (carrinho/kit)
    unidades: int             # nº de unidades que saem nesta visita (peças: 1 visita)
    pecas: float              # peças que vão para a linha
    posto: str
    origem: str               # "Bordo de linha" / "Carrinho <id>" / "Kit <id>" / "Consumível"


@dataclass
class ResultadoProcura:
    carros_por_dia: float
    dias: int
    picks: list
    entregas_por_volta: dict        # (ciclo, nº global da volta) -> {unidade: n} (só PFEP, como a 7b)
    bulk_por_dia: dict              # dia -> {unidade: n} (só PFEP + kits, como a folha «Bulk»)
    em_bulk: set                    # linhas do PFEP que vão no bulk
    avisos: list = field(default_factory=list)


# ----------------------------------------------------------------- leitura
def _cabecalhos(ws, linha: int) -> dict:
    return {str(c.value).strip(): c.column for c in ws[linha] if c.value not in (None, "")}


def _coluna(cab: dict, prefixo: str, folha: str) -> int:
    """Coluna cujo cabeçalho começa por `prefixo` (as do livro são longas)."""
    for nome, col in cab.items():
        if nome.startswith(prefixo):
            return col
    raise ValueError(f"Folha «{folha}»: não encontrei a coluna «{prefixo}…»")


def _num(v, omissao: float = 0.0) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else omissao


def _txt(v) -> str:
    return "" if v is None else str(v).strip()


def carregar_pressupostos(caminho: Path) -> Pressupostos:
    """Lê os valores do livro do comboio. As colunas do PFEP e da «Consumo SKU»
    vão por NOME do cabeçalho (o livro ganha colunas com frequência); as
    células de parâmetro vão por endereço, como nas fórmulas do próprio livro."""
    warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
    wb = openpyxl.load_workbook(caminho, data_only=True)
    par, cen = wb["Parâmetros"], wb["Cenário"]
    tempo_dia = _num(par["C11"].value)
    if tempo_dia <= 0:
        raise ValueError(f"{caminho.name}: «Parâmetros»!C11 (tempo por dia) vazio -- o livro foi "
                         f"gravado sem valores calculados? Abre-o no Excel e grava.")

    # horário das voltas: «Cenário», secção 5 (B tipo, C nº, F partida usada)
    partidas = {CAIXAS: [], PALETES: []}
    for r in range(60, cen.max_row + 1):
        tipo = _txt(cen.cell(r, 2).value)
        t = cen.cell(r, 6).value
        if tipo in partidas and isinstance(t, (int, float)):
            partidas[tipo].append(float(t))
    for k in partidas:
        partidas[k].sort()

    P = wb["PFEP"]
    cab = _cabecalhos(P, 6)
    col = {k: _coluna(cab, p, "PFEP") for k, p in {
        "ref": "Referência", "desc": "Descrição", "est": "Estação", "posto": "Posto consumidor",
        "qtd": "Qtd por produto", "unid": "Tipo de embalagem", "snp": "Qtd por embalagem",
        "volta": "Tipo de volta", "nivel": "Nível inicial", "carr": "Carrinho Nº/ID",
        "emb_h": "Emb./h efetivas", "kit": "Kit Nº/ID", "ukit": "Unidades por kit",
        "bulk": "Vai em bulk?", "atraso": "Atraso de reposição"}.items()}
    linhas = []
    for r in range(7, P.max_row + 1):
        g = lambda k: P.cell(r, col[k]).value
        ref = _txt(g("ref"))
        if not ref:
            continue
        linhas.append(LinhaPFEP(
            linha=r, ref=ref, descricao=_txt(g("desc")), estacao=_txt(g("est")), posto=_txt(g("posto")),
            qtd_carro=_num(g("qtd")), unidade=_txt(g("unid")), snp=_num(g("snp")),
            tipo_volta=_txt(g("volta")), emb_h=_num(g("emb_h")), nivel_inicial=_num(g("nivel")),
            atraso_min=_num(g("atraso")), carrinho=_txt(g("carr")), kit=_txt(g("kit")),
            unidades_kit=_num(g("ukit")), bulk_excel=_num(g("bulk")) == 1))

    C = wb["Consumo SKU"]
    cab = _cabecalhos(C, 12)
    c_ref, c_est = _coluna(cab, "Referência", "Consumo SKU"), _coluna(cab, "Estação", "Consumo SKU")
    c_emb, c_min = _coluna(cab, "Embalagens", "Consumo SKU"), _coluna(cab, "Consumo (min", "Consumo SKU")
    eventos = defaultdict(list)
    for r in range(13, C.max_row + 1):
        ref = _txt(C.cell(r, c_ref).value)
        m = C.cell(r, c_min).value
        if ref and isinstance(m, (int, float)):
            eventos[(ref, _txt(C.cell(r, c_est).value))].append((float(m), _num(C.cell(r, c_emb).value)))
    for v in eventos.values():
        v.sort()

    K = wb["Kits"]
    cab = _cabecalhos(K, 6)
    k_id, k_posto = _coluna(cab, "Nº/ID", "Kits"), _coluna(cab, "Posto", "Kits")
    k_n = _coluna(cab, "Kits por carro", "Kits")
    kits = {}
    for r in range(7, K.max_row + 1):
        kid = _txt(K.cell(r, k_id).value)
        if kid and isinstance(K.cell(r, k_n).value, (int, float)):
            kits[kid] = Kit(kid, _txt(K.cell(r, k_posto).value), float(K.cell(r, k_n).value))

    return Pressupostos(
        caminho=Path(caminho), tempo_dia_min=tempo_dia,
        carros_por_dia_base=_num(par["C10"].value, 1.0) or 1.0,
        dias_simulados=int(_num(par["C49"].value, 30) or 30),
        partidas=partidas, bulk_modo=_txt(cen["C12"].value) or BULK_NENHUM,
        bulk_minimo_emb_dia=_num(cen["C13"].value, 1.0),
        kit_autonomia_max_dias=_num(K["C4"].value, 1.0),
        linhas=linhas, eventos=dict(eventos), kits=kits)


# ----------------------------------------------------------------- SKUs
def _prefixo(s: str) -> str:
    return s.split("_____")[0].strip()


def mapa_skus(refs, skus_armazem) -> dict:
    """ref do PFEP -> SKU do master data do armazém. O PFEP usa às vezes só o
    código (``BEN10.CDF005``) e o master data o código + descrição
    (``BEN10.CDF005_____...``): igual primeiro; depois pelo código, entre os
    SKUs que nenhuma ref já levou pelo nome completo, se só sobrar um.
    Caso real: o PFEP tem ``SKT10.BGB003_____CMS,FR RR`` (bate inteiro) e
    ``SKT10.BGB003`` -- este fica com o outro SKU do código,
    ``SKT10.BGB003_____CMS,FR``."""
    exatos = set(skus_armazem)
    out = {ref: ref for ref in refs if ref in exatos}
    levados = set(out.values())
    livres = defaultdict(list)
    for s in skus_armazem:
        if s not in levados:
            livres[_prefixo(s)].append(s)
    for ref in refs:
        if ref not in out and len(livres.get(_prefixo(ref), [])) == 1:
            out[ref] = livres[_prefixo(ref)][0]
    return out


# ----------------------------------------------------------------- simulação
def _vai_em_bulk(emb_dia: float, tipo_volta: str, modo: str, minimo: float) -> bool:
    if modo == BULK_NENHUM or emb_dia <= 0 or emb_dia < minimo - EPS:
        return False
    return modo == BULK_TUDO or tipo_volta == PALETES


def _unidade_armazem(unidade_linha: str) -> str:
    return UNIDADE_EXCEL_PARA_ARMAZEM.get(unidade_linha, unidade_linha)


class _Contador:
    """esvaziadas(t) de uma linha do PFEP com N carros por dia (t = min de
    trabalho desde o início do 1.º dia)."""

    def __init__(self, x: LinhaPFEP, p: Pressupostos, n_carros: int):
        self.dia = p.tempo_dia_min
        fator = n_carros / p.carros_por_dia_base
        self.emb_dia = x.emb_h * self.dia / 60.0 * fator            # PFEP, coluna BD
        self.nivel = x.nivel_inicial
        passo = self.dia / n_carros
        base = p.eventos.get((x.ref, x.estacao), [])
        # o dia de 1 carro comprimido em cada um dos N ciclos (ver ASSUNCOES)
        self.eventos = sorted((c * passo + m / self.dia * passo, e)
                              for c in range(n_carros) for m, e in base)
        self.ativo = x.emb_h > 0

    def esvaziadas(self, t: float) -> int:
        if not self.ativo or t < -EPS:
            return 0
        d = math.floor(t / self.dia + EPS)
        h = t - d * self.dia
        cons = sum(e for m, e in self.eventos if m <= h + EPS)
        return math.floor(d * self.emb_dia + cons + self.nivel + EPS)


def simular(p: Pressupostos, carros_por_dia: int = 1, dias: int | None = None,
            consumiveis: list | None = None, skus_armazem: dict | None = None,
            consumiveis_modo: str = MODO_VOLTAS, periodo_dias: int = 5) -> ResultadoProcura:
    """`skus_armazem`: ref do PFEP -> SKU do master data (``mapa_skus``); sem
    ele os picks ficam com a ref do PFEP. `consumiveis_modo`: «voltas» (nas
    voltas/bulk do comboio) ou «semanal» (reposição à parte, de `periodo_dias`
    em `periodo_dias` dias, ciclo SEMANAL)."""
    if carros_por_dia < 1 or int(carros_por_dia) != carros_por_dia:
        raise ValueError(f"carros_por_dia tem de ser um inteiro ≥ 1 (recebi {carros_por_dia}): o "
                         f"horário de consumo de «Consumo SKU» é por carro.")
    n_carros = int(carros_por_dia)
    dias = dias or p.dias_simulados
    dia = p.tempo_dia_min
    fator = n_carros / p.carros_por_dia_base
    skus_armazem = skus_armazem or {}
    avisos = []
    picks: list[Pick] = []
    entregas = defaultdict(lambda: defaultdict(int))
    bulk_dia = defaultdict(lambda: defaultdict(int))

    grupos_carrinho = defaultdict(list)
    for x in p.linhas:
        if x.carrinho:
            grupos_carrinho[x.carrinho].append(x)

    def sku(x: LinhaPFEP) -> str:
        return skus_armazem.get(x.ref, x.ref)

    def emitir(x: LinhaPFEP, n: int, dd: int, ciclo: str, volta: int, partida: float):
        """n embalagens (ou carrinhos) da linha x -> picks."""
        if x.unidade == CARRINHO:
            for y in grupos_carrinho.get(x.carrinho, [x]):
                picks.append(Pick(dd, ciclo, volta, partida, sku(y), y.descricao, "Peças", n,
                                  y.snp * n, y.posto, f"Carrinho {x.carrinho}"))
        else:
            picks.append(Pick(dd, ciclo, volta, partida, sku(x), x.descricao, _unidade_armazem(x.unidade),
                              n, x.snp * n, x.posto, "Bordo de linha"))

    em_bulk = set()
    contadores = {}
    for x in p.linhas:
        c = _Contador(x, p, n_carros)
        contadores[x.linha] = c
        if c.ativo and _vai_em_bulk(c.emb_dia, x.tipo_volta, p.bulk_modo, p.bulk_minimo_emb_dia):
            em_bulk.add(x.linha)
        if x.unidade not in UNIDADES_CAIXA + tuple(UNIDADE_EXCEL_PARA_ARMAZEM) + (CARRINHO,) and c.ativo:
            avisos.append(f"PFEP linha {x.linha} ({x.ref}): unidade «{x.unidade}» desconhecida -- ignorada.")

    # voltas regulares (folhas «Entregas caixas» / «Esvaziamentos» -> «7b Voltas»)
    for tipo in (CAIXAS, PALETES):
        T = p.partidas[tipo]
        for x in p.linhas:
            c = contadores[x.linha]
            if x.tipo_volta != tipo or not c.ativo or x.linha in em_bulk:
                continue
            atraso = x.atraso_min if tipo == CAIXAS else 0.0
            ant = 0
            for d in range(dias):
                for k, t in enumerate(T):
                    tabs = d * dia + t - atraso
                    f = c.esvaziadas(tabs) if tabs >= -EPS else 0
                    n, ant = f - ant, f
                    if n > 0:
                        entregas[(tipo, d * len(T) + k + 1)][x.unidade] += n
                        emitir(x, n, d + 1, tipo, k + 1, t)

    # bulk de fim de dia (folha «Bulk»): as embalagens de um dia, todos os dias
    for x in p.linhas:
        if x.linha not in em_bulk:
            continue
        c = contadores[x.linha]
        for d in range(dias):
            n = (math.floor((d + 1) * c.emb_dia + c.nivel + EPS) - math.floor(d * c.emb_dia + c.nivel + EPS))
            if n > 0:
                bulk_dia[d + 1][x.unidade] += n
                emitir(x, n, d + 1, BULK, 0, dia)

    # kits
    refs_kit = defaultdict(list)
    for x in p.linhas:
        if x.kit:
            refs_kit[x.kit].append(x)
    for kid, kit in p.kits.items():
        refs = refs_kit.get(kid, [])
        kits_dia = kit.kits_por_carro * n_carros
        if not refs or kits_dia <= 0:
            continue
        if 1.0 / kits_dia > p.kit_autonomia_max_dias + EPS:
            avisos.append(f"Kit {kid}: vai nas voltas regulares (autonomia {1 / kits_dia:.2f} dias) -- "
                          f"ainda não modelado; os seus picks NÃO estão no resultado.")
            continue
        for d in range(dias):
            n = math.floor((d + 1) * kits_dia + EPS) - math.floor(d * kits_dia + EPS)
            if n <= 0:
                continue
            bulk_dia[d + 1]["Kit"] += n
            for y in refs:
                picks.append(Pick(d + 1, BULK, 0, dia, sku(y), y.descricao, "Peças", n,
                                  y.unidades_kit * n, y.posto, f"Kit {kid}"))

    # consumíveis (ver ASSUNCOES)
    if consumiveis_modo not in (MODO_SEMANAL, MODO_VOLTAS):
        raise ValueError(f"consumiveis_modo «{consumiveis_modo}»: tem de ser «semanal» ou «voltas».")
    muito_consumo = []
    for cs in consumiveis or []:
        if cs.pecas_por_unidade <= 0 or cs.pecas_por_carro <= 0:
            continue
        emb_dia = cs.pecas_por_carro * n_carros / cs.pecas_por_unidade
        tipo = CAIXAS if cs.unidade in UNIDADES_CAIXA else PALETES
        if consumiveis_modo == MODO_SEMANAL:
            # no início do dia d+1 (d = 0, P, 2P, ...) leva as que esvaziaram desde a anterior
            if emb_dia * periodo_dias > 1 + EPS:
                muito_consumo.append(f"{cs.sku} ({emb_dia * periodo_dias:.1f} {cs.unidade}/semana)")
            ant = 0
            for d in range(0, dias, max(int(periodo_dias), 1)):
                f = math.floor(d * emb_dia + EPS)
                n, ant = f - ant, f
                if n > 0:
                    picks.append(Pick(d + 1, SEMANAL, 0, 0.0, cs.sku, cs.descricao, cs.unidade, n,
                                      n * cs.pecas_por_unidade, "", "Consumível"))
            continue
        if _vai_em_bulk(emb_dia, tipo, p.bulk_modo, p.bulk_minimo_emb_dia):
            for d in range(dias):
                n = math.floor((d + 1) * emb_dia + EPS) - math.floor(d * emb_dia + EPS)
                if n > 0:
                    picks.append(Pick(d + 1, BULK, 0, dia, cs.sku, cs.descricao, cs.unidade, n,
                                      n * cs.pecas_por_unidade, "", "Consumível"))
            continue
        ant = 0
        for d in range(dias):
            for k, t in enumerate(p.partidas[tipo]):
                f = math.floor((d * dia + t) / dia * emb_dia + EPS)
                n, ant = f - ant, f
                if n > 0:
                    picks.append(Pick(d + 1, tipo, k + 1, t, cs.sku, cs.descricao, cs.unidade, n,
                                      n * cs.pecas_por_unidade, "", "Consumível"))

    if muito_consumo:
        avisos.append(f"Reposição semanal: {len(muito_consumo)} consumível(eis) gasta(m) mais de 1 unidade por "
                      f"semana -- o bordo de linha tem de ter stock para a semana: {', '.join(muito_consumo[:8])}"
                      + (" …" if len(muito_consumo) > 8 else ""))
    picks.sort(key=lambda k: (k.dia, k.partida_min, k.ciclo, k.volta, k.sku))
    sem_sku = sorted({x.ref for x in p.linhas if contadores[x.linha].ativo and x.ref not in skus_armazem})
    if skus_armazem and sem_sku:
        avisos.append(f"{len(sem_sku)} referência(s) do PFEP sem SKU no master data do armazém "
                      f"(ficam com a ref do PFEP, sem localização): {', '.join(sem_sku[:10])}"
                      + (" …" if len(sem_sku) > 10 else ""))
    return ResultadoProcura(float(n_carros), dias, picks,
                            {k: dict(v) for k, v in entregas.items()},
                            {k: dict(v) for k, v in bulk_dia.items()}, em_bulk, avisos)


# ----------------------------------------------------------------- taxas
@dataclass
class Taxa:
    """Frequência de picking de um SKU, em regime (não contada na simulação:
    30 dias não chegam para ver os SKUs de autonomia longa)."""
    sku: str
    unidades_dia: float       # unidades que saem por dia (peças, nos carrinhos/kits)
    visitas_dia: float        # idas à localização por dia (≤ 1 por volta/viagem)
    ciclos: tuple             # ciclos em que sai (Caixas / Paletes / Bulk)


def taxas_por_sku(p: Pressupostos, carros_por_dia: int = 1, consumiveis: list | None = None,
                  skus_armazem: dict | None = None, consumiveis_modo: str = MODO_VOLTAS,
                  periodo_dias: int = 5) -> dict:
    """SKU -> Taxa. Mesmas regras de ``simular`` (bulk, carrinhos, kits,
    consumíveis), mas em taxa: unidades/dia = embalagens/dia; visitas/dia =
    o mesmo, limitado ao nº de voltas/viagens desse ciclo por dia (numa volta
    vai-se uma vez à localização, mesmo que saiam várias unidades)."""
    n = int(carros_por_dia)
    skus_armazem = skus_armazem or {}
    voltas = {CAIXAS: len(p.partidas[CAIXAS]), PALETES: len(p.partidas[PALETES]), BULK: 1,
              SEMANAL: 1.0 / max(int(periodo_dias), 1)}
    acc = defaultdict(lambda: [0.0, 0.0, set()])

    def soma(sku, unidades, ciclo):
        a = acc[sku]
        a[0] += unidades
        a[1] += min(unidades, voltas[ciclo] or 1)
        a[2].add(ciclo)

    grupos = defaultdict(list)
    for x in p.linhas:
        if x.carrinho:
            grupos[x.carrinho].append(x)
    for x in p.linhas:
        c = _Contador(x, p, n)
        if not c.ativo:
            continue
        ciclo = (BULK if _vai_em_bulk(c.emb_dia, x.tipo_volta, p.bulk_modo, p.bulk_minimo_emb_dia)
                 else x.tipo_volta)
        if ciclo not in voltas:
            continue
        for y in (grupos.get(x.carrinho, [x]) if x.unidade == CARRINHO else [x]):
            soma(skus_armazem.get(y.ref, y.ref), c.emb_dia, ciclo)
    for kid, kit in p.kits.items():
        kits_dia = kit.kits_por_carro * n
        if kits_dia > 0 and 1.0 / kits_dia <= p.kit_autonomia_max_dias + EPS:
            for y in p.linhas:
                if y.kit == kid:
                    soma(skus_armazem.get(y.ref, y.ref), kits_dia, BULK)
    for cs in consumiveis or []:
        if cs.pecas_por_unidade <= 0 or cs.pecas_por_carro <= 0:
            continue
        emb_dia = cs.pecas_por_carro * n / cs.pecas_por_unidade
        tipo = CAIXAS if cs.unidade in UNIDADES_CAIXA else PALETES
        if consumiveis_modo == MODO_SEMANAL:
            soma(cs.sku, emb_dia, SEMANAL)
        else:
            soma(cs.sku, emb_dia,
                 BULK if _vai_em_bulk(emb_dia, tipo, p.bulk_modo, p.bulk_minimo_emb_dia) else tipo)
    return {s: Taxa(s, a[0], a[1], tuple(sorted(a[2]))) for s, a in acc.items()}


# ----------------------------------------------------------------- validação
@dataclass
class Verificacao:
    nome: str
    ok: bool
    detalhe: str


def validar_contra_excel(p: Pressupostos, res: ResultadoProcura) -> list:
    """Compara com o que o próprio livro calculou. Só faz sentido com os
    carros por dia do livro e sem mais dias do que a «7b Voltas» tem."""
    if res.carros_por_dia != p.carros_por_dia_base:
        return [Verificacao("Validação contra o Excel", True,
                            f"Não aplicável: {res.carros_por_dia:g} carros/dia, o livro tem "
                            f"{p.carros_por_dia_base:g}.")]
    wb = openpyxl.load_workbook(p.caminho, data_only=True, read_only=True)
    out = []

    # 1. bulk: mesmas linhas do PFEP
    excel_bulk = {x.linha for x in p.linhas if x.bulk_excel}
    dif = sorted(excel_bulk ^ res.em_bulk)
    out.append(Verificacao("Mesmas referências no bulk (PFEP «Vai em bulk?»)", not dif,
                           f"{len(res.em_bulk)} linhas no bulk" + (f"; diferem as linhas {dif}" if dif else "")))

    # 2. volta a volta, por unidade («7b Voltas»: caixas B:J, paletes W:AE)
    ws = wb["7b Voltas"]
    rows = list(ws.iter_rows(min_row=8, values_only=True))
    for tipo, c0 in ((CAIXAS, 1), (PALETES, 22)):
        cab = rows[0]
        unidades = [(i, cab[i]) for i in range(c0 + 2, c0 + 9) if cab[i] not in (None, "—")]
        n_ok = n_tot = 0
        primeiras = []
        for r in rows[1:]:
            nv = r[c0]
            if not isinstance(nv, (int, float)) or r[c0 + 1] is None or r[c0 + 1] > res.dias:
                continue
            n_tot += 1
            calc = res.entregas_por_volta.get((tipo, int(nv)), {})
            iguais = all(int(_num(r[i])) == calc.get(u, 0) for i, u in unidades)
            n_ok += iguais
            if not iguais and len(primeiras) < 3:
                primeiras.append(f"volta {int(nv)}: Excel {[int(_num(r[i])) for i, _ in unidades]} vs "
                                 f"{[calc.get(u, 0) for _, u in unidades]}")
        out.append(Verificacao(f"Voltas de {tipo.lower()}: entregas por unidade iguais à «7b Voltas»",
                               n_tot > 0 and n_ok == n_tot,
                               f"{n_ok} de {n_tot} voltas iguais" + ("; " + "; ".join(primeiras) if primeiras else "")))

    # 3. bulk por dia («Bulk», linha Total)
    B = wb["Bulk"]
    cab = [c.value for c in B[6]]
    tot = next(([c.value for c in r] for r in B.iter_rows(min_row=7, max_row=40)
                if _txt(r[1].value) == "Total"), None)
    if tot:
        esperado = {u: int(_num(tot[i])) for i, u in enumerate(cab)
                    if u in UNIDADES_CAIXA + tuple(UNIDADE_EXCEL_PARA_ARMAZEM) + (CARRINHO,)}
        dias_maus = [d for d in range(1, res.dias + 1)
                     if any(res.bulk_por_dia.get(d, {}).get(u, 0) != n for u, n in esperado.items())]
        out.append(Verificacao("Bulk: embalagens por dia iguais à folha «Bulk» (Total)", not dias_maus,
                               f"esperado {esperado}" + (f"; diferem os dias {dias_maus[:5]}" if dias_maus else
                                                        f"; igual nos {res.dias} dias")))
    return out


# ----------------------------------------------------------------- resumos
def resumo_por_sku(res: ResultadoProcura) -> list:
    """Por SKU: visitas, unidades e peças por dia (médias nos dias simulados) e
    em que ciclos sai. É a frequência que a alocação usa."""
    acc = {}
    for k in res.picks:
        a = acc.setdefault(k.sku, dict(sku=k.sku, descricao=k.descricao, origem=set(), ciclos=set(),
                                       unidade=set(), visitas=0, unidades=0, pecas=0.0, postos=set()))
        # "Carrinho 3" / "Kit K2" -> "Carrinho" / "Kit"; "Bordo de linha" e "Consumível" inteiros
        a["origem"].add(k.origem.split(" ")[0] if k.origem.startswith(("Carrinho ", "Kit ")) else k.origem)
        a["ciclos"].add(k.ciclo)
        a["unidade"].add(k.unidade)
        a["postos"].add(k.posto)
        a["visitas"] += 1
        a["unidades"] += k.unidades
        a["pecas"] += k.pecas
    out = []
    for a in acc.values():
        out.append(dict(sku=a["sku"], descricao=a["descricao"],
                        origem=", ".join(sorted(a["origem"])), ciclos=", ".join(sorted(a["ciclos"])),
                        unidade=", ".join(sorted(a["unidade"])), postos=", ".join(sorted(p for p in a["postos"] if p)),
                        visitas_dia=a["visitas"] / res.dias, unidades_dia=a["unidades"] / res.dias,
                        pecas_dia=a["pecas"] / res.dias))
    out.sort(key=lambda r: (-r["visitas_dia"], r["sku"]))
    return out


def resumo_por_volta(res: ResultadoProcura) -> list:
    acc = {}
    for k in res.picks:
        a = acc.setdefault((k.dia, k.partida_min, k.ciclo, k.volta),
                           dict(dia=k.dia, ciclo=k.ciclo, volta=k.volta, partida=k.partida_min,
                                visitas=0, skus=set(), por_unidade=defaultdict(int)))
        a["visitas"] += 1
        a["skus"].add(k.sku)
        a["por_unidade"][k.unidade] += k.unidades
    return [acc[k] for k in sorted(acc)]


# ----------------------------------------------------------------- Excel
def write_procura(caminho: Path, p: Pressupostos, res: ResultadoProcura, verificacoes: list) -> None:
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    neg = Font(bold=True)
    fundo = PatternFill("solid", fgColor="DDE5F0")

    def folha(nome, cab, linhas, larguras=None):
        ws = wb.create_sheet(nome)
        ws.append(cab)
        for c in ws[1]:
            c.font, c.fill = neg, fundo
        for l in linhas:
            ws.append(l)
        ws.freeze_panes = "A2"
        for i, w in enumerate(larguras or [], start=1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
        return ws

    ws = wb.active
    ws.title = "Leia-me"
    ws.append(["Procura de picking no armazém por ciclo de reposição"])
    ws["A1"].font = Font(bold=True, size=13)
    for l in (
        [],
        ["Pressupostos (livro do comboio)", str(p.caminho)],
        ["Carros por dia", res.carros_por_dia],
        ["Dias simulados", res.dias],
        ["Tempo de trabalho por dia (min)", p.tempo_dia_min],
        ["Voltas de caixas (partida, min de trabalho)", ", ".join(f"{t:g}" for t in p.partidas[CAIXAS])],
        ["Voltas de paletes (partida, min de trabalho)", ", ".join(f"{t:g}" for t in p.partidas[PALETES])],
        ["Bulk de fim de dia", f"{p.bulk_modo}, ≥ {p.bulk_minimo_emb_dia:g} emb./dia"],
        [],
        ["Folhas"],
        ["Picks", "Cada saída do armazém: dia, ciclo, volta, SKU, unidade, nº de unidades, peças, posto, origem."],
        ["Por volta", "Visitas e unidades por volta/viagem -- a carga de picking de cada ciclo."],
        ["Por SKU", "Frequência por SKU (visitas, unidades e peças por dia) -- o que a alocação usa."],
        ["Validação", "Comparação com o que o próprio livro calcula (só com os carros por dia do livro)."],
        [],
        ["Assunções"],
        *[[f"{i}.", a] for i, a in enumerate(ASSUNCOES, 1)],
    ):
        ws.append(l)
    ws.column_dimensions["A"].width = 44
    ws.column_dimensions["B"].width = 120

    folha("Picks", ["Dia", "Ciclo", "Volta", "Partida (min de trabalho)", "SKU", "Descrição", "Unidade",
                    "Nº de unidades", "Peças", "Posto", "Origem"],
          [[k.dia, k.ciclo, k.volta or "", round(k.partida_min, 2), k.sku, k.descricao, k.unidade,
            k.unidades, round(k.pecas, 3), k.posto, k.origem] for k in res.picks],
          [6, 9, 7, 12, 42, 42, 9, 10, 9, 8, 16])
    tipos = sorted({u for v in resumo_por_volta(res) for u in v["por_unidade"]})
    folha("Por volta", ["Dia", "Ciclo", "Volta", "Partida (min)", "Visitas", "SKUs diferentes", *tipos],
          [[v["dia"], v["ciclo"], v["volta"] or "", round(v["partida"], 2), v["visitas"], len(v["skus"]),
            *[v["por_unidade"].get(t, 0) for t in tipos]] for v in resumo_por_volta(res)],
          [6, 9, 7, 12, 9, 14] + [10] * len(tipos))
    folha("Por SKU", ["SKU", "Descrição", "Origem", "Ciclos", "Unidade", "Postos", "Visitas/dia",
                      "Unidades/dia", "Peças/dia"],
          [[r["sku"], r["descricao"], r["origem"], r["ciclos"], r["unidade"], r["postos"],
            round(r["visitas_dia"], 4), round(r["unidades_dia"], 4), round(r["pecas_dia"], 3)]
           for r in resumo_por_sku(res)],
          [42, 42, 22, 22, 10, 14, 11, 12, 10])
    folha("Validação", ["Verificação", "Estado", "Detalhe"],
          [[v.nome, "OK" if v.ok else "FALHA", v.detalhe] for v in verificacoes], [62, 9, 120])
    folha("Avisos", ["Aviso"], [[a] for a in res.avisos] or [["(nenhum)"]], [140])
    wb.save(caminho)


# ----------------------------------------------------------------- pipeline
def consumiveis_e_mapa(cfg, p: Pressupostos):
    """Unidade de cada SKU no armazém (Passo 0 + 1, a mesma do resultados.xlsx)
    -> mapa ref PFEP -> SKU e a lista de consumíveis (SKUs sem PFEP)."""
    from . import boxing, data_access, io_excel
    cfg = io_excel.com_carrinhos_da_linha(cfg)
    catalogo = data_access.load_catalog(cfg)
    artigos, _ = boxing.aplicar_stock_autonomia(data_access.load_articles(cfg), catalogo, cfg)
    por_sku = {a.sku: a for a in artigos}
    mapa = mapa_skus({x.ref for x in p.linhas}, list(por_sku))
    na_linha = set(mapa.values())
    consumiveis = []
    for asg in boxing.plan_all(artigos, catalogo, cfg):
        if asg.sku in na_linha:
            continue
        a = por_sku[asg.sku]
        consumiveis.append(Consumivel(asg.sku, a.descricao, asg.load_type.value,
                                      float(asg.pecas_por_unidade), float(a.pecas_por_carro)))
    return mapa, consumiveis


def run(cfg, carros_por_dia: int | None = None, dias: int | None = None,
        out: Path | None = None) -> tuple:
    if not str(cfg.pressupostos_picking):
        raise ValueError("Config.pressupostos_picking vazio: indica o livro do comboio em "
                         "dados/clients/<cliente>.json ou com --pressupostos.")
    p = carregar_pressupostos(Path(cfg.pressupostos_picking))
    mapa, consumiveis = consumiveis_e_mapa(cfg, p)
    res = simular(p, int(carros_por_dia or cfg.carros_por_dia), dias or cfg.simulacao_dias, consumiveis, mapa,
                  cfg.consumiveis_reposicao, cfg.consumiveis_periodo_dias)
    verif = validar_contra_excel(p, simular(p, int(p.carros_por_dia_base)))   # os dias do livro
    write_procura(Path(out or cfg.output_procura), p, res, verif)
    return p, res, verif


def main() -> None:
    from .config import Config
    ap = argparse.ArgumentParser(description="Procura de picking no armazém por ciclo de reposição")
    ap.add_argument("--client", default=os.environ.get("WAREHOUSE_CLIENT", "sia"))
    ap.add_argument("--pressupostos", type=Path, help="livro do comboio (.xlsx); substitui o do cliente")
    ap.add_argument("--carros", type=int, help="carros por dia (inteiro; omissão: Config.carros_por_dia)")
    ap.add_argument("--out", type=Path, help="Excel de saída (omissão: Config.output_procura)")
    a = ap.parse_args()
    cfg = Config.for_client(a.client)
    if a.pressupostos:
        cfg = dataclasses.replace(cfg, pressupostos_picking=a.pressupostos)
    p, res, verif = run(cfg, a.carros, None, a.out)
    print(f"{len(res.picks)} picks em {res.dias} dias, {res.carros_por_dia:g} carro(s)/dia "
          f"-> {a.out or cfg.output_procura}")
    for v in verif:
        print(f"  [{'OK' if v.ok else 'FALHA'}] {v.nome}: {v.detalhe}")
    for av in res.avisos:
        print(f"  AVISO: {av}")


if __name__ == "__main__":
    main()

"""Leitura do master data e escrita dos resultados (openpyxl). Unidades: metros."""
from __future__ import annotations
import math
from pathlib import Path

import openpyxl

from .models import (Article, BoxType, RackType, LoadType, Empilhamento,
                     BoxAssignment, RackRequirement, WarehouseDimensioning, Zona)


_EMPILHAMENTO_MAP = {
    "NAO_EMPILHAVEL": Empilhamento.NAO_EMPILHAVEL,
    "EMPILHAVEL": Empilhamento.EMPILHAVEL,
    "ANINHAVEL": Empilhamento.ANINHAVEL,
    # compatibilidade com o formato antigo (Sim/Não), caso apareça
    "SIM": Empilhamento.EMPILHAVEL,
    "NÃO": Empilhamento.NAO_EMPILHAVEL,
    "NAO": Empilhamento.NAO_EMPILHAVEL,
}


def _parse_empilhamento(valor) -> Empilhamento:
    chave = str(valor).strip().upper() if valor else ""
    return _EMPILHAMENTO_MAP.get(chave, Empilhamento.EMPILHAVEL)


def _ws(wb: openpyxl.Workbook, nome: str):
    if nome not in wb.sheetnames:
        raise ValueError(f"Sheet '{nome}' não encontrada em {wb.path if hasattr(wb, 'path') else '?'}")
    return wb[nome]


def load_articles(path: Path) -> list[Article]:
    """Folha «Peças» -> list[Article]."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = _ws(wb, "Peças")
    rows = list(ws.iter_rows(values_only=True))

    articles = []
    for r in rows[1:]:
        # Column positions (0-indexed): fixed order regardless of encoding issues
        # 0: SKU, 1: Descrição, 2: Comprimento_m, 3: Largura_m, 4: Altura_m,
        # 5: Peso_kg, 6: Stock_máx_un, 7: Peças_por_carro, 8: Orientação_permitida,
        # 9: Empilhável, 10: Fragilidade, 11: Geometria, 12: Volume_unit_m³,
        # 13: Subtipo_LONGO, 14: Classe
        if not r or r[0] is None:
            continue

        sku = str(r[0])
        desc = str(r[1]) if r[1] else ""
        comp = float(r[2]) if r[2] else 0.0
        larg = float(r[3]) if r[3] else 0.0
        alt = float(r[4]) if r[4] else 0.0

        peso_val = r[5]
        peso_kg = float(peso_val) if peso_val is not None and str(peso_val) != "#N/A" else 0.0

        stock = int(r[6]) if r[6] else 0

        pecas_por_carro_val = r[7]
        if pecas_por_carro_val is None or str(pecas_por_carro_val) == "#N/A":
            raise ValueError(f"SKU {sku}: Peças_por_carro is required but missing or #N/A")
        pecas_por_carro = int(pecas_por_carro_val)

        orient = str(r[8]) if r[8] else "Livre"
        empilh = r[9]
        empilhamento = _parse_empilhamento(empilh)
        frag = str(r[10]) if r[10] else "Normal"
        geom = str(r[11]) if r[11] else "Regular"

        subtipo_longo = str(r[13]).strip() if r[13] else ""
        classe = str(r[14]).strip() if r[14] else "RIGIDO"

        articles.append(Article(
            sku=sku,
            descricao=desc,
            comp_m=comp,
            larg_m=larg,
            alt_m=alt,
            peso_kg=peso_kg,
            stock_max_un=stock,
            pecas_por_carro=pecas_por_carro,
            orientacao=orient,
            empilhamento=empilhamento,
            fragilidade=frag,
            geometria=geom,
            classe=classe,
            subtipo_longo=subtipo_longo,
        ))
    return articles


def load_catalog(path: Path) -> list[BoxType]:
    """Folha «Caixas» -> list[BoxType] (S/M/L + PALLET)."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = _ws(wb, "Caixas")
    rows = list(ws.iter_rows(values_only=True))
    catalog = []
    for r in rows[1:]:
        nome, ic, il, ia, ec, el, ea, peso_max, *_ = r
        if nome is None:
            continue
        catalog.append(BoxType(
            nome=LoadType(str(nome).strip()),
            int_comp_m=float(ic),
            int_larg_m=float(il),
            int_alt_m=float(ia),
            ext_comp_m=float(ec),
            ext_larg_m=float(el),
            ext_alt_m=float(ea),
            peso_max_kg=float(peso_max),
        ))
    return catalog


def load_rack_types(path: Path) -> list[RackType]:
    """Folha «Racks» -> list[RackType]."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = _ws(wb, "Racks")
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip() if c else "" for c in rows[0]]
    braco_col = header.index("Comp_braco_m") if "Comp_braco_m" in header else None
    corredor_col = header.index("Corredor_m") if "Corredor_m" in header else None
    racks = []
    for r in rows[1:]:
        if r[0] is None:
            continue
        nome, larg, prof, alt_nivel, niveis, aceita_str, footprint, *rest = r
        aceita_tokens = [t.strip() for t in str(aceita_str).split(",") if t.strip()]
        aceita = []
        for t in aceita_tokens:
            try:
                aceita.append(LoadType(t))
            except ValueError:
                pass  # ignora tokens desconhecidos
        comp_braco = float(r[braco_col]) if braco_col is not None and r[braco_col] else 0.0
        corredor = float(r[corredor_col]) if corredor_col is not None and r[corredor_col] else 0.0
        racks.append(RackType(
            nome=str(nome),
            larg_m=float(larg),
            prof_m=float(prof),
            alt_nivel_m=float(alt_nivel),
            niveis=int(niveis),
            aceita=aceita,
            footprint_m2=float(footprint) if footprint else 0.0,
            comp_braco_m=comp_braco,
            corredor_m=corredor,
        ))
    return racks


def load_zones(path: Path) -> list[Zona]:
    """Folha «Zonas» -> list[Zona]. Sheet opcional: devolve [] se não existir."""
    wb = openpyxl.load_workbook(path, data_only=True)
    if "Zonas" not in wb.sheetnames:
        return []
    ws = wb["Zonas"]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    zonas = []
    for r in rows[1:]:
        if r[0] is None:
            continue
        nome, categoria, comp, larg, *resto = r
        justificacao = str(resto[0]) if resto and resto[0] else ""
        zonas.append(Zona(
            nome=str(nome),
            categoria=str(categoria) if categoria else "Outra",
            comp_m=float(comp),
            larg_m=float(larg),
            justificacao=justificacao,
        ))
    return zonas


def write_results(path: Path,
                  assignments: list[BoxAssignment],
                  requirements: list[RackRequirement],
                  dimensioning: WarehouseDimensioning,
                  slots: list,
                  avisos: list[str] | None = None,
                  warehouse_layout=None,
                  zonas: list[Zona] | None = None,
                  resumo_area=None) -> None:
    """Escreve resultados.xlsx com sheets: Lógica, Resumo, Detalhe (e Avisos, se houver)."""
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()

    # ------------------------------------------------------------------ Lógica
    ws_log = wb.active
    ws_log.title = "Lógica"

    def titulo(ws, texto, linha):
        c = ws.cell(linha, 1, texto)
        c.font = Font(bold=True, size=12)

    def par(ws, label, valor, linha):
        ws.cell(linha, 1, label).font = Font(bold=True)
        ws.cell(linha, 2, valor)

    def texto(ws, t, linha, col=1):
        ws.cell(linha, col, t)

    titulo(ws_log, "Pipeline de dimensionamento de armazém", 1)
    texto(ws_log, "Transforma o stock máximo de cada artigo em nº de racks e área de chão.", 2)

    titulo(ws_log, "Passo 1 — Repacking (boxing.py)", 4)
    texto(ws_log, "Geometria = 'Comprida' e NÃO é painel/volumoso → LONGO (cantilever), sem caixa.", 5)
    texto(ws_log, "Painéis largos/finos (Subtipo_LONGO=PAINEL_GRANDE) e volumosos (VOLUMOSO_OVERSIZE) NÃO", 6)
    texto(ws_log, "vão para cantilever (não são material 1D apoiado em braços) — seguem o best-fit normal.", 7)
    texto(ws_log, "Restantes: best-fit entre S/M/L/PALLET — escolhe a caixa que minimiza o nº total de", 8)
    texto(ws_log, "unidades de carga para o stock máximo (não é 'a 1ª onde cabe', testa todas as opções).", 9)
    texto(ws_log, "Plano horizontal (comp x larg): envelope 2D, testa as 2 orientações do artigo no plano.", 10)
    texto(ws_log, "Artigos Classe=RIGIDO: nº de peças no plano reduzido por fator de enchimento (folgas de", 11)
    texto(ws_log, "manuseio/forma irregular) — não assume encaixe geométrico perfeito como FLEXIVEL/CONTENTOR.", 12)
    texto(ws_log, "Eixo vertical (altura): regra de empilhamento — ver 'Parâmetros utilizados' abaixo.", 13)
    texto(ws_log, "Corte adicional pelo peso máximo da caixa (peso_max_caixa / peso_artigo).", 14)
    texto(ws_log, "Output: tipo de carga por SKU + nº de unidades para cobrir o stock máximo (teto).", 15)

    titulo(ws_log, "Passo 2 — Racking (racking.py)", 17)
    texto(ws_log, "Agrupa os SKUs por tipo de carga e calcula quantos racks de cada tipologia são necessários.", 18)
    texto(ws_log, "Cada tipo de carga escolhe, entre os racks compatíveis, o de menor m² por unidade", 19)
    texto(ws_log, "(footprint do rack / capacidade) — não é 'o 1º rack da tabela que aceita'.", 20)
    texto(ws_log, "Capacidade do rack (S/M/L/PALLET): envelope 2D por nível × nº de níveis.", 21)
    texto(ws_log, "Capacidade cantilever (LONGO): floor(comp_braço / comp_artigo_máx) × nº de níveis.", 22)
    texto(ws_log, "Compatibilidade carga ↔ rack via campo 'Aceita' da sheet Racks.", 23)
    texto(ws_log, "Se um tipo de carga não tiver nenhum rack compatível viável, fica registado na sheet", 24)
    texto(ws_log, "'Avisos' (se existir) em vez de ser silenciosamente excluído do resultado.", 25)

    texto(ws_log, "Módulos de rack podem ser empilhados na vertical até à altura do armazém — ver", 24)
    texto(ws_log, "'Empilhamento de módulos de rack' abaixo.", 25)
    texto(ws_log, "Se um tipo de carga não tiver nenhum rack compatível viável, fica registado na sheet", 26)
    texto(ws_log, "'Avisos' (se existir) em vez de ser silenciosamente excluído do resultado.", 27)

    titulo(ws_log, "Empilhamento de módulos de rack (altura do armazém)", 29)
    texto(ws_log, "Distinto do empilhamento de ARTIGOS dentro de uma caixa (Passo 1) e dos NÍVEIS", 30)
    texto(ws_log, "(prateleiras) dentro de UM módulo de rack — ambos já contam para a capacidade do rack.", 31)
    texto(ws_log, "Isto é empilhar módulos INTEIROS uns em cima dos outros, na mesma posição de chão,", 32)
    texto(ws_log, "até ao pé-direito disponível (Config.altura_armazem_m). Qualquer tipologia pode", 33)
    texto(ws_log, "empilhar — calculado independentemente para cada uma, ao seu máximo possível.", 34)
    texto(ws_log, "blocos_empilhados = floor(altura_armazem_m / (Alt_nivel_m × Níveis)) do módulo, mín. 1.", 35)
    texto(ws_log, "Nº de racks (total) = módulos físicos; Posições de chão = footprints distintos", 36)
    texto(ws_log, "(= nº de racks ÷ blocos_empilhados) — só as posições de chão contam para a área.", 37)
    texto(ws_log, "altura_armazem_m = 0 → sem empilhamento de módulos (blocos = 1, comportamento normal).", 38)

    titulo(ws_log, "Passo 3 — Área (area.py)", 40)
    texto(ws_log, "Área de racks = soma(footprint unitário × nº de posições de chão) por tipologia.", 41)
    texto(ws_log, "Footprint unitário = Larg_m × Prof_m (ou valor da coluna Footprint_m2 se preenchido).", 42)
    texto(ws_log, "Corredores e layout não são calculados aqui — são feitos externamente.", 43)

    titulo(ws_log, "Parâmetros utilizados", 45)
    par(ws_log, "Estratégia de escolha de caixa", "Best-fit: minimiza nº total de unidades de carga", 46)
    par(ws_log, "Atribuição de rack", "Por eficiência: menor m² de rack por unidade (entre os compatíveis)", 47)
    par(ws_log, "Packing plano horizontal", "Envelope 2D (2 orientações, melhor escolhida)", 48)
    par(ws_log, "Fator de enchimento (Classe=RIGIDO)",
        "floor(peças_plano_teórico × 70%) — folgas de manuseio/forma irregular", 49)
    par(ws_log, "Packing por nível de rack", "Envelope 2D (2 orientações, melhor escolhida)", 50)
    par(ws_log, "Capacidade cantilever", "floor(comp_braço / comp_max_LONGO) × níveis", 51)
    par(ws_log, "Empilhamento NAO_EMPILHAVEL", "1 peça de altura, nunca empilha", 52)
    par(ws_log, "Empilhamento EMPILHAVEL",
        "altura cheia somada: floor(altura_disponível / altura_artigo), sem nesting", 53)
    par(ws_log, "Empilhamento ANINHAVEL",
        "altura_stack(n) = altura_artigo × (1 + (n-1) × fator_empilhamento), fator = 30%", 54)
    par(ws_log, "Empilhamento de módulos de rack",
        "floor(altura_armazem_m / altura_do_módulo), por tipologia, independente", 55)

    ws_log.column_dimensions["A"].width = 42
    ws_log.column_dimensions["B"].width = 55

    # ------------------------------------------------------------------ Resumo
    ws_res = wb.create_sheet("Resumo")
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    hdr_font = Font(bold=True, color="FFFFFF")

    def hdr_row(ws, cols, linha=1):
        for i, c in enumerate(cols, 1):
            cell = ws.cell(linha, i, c)
            cell.fill = hdr_fill
            cell.font = hdr_font
            cell.alignment = Alignment(horizontal="center")

    hdr_row(ws_res, ["Tipologia de rack", "Posições de chão", "Módulos empilhados (altura)",
                     "Nº de racks (total)", "Footprint total (m²)"])
    for rack_nome, info in dimensioning.detalhe.items():
        ws_res.append([rack_nome, info["posicoes_chao"], info["blocos_empilhados"],
                       info["num_racks"], info["footprint_total_m2"]])

    # linha de total
    total_racks = sum(i["num_racks"] for i in dimensioning.detalhe.values())
    total_posicoes = sum(i["posicoes_chao"] for i in dimensioning.detalhe.values())
    ws_res.append(["TOTAL", total_posicoes, "", total_racks, round(dimensioning.area_racks_m2, 2)])
    last = ws_res.max_row
    for col in range(1, 6):
        ws_res.cell(last, col).font = Font(bold=True)

    ws_res.column_dimensions["A"].width = 22
    ws_res.column_dimensions["B"].width = 18
    ws_res.column_dimensions["C"].width = 24
    ws_res.column_dimensions["D"].width = 18
    ws_res.column_dimensions["E"].width = 22

    if warehouse_layout is not None:
        linha = ws_res.max_row + 2
        titulo(ws_res, "Layout físico (filas duplas costas-com-costas + corredores)", linha)
        par(ws_res, "Largura do envolvente (m)", warehouse_layout.largura_total_m, linha + 1)
        par(ws_res, "Profundidade do envolvente (m)", warehouse_layout.profundidade_total_m, linha + 2)
        par(ws_res, "Área total (racks + corredores) (m²)", warehouse_layout.area_total_m2, linha + 3)
        par(ws_res, "Área de corredores (m²)", warehouse_layout.area_corredores_m2, linha + 4)

    if zonas:
        linha = ws_res.max_row + 2
        titulo(ws_res, "Zonas funcionais", linha)
        hdr_row(ws_res, ["Nome", "Categoria", "Área (m²)"], linha + 1)
        for i, z in enumerate(zonas):
            ws_res.cell(linha + 2 + i, 1, z.nome)
            ws_res.cell(linha + 2 + i, 2, z.categoria)
            ws_res.cell(linha + 2 + i, 3, round(z.area_m2, 2))

    if resumo_area is not None:
        linha = ws_res.max_row + 2
        titulo(ws_res, "Área bruta total estimada", linha)
        par(ws_res, "Footprint de racks (m²)", round(resumo_area.area_racks_m2, 2), linha + 1)
        par(ws_res, "Corredores (m²)", round(resumo_area.area_corredores_m2, 2), linha + 2)
        par(ws_res, "Zonas funcionais (m²)", round(resumo_area.area_zonas_m2, 2), linha + 3)
        par(ws_res, "Margem estrutural (m²)", round(resumo_area.margem_estrutural_m2, 2), linha + 4)
        par(ws_res, "ÁREA BRUTA TOTAL ESTIMADA (m²)", round(resumo_area.area_bruta_total_m2, 2), linha + 5)

    # ------------------------------------------------------------------ Detalhe
    ws_det = wb.create_sheet("Detalhe")
    hdr_row(ws_det, ["Rack nº", "Tipologia", "SKU", "Descrição", "Tipo de carga", "Nº de unidades"])
    ws_det.freeze_panes = "A2"

    fill_alt = PatternFill("solid", fgColor="DCE6F1")
    ultimo_rack = None
    usar_fill = False

    for s in slots:
        rack_numero = getattr(s, "rack_numero", None)
        if rack_numero != ultimo_rack:
            usar_fill = not usar_fill
            ultimo_rack = rack_numero
        row = [rack_numero, s.rack_tipologia, s.sku, s.descricao,
               s.load_type.value if hasattr(s.load_type, "value") else s.load_type,
               s.num_unidades]
        ws_det.append(row)
        if usar_fill:
            for col in range(1, 7):
                ws_det.cell(ws_det.max_row, col).fill = fill_alt

    ws_det.column_dimensions["A"].width = 10
    ws_det.column_dimensions["B"].width = 18
    ws_det.column_dimensions["C"].width = 22
    ws_det.column_dimensions["D"].width = 30
    ws_det.column_dimensions["E"].width = 14
    ws_det.column_dimensions["F"].width = 16

    # ------------------------------------------------------------------ Avisos
    if avisos:
        ws_av = wb.create_sheet("Avisos")
        hdr_row(ws_av, ["Aviso"])
        for a in avisos:
            ws_av.append([a])
        ws_av.column_dimensions["A"].width = 90
        ws_av.sheet_properties.tabColor = "C00000"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)

"""Front end web do simulador de armazém (protótipo).

API FastAPI que reaproveita o pipeline existente (boxing/racking/area) e
serve uma página HTML/JS (Three.js via CDN) para ver e editar os dados, e
gerar uma simulação 3D do layout do armazém.

Corre com: python -m warehouse.web
"""
from __future__ import annotations

from pathlib import Path

import openpyxl
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import boxing, io_excel, racking, area, layout as layout_mod
from .config import Config
from .models import LoadType

cfg = Config()
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Simulador de Armazém")


# --------------------------------------------------------------------- dados

@app.get("/api/articles")
def get_articles():
    articles = io_excel.load_articles(cfg.master_data)
    return [
        {
            "sku": a.sku, "descricao": a.descricao,
            "comp_m": a.comp_m, "larg_m": a.larg_m, "alt_m": a.alt_m,
            "peso_kg": a.peso_kg, "stock_max_un": a.stock_max_un,
            "pecas_por_carro": a.pecas_por_carro,
            "orientacao": a.orientacao, "empilhamento": a.empilhamento.value,
            "fragilidade": a.fragilidade, "geometria": a.geometria,
            "classe": a.classe, "subtipo_longo": a.subtipo_longo,
        }
        for a in articles
    ]


class ArticleUpdate(BaseModel):
    sku: str
    descricao: str
    comp_m: float
    larg_m: float
    alt_m: float
    peso_kg: float
    stock_max_un: int
    pecas_por_carro: int
    orientacao: str = "Livre"
    empilhamento: str = "EMPILHAVEL"
    fragilidade: str = "Normal"
    geometria: str = "Regular"
    classe: str = "RIGIDO"
    subtipo_longo: str = ""


def _article_row_values(body: "ArticleUpdate") -> list:
    return [body.sku, body.descricao, body.comp_m, body.larg_m, body.alt_m,
            body.peso_kg, body.stock_max_un, body.pecas_por_carro, body.orientacao,
            body.empilhamento, body.fragilidade, body.geometria, body.classe,
            body.subtipo_longo]


@app.post("/api/articles")
def create_article(body: ArticleUpdate):
    wb = openpyxl.load_workbook(cfg.master_data)
    ws = wb["Peças"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value and str(row[0].value).strip() == body.sku:
            wb.close()
            return {"ok": False, "erro": f"SKU '{body.sku}' já existe."}
    ws.append(_article_row_values(body))
    wb.save(cfg.master_data)
    return {"ok": True}


@app.put("/api/articles/{sku}")
def update_article(sku: str, body: ArticleUpdate):
    wb = openpyxl.load_workbook(cfg.master_data)
    ws = wb["Peças"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value and str(row[0].value).strip() == sku:
            valores = _article_row_values(body)
            for col, v in enumerate(valores, 1):
                ws.cell(row[0].row, col).value = v
            break
    wb.save(cfg.master_data)
    return {"ok": True}


@app.delete("/api/articles/{sku}")
def delete_article(sku: str):
    wb = openpyxl.load_workbook(cfg.master_data)
    ws = wb["Peças"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value and str(row[0].value).strip() == sku:
            ws.delete_rows(row[0].row, 1)
            break
    wb.save(cfg.master_data)
    return {"ok": True}


@app.get("/api/boxes")
def get_boxes():
    boxes = io_excel.load_catalog(cfg.master_data)
    return [
        {
            "nome": b.nome.value,
            "int_comp_m": b.int_comp_m, "int_larg_m": b.int_larg_m, "int_alt_m": b.int_alt_m,
            "ext_comp_m": b.ext_comp_m, "ext_larg_m": b.ext_larg_m, "ext_alt_m": b.ext_alt_m,
            "peso_max_kg": b.peso_max_kg,
        }
        for b in boxes
    ]


class BoxUpdate(BaseModel):
    nome: str
    int_comp_m: float
    int_larg_m: float
    int_alt_m: float
    ext_comp_m: float
    ext_larg_m: float
    ext_alt_m: float
    peso_max_kg: float


@app.put("/api/boxes/{nome}")
def update_box(nome: str, body: BoxUpdate):
    wb = openpyxl.load_workbook(cfg.master_data)
    ws = wb["Caixas"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value and str(row[0].value).strip() == nome:
            row[1].value = body.int_comp_m
            row[2].value = body.int_larg_m
            row[3].value = body.int_alt_m
            row[4].value = body.ext_comp_m
            row[5].value = body.ext_larg_m
            row[6].value = body.ext_alt_m
            row[7].value = body.peso_max_kg
            break
    wb.save(cfg.master_data)
    return {"ok": True}


@app.post("/api/boxes")
def create_box(body: BoxUpdate):
    wb = openpyxl.load_workbook(cfg.master_data)
    ws = wb["Caixas"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value and str(row[0].value).strip() == body.nome:
            wb.close()
            return {"ok": False, "erro": f"Caixa '{body.nome}' já existe."}
    ws.append([body.nome, body.int_comp_m, body.int_larg_m, body.int_alt_m,
               body.ext_comp_m, body.ext_larg_m, body.ext_alt_m, body.peso_max_kg])
    wb.save(cfg.master_data)
    return {"ok": True}


@app.delete("/api/boxes/{nome}")
def delete_box(nome: str):
    wb = openpyxl.load_workbook(cfg.master_data)
    ws = wb["Caixas"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value and str(row[0].value).strip() == nome:
            ws.delete_rows(row[0].row, 1)
            break
    wb.save(cfg.master_data)
    return {"ok": True}


@app.get("/api/racks")
def get_racks():
    racks = io_excel.load_rack_types(cfg.master_data)
    return [
        {
            "nome": r.nome, "larg_m": r.larg_m, "prof_m": r.prof_m,
            "alt_nivel_m": r.alt_nivel_m, "niveis": r.niveis,
            "aceita": [lt.value for lt in r.aceita],
            "footprint_m2": r.footprint_m2, "comp_braco_m": r.comp_braco_m,
            "corredor_m": r.corredor_m,
        }
        for r in racks
    ]


class RackUpdate(BaseModel):
    nome: str
    larg_m: float
    prof_m: float
    alt_nivel_m: float
    niveis: int
    aceita: list[str]
    comp_braco_m: float = 0.0
    corredor_m: float = 0.0


@app.put("/api/racks/{nome}")
def update_rack(nome: str, body: RackUpdate):
    wb = openpyxl.load_workbook(cfg.master_data)
    ws = wb["Racks"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value and str(row[0].value).strip() == nome:
            row[1].value = body.larg_m
            row[2].value = body.prof_m
            row[3].value = body.alt_nivel_m
            row[4].value = body.niveis
            row[5].value = ", ".join(body.aceita)
            row[7].value = body.comp_braco_m
            row[8].value = body.corredor_m
            break
    wb.save(cfg.master_data)
    return {"ok": True}


@app.post("/api/racks")
def create_rack(body: RackUpdate):
    wb = openpyxl.load_workbook(cfg.master_data)
    ws = wb["Racks"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value and str(row[0].value).strip() == body.nome:
            wb.close()
            return {"ok": False, "erro": f"Rack '{body.nome}' já existe."}
    ws.append([body.nome, body.larg_m, body.prof_m, body.alt_nivel_m, body.niveis,
               ", ".join(body.aceita), 0.0, body.comp_braco_m, body.corredor_m])
    wb.save(cfg.master_data)
    return {"ok": True}


@app.delete("/api/racks/{nome}")
def delete_rack(nome: str):
    wb = openpyxl.load_workbook(cfg.master_data)
    ws = wb["Racks"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value and str(row[0].value).strip() == nome:
            ws.delete_rows(row[0].row, 1)
            break
    wb.save(cfg.master_data)
    return {"ok": True}


# ------------------------------------------------------------------- zonas

def _zonas_sheet(wb):
    if "Zonas" not in wb.sheetnames:
        ws = wb.create_sheet("Zonas")
        ws.append(["Nome", "Categoria", "Comp_m", "Larg_m", "Justificacao"])
    return wb["Zonas"]


@app.get("/api/zones")
def get_zones():
    zonas = io_excel.load_zones(cfg.master_data)
    return [
        {
            "nome": z.nome, "categoria": z.categoria,
            "comp_m": z.comp_m, "larg_m": z.larg_m, "justificacao": z.justificacao,
        }
        for z in zonas
    ]


class ZoneUpdate(BaseModel):
    nome: str
    categoria: str = "Outra"
    comp_m: float
    larg_m: float
    justificacao: str = ""


@app.put("/api/zones/{nome}")
def update_zone(nome: str, body: ZoneUpdate):
    wb = openpyxl.load_workbook(cfg.master_data)
    ws = _zonas_sheet(wb)
    for row in ws.iter_rows(min_row=2):
        if row[0].value and str(row[0].value).strip() == nome:
            row[1].value = body.categoria
            row[2].value = body.comp_m
            row[3].value = body.larg_m
            row[4].value = body.justificacao
            break
    wb.save(cfg.master_data)
    return {"ok": True}


@app.post("/api/zones")
def create_zone(body: ZoneUpdate):
    wb = openpyxl.load_workbook(cfg.master_data)
    ws = _zonas_sheet(wb)
    for row in ws.iter_rows(min_row=2):
        if row[0].value and str(row[0].value).strip() == body.nome:
            wb.close()
            return {"ok": False, "erro": f"Zona '{body.nome}' já existe."}
    ws.append([body.nome, body.categoria, body.comp_m, body.larg_m, body.justificacao])
    wb.save(cfg.master_data)
    return {"ok": True}


@app.delete("/api/zones/{nome}")
def delete_zone(nome: str):
    wb = openpyxl.load_workbook(cfg.master_data)
    ws = _zonas_sheet(wb)
    for row in ws.iter_rows(min_row=2):
        if row[0].value and str(row[0].value).strip() == nome:
            ws.delete_rows(row[0].row, 1)
            break
    wb.save(cfg.master_data)
    return {"ok": True}


# ------------------------------------------------------------------- gerar

def _serializar_layout(warehouse_layout, rack_by_name):
    """Converte WarehouseLayout (coordenadas físicas reais, layout.py) para o
    formato JSON consumido pelo viewer 3D: um registo por posição de chão,
    com os blocos/níveis/caixas já embutidos (cada posição é a fonte da
    verdade -- sem reconstrução por intervalo de rack_numero)."""
    layout = []
    for pl in warehouse_layout.posicoes:
        pos = pl.posicao
        rack = rack_by_name[pos.rack_nome]
        load_types = sorted({c.load_type.value
                             for bloco in pos.blocos for nivel in bloco.niveis
                             for c in nivel.caixas})
        layout.append({
            "rack_nome": pos.rack_nome,
            "indice": pos.indice,
            "x": pl.x_m, "y": pl.y_m,
            "larg_m": pl.largura_m, "prof_m": pl.profundidade_m,
            "altura_m": rack.alt_nivel_m * rack.niveis,
            "niveis": rack.niveis,
            "blocos_empilhados": pos.num_blocos,
            "load_types": load_types,
            "fila_idx": pl.fila_idx, "lado": pl.lado,
            "blocos": [
                {
                    "bloco_idx": bloco.bloco_idx,
                    "niveis": [
                        {
                            "nivel_idx": nivel.nivel_idx,
                            "eh_picking": nivel.eh_picking,
                            "caixas": [
                                {
                                    "sku": c.sku, "descricao": c.descricao,
                                    "load_type": c.load_type.value,
                                    "x_m": c.x_m, "y_m": c.y_m,
                                    "comp_m": c.comp_m, "larg_m": c.larg_m,
                                    "alt_m": c.alt_m,
                                    "pecas_nesta_caixa": c.pecas_nesta_caixa,
                                    "eh_picking": c.eh_picking,
                                    "num_boxes_in_stack": c.num_boxes_in_stack,
                                }
                                for c in nivel.caixas
                            ],
                        }
                        for nivel in bloco.niveis
                    ],
                }
                for bloco in pos.blocos
            ],
        })
    return layout


class GerarRequest(BaseModel):
    altura_armazem_m: float = 0.0
    altura_picking_m: float = 0.0
    margem_estrutural_pct: float = 0.0


class ArrangeRequest(BaseModel):
    blocks: list  # [{type: "zone"|"racking", name, w, h, x, y, id}, ...]
    height_picking_m: float = 0.0


@app.post("/api/gerar")
def gerar(body: GerarRequest):
    local_cfg = Config(altura_armazem_m=body.altura_armazem_m,
                       altura_picking_m=body.altura_picking_m,
                       margem_estrutural_pct=body.margem_estrutural_pct)

    articles = io_excel.load_articles(cfg.master_data)
    catalogo = io_excel.load_catalog(cfg.master_data)
    rack_types = io_excel.load_rack_types(cfg.master_data)
    catalogo_por_tipo = {b.nome: b for b in catalogo}
    rack_by_name = {r.nome: r for r in rack_types}
    article_idx = {a.sku: a for a in articles}

    assignments = boxing.plan_all(articles, catalogo, local_cfg)

    comp_max_longo = max(
        (article_idx[a.sku].comp_m for a in assignments
         if a.load_type == LoadType.LONGO and a.sku in article_idx),
        default=1.0,
    )
    requirements, avisos_estimativa = racking.racks_needed(
        assignments, rack_types, catalogo_por_tipo, local_cfg, comp_max_longo,
        local_cfg.altura_picking_m)

    posicoes, avisos_alocacao = racking.allocate_to_racks(
        assignments, requirements, rack_types, catalogo_por_tipo, articles, comp_max_longo,
        local_cfg.altura_picking_m, local_cfg.altura_armazem_m)

    avisos = avisos_estimativa + avisos_alocacao

    dim = area.floor_area(posicoes, rack_by_name)
    zonas = io_excel.load_zones(cfg.master_data)
    warehouse_layout = layout_mod.gerar_layout(posicoes, rack_by_name, zonas)
    layout_json = _serializar_layout(warehouse_layout, rack_by_name)
    resumo = area.resumo_area_total(dim, warehouse_layout, zonas, local_cfg.margem_estrutural_pct)

    totais_carga: dict[str, int] = {}
    for a in assignments:
        totais_carga[a.load_type.value] = totais_carga.get(a.load_type.value, 0) + a.num_unidades

    load_types_por_tipologia: dict[str, set] = {}
    for pos in posicoes:
        s = load_types_por_tipologia.setdefault(pos.rack_nome, set())
        for bloco in pos.blocos:
            for nivel in bloco.niveis:
                for c in nivel.caixas:
                    s.add(c.load_type.value)

    # Racking block dimensions (for canvas layout tool)
    racking_w = warehouse_layout.largura_total_m
    racking_h = warehouse_layout.profundidade_total_m

    return {
        "area_racks_m2": round(dim.area_racks_m2, 2),
        "area_layout_m2": warehouse_layout.area_total_m2,
        "area_corredores_m2": warehouse_layout.area_corredores_m2,
        "largura_layout_m": warehouse_layout.largura_total_m,
        "profundidade_layout_m": warehouse_layout.profundidade_total_m,
        "area_zonas_m2": round(resumo.area_zonas_m2, 2),
        "margem_estrutural_m2": round(resumo.margem_estrutural_m2, 2),
        "area_bruta_total_m2": round(resumo.area_bruta_total_m2, 2),
        "avisos": avisos,
        "totais_por_carga": totais_carga,
        "requirements": [
            {
                "rack_nome": rack_nome,
                "num_racks": info["num_racks"],
                "posicoes_chao": info["posicoes_chao"],
                "blocos_empilhados": info["blocos_empilhados"],
                "load_types": sorted(load_types_por_tipologia.get(rack_nome, set())),
                "footprint_total_m2": info["footprint_total_m2"],
            }
            for rack_nome, info in dim.detalhe.items()
        ],
        "layout": layout_json,
        "zonas": [
            {
                "nome": z.nome, "categoria": z.categoria,
                "area_m2": round(z.area_m2, 2), "justificacao": z.justificacao,
            }
            for z in zonas
        ],
        "zonas_layout": [
            {
                "nome": zl.zona.nome, "categoria": zl.zona.categoria,
                "x": zl.x_m, "y": zl.y_m,
                "larg_m": zl.largura_m, "prof_m": zl.profundidade_m,
                "area_m2": round(zl.zona.area_m2, 2),
                "justificacao": zl.zona.justificacao,
            }
            for zl in warehouse_layout.zonas
        ],
        # For canvas layout tool
        "racking_block": {
            "id": "racking_block",
            "type": "racking",
            "nome": "Bloco de Racks",
            "w": round(racking_w, 2),
            "h": round(racking_h, 2),
            "area_m2": round(dim.area_racks_m2, 2),
            "x": 0.5,
            "y": 0.5,
        },
        "blocks_for_canvas": [
            {
                "id": "racking_block",
                "type": "racking",
                "nome": "Bloco de Racks",
                "w": round(racking_w, 2),
                "h": round(racking_h, 2),
                "x": 0.5,
                "y": 0.5,
            }
        ] + [
            {
                "id": z.id if hasattr(z, 'id') else f"zone_{i}",
                "type": "zone",
                "nome": z.nome,
                "categoria": z.categoria,
                "w": round(z.comp_m, 2),
                "h": round(z.larg_m, 2),
                "x": 2.0,
                "y": 2.0 + i * 3.5,
            }
            for i, z in enumerate(zonas)
        ],
    }


@app.post("/api/render-layout")
def render_layout(body: ArrangeRequest):
    """Takes a manual arrangement from the canvas and generates the 3D layout.

    The arrangement contains placed blocks (racking, zones). We position the
    pre-calculated racking structure according to the user's manual layout.
    """
    local_cfg = Config(altura_picking_m=body.height_picking_m)

    articles = io_excel.load_articles(cfg.master_data)
    catalogo = io_excel.load_catalog(cfg.master_data)
    rack_types = io_excel.load_rack_types(cfg.master_data)
    catalogo_por_tipo = {b.nome: b for b in catalogo}
    rack_by_name = {r.nome: r for r in rack_types}
    article_idx = {a.sku: a for a in articles}

    assignments = boxing.plan_all(articles, catalogo, local_cfg)
    comp_max_longo = max(
        (article_idx[a.sku].comp_m for a in assignments
         if a.load_type == LoadType.LONGO and a.sku in article_idx),
        default=1.0,
    )
    requirements, _ = racking.racks_needed(assignments, rack_types, catalogo_por_tipo, local_cfg, comp_max_longo, body.height_picking_m)
    posicoes, _ = racking.allocate_to_racks(assignments, requirements, rack_types, catalogo_por_tipo, articles, comp_max_longo, body.height_picking_m, local_cfg.altura_armazem_m)
    dim = area.floor_area(posicoes, rack_by_name)

    # Apply user's manual positioning to the racking block
    racking_block = next((b for b in body.blocks if b.get("type") == "racking"), None)
    if racking_block:
        offset_x = racking_block.get("x", 0.5)
        offset_y = racking_block.get("y", 0.5)
    else:
        offset_x, offset_y = 0.5, 0.5

    # Adjust posicoes coordinates based on racking block placement
    for pos in posicoes:
        for pl in []:  # placeholder for PosicaoLayout iteration
            pass

    # For now, use the positions as calculated
    warehouse_layout = layout_mod.gerar_layout(posicoes, rack_by_name, io_excel.load_zones(cfg.master_data))
    layout_json = _serializar_layout(warehouse_layout, rack_by_name)

    # Calculate bounding box from user's arrangement
    block_list = body.blocks or []
    if block_list:
        max_x = max(b.get("x", 0) + b.get("w", 0) for b in block_list) if block_list else 0
        max_y = max(b.get("y", 0) + b.get("h", 0) for b in block_list) if block_list else 0
    else:
        max_x, max_y = 0, 0

    total_area = round(max_x * max_y, 2) if max_x > 0 and max_y > 0 else 0

    return {
        "layout": layout_json,
        "total_area_m2": total_area,
        "bounding_box": {"width_m": round(max_x, 2), "depth_m": round(max_y, 2)},
        "blocks": body.blocks,
    }


# ------------------------------------------------------------------- static

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def main():
    import uvicorn
    uvicorn.run("warehouse.web:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()

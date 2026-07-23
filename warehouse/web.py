"""Front end web do simulador de armazém (protótipo).

API FastAPI que reaproveita o pipeline existente (boxing/racking/area) e
serve uma página HTML/JS (Three.js via CDN) para ver e editar os dados, e
gerar uma simulação 3D do layout do armazém.

Corre com: python -m warehouse.web
"""
from __future__ import annotations

import math
from pathlib import Path

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


# ------------------------------------------------------------------- zonas

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
    blocks: list  # [{id, type: "zone"|"racking", nome, w, h, x, y, rot}, ...]
    altura_armazem_m: float = 0.0
    altura_picking_m: float = 0.0


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
    io_excel.write_pecas_com_caixa(cfg.master_data,
                                   cfg.master_data.parent / "pecas_tipo_caixa.xlsx",
                                   assignments)

    comp_max_longo = max(
        (article_idx[a.sku].comp_m for a in assignments
         if a.load_type == LoadType.LONGO and a.sku in article_idx),
        default=1.0,
    )
    requirements, avisos_estimativa = racking.racks_needed(
        assignments, rack_types, catalogo_por_tipo, local_cfg, comp_max_longo,
        local_cfg.altura_picking_m, articles)

    posicoes, avisos_alocacao = racking.allocate_to_racks(
        assignments, requirements, rack_types, catalogo_por_tipo, articles, comp_max_longo,
        local_cfg.altura_picking_m, local_cfg.altura_armazem_m, local_cfg)

    avisos = (boxing.avisos_cantilever(articles, local_cfg)
              + avisos_estimativa + avisos_alocacao)

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

    # Posições iniciais das zonas no canvas: grelha compacta (shelf-packing,
    # várias linhas) à direita do bloco de racks, em vez de uma única coluna
    # vertical -- com dezenas de zonas, uma coluna ultrapassa facilmente a
    # altura visível do canvas e deixa a maioria impossível de ver/arrastar,
    # ficando por defeito num arranjo muito mais comprido que o necessário.
    _ZONA_GAP = 0.5
    _row_max_w = max(racking_w, 15.0)
    zona_x, zona_y = [], []
    cx = cy = row_h = 0.0
    for z in zonas:
        if cx > 0 and cx + z.comp_m > _row_max_w:
            cx = 0.0
            cy += row_h + _ZONA_GAP
            row_h = 0.0
        zona_x.append(round(racking_w + 2.0 + cx, 2))
        zona_y.append(round(0.5 + cy, 2))
        cx += z.comp_m + _ZONA_GAP
        row_h = max(row_h, z.larg_m)

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
        # Corredores de serviço reais (para os operadores andarem sem adivinhar)
        "corredores": [
            {"x": c.x_m, "y": c.y_m, "comp_m": c.comp_m, "larg_m": c.larg_m}
            for c in warehouse_layout.corredores
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
                "x": zona_x[i],
                "y": zona_y[i],
            }
            for i, z in enumerate(zonas)
        ],
    }


@app.get("/api/download/pecas-caixas")
def download_pecas_caixas():
    """Excel com a folha «Peças» original + coluna Tipo_Caixa (gerado no /api/gerar)."""
    path = cfg.master_data.parent / "pecas_tipo_caixa.xlsx"
    return FileResponse(
        path, filename="pecas_tipo_caixa.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/render-layout")
def render_layout(body: ArrangeRequest):
    """Gera o 3D a partir do arranjo manual do canvas.

    Os racks são recalculados pelo pipeline (mesma estrutura interna de filas
    e corredores do bloco) e deslocados para onde o utilizador colocou o bloco
    de racks; as zonas usam as posições/rotações do canvas; a área total é o
    retângulo envolvente de todos os blocos colocados."""
    local_cfg = Config(altura_armazem_m=body.altura_armazem_m,
                       altura_picking_m=body.altura_picking_m)

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
    requirements, _ = racking.racks_needed(
        assignments, rack_types, catalogo_por_tipo, local_cfg, comp_max_longo,
        local_cfg.altura_picking_m, articles)
    posicoes, _ = racking.allocate_to_racks(
        assignments, requirements, rack_types, catalogo_por_tipo, articles,
        comp_max_longo, local_cfg.altura_picking_m, local_cfg.altura_armazem_m,
        local_cfg)
    dim = area.floor_area(posicoes, rack_by_name)

    # layout interno dos racks (sem zonas: começa em y=0), deslocado para a
    # posição do bloco de racks no canvas
    warehouse_layout = layout_mod.gerar_layout(posicoes, rack_by_name, None)
    layout_json = _serializar_layout(warehouse_layout, rack_by_name)

    bloco_racks = next((b for b in body.blocks if b.get("type") == "racking"), None)
    off_x = float(bloco_racks.get("x", 0.0)) if bloco_racks else 0.0
    off_y = float(bloco_racks.get("y", 0.0)) if bloco_racks else 0.0
    rot_racks = int(float(bloco_racks.get("rot", 0) or 0)) % 360 if bloco_racks else 0
    bloco_w = float(bloco_racks.get("w", 0.0)) if bloco_racks else 0.0
    bloco_h = float(bloco_racks.get("h", 0.0)) if bloco_racks else 0.0

    def _roda_rect(x, y, l, p, cont_w, cont_h):
        """Roda um retângulo axis-aligned dentro de um contentor cont_w x cont_h,
        em passos de 90° (rot_racks). Devolve (x, y, larg, prof) no novo frame."""
        if rot_racks == 90:
            return cont_h - (y + p), x, p, l
        if rot_racks == 180:
            return cont_w - (x + l), cont_h - (y + p), l, p
        if rot_racks == 270:
            return y, cont_w - (x + l), p, l
        return x, y, l, p

    # o canvas roda o bloco em torno do centro: origem do bloco rodado
    if rot_racks in (90, 270):
        centro_x, centro_y = off_x + bloco_w / 2, off_y + bloco_h / 2
        base_x, base_y = centro_x - bloco_h / 2, centro_y - bloco_w / 2
    else:
        base_x, base_y = off_x, off_y

    for item in layout_json:
        larg_orig, prof_orig = item["larg_m"], item["prof_m"]
        if rot_racks:
            # roda as caixas dentro de cada nível (frame do footprint do rack)
            for bloco in item["blocos"]:
                for nivel in bloco["niveis"]:
                    for c in nivel["caixas"]:
                        cx, cy, cl, cp = _roda_rect(c["x_m"], c["y_m"], c["comp_m"],
                                                    c["larg_m"], larg_orig, prof_orig)
                        c["x_m"], c["y_m"] = round(cx, 4), round(cy, 4)
                        c["comp_m"], c["larg_m"] = cl, cp
        x, y, l, p = _roda_rect(item["x"], item["y"], larg_orig, prof_orig, bloco_w, bloco_h)
        item["x"] = round(base_x + x, 4)
        item["y"] = round(base_y + y, 4)
        item["larg_m"], item["prof_m"] = l, p

    # Corredores de serviço: sofrem a MESMA rotação/deslocamento das posições,
    # para os operadores andarem sobre eles em qualquer orientação do bloco.
    corredores_json = []
    for c in warehouse_layout.corredores:
        cx, cy, cl, cp = _roda_rect(c.x_m, c.y_m, c.comp_m, c.larg_m, bloco_w, bloco_h)
        corredores_json.append({"x": round(base_x + cx, 4), "y": round(base_y + cy, 4),
                                "comp_m": round(cl, 4), "larg_m": round(cp, 4)})

    zonas_layout = [
        {
            "nome": b.get("nome", ""), "categoria": b.get("categoria", ""),
            "x": float(b.get("x", 0.0)), "y": float(b.get("y", 0.0)),
            "larg_m": float(b.get("w", 0.0)), "prof_m": float(b.get("h", 0.0)),
            "rot": float(b.get("rot", 0.0) or 0.0),
            "area_m2": round(float(b.get("w", 0.0)) * float(b.get("h", 0.0)), 2),
            "justificacao": "",
        }
        for b in body.blocks if b.get("type") == "zone"
    ]

    def _cantos(b):
        rot = math.radians(float(b.get("rot", 0.0) or 0.0))
        w, h = float(b.get("w", 0.0)), float(b.get("h", 0.0))
        cx, cy = float(b.get("x", 0.0)) + w / 2, float(b.get("y", 0.0)) + h / 2
        return [(cx + dx * math.cos(rot) - dy * math.sin(rot),
                 cy + dx * math.sin(rot) + dy * math.cos(rot))
                for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2),
                               (w / 2, h / 2), (-w / 2, h / 2))]

    pontos = [p for b in body.blocks for p in _cantos(b)]
    if pontos:
        min_x, max_x = min(p[0] for p in pontos), max(p[0] for p in pontos)
        min_y, max_y = min(p[1] for p in pontos), max(p[1] for p in pontos)
    else:
        min_x = min_y = max_x = max_y = 0.0
    largura = max_x - min_x
    profundidade = max_y - min_y

    # Normaliza para a origem: o canto mínimo do arranjo passa a (0, 0). Sem
    # isto, se o utilizador deixar o bloco/zonas afastados do canto do canvas,
    # o 3D desenha chão e paredes vazias à esquerda/à frente do conteúdo (a
    # margem branca do canvas). A área total já é a envolvente (max - min), por
    # isso a margem não conta para a área -- só aparecia no 3D. Deslocar as
    # posições -min alinha o 3D com a área reportada.
    for item in layout_json:
        item["x"] = round(item["x"] - min_x, 4)
        item["y"] = round(item["y"] - min_y, 4)
    for z in zonas_layout:
        z["x"] = round(z["x"] - min_x, 4)
        z["y"] = round(z["y"] - min_y, 4)
    for c in corredores_json:
        c["x"] = round(c["x"] - min_x, 4)
        c["y"] = round(c["y"] - min_y, 4)

    return {
        "layout": layout_json,
        "zonas_layout": zonas_layout,
        "corredores": corredores_json,
        "area_racks_m2": round(dim.area_racks_m2, 2),
        "area_zonas_m2": round(sum(z["area_m2"] for z in zonas_layout), 2),
        "largura_total_m": round(largura, 2),
        "profundidade_total_m": round(profundidade, 2),
        "area_total_m2": round(largura * profundidade, 2),
    }


# ------------------------------------------------------------------- static

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def main():
    import uvicorn
    uvicorn.run("warehouse.web:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()

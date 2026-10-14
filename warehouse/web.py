"""Front end web do simulador de armazém (protótipo).

API FastAPI que reaproveita o pipeline existente (boxing/racking/area) e
serve uma página HTML/JS (Three.js via CDN) para ver e editar os dados, e
gerar uma simulação 3D do layout do armazém.

Corre com: python -m warehouse.web
"""
from __future__ import annotations

import dataclasses
import math
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import boxing, io_excel, data_access, racking, area, layout as layout_mod
from .cenario_autonomia import aplicar_autonomia
from .config import Config
from .models import LoadType

# WAREHOUSE_CLIENT escolhe qual dados/clients/<nome>.json carregar (default
# "sia" -- comportamento de sempre para quem não define a variável).
# WAREHOUSE_MASTER_DATA continua a existir por cima disso, para apontar o
# servidor para outro master data (ex.: comparar versões) sem editar código
# nem o ficheiro do cliente -- omitido = usa o master_data do cliente tal qual.
cfg = Config.for_client(os.environ.get("WAREHOUSE_CLIENT", "sia"))
_master_data_override = os.environ.get("WAREHOUSE_MASTER_DATA")
if _master_data_override:
    cfg.master_data = Path(_master_data_override)
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Simulador de Armazém")


@app.get("/healthz")
def healthz():
    """Health check para deployment (load balancer / readiness probe --
    ver Dockerfile e DEPLOYMENT.md). Não toca em dados: só confirma que o
    processo está de pé e qual cliente/origem de dados está carregado --
    útil para diagnosticar rápido "isto é o container certo?" sem abrir
    o /api/articles inteiro."""
    return {"status": "ok", "client": cfg.client_name, "data_source": cfg.data_source}


# --------------------------------------------------------------------- dados

@app.get("/api/articles")
def get_articles():
    articles = data_access.load_articles(cfg)
    catalogo = data_access.load_catalog(cfg)
    articles, _ = boxing.aplicar_stock_autonomia(
        articles, catalogo, io_excel.com_carrinhos_da_linha(cfg))  # Passo 0
    return [
        {
            "sku": a.sku, "descricao": a.descricao,
            "comp_m": a.comp_m, "larg_m": a.larg_m, "alt_m": a.alt_m,
            "peso_kg": a.peso_kg, "stock_max_un": a.stock_max_un,
            "stock_atual_un": a.stock_atual_un,
            "pecas_por_carro": a.pecas_por_carro,
            "altura_override": a.altura_override, "eixo_aninhagem": a.eixo_aninhagem,
            "empilhamento": a.empilhamento.value,
            "fragilidade": a.fragilidade, "geometria": a.geometria,
            "classe": a.classe, "estacao": a.estacao,
            "caixa_forcada": a.caixa_forcada, "pecas_caixa_forcada": a.pecas_caixa_forcada,
        }
        for a in articles
    ]


@app.get("/api/boxes")
def get_boxes():
    boxes = data_access.load_catalog(cfg)
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
    racks = data_access.load_rack_types(cfg)
    return [
        {
            "nome": r.nome, "larg_m": r.larg_m, "prof_m": r.prof_m,
            "alt_nivel_m": r.alt_nivel_m, "niveis": r.niveis,
            "aceita": [lt.value for lt in r.aceita],
            "footprint_m2": r.footprint_m2, "corredor_m": r.corredor_m,
        }
        for r in racks
    ]


# ------------------------------------------------------------------- zonas

@app.get("/api/zones")
def get_zones():
    zonas = data_access.load_zones(cfg)
    return [
        {
            "nome": z.nome, "categoria": z.categoria,
            "comp_m": z.comp_m, "larg_m": z.larg_m, "justificacao": z.justificacao,
        }
        for z in zonas
    ]


# ------------------------------------------------------------------- gerar

def _serializar_layout(posicoes: list, rack_by_name):
    """Converte uma lista de PosicaoLayout (coordenadas físicas reais,
    layout.py) para o formato JSON consumido pelo viewer 3D: um registo por
    posição de chão, com os blocos/níveis/caixas já embutidos (cada posição é
    a fonte da verdade -- sem reconstrução por intervalo de rack_numero).
    Recebe a lista diretamente (não um WarehouseLayout inteiro) para poder
    servir também `.posicoes` reais e `posicoes_fantasma` com a mesma função."""
    layout = []
    for pl in posicoes:
        pos = pl.posicao
        rack = rack_by_name[pos.rack_nome]
        load_types = sorted({c.load_type.value
                             for bloco in pos.blocos for nivel in bloco.niveis
                             for c in nivel.caixas})
        # Nº de níveis REAL desta posição -- normalmente = rack.niveis (o
        # catálogo), mas as tipologias de empilhamento nível a nível (ver
        # Config.empilhamento_por_nivel/racking.niveis_efetivos) podem ter
        # menos, quando o pé-direito não dá para o módulo completo. Ler do
        # próprio bloco (não do catálogo às cegas) é o que faz o 3D respeitar
        # de facto o limite -- senão a estrutura desenhava sempre a altura
        # cheia do catálogo, mesmo com menos níveis lá dentro.
        niveis_reais = len(pos.blocos[0].niveis) if pos.blocos and pos.blocos[0].niveis else rack.niveis
        layout.append({
            "rack_nome": pos.rack_nome,
            "indice": pos.indice,
            "x": pl.x_m, "y": pl.y_m,
            "larg_m": pl.largura_m, "prof_m": pl.profundidade_m,
            "altura_m": rack.alt_nivel_m * niveis_reais,
            "niveis": niveis_reais,
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


# Ordem de preferência do tipo de carga "de exemplo" (maior/mais vistoso
# primeiro) para encher visualmente uma posição de rack manual que não teve
# nenhum conteúdo real emprestado (ver render_layout/_caixas_ficticias) --
# só entra em jogo o tipo que a própria tipologia aceitar de facto.
_ORDEM_DUMMY = (LoadType.PALLET, LoadType.MEIA, LoadType.L, LoadType.M, LoadType.S, LoadType.XS)


def _caixas_ficticias(rack, catalogo_por_tipo: dict) -> list[dict]:
    """Grelha de caixas "de exemplo" (sem SKU real, `pecas_nesta_caixa=0`) do
    tamanho de um nível desta tipologia -- só para uma posição manual (Passo
    4, sandbox) não ficar com o aspeto de rack vazia quando não há stock
    real emprestado (ver Config/render_layout: `pool_posicoes_reais`
    esgotada ou inexistente). Encaixe geométrico simples (floor, sem
    fill_factor -- é só visual, não conta para nada), testando as 2
    orientações do plano, mesmo espírito de `racking.rack_capacity`."""
    box = None
    load_type = None
    for lt in _ORDEM_DUMMY:
        if lt in rack.aceita and lt in catalogo_por_tipo:
            load_type, box = lt, catalogo_por_tipo[lt]
            break
    if box is None:
        return []
    op1 = (math.floor(rack.larg_m / box.ext_comp_m), math.floor(rack.prof_m / box.ext_larg_m),
          box.ext_comp_m, box.ext_larg_m)
    op2 = (math.floor(rack.larg_m / box.ext_larg_m), math.floor(rack.prof_m / box.ext_comp_m),
          box.ext_larg_m, box.ext_comp_m)
    n_c, n_l, dim_c, dim_l = max((op1, op2), key=lambda o: o[0] * o[1])
    if n_c <= 0 or n_l <= 0:
        return []
    alt_m = min(box.ext_alt_m, max(rack.alt_nivel_m - 0.05, 0.05))
    return [
        {
            "sku": "(exemplo)", "descricao": "Carga de exemplo -- sandbox, sem SKU real",
            "load_type": load_type.value,
            "x_m": round(i * dim_c, 4), "y_m": round(j * dim_l, 4),
            "comp_m": round(dim_c, 4), "larg_m": round(dim_l, 4), "alt_m": round(alt_m, 4),
            "pecas_nesta_caixa": 0, "eh_picking": False, "num_boxes_in_stack": 1,
        }
        for j in range(n_l) for i in range(n_c)
    ]


class GerarRequest(BaseModel):
    altura_armazem_m: float = 0.0
    altura_picking_m: float = 0.0
    margem_estrutural_pct: float = 0.0
    # envelope do bloco de racks (0 = sem limite); ver Config.largura_max_m
    largura_max_m: float = 0.0
    profundidade_max_m: float = 0.0
    # cenário de autonomia (nº de carros); 0 = usa o master data tal como está,
    # sem escalar stock_max_un. Ver warehouse.cenario_autonomia.
    carros_alvo: float = 0.0


class ArrangeRequest(BaseModel):
    blocks: list  # [{id, type: "zone"|"racking", nome, w, h, x, y, rot}, ...]
    altura_armazem_m: float = 0.0
    altura_picking_m: float = 0.0
    # Envelope do bloco de racks: TEM de ser o mesmo com que /api/gerar produziu
    # o bloco que está no canvas. Sem isto, o render recalculava o layout LIVRE
    # (forma diferente) e depois rodava-o/deslocava-o usando as dimensões do
    # bloco do canvas -- as posições eram espremidas num retângulo que não era o
    # delas e as fiadas apareciam cortadas a meio (ver _roda_rect).
    largura_max_m: float = 0.0
    profundidade_max_m: float = 0.0
    # Idem: TEM de ser o mesmo carros_alvo que /api/gerar usou para o bloco que
    # está no canvas -- senão o render volta a usar o stock do master data sem
    # escala, e o bloco 3D deixa de corresponder ao que está desenhado.
    carros_alvo: float = 0.0


def _caixas_especiais_do_armazem() -> dict:
    """Carrinhos da folha «Caixas Especiais» -> {sku: CaixaEspecial}. O armazém
    só os usa com Config.caixas_especiais_no_armazem (são os carrinhos da
    linha); desligado (default) devolve {} e esses SKUs seguem o best-fit."""
    if not cfg.caixas_especiais_no_armazem:
        return {}
    return io_excel.caixas_especiais_por_sku(
        io_excel.load_caixas_especiais(cfg.master_data))


@app.post("/api/gerar")
def gerar(body: GerarRequest):
    # dataclasses.replace(cfg, ...) -- NÃO Config(...) -- porque `cfg` é o
    # Config do CLIENTE (Config.for_client, carregado uma vez ao subir o
    # servidor): um Config(...) bare aqui reconstruía TODOS os campos a partir
    # dos defaults do dataclass, incluindo oversize_racks/oversize_par_racks/
    # max_modulos_por_tipologia/empilhamento_por_nivel/picking_manual_racks --
    # ou seja, cada pedido silenciosamente esquecia a configuração do
    # cliente e usava os defaults "de fábrica" (que só por coincidência
    # calham a ser os valores da SIA). Para outro cliente isto desativava as
    # regras de negócio dele em TODOS os pedidos, sem aviso nenhum -- exatamente
    # o modo de falha que verificar_chaves_substring_sem_correspondencia
    # existe para apanhar (ver main.run), só que aqui a causa não era um nome
    # de tipologia trocado, era o próprio Config a ser descartado a cada pedido.
    local_cfg = dataclasses.replace(
        cfg, altura_armazem_m=body.altura_armazem_m,
        altura_picking_m=body.altura_picking_m,
        margem_estrutural_pct=body.margem_estrutural_pct,
        largura_max_m=body.largura_max_m,
        profundidade_max_m=body.profundidade_max_m,
        caixas_especiais=_caixas_especiais_do_armazem())
    local_cfg = io_excel.com_carrinhos_da_linha(local_cfg)   # Config.unidade_da_linha

    articles = data_access.load_articles(cfg)
    catalogo = data_access.load_catalog(cfg)
    articles, autonomia_resultados = boxing.aplicar_stock_autonomia(
        articles, catalogo, local_cfg)  # Passo 0
    if body.carros_alvo > 0:
        articles = aplicar_autonomia(articles, body.carros_alvo)
        # O rasto do Passo 0 foi calculado ANTES deste reescalamento, por isso
        # o "stock teórico p/ autonomia mínima" que traz já não corresponde ao
        # stock que o resto do pipeline usa. Descarta-se -- mesma escolha do
        # cenario_autonomia.py, que injeta articles já escalados e passa
        # autonomia_resultados=None. A folha Autonomia continua a sair, com o
        # stock usado real e "-" nas colunas do Passo 0, em vez de mostrar
        # dois números que não batem certo.
        autonomia_resultados = None
    rack_types = data_access.load_rack_types(cfg)
    # ANTES de com_par_oversize -- ver a mesma nota em main.run.
    avisos_config = racking.verificar_chaves_substring_sem_correspondencia(rack_types, local_cfg)
    rack_types = racking.com_par_oversize(rack_types, local_cfg)
    catalogo_por_tipo = {b.nome: b for b in catalogo}
    rack_by_name = {r.nome: r for r in rack_types}
    article_idx = {a.sku: a for a in articles}

    assignments = boxing.plan_all(articles, catalogo, local_cfg)
    justificacoes = boxing.justificar_all(articles, catalogo, local_cfg, assignments)
    # Ficheiro de download "Peças + Tipo_Caixa" -- feature do fluxo Excel
    # (copia a folha "Peças" original e acrescenta uma coluna): não tem
    # equivalente ainda com data_source="supabase" (não há "folha original"
    # para copiar). Guarda-se para não rebentar num cliente que não tem
    # master_data a apontar para um .xlsx real -- ver /api/download/pecas-caixas.
    if cfg.data_source == "excel":
        io_excel.write_pecas_com_caixa(cfg.master_data,
                                       cfg.master_data.parent / "pecas_tipo_caixa.xlsx",
                                       assignments)

    requirements, avisos_estimativa = racking.racks_needed(
        assignments, rack_types, catalogo_por_tipo, local_cfg,
        local_cfg.altura_picking_m, articles)

    posicoes, avisos_alocacao = racking.allocate_to_racks(
        assignments, requirements, rack_types, catalogo_por_tipo, articles,
        local_cfg.altura_picking_m, local_cfg.altura_armazem_m, local_cfg)

    avisos = avisos_config + avisos_estimativa + avisos_alocacao

    zonas = data_access.load_zones(cfg)

    # OVERSIZE ao chão: só existe quando NÃO há tipologia wide-span NEM par
    # emparelhado (o rack tem prioridade). A área vem das cotas + stock, não de
    # um valor fixo. Calcula-se ANTES do layout, para as pilhas receberem
    # coordenadas e aparecerem no 3D.
    artigos_chao: list[tuple] = []
    area_chao_m2, detalhe_chao = 0.0, []
    if local_cfg.oversize_ao_chao and not racking._melhor_oversize_ativo(
            rack_types, local_cfg):
        artigos_chao = [
            (a.sku, art.comp_m, art.larg_m, art.alt_m, art.peso_kg, a.num_unidades, art.altura_override)
            for a in assignments if a.load_type == LoadType.OVERSIZE
            for art in [article_idx.get(a.sku)] if art
        ]
    # OVERSIZE2 (caixa especial): ao solo, dentro do footprint da rack
    # OVERSIZE quando cabe (ver main.run) -- artigos_chao_oversize2 decide
    # por SKU, só devolve os que não cabem.
    artigos_chao += area.artigos_chao_oversize2(assignments, local_cfg, rack_types)
    if artigos_chao:
        area_chao_m2, detalhe_chao = area.area_oversize_chao(
            artigos_chao, local_cfg.oversize_chao_altura_max_m,
            local_cfg.oversize_chao_peso_max_kg, local_cfg.oversize_chao_acesso_pct)

    warehouse_layout = layout_mod.gerar_layout(
        posicoes, rack_by_name, zonas,
        local_cfg.largura_max_m, local_cfg.profundidade_max_m,
        artigos_chao, local_cfg.oversize_chao_altura_max_m,
        local_cfg.oversize_chao_peso_max_kg, local_cfg.picking_manual_racks)
    avisos = avisos + warehouse_layout.avisos
    layout_json = _serializar_layout(warehouse_layout.posicoes, rack_by_name)

    # Geometria real do bloco de racks (posições + corredores) no referencial
    # LOCAL do próprio bloco (y0=0, sem o deslocamento das zonas) -- mesma
    # chamada com zonas=None que /api/render-layout já faz para reposicionar o
    # bloco no canvas. Serve só para o canvas 2D desenhar a forma real em vez
    # de um retângulo único; não substitui warehouse_layout (que continua a ser
    # a fonte da verdade da área/avisos).
    #
    # `espacos_livres` NÃO vai aqui de propósito. Media-se que só cobre um dos
    # três tipos de folga do bloco -- a sobra à direita da última fiada de cada
    # prateleira -- e deixa de fora a folga vertical por baixo de um chunk mais
    # raso que a sua prateleira e a cauda das fiadas incompletas. Nos dados da
    # SIA isso eram 5.04 m² pintados de verde contra 24.93 m² de folga real,
    # ou seja o verde dava a entender que o espaço livre estava mapeado quando
    # a maior parte dele não estava. Até o cálculo cobrir os três casos, o
    # canvas mostra só o que sabe: roxo = rack, cinzento = corredor, branco =
    # por classificar. `WarehouseLayout.espacos_livres` mantém-se intacto --
    # é outro consumidor (gerar_racks_fantasma, ver /api/render-layout).
    layout_local = layout_mod.gerar_layout(
        posicoes, rack_by_name, None,
        local_cfg.largura_max_m, local_cfg.profundidade_max_m,
        artigos_chao, local_cfg.oversize_chao_altura_max_m,
        local_cfg.oversize_chao_peso_max_kg, local_cfg.picking_manual_racks)
    racking_geometria = {
        "posicoes": [
            {"rack_nome": pl.posicao.rack_nome, "x": pl.x_m, "y": pl.y_m,
             "w": pl.largura_m, "h": pl.profundidade_m}
            for pl in layout_local.posicoes
        ],
        "corredores": [
            {"x": c.x_m, "y": c.y_m, "w": c.comp_m, "h": c.larg_m}
            for c in layout_local.corredores
        ],
    }

    # floor_area DEPOIS do layout: as pilhas são uma fiada do bloco e o seu
    # footprint conta como área ocupada (senão aparecia como corredor).
    dim = area.floor_area(posicoes, rack_by_name, warehouse_layout.pilhas_chao)
    resumo = area.resumo_area_total(dim, warehouse_layout, zonas,
                                    local_cfg.margem_estrutural_pct, area_chao_m2)

    # resultados.xlsx -- MESMA folha-a-folha que o CLI (main.run) produz: até
    # agora só saía por `python -m warehouse.main`, e quem usava o botão no
    # browser ficava sem Lógica/Resumo/Detalhe/Autonomia/Validação/Avisos.
    # Usa local_cfg (não cfg) para o ficheiro refletir a altura/picking/margem
    # que o utilizador pôs no formulário, não os defaults do cliente.
    #
    # Falhar a escrita NÃO pode rebentar o /api/gerar: o caso normal é o
    # ficheiro estar aberto no Excel (PermissionError no Windows), e perder o
    # dimensionamento todo por causa disso seria pior do que ficar sem o
    # download. Vira aviso, que já tem sítio próprio na resposta e na UI.
    resultados_escrito = False
    try:
        io_excel.write_results(
            local_cfg.output, assignments, requirements, dim,
            io_excel.flatten_posicoes_para_detalhe(posicoes), avisos,
            warehouse_layout, zonas, resumo, articles, justificacoes,
            autonomia_resultados)
        resultados_escrito = True
    except OSError as e:
        avisos = avisos + [
            f"Não foi possível escrever {local_cfg.output.name}: {e}. "
            "O dimensionamento está correto -- só o download do Excel é que "
            "não ficou disponível (o ficheiro costuma estar aberto no Excel)."]

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
    # Dimensões do bloco COMO ELE É no layout: incluem a fiada de armazenagem ao
    # chão, se houver (ver layout.gerar_layout). O canvas precisa do retângulo
    # real, senão o utilizador arranja o armazém com um bloco menor do que o que
    # vai ocupar.
    racking_w = warehouse_layout.largura_total_m
    racking_h = warehouse_layout.profundidade_total_m
    tem_chao = bool(warehouse_layout.pilhas_chao)

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
        "racking_geometria": racking_geometria,
        "largura_layout_m": warehouse_layout.largura_total_m,
        "profundidade_layout_m": warehouse_layout.profundidade_total_m,
        "area_zonas_m2": round(resumo.area_zonas_m2, 2),
        "area_oversize_chao_m2": round(resumo.area_oversize_chao_m2, 2),
        "detalhe_oversize_chao": detalhe_chao,
        # pilhas com coordenadas, para o 3D as desenhar (ver PilhaChao)
        "pilhas_chao": [
            {"sku": p.sku, "x": p.x_m, "y": p.y_m,
             "comp_m": p.comp_m, "larg_m": p.larg_m,
             "alt_peca_m": p.alt_peca_m, "n_pecas": p.n_pecas,
             "peso_kg": p.peso_kg, "altura_total_m": round(p.altura_total_m, 3)}
            for p in warehouse_layout.pilhas_chao
        ],
        "margem_estrutural_m2": round(resumo.margem_estrutural_m2, 2),
        "area_bruta_total_m2": round(resumo.area_bruta_total_m2, 2),
        "avisos": avisos,
        # Só com isto a True é que faz sentido a UI mostrar o link de download
        # (ver /api/download/resultados).
        "resultados_excel": resultados_escrito,
        # Ocupação do espaço de armazenagem CONSTRUÍDO (níveis/área/volume).
        # Conta só os racks reais; as filas sandbox que o utilizador põe no
        # canvas entram do lado do cliente, via capacidade_por_tipologia.
        "ocupacao": racking.resumo_ocupacao(posicoes, rack_by_name),
        # Capacidade de UMA posição de chão, por tipologia, com esta config --
        # inclui tipologias sem racks reais (o utilizador pode na mesma pôr uma
        # fila sandbox dessas) e as sintéticas "(emparelhada)", que o
        # /api/racks não conhece por ler a folha «Racks» tal e qual.
        "capacidade_por_tipologia": {
            r.nome: racking.capacidade_por_posicao(r, local_cfg) for r in rack_types
        },
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
            "nome": "Bloco de Racks + chao" if tem_chao else "Bloco de Racks",
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
                "nome": "Bloco de Racks + chao" if tem_chao else "Bloco de Racks",
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
    """Excel com a folha «Peças» original + coluna Tipo_Caixa (gerado no /api/gerar).

    Só existe no fluxo Excel (ver a mesma nota em gerar()) -- um cliente
    data_source='supabase' não tem uma "folha original" para copiar."""
    if cfg.data_source != "excel":
        raise HTTPException(
            status_code=404,
            detail="Download 'pecas_tipo_caixa.xlsx' só está disponível para "
                    "clientes com data_source='excel'.")
    path = cfg.master_data.parent / "pecas_tipo_caixa.xlsx"
    return FileResponse(
        path, filename="pecas_tipo_caixa.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/download/resultados")
def download_resultados():
    """resultados.xlsx do último /api/gerar: folhas Lógica, Resumo, Detalhe,
    Autonomia, Validação e Avisos.

    Ao contrário do pecas_tipo_caixa.xlsx, este existe para qualquer
    data_source: o write_results constrói o workbook de raiz a partir do
    output do pipeline, nunca lê o master data."""
    path = cfg.output
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"{path.name} ainda não foi gerado -- corre «Gerar racks» primeiro.")
    return FileResponse(
        path, filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/render-layout")
def render_layout(body: ArrangeRequest):
    """Gera o 3D a partir do arranjo manual do canvas.

    Os racks são recalculados pelo pipeline (mesma estrutura interna de filas
    e corredores do bloco) e deslocados para onde o utilizador colocou o bloco
    de racks; as zonas usam as posições/rotações do canvas; a área total é o
    retângulo envolvente de todos os blocos colocados."""
    # Ver a nota equivalente em gerar(): tem de ser dataclasses.replace(cfg, ...),
    # não Config(...), para não perder a configuração do cliente a cada pedido.
    local_cfg = dataclasses.replace(
        cfg, altura_armazem_m=body.altura_armazem_m,
        altura_picking_m=body.altura_picking_m,
        largura_max_m=body.largura_max_m,
        profundidade_max_m=body.profundidade_max_m,
        caixas_especiais=_caixas_especiais_do_armazem())
    local_cfg = io_excel.com_carrinhos_da_linha(local_cfg)   # Config.unidade_da_linha

    articles = data_access.load_articles(cfg)
    catalogo = data_access.load_catalog(cfg)
    articles, _ = boxing.aplicar_stock_autonomia(articles, catalogo, local_cfg)  # Passo 0
    if body.carros_alvo > 0:
        articles = aplicar_autonomia(articles, body.carros_alvo)
    rack_types = data_access.load_rack_types(cfg)
    rack_types = racking.com_par_oversize(rack_types, local_cfg)
    catalogo_por_tipo = {b.nome: b for b in catalogo}
    rack_by_name = {r.nome: r for r in rack_types}
    article_idx = {a.sku: a for a in articles}

    assignments = boxing.plan_all(articles, catalogo, local_cfg)
    requirements, _ = racking.racks_needed(
        assignments, rack_types, catalogo_por_tipo, local_cfg,
        local_cfg.altura_picking_m, articles)
    posicoes, _ = racking.allocate_to_racks(
        assignments, requirements, rack_types, catalogo_por_tipo, articles,
        local_cfg.altura_picking_m, local_cfg.altura_armazem_m,
        local_cfg)

    # OVERSIZE ao chão (ver /api/gerar): antes do layout, para as pilhas
    # receberem coordenadas e serem desenhadas no 3D.
    artigos_chao: list[tuple] = []
    area_chao_m2 = 0.0
    if local_cfg.oversize_ao_chao and not racking._melhor_oversize_ativo(
            rack_types, local_cfg):
        artigos_chao = [
            (a.sku, art.comp_m, art.larg_m, art.alt_m, art.peso_kg, a.num_unidades, art.altura_override)
            for a in assignments if a.load_type == LoadType.OVERSIZE
            for art in [article_idx.get(a.sku)] if art
        ]
    # OVERSIZE2 (caixa especial): ver /api/gerar.
    artigos_chao += area.artigos_chao_oversize2(assignments, local_cfg, rack_types)
    if artigos_chao:
        area_chao_m2, _ = area.area_oversize_chao(
            artigos_chao, local_cfg.oversize_chao_altura_max_m,
            local_cfg.oversize_chao_peso_max_kg, local_cfg.oversize_chao_acesso_pct)

    # layout interno dos racks (sem zonas: começa em y=0), deslocado para a
    # posição do bloco de racks no canvas
    warehouse_layout = layout_mod.gerar_layout(
        posicoes, rack_by_name, None,
        local_cfg.largura_max_m, local_cfg.profundidade_max_m,
        artigos_chao, local_cfg.oversize_chao_altura_max_m,
        local_cfg.oversize_chao_peso_max_kg, local_cfg.picking_manual_racks)
    dim = area.floor_area(posicoes, rack_by_name, warehouse_layout.pilhas_chao)
    layout_json = _serializar_layout(warehouse_layout.posicoes, rack_by_name)

    # Racks fantasma (preview, Passo 4): uma posição vazia por slot inteiro
    # que cabe em cada espaço livre, mesma tipologia da fiada que estendem.
    # Sempre calculadas (é barato -- não têm caixas ainda) e sempre devolvidas;
    # o frontend decide mostrar/esconder com a checkbox, sem precisar de pedir
    # de novo (mesmo padrão dos "Operadores" já existentes).
    racks_fantasma = layout_mod.gerar_racks_fantasma(
        warehouse_layout.espacos_livres, rack_by_name,
        local_cfg.altura_armazem_m, local_cfg.max_modulos_por_tipologia,
        local_cfg.altura_picking_m, local_cfg.empilhamento_por_nivel)
    layout_fantasma_json = _serializar_layout(racks_fantasma, rack_by_name)

    bloco_racks = next((b for b in body.blocks if b.get("type") == "racking"), None)
    off_x = float(bloco_racks.get("x", 0.0)) if bloco_racks else 0.0
    off_y = float(bloco_racks.get("y", 0.0)) if bloco_racks else 0.0
    rot_racks = int(float(bloco_racks.get("rot", 0) or 0)) % 360 if bloco_racks else 0
    bloco_w = float(bloco_racks.get("w", 0.0)) if bloco_racks else 0.0
    bloco_h = float(bloco_racks.get("h", 0.0)) if bloco_racks else 0.0

    def _roda_rect_rot(x, y, l, p, cont_w, cont_h, rot):
        """Roda um retângulo axis-aligned dentro de um contentor cont_w x cont_h,
        em passos de 90°. Devolve (x, y, larg, prof) no novo frame. Rotação
        explícita (não fixa a rot_racks) -- reaproveitada por _roda_rect
        (bloco de racks principal) e por _colocar_bloco_manual (cada fila de
        racks manuais é o seu próprio bloco, com a sua própria rotação)."""
        rot = int(rot) % 360
        if rot == 90:
            return cont_h - (y + p), x, p, l
        if rot == 180:
            return cont_w - (x + l), cont_h - (y + p), l, p
        if rot == 270:
            return y, cont_w - (x + l), p, l
        return x, y, l, p

    def _roda_rect(x, y, l, p, cont_w, cont_h):
        return _roda_rect_rot(x, y, l, p, cont_w, cont_h, rot_racks)

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

    # Racks fantasma: MESMA rotação/deslocamento das posições reais (sem
    # caixas lá dentro para rodar -- estão vazias nesta fase).
    for item in layout_fantasma_json:
        larg_orig, prof_orig = item["larg_m"], item["prof_m"]
        x, y, l, p = _roda_rect(item["x"], item["y"], larg_orig, prof_orig, bloco_w, bloco_h)
        item["x"] = round(base_x + x, 4)
        item["y"] = round(base_y + y, 4)
        item["larg_m"], item["prof_m"] = l, p

    # Racks MANUAIS (sandbox de capacidade -- ver layout.gerar_fila_manual):
    # cada fila é o seu PRÓPRIO bloco no canvas, com posição/rotação própria
    # (não a do bloco de racks principal). Mesma técnica de _roda_rect, só que
    # aplicada fila a fila em vez de uma vez só para o bloco inteiro.
    def _colocar_bloco_manual(items_json, off_x, off_y, rot, cont_w, cont_h):
        rot = int(float(rot or 0)) % 360
        if rot in (90, 270):
            centro_x, centro_y = off_x + cont_w / 2, off_y + cont_h / 2
            bx, by = centro_x - cont_h / 2, centro_y - cont_w / 2
        else:
            bx, by = off_x, off_y
        for item in items_json:
            larg_orig, prof_orig = item["larg_m"], item["prof_m"]
            x, y, l, p = _roda_rect_rot(item["x"], item["y"], larg_orig, prof_orig, cont_w, cont_h, rot)
            item["x"] = round(bx + x, 4)
            item["y"] = round(by + y, 4)
            item["larg_m"], item["prof_m"] = l, p

    # Sem bloco de racks (sandbox puro): as posições REAIS que o Passo 2 já
    # alocou (com caixas a sério, a partir do stock) ficam disponíveis para
    # "emprestar" às filas manuais da MESMA tipologia -- sem bloco de racks
    # não há onde as desenhar de outra forma, e ficariam perdidas em
    # silêncio. Agrupadas por tipologia, uma lista que cada fila manual vai
    # ESVAZIANDO (pool.pop) à medida que preenche os seus slots.
    posicoes_reais_por_tipo: dict[str, list] = {}
    if bloco_racks is None:
        for pos in posicoes:
            posicoes_reais_por_tipo.setdefault(pos.rack_nome, []).append(pos)

    racks_manuais_json: list = []
    avisos_manuais: list[str] = []
    for b in body.blocks:
        if b.get("type") != "rack_manual":
            continue
        tipo_nome = b.get("rack_tipo")
        rack = rack_by_name.get(tipo_nome)
        nome_bloco = b.get("nome") or "Fila manual"
        n = int(float(b.get("n", 0) or 0))
        if rack is None:
            avisos_manuais.append(
                f"Fila manual '{nome_bloco}': tipologia '{tipo_nome}' não existe na folha «Racks» -- ignorada.")
            continue
        if n <= 0:
            continue
        dupla = bool(b.get("dupla"))
        pool = posicoes_reais_por_tipo.get(tipo_nome)
        posicoes_locais = layout_mod.gerar_fila_manual(
            rack, n, dupla, local_cfg.altura_armazem_m, local_cfg.max_modulos_por_tipologia, pool,
            local_cfg.empilhamento_por_nivel)
        itens_json = _serializar_layout(posicoes_locais, rack_by_name)

        # Posições que não vieram do pool real (ou o pool esgotou-se antes de
        # lá chegar) ficam com o aspeto de rack vazia -- enche-se com caixas
        # "de exemplo" (sem SKU real, pecas_nesta_caixa=0) só para o 3D não
        # parecer despovoado. Nunca mistura com conteúdo real: só entra em
        # níveis que já vinham 100% vazios do gerar_fila_manual.
        caixas_exemplo = _caixas_ficticias(rack, catalogo_por_tipo)
        if caixas_exemplo:
            for item in itens_json:
                vazio = not any(c for bloco in item["blocos"] for nivel in bloco["niveis"] for c in nivel["caixas"])
                if not vazio:
                    continue
                for bloco in item["blocos"]:
                    for nivel in bloco["niveis"]:
                        nivel["caixas"] = [dict(c) for c in caixas_exemplo]

        cont_w = n * rack.larg_m
        cont_h = rack.prof_m * (2 if dupla else 1)
        _colocar_bloco_manual(itens_json, float(b.get("x", 0.0)), float(b.get("y", 0.0)),
                              b.get("rot", 0), cont_w, cont_h)
        racks_manuais_json.extend(itens_json)

    # O que sobrou por preencher (mais posições reais do que slots manuais
    # desenhados, ou uma tipologia com stock real mas NENHUMA fila manual) --
    # nunca se perde em silêncio: avisa-se com quantas posições reais
    # (não peças/caixas) ficaram de fora.
    for nome_tipo, sobra in posicoes_reais_por_tipo.items():
        if sobra:
            avisos_manuais.append(
                f"Tipologia '{nome_tipo}': {len(sobra)} posição/posições reais (com stock) não couberam "
                f"nas filas manuais desenhadas -- alarga-as ou acrescenta mais uma fila desta tipologia.")

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
    for item in layout_fantasma_json:
        item["x"] = round(item["x"] - min_x, 4)
        item["y"] = round(item["y"] - min_y, 4)
    for item in racks_manuais_json:
        item["x"] = round(item["x"] - min_x, 4)
        item["y"] = round(item["y"] - min_y, 4)
    for z in zonas_layout:
        z["x"] = round(z["x"] - min_x, 4)
        z["y"] = round(z["y"] - min_y, 4)
    for c in corredores_json:
        c["x"] = round(c["x"] - min_x, 4)
        c["y"] = round(c["y"] - min_y, 4)

    # Pilhas no chão: são uma fiada DENTRO do bloco de racks (ver
    # layout.gerar_layout), por isso rodam e deslocam-se exatamente como as
    # posições de rack -- mesmo frame, mesmo contentor, mesma base.
    pilhas_json = []
    for p in warehouse_layout.pilhas_chao:
        px, py, pl, pp = _roda_rect(p.x_m, p.y_m, p.comp_m, p.larg_m,
                                    bloco_w, bloco_h)
        pilhas_json.append(
            {"sku": p.sku,
             "x": round(base_x + px - min_x, 4),
             "y": round(base_y + py - min_y, 4),
             "comp_m": round(pl, 4), "larg_m": round(pp, 4),
             "alt_peca_m": p.alt_peca_m, "n_pecas": p.n_pecas,
             "peso_kg": p.peso_kg, "altura_total_m": round(p.altura_total_m, 3)})

    # NOTA: já não é preciso reajustar/renormalizar por causa das pilhas. Quando
    # elas eram um bloco SEPARADO (além de bloco_h) caíam em coordenadas
    # negativas ao rodar e ficavam fora das paredes; agora que são uma fiada
    # dentro do bloco, o _roda_rect e a normalização dos racks já as cobrem.

    # Contorno do Cubo (edifício real, ver "Cabe no cubo?" no canvas 2D): o
    # frontend manda-o como mais um bloco (type="cubo", nunca rodado) só para
    # entrar na normalização acima (_cantos/pontos já o incluem no min_x/min_y
    # -- sem alteração nenhuma aqui). Devolve-se a MESMA posição normalizada
    # dos restantes, para o 3D o desenhar exatamente onde o 2D o mostra
    # relativo ao arranjo. Não é um bloco arrastável nem entra na área
    # reportada -- é só referência visual fixa.
    cubo_block = next((b for b in body.blocks if b.get("type") == "cubo"), None)
    cubo_json = None
    if cubo_block is not None:
        cw, ch = float(cubo_block.get("w", 0.0)), float(cubo_block.get("h", 0.0))
        cubo_json = {
            "x": round(float(cubo_block.get("x", 0.0)) - min_x, 4),
            "y": round(float(cubo_block.get("y", 0.0)) - min_y, 4),
            "w": cw, "h": ch,
        }

    # Obstáculos (pilares, condutas, zonas interditas, ...): restrições FÍSICAS
    # fixas, do chão ao teto -- NÃO entram no dimensionamento (o nº de racks
    # continua a vir só do stock, como sempre) nem na área reportada. São só
    # referência visual (3D) + aviso se ficarem por baixo do bloco de racks ou
    # de uma zona -- comparação por AABB (retângulo envolvente já rodado): não
    # é geometria exata de polígono rodado, mas chega para avisar, e nunca
    # bloqueia silenciosamente uma sobreposição real.
    def _aabb(cantos):
        xs = [p[0] for p in cantos]
        ys = [p[1] for p in cantos]
        return min(xs), min(ys), max(xs), max(ys)

    def _aabb_overlap(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    obstaculo_blocks = [b for b in body.blocks if b.get("type") == "obstaculo"]
    obstaculos_layout = []
    avisos_obstaculos: list[str] = []
    if obstaculo_blocks:
        aabb_racking = _aabb(_cantos(bloco_racks)) if bloco_racks else None
        aabbs_zonas = [(_aabb(_cantos(b)), b.get("nome") or "zona")
                       for b in body.blocks if b.get("type") == "zone"]
        for ob in obstaculo_blocks:
            nome = ob.get("nome") or "Obstáculo"
            aabb_ob = _aabb(_cantos(ob))
            if aabb_racking is not None and _aabb_overlap(aabb_ob, aabb_racking):
                avisos_obstaculos.append(
                    f"Obstáculo '{nome}' sobrepõe o bloco de racks -- ajusta as posições.")
            for aabb_z, nome_z in aabbs_zonas:
                if _aabb_overlap(aabb_ob, aabb_z):
                    avisos_obstaculos.append(
                        f"Obstáculo '{nome}' sobrepõe a zona '{nome_z}' -- ajusta as posições.")
            obstaculos_layout.append({
                "nome": nome,
                "x": round(float(ob.get("x", 0.0)) - min_x, 4),
                "y": round(float(ob.get("y", 0.0)) - min_y, 4),
                "w": float(ob.get("w", 0.0)), "h": float(ob.get("h", 0.0)),
                "rot": float(ob.get("rot", 0.0) or 0.0),
            })

    # Sem bloco de racks no arranjo (bloco_racks is None -- só racks manuais /
    # obstáculos / zonas / Cubo, nunca se carregou "Gerar racks"): o pipeline
    # real (boxing/racking/layout, acima) corre sempre -- é preciso para as
    # outras coisas poderem ser normalizadas/posicionadas -- mas o resultado
    # não tem posição nenhuma no canvas para ir, porque o utilizador nunca lá
    # pôs um bloco de racks. Sem esta guarda, o 3D desenhava o armazém REAL
    # inteiro (paletes a sério, a partir do stock) espremido em (0,0) por
    # cima de tudo o resto -- o "ficou a loucura" reportado: o utilizador
    # via caixas empilhadas a sério que não pediu, só porque tinha filas
    # manuais no canvas. Sandbox puro (sem bloco de racks) mostra só o que
    # foi colocado à mão.
    if bloco_racks is None:
        layout_json = []
        layout_fantasma_json = []
        pilhas_json = []
        corredores_json = []
        area_racks_reportada = 0.0
        area_chao_m2 = 0.0
    else:
        area_racks_reportada = dim.area_racks_m2

    return {
        "layout": layout_json,
        "layout_fantasma": layout_fantasma_json,
        "pilhas_chao": pilhas_json,
        "area_oversize_chao_m2": round(area_chao_m2, 2),
        "zonas_layout": zonas_layout,
        "corredores": corredores_json,
        "area_racks_m2": round(area_racks_reportada, 2),
        "area_zonas_m2": round(sum(z["area_m2"] for z in zonas_layout), 2),
        "largura_total_m": round(largura, 2),
        "profundidade_total_m": round(profundidade, 2),
        "area_total_m2": round(largura * profundidade, 2),
        "cubo": cubo_json,
        "obstaculos_layout": obstaculos_layout,
        "racks_manuais": racks_manuais_json,
        "avisos": avisos_obstaculos + avisos_manuais,
    }


# ------------------------------------------------------------------- static

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def main():
    import uvicorn
    uvicorn.run("warehouse.web:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()

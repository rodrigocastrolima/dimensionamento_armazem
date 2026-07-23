"""Modelo de domínio. Separa claramente INPUT (medido/dado) de OUTPUT (calculado).

Unidades: METROS e m³ em todo o lado (alinhado com o master_data.xlsx).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class LoadType(str, Enum):
    XS = "XS"             # caixa de picking pequena (bin), abaixo do S
    S = "S"
    M = "M"
    L = "L"
    MEIA = "MEIA"         # meia-palete (0.8 x 0.6): entrada do catálogo, como a PALLET
    PALLET = "PALLET"     # palete: entrada do catálogo (BoxType), packed como um bin
    LONGO = "LONGO"       # material comprido: NÃO é caixa -> vai direto para cantilever
    OVERSIZE = "OVERSIZE"  # não assenta em NENHUMA unidade de carga do catálogo
                           # (nem na footprint da palete): precisa de solução
                           # dedicada. Nunca é inventado como "1 por palete" --
                           # é reportado nos avisos para decisão humana.


class Empilhamento(str, Enum):
    """Classificação de empilhamento vertical de um artigo (coluna 'Empilhável')."""
    NAO_EMPILHAVEL = "NAO_EMPILHAVEL"   # não empilha: 1 peça de altura
    EMPILHAVEL = "EMPILHAVEL"           # empilha inteiro, sem encaixar (sem nesting)
    ANINHAVEL = "ANINHAVEL"             # encaixa/aninha nas peças anteriores do stack


# --------------------------- INPUT ---------------------------
@dataclass
class Article:
    """Artigo a armazenar (master data). stock_max_un é o motor do dimensionamento."""
    sku: str
    descricao: str
    comp_m: float
    larg_m: float
    alt_m: float
    peso_kg: float
    stock_max_un: int
    pecas_por_carro: int           # quantidade para uma car (picking unit)
    orientacao: str = "Livre"      # restrição de rotação no packing
    empilhamento: Empilhamento = Empilhamento.EMPILHAVEL
    fragilidade: str = "Normal"
    geometria: str = "Regular"
    classe: str = "RIGIDO"          # RIGIDO / FLEXIVEL_CONTENTOR
    subtipo_longo: str = ""         # PAINEL_GRANDE / VOLUMOSO_OVERSIZE / "" (fora de LONGO ou sem subtipo)

    @property
    def volume_m3(self) -> float:
        return self.comp_m * self.larg_m * self.alt_m


@dataclass
class BoxType:
    """Unidade de carga do catálogo: S/M/L e também PALLET.

    INT = envelope útil (espaço onde os artigos entram); EXT = ocupação no rack.
    A palete entra aqui como mais uma unidade: INT = footprint x altura máx. de
    empilhamento; EXT inclui o deck. É a forma de operacionalizar "a palete é,
    para o packer, uma caixa" — sem paredes, mas o mesmo bin.
    """
    nome: LoadType
    int_comp_m: float
    int_larg_m: float
    int_alt_m: float
    ext_comp_m: float
    ext_larg_m: float
    ext_alt_m: float
    peso_max_kg: float


@dataclass
class RackType:
    """Tipologia de rack fornecida pelo utilizador."""
    nome: str
    larg_m: float                  # largura do vão / módulo
    prof_m: float                  # profundidade
    alt_nivel_m: float             # altura ÚTIL POR NÍVEL (não o total do prumo)
    niveis: int                    # nº de níveis
    aceita: list[LoadType] = field(default_factory=list)   # cargas compatíveis
    footprint_m2: float = 0.0      # área no chão; calculada se 0 (larg x prof)
    comp_braco_m: float = 0.0      # cantilever: comprimento do braço (0 se N/A)
    corredor_m: float = 0.0        # largura do corredor de serviço (layout.py); 0 = usa default


# --------------------------- OUTPUT ---------------------------
@dataclass
class BoxAssignment:
    """Passo 1: como guardar o stock máximo de um SKU.

    Para LONGO, load_type=LONGO e pecas_por_unidade=1 (o artigo é a própria
    unidade na cantilever; não há caixa).
    """
    sku: str
    load_type: LoadType
    pecas_por_unidade: int
    num_unidades: int              # nº de caixas/paletes/itens p/ o stock máximo


@dataclass
class RackRequirement:
    """Passo 2: racks necessários de uma tipologia (resumo agregado).

    Um RackType pode ser empilhado em módulos na vertical (até à altura do
    armazém), e cada posição de chão pode ter um nº de blocos DIFERENTE
    (decidido posição a posição pelo packer, ver PosicaoChao/allocate_to_racks).
    num_racks = soma de todos os módulos físicos (todas as posições);
    posicoes_chao = nº de posições no chão (footprint);
    blocos_empilhados = MÉDIA arredondada para cima, só para referência/exibição
    -- a verdade por posição está em PosicaoChao.blocos.
    """
    rack_nome: str
    num_racks: int
    load_types: list[LoadType] = field(default_factory=list)
    posicoes_chao: int = 0
    blocos_empilhados: int = 1


@dataclass
class CaixaColocada:
    """Uma caixa (ou stack de caixas) posicionada (Passo 2, packer 2D real).

    x_m/y_m: canto inferior-esquerdo da caixa dentro do nível (footprint
    larg_m x prof_m do rack), já na orientação escolhida pelo packer.
    pecas_nesta_caixa: peças do artigo dentro desta caixa/stack.
    num_boxes_in_stack: quantas caixas físicas estão empilhadas (altura combinada).
    """
    sku: str
    descricao: str
    load_type: LoadType
    x_m: float
    y_m: float
    comp_m: float       # dimensão ao longo de larg_m do rack (já orientada)
    larg_m: float        # dimensão ao longo de prof_m do rack (já orientada)
    alt_m: float
    pecas_nesta_caixa: int = 0
    eh_picking: bool = False
    num_boxes_in_stack: int = 1  # quantas caixas neste stack


@dataclass
class NivelRack:
    """Um nível físico dentro de um bloco: lista de caixas já colocadas (2D)."""
    nivel_idx: int               # 0-based dentro do bloco
    eh_picking: bool
    caixas: list[CaixaColocada] = field(default_factory=list)


@dataclass
class BlocoRack:
    """Um módulo físico empilhado dentro de uma posição de chão."""
    bloco_idx: int                # 0-based, 0 = térreo
    niveis: list[NivelRack] = field(default_factory=list)


@dataclass
class PosicaoChao:
    """Uma posição de chão (footprint) de uma tipologia de rack, com o seu
    próprio nº de blocos empilhados -- decidido individualmente pelo packer,
    não imposto globalmente à tipologia inteira."""
    rack_nome: str
    indice: int                   # 0-based dentro da tipologia
    blocos: list[BlocoRack] = field(default_factory=list)

    @property
    def num_blocos(self) -> int:
        return len(self.blocos)


@dataclass
class WarehouseDimensioning:
    """Passo 3: footprint dos racks por tipologia."""
    area_racks_m2: float
    detalhe: dict = field(default_factory=dict)  # rack_nome -> {"num_racks": int, "footprint_total_m2": float}


@dataclass
class Zona:
    """Zona funcional do armazém (INPUT): área fora dos racks -- receção, expedição,
    equipamento, staging, etc. Dimensão dada diretamente (comp x larg), sem SKU/stock
    associado. Área somada à área total à parte dos corredores de racks."""
    nome: str
    categoria: str          # livre (ex: "Receção", "Expedição", "Equipamento", "Staging")
    comp_m: float
    larg_m: float
    justificacao: str = ""

    @property
    def area_m2(self) -> float:
        return self.comp_m * self.larg_m


@dataclass
class ZonaLayout:
    """Posição física de uma Zona no layout 3D (Passo 3b), ao lado do bloco de racks."""
    zona: Zona
    x_m: float
    y_m: float
    largura_m: float
    profundidade_m: float


@dataclass
class PosicaoLayout:
    """Passo 3b — posição de chão com coordenadas físicas reais (layout.py).

    Cada PosicaoLayout aponta para a PosicaoChao alocada (mesmo conteúdo de
    blocos/níveis/caixas) e acrescenta onde fica fisicamente no chão: filas
    duplas costas-com-costas por tipologia, com corredor de serviço entre
    blocos duplos. orientacao=True -> largura do rack (larg_m) ao longo do
    eixo X da fila; False -> ao longo do eixo Y (fila virada 90°).
    """
    posicao: "PosicaoChao"
    x_m: float
    y_m: float
    largura_m: float    # extensão ocupada no chão ao longo de x
    profundidade_m: float  # extensão ocupada no chão ao longo de y
    fila_idx: int        # índice da fila dupla (0-based) dentro da tipologia
    lado: str             # "frente" ou "tras" (qual das 2 fiadas costas-com-costas)


@dataclass
class WarehouseLayout:
    """Passo 3b: layout físico completo -- posições com coordenadas reais,
    dimensões do retângulo envolvente e área de corredores incluída."""
    posicoes: list[PosicaoLayout] = field(default_factory=list)
    largura_total_m: float = 0.0
    profundidade_total_m: float = 0.0
    area_total_m2: float = 0.0          # racks + corredores, retângulo envolvente
    area_corredores_m2: float = 0.0
    zonas: list[ZonaLayout] = field(default_factory=list)  # posicionadas à parte, ver Zona

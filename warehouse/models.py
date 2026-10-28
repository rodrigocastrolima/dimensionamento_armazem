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
    OVERSIZE = "OVERSIZE"  # não assenta em NENHUMA unidade de carga do catálogo
                           # (nem na footprint da palete): precisa de solução
                           # dedicada. Nunca é inventado como "1 por palete" --
                           # é reportado nos avisos para decisão humana.
    OVERSIZE2 = "OVERSIZE2"  # carrinho dedicado (SKUs em Config.oversize2_skus):
                           # peças transportadas DE PÉ num carrinho de footprint
                           # FIXO (Config.oversize2_comp_m x oversize2_larg_m),
                           # capacidade FIXA (Config.oversize2_capacidade_un) --
                           # não calculada por geometria/packer como as outras.
                           # Vai sempre ao chão (nunca a rack), ver boxing.
                           # choose_load/main.py e linha.elementos_carrinho.


class Empilhamento(str, Enum):
    """Classificação de empilhamento RÍGIDO vertical de um artigo (coluna
    'Empilhável'). Só decide o que acontece no eixo que ficar vertical QUANDO
    esse eixo não é o eixo de aninhagem do artigo (ver Article.
    eixo_aninhagem, coluna 'EIXO_ANINHAGEM') -- aninhar é uma propriedade
    geométrica separada, independente desta classificação (ver CLAUDE.md
    "separar aninhável de empilhável")."""
    NAO_EMPILHAVEL = "NAO_EMPILHAVEL"   # não empilha: 1 peça de altura
    EMPILHAVEL = "EMPILHAVEL"           # empilha inteiro, sem encaixar (sem nesting)


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
    # Peças deste artigo consumidas por CARRO. É FRACIONÁRIO por natureza: 0.5 =
    # 1 peça a cada 2 carros (par esquerdo/direito), 0.03 = 1 a cada ~33. Era
    # `int` e o int() da leitura truncava: os 138 SKUs a 0.5 viravam 0 e deixavam
    # de pedir zona de picking em silêncio (ver io_excel.load_articles).
    pecas_por_carro: float         # quantidade para um carro (picking unit)
    # Coluna 'ALTURA_OVERRIDE' do master data. 'x'/'y'/'z' força essa cota
    # (Comprimento/Largura/Altura) como o eixo vertical (Z) nas unidades de
    # carga; vazio (default) = livre, testam-se as 3 e fica-se com a que mais
    # peças rende (ver boxing.eixos_possiveis). Substitui a antiga
    # 'Orientação_permitida' (Livre/Base fixa/Não deitar).
    altura_override: str = ""
    empilhamento: Empilhamento = Empilhamento.EMPILHAVEL
    # Coluna 'EIXO_ANINHAGEM' do master data. 'x'/'y'/'z' diz qual cota
    # (Comprimento/Largura/Altura) tem a geometria que encaixa/aninha na peça
    # anterior (ver boxing.pecas_no_eixo); vazio (default) = não aninha.
    # Independente de `empilhamento` e de `altura_override`: essa cota pode
    # calhar vertical ou no plano consoante a orientação escolhida (ex.: peça
    # "tombada") -- só limita EM QUE DIREÇÃO a peça aninha, não obriga essa
    # direção a ser a vertical.
    eixo_aninhagem: str = ""
    fragilidade: str = "Normal"
    geometria: str = "Regular"
    classe: str = "RIGIDO"          # RIGIDO / FLEXIVEL_CONTENTOR
    # Stock atual/bruto do master data (coluna 'Stock_máx_un'), só informativo:
    # NÃO alimenta o dimensionamento (esse é sempre stock_max_un, decidido pelo
    # Passo 0 -- ver boxing.stock_para_autonomia). Serve de referência nos
    # resultados (sheet Autonomia) para comparar contra o stock efetivamente
    # usado.
    stock_atual_un: int = 0
    # Estação(ões) de montagem onde a peça é consumida (coluna 'Estação' do
    # master data, opcional). Bruto tal como vem da folha -- pode ter mais do
    # que uma, separadas por vírgula (ex.: "C1, P1"); ver
    # bordo_linha.resolver_estacao para a resolução a uma única estação.
    # "" = sem estação atribuída no master data.
    estacao: str = ""
    # Colunas 'CAIXA_FORCADA'/'PECAS_CAIXA_FORCADA' do master data (opcionais):
    # exceção MANUAL ao best-fit (boxing.choose_load). CAIXA_FORCADA = nome de
    # uma caixa do catálogo (XS/S/M/L/MEIA/PALLET), já em maiúsculas. Com
    # PECAS_CAIXA_FORCADA > 0 a capacidade é essa, tal e qual -- decisão tomada
    # fora do motor (ex.: peças inclinadas numa caixa aberta, que a geometria
    # do motor não modela), sem fill_factor nem arredondamentos. Sem
    # quantidade (0), conta-se pela geometria normal dentro da caixa forçada.
    # "" = best-fit normal.
    caixa_forcada: str = ""
    pecas_caixa_forcada: int = 0

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
class CaixaEspecial:
    """'Caixa especial' definida pelo utilizador (folha «Caixas Especiais»
    do master data, editável também pela UI web em «Caixas & Racks») --
    substitui os antigos Config.oversize2_skus/oversize2_comp_m/larg_m/
    alt_m/capacidade_un: dá override ao Passo 1 (boxing.choose_load) para
    os SKUs listados, ignorando por completo a caixa/quantidade que o
    best-fit normal escolheria (`LoadType.OVERSIZE2`). As dimensões são as
    do EQUIPAMENTO de transporte (ex.: um carrinho dedicado), não da peça --
    Comprimento_m/Largura_m/Altura_m do artigo no master data continuam a
    ser as da peça, só informativas.

    Um registo pode cobrir VÁRIOS SKUs (mesmo equipamento partilhado por
    mais do que uma peça) -- MAS cada SKU tem a sua PRÓPRIA quantidade
    (pedido explícito: "posso querer 4 de uma referência e 2 de outra" no
    mesmo equipamento) -- `quantidades` é sempre um dict {sku: quantidade},
    mesmo com 1 SKU só. As dimensões (comp/larg/alt) são do equipamento em
    si, por isso SÃO partilhadas por todos os SKUs do registo.

    `id` (coluna 'Número/ID') identifica o CARRINHO FÍSICO -- cada id é 1
    carrinho na linha (linha.elementos_carrinho) e a `quantidade` de cada
    SKU é o stock desse SKU na linha, sem Passo 0 (bordo_linha.
    calcular_bordo_linha). `estacao` (coluna 'Estação') é onde o carrinho
    fica; vazio = usa a estação do SKU no master data."""
    quantidades: dict[str, int]   # {sku: peças no carrinho}
    comp_m: float
    larg_m: float
    alt_m: float
    notas: str = ""
    id: str = ""
    estacao: str = ""

    @property
    def skus(self) -> tuple[str, ...]:
        return tuple(self.quantidades.keys())


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
    corredor_m: float = 0.0        # largura do corredor de serviço (layout.py); 0 = usa default
    # True só nas tipologias SINTÉTICAS derivadas por racking.com_par_oversize
    # (nunca nas da folha «Racks»): prof_m já é a profundidade de UM PAR
    # costas-com-costas (2 x a profundidade real de cada rack). Diz ao
    # layout.py para não as emparelhar de novo (senão dobrava a profundidade
    # outra vez) -- cada posição desta tipologia ocupa uma fiada única.
    profundidade_inclui_par: bool = False
    # Alturas de nível HETEROGÉNEAS (bordo de linha, estante dinâmica/flow-rack):
    # vazio (default) = comportamento de sempre, alt_nivel_m uniforme para
    # todos os `niveis`. Quando definido: uma altura por nível, índice 0 =
    # BASE (nível mais baixo), len() tem de bater com `niveis`. Nesse caso
    # alt_nivel_m passa a ser só um valor de REFERÊNCIA (max(alturas_niveis_m),
    # ver io_excel.load_rack_types) -- o motor de capacidade usa sempre
    # racking.altura_nivel/altura_ate_niveis, nunca alt_nivel_m diretamente.
    # No armazém (Rack Porta-Paletes, SIA) são alturas ÚTEIS: deck + carga =
    # passo - viga - folga de manuseio (o passo físico, para o 3D, é
    # racking.cotas_fisicas_niveis). Suportado em rack_capacity, _empacotar_nivel,
    # _abrir_bloco, blocos_empilhados, niveis_efetivos e allocate_to_racks; a
    # estimativa de OVERSIZE (_oversize_plano_altura/rack_capacity_oversize)
    # continua a assumir alt_nivel_m uniforme -- a colocação real respeita
    # cada nível em _empacotar_nivel.
    alturas_niveis_m: tuple[float, ...] = ()


# --------------------------- OUTPUT ---------------------------
@dataclass
class AutonomiaResultado:
    """Passo 0: decisão do stock a usar no dimensionamento (stock_max_un),
    a partir da autonomia mínima em carros (Config.autonomia_minima_carros).
    Ver boxing.stock_para_autonomia. Um SKU por linha, mesma granularidade de
    BoxAssignment -- serve de PROVA (sheet Autonomia) de que o stock final não
    caiu do céu: mostra o stock atual, o teórico e o ajustado lado a lado."""
    sku: str
    stock_atual_un: int             # Stock_máx_un bruto do master data (referência)
    stock_teorico_un: int           # ceil(autonomia_minima_carros x pecas_por_carro)
    load_type: "LoadType | None"    # unidade de carga usada para encher o stock teórico
    pecas_por_unidade_pratico: int  # capacidade prática (capacidade_reportada) dessa unidade
    num_unidades: int               # ceil(stock_teorico_un / pecas_por_unidade_pratico)
    stock_ajustado_un: int          # = Article.stock_max_un final (o que entra no Passo 1)
    override_manual: bool           # True se veio de Config.stock_autonomia_override_sku


@dataclass
class BoxAssignment:
    """Passo 1: como guardar o stock máximo de um SKU."""
    sku: str
    load_type: LoadType
    pecas_por_unidade: int
    num_unidades: int              # nº de caixas/paletes/itens p/ o stock máximo


@dataclass
class BoxJustificacao:
    """Passo 1, rasto de cálculo por SKU: PROVA de que a unidade de carga
    escolhida (BoxAssignment) é a correta, não só qual foi escolhida.

    Um SKU por linha (mesma granularidade de BoxAssignment). Campos de
    caixa/plano ficam None para OVERSIZE, cujo critério de admissão é
    geométrico e não de encaixe (ver `criterio`, sempre preenchido em texto)."""
    sku: str
    descricao: str
    comp_m: float
    larg_m: float
    alt_m: float
    peso_kg: float
    load_type: LoadType
    orientacao_usada: str            # qual cota ficou vertical (texto legível)
    caixa_int_comp_m: float | None
    caixa_int_larg_m: float | None
    caixa_int_alt_m: float | None
    pecas_por_plano_teorico: int | None   # floor geométrico, antes do fill_factor
    pecas_por_plano: int | None           # depois do fill_factor (70%)
    pecas_por_altura: int | None          # empilhamento vertical (pieces_in_stack)
    pecas_por_peso: int | None            # None = peso não limita (desconhecido ou não aplicável)
    fator_limitante: str             # "Geometria" | "Peso" | "N/A (armazenagem OVERSIZE)"
    pecas_por_unidade: int           # valor final usado no dimensionamento (= BoxAssignment.pecas_por_unidade)
    stock_max_un: int
    num_unidades: int
    criterio: str                    # explicação textual do cálculo/critério aplicado
    # Só PALLET/MEIA: deck + carga real (boxing.altura_real_unidade), a altura
    # com que a unidade vai para o racking -- não a nominal do catálogo.
    altura_real_unidade_m: float | None = None


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
class PilhaChao:
    """Uma pilha de peças volumosas arrumada no CHÃO, sem rack (block stacking).

    Existe para o 3D poder desenhar o que `area.area_oversize_chao` dimensiona:
    sem isto a área era reportada mas invisível na cena. Não é uma PosicaoChao --
    não tem tipologia, módulos nem níveis; é só um empilhamento de peças soltas.

    comp_m/larg_m são a footprint da peça DEITADA (as duas maiores cotas);
    alt_peca_m é a menor cota, e a pilha tem `n_pecas` dessas em altura.
    """
    sku: str
    descricao: str
    x_m: float
    y_m: float
    comp_m: float          # ao longo de x
    larg_m: float          # ao longo de y
    alt_peca_m: float      # altura de UMA peça
    n_pecas: int           # peças nesta pilha
    peso_kg: float = 0.0   # peso total da pilha

    @property
    def altura_total_m(self) -> float:
        return self.alt_peca_m * self.n_pecas


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
class Corredor:
    """Faixa de circulação de serviço entre filas de racks (Passo 3b).

    É a faixa por onde um operador anda e de onde faz picking. O layout já a
    calcula ao dispor as filas duplas; guardá-la explicitamente evita que o
    frontend a tenha de reconstruir por adivinhação (que só funcionava numa
    orientação). Um corredor é sempre um retângulo axis-aligned no plano do
    chão: x_m/y_m é o canto, comp_m ao longo de x, larg_m ao longo de y. A
    dimensão MAIOR é o eixo de circulação."""
    x_m: float
    y_m: float
    comp_m: float          # extensão ao longo de x
    larg_m: float          # extensão ao longo de y


@dataclass
class EspacoLivre:
    """Espaço genuinamente vazio dentro do retângulo envolvente do bloco de
    racks (Passo 3b): parte de uma fiada que não chega à largura-alvo comum
    do bloco inteiro (ver layout._larguras_alinhadas) -- tipicamente a última
    fiada de uma tipologia, ou uma tipologia mais estreita que a largura-alvo.

    Distinto de Corredor (esse é circulação medida, sempre "ocupado") e da
    footprint dos racks -- é chão livre, sem função nenhuma atribuída ainda.
    Só geometria informativa: nunca conta para area.floor_area/
    resumo_area_total (mesmo cuidado já aplicado a pilhas_chao/
    area_oversize_chao -- ver CLAUDE.md)."""
    rack_nome: str
    fila_idx: int
    lado: str
    x_m: float
    y_m: float
    comp_m: float           # extensão ao longo de x
    larg_m: float            # extensão ao longo de y (= profundidade da fiada)


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
    corredores: list[Corredor] = field(default_factory=list)  # faixas de serviço reais
    espacos_livres: list[EspacoLivre] = field(default_factory=list)  # ver EspacoLivre
    # Pilhas de peças volumosas no chão (sem rack). Como as zonas, só recebem
    # coordenadas para não sobrepor os racks -- a área conta em
    # ResumoAreaTotal.area_oversize_chao_m2, não em largura/profundidade_total_m.
    pilhas_chao: list[PilhaChao] = field(default_factory=list)
    # Avisos do envelope (Config.largura_max_m / profundidade_max_m): o layout
    # respeita a largura mas o stock manda no nº de racks, por isso a
    # profundidade pode exceder o teto. Lista vazia = cabe tudo (ou sem limites).
    avisos: list[str] = field(default_factory=list)

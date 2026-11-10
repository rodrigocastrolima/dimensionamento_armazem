"""Parâmetros e decisões de negócio. Mudar estes valores muda resultados.

Campos CLIENT-SPECIFIC (os que mudam de cliente para cliente -- ver auditoria
"CORE vs CLIENT-SPECIFIC") já não se editam aqui: vivem em
`dados/clients/<nome>.json` e entram via `Config.for_client(nome)` (ver
classmethod ao fundo do ficheiro). Os valores por omissão abaixo continuam a
existir só como fallback de um `Config()` nu (ex.: testes que constroem
Config() sem cliente nenhum) -- não são "a configuração", são só o default de
campo do dataclass.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
import os
from pathlib import Path

# dados/clients/<nome>.json vive ao lado de dados/ (irmão de warehouse/), não
# relativo à cwd -- para Config.for_client funcionar tanto a partir de
# `python -m warehouse.main` (cwd = dimensionamento_armazem/) como de dentro
# de pytest (cwd pode ser outro). master_data/output continuam relativos à
# cwd de sempre (comportamento inalterado: quem já corria a partir da pasta
# certa continua a funcionar sem tocar em nada).
_CLIENTS_DIR = Path(__file__).resolve().parent.parent / "dados" / "clients"
_REPO_DIR = Path(__file__).resolve().parent.parent


def _caminho_do_repo(valor: str) -> Path:
    """Caminho relativo à raiz do repositório, absoluto e NORMALIZADO (sem
    «..»). Sem normalizar, «<repo>\\..\\..\\..\\Linha de Montagem\\...» passa
    os 260 caracteres do Windows (MAX_PATH) e o open() dá «No such file»,
    embora o caminho resolvido tenha só ~245."""
    if not str(valor):
        return Path("")
    p = Path(valor)
    if not p.is_absolute():
        p = _REPO_DIR / p
    return Path(os.path.normpath(p))


# Nomes de campo do dataclass que Config.for_client aceita ler do JSON do
# cliente, com o conversor a aplicar ao valor lido (JSON só tem list/dict/
# str/float/int -- os campos Path/tuple do dataclass precisam de conversão
# explícita). Mantido como tabela em vez de "aceitar tudo o que vier no JSON"
# para um cliente não poder, por engano, injetar um campo CORE (ex.:
# fill_factor_plano) através do ficheiro errado.
_CLIENT_FIELD_CONVERTERS = {
    "data_source": str,
    "master_data": Path,
    "output": Path,
    "autonomia_minima_carros": float,
    "pallet_ultimo_recurso": bool,
    "unidade_da_linha": bool,
    "capacidade_override_sku": dict,
    "stock_autonomia_override_sku": dict,
    "palete_por_sku": dict,
    "oversize_racks": tuple,
    "oversize_par_racks": tuple,
    "max_modulos_por_tipologia": dict,
    "empilhamento_por_nivel": tuple,
    "picking_manual_racks": tuple,
    "pilar_rack_m": dict,
    "pressupostos_picking": _caminho_do_repo,
    "output_procura": Path,
}


@dataclass
class Config:
    # Nome do cliente que originou esta instância (ver Config.for_client).
    # "" = Config() nu, sem cliente associado (comportamento de sempre, usado
    # por quem constrói Config() diretamente, ex. testes/racking.py:648).
    client_name: str = ""

    # De onde vêm os dados deste cliente: "excel" (master_data, comportamento
    # de sempre) ou "supabase" (client_name identifica as linhas nas tabelas
    # clients/articles/box_catalog/rack_types/zones -- ver
    # warehouse/data_access.py e supabase/README.md). Escolhido por
    # configuração, não por código: o resto do pipeline recebe sempre os
    # mesmos dataclasses (Article/BoxType/RackType/Zona) de qualquer origem.
    data_source: str = "excel"

    # caminhos (fallback de um Config() nu -- ver nota no topo do ficheiro;
    # o valor real de cada cliente vem de dados/clients/<nome>.json).
    # master_data só é lido quando data_source == "excel"; output (o
    # resultados.xlsx que o pipeline ESCREVE) é sempre um ficheiro, mesmo
    # com data_source == "supabase" -- a origem dos dados de entrada e o
    # relatório de saída são eixos independentes.
    master_data: Path = Path("dados/master_data_classificado_v3.xlsx")
    output: Path = Path("dados/resultados.xlsx")

    # Procura de picking (warehouse/procura.py): livro do comboio de onde vêm
    # os pressupostos do abastecimento à linha (PFEP, «Consumo SKU», horário
    # das voltas, regra do bulk) -- é o utilizador que os decide lá, aqui só
    # se leem. Vazio = procura desligada. carros_por_dia: inteiro >= 1 (o
    # horário de consumo é por carro); 1 = o do livro, validado contra ele.
    pressupostos_picking: Path = Path("")
    output_procura: Path = Path("dados/procura_picking.xlsx")
    carros_por_dia: int = 1
    # Horizonte da simulação (dias úteis) -- não é escolha do utilizador. O
    # padrão de consumo não se repete na prática (cada referência tem o seu
    # período, de 1 a 1600 dias; o m.m.c. dá ~4e16 dias), e os picos aparecem
    # quando os períodos coincidem: com nível inicial 0 o 1.º é ao dia 31, que
    # o livro (30 dias, limite das 300 linhas da «7b») não vê. 250 = 1 ano de
    # operação. A validação contra o livro corre à parte com os 30 dias dele.
    simulacao_dias: int = 250
    # Consumíveis (SKUs sem PFEP): «semanal» = reposição à parte, fora do
    # comboio, de consumiveis_periodo_dias em consumiveis_periodo_dias dias
    # úteis (decisão do utilizador, 2026-11-05: "reposição semanal à parte por
    # agora"); «voltas» = nas voltas de caixas/paletes e no bulk, como as
    # referências do PFEP. 5 dias úteis = 1 semana (ASSUNÇÃO: 1 turno, 5 dias).
    consumiveis_reposicao: str = "semanal"
    consumiveis_periodo_dias: int = 5

    # Tempos de picking (warehouse/tempos.py) -- também são o custo da alocação.
    # Uma viagem por unidade (ida e volta), como o livro. Velocidades: ASSUNÇÃO
    # (o livro não as tem; o manuseio é calibrado para a média bater com ele).
    picking_velocidade_m_s: dict = field(default_factory=lambda: {
        "a pé": 0.8, "order-picker": 1.5, "empilhador": 2.0})
    # equipamento por tipologia (substring do nome); o resto vai a pé. As peças
    # de carrinho e de kit vão sempre a pé.
    picking_equipamento: dict = field(default_factory=lambda: {
        "prateleira": "order-picker", "porta-paletes": "empilhador"})
    # +30 s se o topo do nível passar de 1,6 m (zona de alcance): os 30 s são os
    # do livro («Arm Picking + Preparação», linha 56).
    picking_altura_limite_m: float = 1.6
    picking_altura_extra_min: float = 0.5
    picking_grelha_m: float = 0.25          # passo da grelha dos percursos
    # passagem mínima para um operador com carro: uma fresta mais estreita entre
    # racks/obstáculos não é caminho (tempos.construir_grelha, folga = metade)
    picking_passagem_min_m: float = 0.8
    # para onde vai o picking (o operador leva aí cada unidade): nome da zona
    # (igual, sem maiúsculas; senão a 1.ª que o contenha). Pedido do utilizador
    # (2026-11-05): a «ZONA DE ESPERA», não a zona do comboio.
    picking_zona_destino: str = "ZONA DE ESPERA"
    # voltas regulares no horário do livro TODOS os dias (regra do utilizador,
    # 2026-11-05: o horário é calculado pelo livro para o pior dos 30 dias, o
    # mais cedo possível; num dia mais pesado a volta sai atrasada). False =
    # cada dia parte mal possa. A hora mínima do bulk (13:00) e a ordem «caixas
    # primeiro» vêm do livro («Parâmetros» C104 e C106).
    voltas_horario_do_livro: bool = True
    tempos_calibracao: Path = Path("dados/tempos_calibracao.json")   # manuseio calibrado (referência)

    # Alocação às racks do arranjo (warehouse/alocacao.py). Custo de um nível =
    # minutos de uma visita (ida e volta + altura, tempos.custo_visita_min).
    # Contiguidade: a unidade seguinte de um SKU fica numa posição onde ele já
    # está (ou num módulo vizinho) se lá houver lugar até esta diferença de
    # custo (min) face ao mais barato. 1,0 min (2026-11-05, com a zona de bulk):
    # nas prateleiras a diferença máxima é ~0,8 min (+30 s de altura + andar),
    # por isso um SKU fica sempre junto -- com 0,1 ficavam 5 SKUs partidos por
    # módulos não vizinhos, por +0,3 min/dia de deslocação. 0 = só custo (espalha).
    alocacao_tolerancia_contiguo_min: float = 1.0
    # zona de bulk (decisão do utilizador, 2026-11-05): o que vai no bulk sai
    # todos os dias e prepara-se de uma vez, num percurso -> arruma-se primeiro,
    # junto ao destino do picking; depois as voltas, depois os consumíveis.
    # False = só frequência × custo da visita (o bulk espalhava-se pelos níveis
    # baixos de todos os módulos, porque subir acima de 1,6 m custa mais do que andar).
    alocacao_zona_bulk: bool = True
    # OVERSIZE em racks normais (decisão do utilizador, 2026-11-09): numa fila
    # DUPLA de porta-paletes com corredor dos dois lados, o mesmo módulo da
    # frente e de trás funciona como uma emparelhada (posição «frente+trás»).
    alocacao_oversize_em_pares: bool = True
    output_alocacao: Path = Path("dados/alocacao.xlsx")
    alocacao_fixacoes: Path = Path("dados/alocacao_fixacoes.json")   # {sku: endereço}
    alocacao_estado: Path = Path("dados/alocacao_estado.json")       # última alocação guardada

    # Passo 1 — escolha de caixa
    # NOTA: existiu aqui um `box_strategy` (enum BoxStrategy com FEWEST_BOXES /
    # MIN_WASTE / SMALLEST_FIT) e um `min_fill_ratio`. Foram REMOVIDOS por serem
    # código morto: só FEWEST_BOXES chegou a ser implementado (boxing.choose_load
    # minimiza ceil(stock_max_un / pecas_por_unidade), empate -> caixa mais
    # pequena) e `min_fill_ratio` só servia o SMALLEST_FIT. A decisão está
    # fechada e validada: minimizar nº de caixas é o que minimiza chão --
    # estratégias de volume mínimo dão blocos maiores. Não reintroduzir como
    # parâmetro: parecia configurável, mas ninguém o lia.
    pallet_threshold_unidades: int = 6   # acima disto em caixas L -> palete

    # Decisão de negócio (não de espaço): usar a PALETE só como ÚLTIMO recurso
    # em vez de a deixar competir normalmente no best-fit de boxing.choose_load.
    # Por omissão (False) o motor continua a escolher a palete sempre que ela
    # minimiza o nº de unidades de carga (comportamento validado como o que
    # minimiza chão -- ver a nota acima). Ligar isto TROCA chão por
    # movimentabilidade: caixas L/MEIA/M/S/XS são mais fáceis e seguras de
    # movimentar à mão/porta-paletes do que uma palete cheia, o que pode
    # compensar custar mais área num armazém já grande. Um SKU só continua em
    # PALLET se não couber fisicamente em NENHUMA caixa mais pequena -- o
    # mesmo fallback que já existia para decidir OVERSIZE não muda.
    #
    # CLIENT-SPECIFIC: é uma escolha operacional de CADA cliente (não uma
    # verdade universal do algoritmo) -- vive em dados/clients/<nome>.json,
    # não aqui.
    pallet_ultimo_recurso: bool = False

    # Decisão de negócio (SIA, 2026-10-14): a unidade de movimentação de cada
    # SKU é decidida pela LINHA (dimensionamento_linha, bordo de linha) e é
    # imposta aos fornecedores -- o armazém HERDA-A em vez de escolher a sua.
    # True = boxing.choose_load devolve o tipo de carga que a linha escolhe
    # (boxing.unidade_da_linha: mesmo motor, alvo de autonomia_minima_carros_linha
    # carros, regras da linha); o armazém só decide QUANTAS dessas unidades
    # guarda (Passo 0 com autonomia_minima_carros). Com isto ligado,
    # pallet_ultimo_recurso deixa de mandar nos SKUs herdados (a linha não o
    # tem). Exceção: SKUs que na linha vão em CARRINHO (skus_carrinho_linha)
    # -- não há carrinhos no armazém, seguem o best-fit próprio do armazém.
    # CLIENT-SPECIFIC (dados/clients/<nome>.json).
    unidade_da_linha: bool = False

    # Autonomia (em carros) para a qual a linha escolhe a unidade -- TEM de ser
    # igual a Config.autonomia_minima_carros_linha do dimensionamento_linha.
    autonomia_minima_carros_linha: float = 1.0

    # SKUs que na linha vão num carrinho (folha «Caixas Especiais»). Vazio por
    # omissão: main.py/web.py preenchem-no a partir do master data
    # (io_excel.skus_carrinho_linha) quando unidade_da_linha está ligado.
    skus_carrinho_linha: frozenset = frozenset()

    # Fator usado pela fórmula de aninhagem (boxing.pecas_no_eixo), aplicada a
    # QUALQUER eixo -- vertical ou do plano -- que seja o `Article.
    # eixo_aninhagem` da peça (coluna 'EIXO_ANINHAGEM', ver CLAUDE.md "separar
    # aninhável de empilhável"): cada peça extra acrescenta só
    # fator_empilhamento x dimensão, em vez da dimensão inteira. Independente
    # da coluna 'Empilhável' (Empilhamento), que só decide o eixo vertical
    # QUANDO não é o de aninhagem:
    #   NAO_EMPILHAVEL -> 1 peça de altura nesse eixo.
    #   EMPILHAVEL     -> altura cheia somada (sem nesting), só limitado pela
    #                     altura disponível e pelo peso máximo da caixa.
    # Nos outros eixos (não vertical, não aninhagem) é sempre encaixe rígido.
    fator_empilhamento: float = 0.3

    # Fator de enchimento real no plano horizontal (comp x larg), substituindo o
    # floor() teórico de encaixe perfeito. Folgas de manuseio/forma irregular e
    # encaixe imperfeito reduzem o nº de peças por nível face ao máximo
    # geométrico. Aplicado a TODAS as classes e tipos de carga (XS/S/M/L/MEIA/
    # PALLET, RIGIDO e FLEXIVEL/CONTENTOR) — cobre também o encaixe-de-gume
    # irreal de peças finas postas ao alto pela rotação de orientação (ver
    # boxing.pieces_per_box). 0.80 (pedido explícito, 2026-09): 0.70 era
    # conservador demais para peças regulares/rígidas, sem validação contra
    # dados reais.
    fill_factor_plano: float = 0.80

    # --- Capacidade PRÁTICA reportada por caixa (só afeta o nº de peças que se
    # reporta dentro de cada caixa; NÃO afeta o dimensionamento: o nº de caixas,
    # racks e a área vêm de pecas_por_unidade, que não é tocado aqui).
    # O máximo geométrico a fill_factor_plano dá números impraticáveis de
    # manusear em peça miúda (ex.: 98.112 anilhas num bin XS). Estes limites
    # trazem-nos para quantidades de manuseio reais:
    #   1) teto absoluto de peças por caixa, por tipo (ausente = sem teto);
    #   2) acima de 100 peças, arredonda à centena inferior (número redondo de
    #      contagem/reposição). Abaixo de 100 mantém o valor exato -- arredondar
    #      daria 0, que não é uma capacidade.
    capacidade_teto_por_caixa: dict = field(default_factory=lambda: {
        "XS": 1000, "S": 1000, "MEIA": 1000,
    })
    capacidade_arredonda_centena: tuple = ("XS", "S", "M", "MEIA")
    # Ajustes manuais por SKU (decisão de negócio, prevalece sobre as regras)
    capacidade_override_sku: dict = field(default_factory=lambda: {
        "BEN00.FHG111": 100,
    })

    # Palete decidida à mão por SKU (decisão de negócio, prevalece sobre a
    # unidade da linha e o best-fit): {sku: {"pecas": n, "vertical": "x|y|z"}}.
    # A peça vai numa PALLET com n peças, 1 por camada (a base da carga é a da
    # peça), com a cota `vertical` na vertical. Sem tolerância lateral: a
    # carga pode sair da palete. No rack conta a largura REAL na viga
    # (max(palete, peça)); o que sai para a frente/trás não ocupa rack (só se
    # reporta). Ver boxing.palete_por_sku / base_palete_por_sku.
    palete_por_sku: dict = field(default_factory=dict)

    # --- Passo 0 — stock por autonomia mínima (boxing.stock_para_autonomia) ---
    # Substitui o antigo "Stock_máx_10un_a_utilizar" da master data v2, que era
    # calculado à mão FORA do código (10 carros de autonomia, arredondado para
    # cima para encher por completo a caixa/palete escolhida) e colado como
    # INPUT -- o pipeline não sabia que essa decisão tinha sido tomada, só
    # consumia o número já pronto. Fica algorítmico: por SKU, calcula
    # ceil(autonomia_minima_carros x Peças_por_carro) e arredonda para cima
    # para encher a capacidade PRÁTICA reportada (capacidade_reportada, com
    # teto + arredondamento à centena) da unidade de carga escolhida -- não a
    # capacidade geométrica bruta. Validado por reverse-engineering: bate
    # certo com 716/735 SKUs (97.4%) da master data v2 atual.
    autonomia_minima_carros: float = 10.0

    # Ajustes manuais por SKU ao stock de autonomia (decisão de negócio,
    # prevalece sobre a fórmula). São os 19 SKUs (2.6%) herdados da v2 cujo
    # "Stock_máx_10un_a_utilizar" não batia certo com a fórmula -- preservados
    # aqui em vez de perdidos na migração.
    stock_autonomia_override_sku: dict = field(default_factory=lambda: {
        "BOL00050": 100, "BOL00206": 300, "BOL00220": 300, "BOL00238": 100,
        "BOL00295": 100, "BOL00319": 400, "CLI00008": 1000, "CLI00013": 700,
        "CLI00017": 900, "CLI00039": 1000, "GRM00001": 500, "GRM00002": 300,
        "RBC00001": 700, "RBM00010": 800, "WAS00034": 1000, "WAS00040": 1000,
        "WAS00045": 1000, "WAS00052": 1000, "WAS00061": 500,
    })

    # NOTA: existiu aqui um `aisle_factor` (corredores como % da footprint dos
    # racks). Foi REMOVIDO por ser código morto: desde que layout.py passou a
    # dispor os racks fisicamente, a área de corredores é MEDIDA (retângulo
    # envolvente - footprint dos racks) e as larguras vêm de RackType.corredor_m
    # / CORREDOR_DEFAULT_M / CORREDOR_TRANSICAO_M. O fator já não era lido por
    # ninguém, mas parecia configurável -- afiná-lo não mudava nada.

    # Altura útil do armazém (pé-direito disponível para empilhar módulos de
    # rack). Qualquer tipologia pode ser empilhada na vertical: nº de módulos
    # por posição de chão = floor(altura_armazem_m / (alt_nivel_m x niveis))
    # desse RackType. 0 = sem limite de altura (não empilha módulos, niveis
    # do Excel já são o total).
    altura_armazem_m: float = 0.0

    # Cotas físicas do porta-paletes. «Alturas_Niveis_m» da folha «Racks» são
    # alturas ÚTEIS (deck + carga) = passo - viga - folga de manuseio; o motor
    # só usa essas. Estes dois valores servem para voltar ao PASSO físico
    # (passo = útil + folga + viga) e desenhar as vigas no 3D onde estão de
    # facto (racking.cotas_fisicas_niveis). Só se aplicam às tipologias com
    # Alturas_Niveis_m; nas uniformes (Prateleira Leve) Alt_nivel_m já é o passo.
    viga_m: float = 0.09
    folga_manuseio_m: float = 0.10

    # Tolerância lateral ao encaixar peças em PALLET/MEIA: soma-se aos DOIS lados
    # de cada cota do plano (Comprimento e Largura), tanto no cálculo principal
    # de peças-por-unidade (boxing._pecas_no_envelope) como na decisão OVERSIZE
    # vs PALLET/MEIA -- é a MESMA regra para as duas perguntas, não duas
    # separadas (ver Decisão fechada "ALTURA_OVERRIDE e tolerância de palete").
    #
    # Substitui o antigo `pallet_transbordo_max_pct` (fração do lado maior,
    # assimétrico, só usado num fallback à parte `_assenta_na_palete`, agora
    # removido) -- pedido explícito (2026-09) por ser mais simples e simétrico:
    # se a peça cabe na footprint da palete/meia-palete + 0.10 m por lado (em
    # QUALQUER orientação permitida pelo ALTURA_OVERRIDE), entra normalmente
    # (conta como PALLET/MEIA, pode empilhar/aninhar); senão é OVERSIZE.
    pallet_tolerancia_lateral_m: float = 0.10

    # --- OVERSIZE ao CHÃO (block stacking) ------------------------------------
    # Peças volumosas que não assentam na palete podem ser arrumadas EM PILHA NO
    # CHÃO, sem rack -- é o "block stacking" clássico. Escolhe-se isto quando são
    # poucas referências: montar uma tipologia de rack inteira para 3 SKUs não se
    # justifica. A área é CALCULADA a partir das cotas e do stock (não é um
    # número fixo na folha «Zonas»), por isso acompanha o master data.
    #
    # Ativo quando não há tipologia que aceite OVERSIZE (ver oversize_racks): o
    # rack tem prioridade, o chão é a alternativa.
    oversize_ao_chao: bool = True

    # Limites da pilha ao chão. Sem estrutura a suportar, o que trava é o
    # alcance do operador e o bom senso de carga sobre a peça de baixo.
    oversize_chao_altura_max_m: float = 1.8      # alcance/estabilidade
    oversize_chao_peso_max_kg: float = 1000.0    # carga na peça do fundo

    # Acesso: fração da área de pilhas somada como corredor/manuseio em volta do
    # bloco. 0.45 = +45%, coerente com um acesso frontal de ~1.2 m num bloco
    # destas dimensões. Não é corredor de rack (não há filas), é o espaço para
    # chegar às pilhas.
    oversize_chao_acesso_pct: float = 0.45

    # --- OVERSIZE em rack wide-span (bulk rack) -------------------------------
    # Peças volumosas que não assentam na footprint da palete (boxing -> OVERSIZE)
    # passam a ser dimensionadas numa tipologia própria em vez de ficarem só em
    # aviso. "A própria rack é a superfície": os componentes ficam POUSADOS no
    # nível, sem palete nem caixa -- a capacidade é de outro tipo (footprint
    # do nível + empilhamento, não unidades de carga).
    #
    # Referência da indústria: wide span / longspan shelving ("bulk rack") --
    # construção de porta-paletes com aplicação de estante, para carga NÃO
    # paletizada e carregada à mão. Profundidades standard 450-1200 mm (métrico)
    # ou até 60" = 1524 mm; vãos 1200/1500/1800/2400/2700 mm; 500-1000 kg/nível.
    # O porta-paletes normal não serve: só vai até 48" (1219 mm) de profundidade
    # e as peças aqui precisam de ~1.38 m.
    #
    # Tipologias da folha «Racks» cujo nome contenha uma destas chaves aceitam
    # OVERSIZE (case-insensitive). Vazio = nenhuma; os OVERSIZE voltam a ficar
    # só em aviso, como antes.
    oversize_racks: tuple = ("wide", "bulk")

    # Peso máximo por nível da wide-span (kg). Nas peças reais é o PESO, não a
    # altura, que trava o empilhamento: uma peça de 216 kg empilharia 8x por
    # altura mas só 4x por peso. 0 = sem limite de peso.
    oversize_peso_max_nivel_kg: float = 1000.0

    # Folga de manuseio descontada à altura do nível, ao empilhar peças pousadas
    # (é preciso espaço para as mãos/equipamento entre a peça de cima e a viga).
    # Não se aplica às tipologias com «Alturas_Niveis_m»: essas alturas já são
    # úteis, com a folga descontada na folha (ver racking._oversize_plano_altura).
    oversize_folga_altura_m: float = 0.05

    # --- OVERSIZE2: "caixas especiais" (equipamento de transporte dedicado) --
    # Alguns SKUs (ex.: laterais/tejadilho, compridos e finos) não passam pelo
    # best-fit normal nem pelo block-stacking OVERSIZE genérico -- têm um
    # equipamento de transporte próprio (tipicamente um carrinho), footprint
    # e capacidade FIXOS (não calculados por geometria/packer, é a capacidade
    # do equipamento). boxing.choose_load intercepta estes SKUs ANTES do
    # best-fit (nenhum cálculo geométrico é feito) e devolve sempre
    # (OVERSIZE2, <quantidade da caixa especial>).
    #
    # Substituiu (2026-09, pedido explícito -- "não fazer hardcode no
    # config") uma versão anterior com os SKUs/dimensões escritos à mão aqui
    # (oversize2_skus/oversize2_comp_m/larg_m/alt_m/capacidade_un). Agora é
    # dado do master data (folha «Caixas Especiais», ver io_excel.
    # load_caixas_especiais), editável também na UI web («Caixas & Racks») --
    # decisão de negócio, não deve exigir alterar código. Um registo
    # (`models.CaixaEspecial`) pode cobrir vários SKUs (mesmo equipamento
    # partilhado); este dict resolve por SKU individual (chaves repetidas
    # apontam para o MESMO objeto quando partilham registo).
    #
    # NÃO muda as dimensões do artigo (Comprimento_m/Largura_m/Altura_m no
    # master data continuam as da PEÇA, só informativas/de referência) -- só
    # o tipo de carga e a capacidade da unidade de transporte.
    #
    # Vazio por omissão: quem corre o pipeline (main.py/web.py/bordo_linha.py)
    # carrega isto do master data e substitui via dataclasses.replace ANTES
    # de chamar boxing/racking/area/linha -- Config em si não tem dados
    # hardcoded aqui, só o tipo/default.
    caixas_especiais: dict = field(default_factory=dict)

    # A folha «Caixas Especiais» passou (2026-09-28) a descrever os carrinhos
    # da LINHA: 1 por Número/ID, Quantidade = stock desse SKU na linha. False
    # = o armazém central (main.run, /api/gerar, /api/render-layout) ignora-a
    # e dimensiona esses SKUs pelo best-fit normal; o bordo de linha e o
    # layout da linha usam-na sempre. True = o armazém volta a usá-la como
    # override do Passo 1 (OVERSIZE2, ceil(stock de 10 carros / quantidade)
    # carrinhos por SKU) -- medido com os 6 carrinhos da folha nessa data:
    # área de racks 86.6 -> 258.3 m², área bruta 590.7 -> 932.7 m².
    caixas_especiais_no_armazem: bool = False

    # Onde ficam os carrinhos (OVERSIZE2) quando o armazém os usa. False (default)
    # = no CHÃO, como o equipamento móvel que são: fiada própria do bloco
    # (area.artigos_chao_oversize2), sem ocupar módulos de rack. True = o
    # comportamento do dimensionamento_linha: dentro do nível de chão da rack
    # OVERSIZE (wide-span/emparelhada) quando o carrinho cabe. Em rack, cada
    # carrinho gasta um módulo inteiro (ex.: 6,5 m² para um carrinho de 2,2 m²)
    # e obriga a abrir racks emparelhados só para eles.
    carrinhos_em_rack: bool = False

    # --- OVERSIZE em par de racks emparelhadas (costas-com-costas) -----------
    # Alternativa à wide-span quando não há tipologia dedicada na folha «Racks»:
    # duas posições de uma tipologia já existente (ex.: Rack Porta-Paletes,
    # 2.7 x 1.2 m), colocadas costas-com-costas como aliás já é o padrão do
    # layout, formam uma superfície contínua de larg_m x (2 x prof_m) -- aqui
    # 2.7 x 2.4 m -- suficiente para peças que não cabem numa profundidade só.
    #
    # `racking.com_par_oversize` deriva, EM MEMÓRIA (nunca escrito no Excel),
    # uma tipologia sintética "<nome base> (emparelhada)" com prof_m dobrado, e
    # usa o MESMO motor de cálculo da wide-span (rack_capacity_oversize) -- a
    # peça fica pousada no nível, sem palete, testam-se as 2 orientações no
    # plano. PARTILHA o stock normal da tipologia base (aceita os mesmos tipos
    # de carga + OVERSIZE): o packer por nível já mistura tipos de carga sem
    # sobrepor footprints, por isso uma palete normal pode ocupar o espaço que
    # o OVERSIZE não enche num nível já aberto -- otimiza espaço sem nunca
    # partilhar a mesma área física ao mesmo tempo.
    #
    # Prioridade quando ambas existem: wide-span dedicada (oversize_racks)
    # primeiro -- se a folha já tem uma tipologia própria, usa-se essa; o par
    # emparelhado é o 2º recurso, antes de cair para o chão.
    #
    # Tipologias da folha «Racks» cujo nome contenha uma destas chaves (case-
    # insensitive) podem ser emparelhadas. Vazio = nenhuma (comportamento
    # anterior: só wide-span ou chão).
    oversize_par_racks: tuple = ("porta-paletes",)

    # Corredor de serviço da tipologia sintética (empilhador -- é o mesmo
    # equipamento que serve a tipologia base). Independente do `Corredor_m` da
    # folha «Racks»: a sintética não é uma linha do Excel, por isso o valor
    # dela vive aqui, não lá. Para mudar o corredor da tipologia BASE (ex.:
    # Rack Porta-Paletes), edita a folha «Racks» -- essa é INPUT, este não.
    oversize_par_corredor_m: float = 3.0

    # Máximo de MÓDULOS de rack empilhados (racks inteiros uns por cima dos
    # outros na mesma posição de chão), por tipologia cujo nome contenha uma
    # destas chaves. Não confundir com RackType.niveis, que são as prateleiras
    # DENTRO de um módulo (input da folha «Racks», não se mexe).
    #
    # Chave = substring do nome da tipologia (case-insensitive); valor = máx. de
    # módulos. Tipologias não listadas não têm limite próprio (só a altura).
    #
    # Vazio por defeito: decisão de negócio explícita (instrução direta) de usar
    # sempre o MÁXIMO de altura disponível em todas as tipologias -- inclui a
    # prateleira leve, que antes ficava travada a 2 módulos (a razão original
    # era o alcance do operador a pé, mas deixou de se aplicar). O mecanismo
    # fica disponível para quem precisar de voltar a travar uma tipologia
    # específica (ex.: `{"prateleira": 2}`), só não trava nada por omissão.
    max_modulos_por_tipologia: dict = field(default_factory=dict)

    # Tipologias cujo nome contenha uma destas chaves empilham NÍVEL A NÍVEL
    # em vez de MÓDULO A MÓDULO (ver max_modulos_por_tipologia acima, que é o
    # eixo oposto -- quantos módulos INTEIROS). Aqui o próprio módulo único
    # desta tipologia ajusta o seu nº de níveis ao pé-direito disponível
    # (racking.niveis_efetivos), em vez de arredondar para baixo ao nº de
    # módulos completos (racking.blocos_empilhados, que ou desperdiça
    # pé-direito -- um módulo de Porta-Paletes de 7.2 m não cabe todo num
    # armazém de 5 m -- ou fica sem nenhum módulo, corrigido para 1 e
    # ultrapassando mesmo assim o teto).
    #
    # Ex.: Rack Porta-Paletes (Alt_nivel_m=1.8, 4 níveis = 7.2 m de módulo)
    # num armazém de 5 m: módulo a módulo dava 7.2 m (só cabe 1, e esse já
    # excede); nível a nível dá 2 níveis = 3.6 m, que cabe mesmo.
    #
    # Chave = substring do nome da tipologia (case-insensitive), mesmo padrão
    # de max_modulos_por_tipologia/oversize_racks. Vazio = nenhuma tipologia
    # usa este modo (volta ao módulo a módulo para todas).
    #
    # Decisão de negócio explícita: Prateleira Leve mantém-se módulo a módulo
    # (várias prateleiras completas empilhadas fazem sentido servidas por
    # operador a pé); Rack Porta-Paletes passa a nível a nível.
    empilhamento_por_nivel: tuple = ("porta-paletes",)

    # Modo de armazém picking: altura MÁXIMA (m) de alcance do operador. Um
    # nível conta como zona de picking se o seu TOPO (base + altura do nível)
    # ficar dentro deste valor -- o nível inteiro tem de estar ao alcance, por
    # isso conta o limite superior, não a base (ex.: nível de 1.8 m com picking
    # a 2 m é picking; o nível seguinte, topo a 3.6 m, já não).
    # Cada SKU distinto tem garantida >=1 caixa nessa zona; o resto
    # do stock vai para reserva acima. Se a capacidade de picking não chegar
    # para todos os SKUs, abrem-se mais posições de chão até caber.
    # 0 = modo picking desativado.
    altura_picking_m: float = 0.0

    # Margem estrutural (paredes, pilares, etc.) aplicada sobre racks + corredores +
    # zonas funcionais, para chegar à área bruta total do edifício.
    # 0 = sem margem estrutural aplicada.
    margem_estrutural_pct: float = 0.0

    # --- Envelope do bloco de racks (Passo 3b, layout.py) ---------------------
    # Limita o retângulo de racks + corredores. NÃO abrange as zonas funcionais,
    # que continuam a ser posicionadas à parte (ver a decisão sobre Zona).
    #
    # O nº de racks continua a vir do STOCK (Passo 2 intocado): estas cotas são
    # restrição de FORMA, não de capacidade. Se o que o stock exige não couber, o
    # layout arruma o que pode e emite aviso com o excesso -- nunca deixa stock
    # de fora em silêncio nem inventa capacidade.
    #
    # largura_max_m: TETO da largura do bloco -- não um alvo. O layout parte
    #   sempre da forma que minimiza a área e só REDUZ as tipologias que excedam
    #   o limite; dar um teto acima do ótimo livre não muda nada. Se uma
    #   tipologia tiver uma posição mais larga que este valor, é impossível
    #   respeitá-lo -> aviso.
    # profundidade_max_m: teto de AVISO. Com a largura fixa, a profundidade é
    #   consequência (livre); ultrapassá-la não reorganiza nada, só avisa.
    # 0 = sem limite (comportamento original: o layout escolhe a forma).
    largura_max_m: float = 0.0
    profundidade_max_m: float = 0.0

    # Tipologias servidas por picking MANUAL (operador a pé) continuam livres
    # para partilhar prateleira (shelf packing) com as restantes -- só ficam
    # garantidas CONTÍGUAS entre si (ver layout._empacotar_tipologias): nunca
    # ficam fragmentadas em bandas do bloco separadas por uma tipologia
    # diferente pelo meio. É restrição operacional: quem faz picking manual
    # deve encontrar a zona toda concentrada, não espalhada pelo armazém --
    # mas não há problema em ela partilhar uma fila com outra tipologia.
    #
    # Chave = substring do nome da tipologia (case-insensitive), mesmo padrão
    # de `max_modulos_por_tipologia`/`oversize_racks`. Vazio = nenhuma
    # garantia de contiguidade (comportamento anterior).
    picking_manual_racks: tuple = ("prateleira",)

    # Largura (m) de UM pilar por tipologia de rack. A `Larg_m` da folha
    # «Racks» é o vão LIVRE entre pilares (a capacidade usa-a tal e qual); o
    # pilar só entra no ESPAÇO NO CHÃO: cada módulo passa a ocupar
    # `Larg_m + pilar` no layout, na sandbox (filas manuais) e na área, para o
    # armazém simulado espelhar o comprimento real das filas.
    #
    # Chave = substring do nome da tipologia (case-insensitive; a primeira que
    # casar ganha); valor = largura do pilar em metros. Aplicado em
    # data_access.load_rack_types (vale para Excel e Supabase). Vazio ou
    # tipologia não listada = sem pilar (comportamento anterior). Ex.: SIA usa
    # {"porta-paletes": 0.09} (montante típico de 80-100 mm); a Prateleira
    # Leve (1.80 m) não está lá porque se assume que a medida já é total.
    pilar_rack_m: dict = field(default_factory=dict)

    # ------------------------------------------------------------ multi-cliente
    @classmethod
    def for_client(cls, name: str, clients_dir: Path | None = None) -> "Config":
        """Constrói um Config para o cliente `name`, a partir de
        `dados/clients/<name>.json`.

        Só os campos CLIENT-SPECIFIC identificados na auditoria (caminhos,
        overrides manuais por SKU, autonomia mínima, e as chaves de substring
        que casam nomes de tipologia de rack) vêm do JSON -- ver
        `_CLIENT_FIELD_CONVERTERS`. Tudo o resto do Config (fatores de
        enchimento/empilhamento, limites de picking/altura de armazém, etc.)
        continua a ser CORE: o mesmo para qualquer cliente, só ajustável por
        quem corre o pipeline (CLI/UI web), nunca por identidade de cliente.

        Um novo cliente = um novo `dados/clients/<nome>.json` + o Excel dele
        no caminho que esse ficheiro apontar -- nunca uma edição a este
        módulo. `dados/clients/_template.json` é o ponto de partida (valores
        placeholder, sem SKUs reais) para copiar ao criar um cliente novo.
        """
        base = clients_dir or _CLIENTS_DIR
        path = base / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Sem configuração para o cliente '{name}': {path} não existe. "
                f"Cria dados/clients/{name}.json (usa dados/clients/_template.json "
                f"como ponto de partida)."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        # chaves "_algo" são comentário (JSON não tem comentários nativos),
        # nunca um campo -- ignoradas antes de validar campos desconhecidos.
        data = {k: v for k, v in data.items() if not k.startswith("_")}

        desconhecidos = set(data) - set(_CLIENT_FIELD_CONVERTERS)
        if desconhecidos:
            raise ValueError(
                f"dados/clients/{name}.json tem campo(s) não reconhecido(s) como "
                f"CLIENT-SPECIFIC: {sorted(desconhecidos)}. Se é um parâmetro "
                f"CORE (afeta todos os clientes da mesma forma), pertence a "
                f"Config, não ao ficheiro do cliente; se é mesmo específico "
                f"deste cliente, acrescenta-o a _CLIENT_FIELD_CONVERTERS primeiro."
            )

        cfg = cls(client_name=name)
        for campo, conversor in _CLIENT_FIELD_CONVERTERS.items():
            if campo in data:
                setattr(cfg, campo, conversor(data[campo]))

        if cfg.data_source not in ("excel", "supabase"):
            raise ValueError(
                f"dados/clients/{name}.json: data_source='{cfg.data_source}' "
                f"desconhecido -- só 'excel' ou 'supabase' (ver warehouse/data_access.py)."
            )
        return cfg

"""Parâmetros e decisões de negócio. Mudar estes valores muda resultados."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    # caminhos
    master_data: Path = Path("dados/master_data_classificado_v1.xlsx")
    output: Path = Path("dados/resultados.xlsx")

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

    # Empilhamento de artigos (dentro de caixa/palete), eixo da altura.
    # Classificação em 3 vias vinda da coluna 'Empilhável' (ver Empilhamento):
    #   NAO_EMPILHAVEL -> 1 peça de altura.
    #   EMPILHAVEL     -> altura cheia somada (sem nesting), só limitado pela
    #                     altura disponível e pelo peso máximo da caixa.
    #   ANINHAVEL      -> cada peça extra do stack acrescenta apenas
    #                     fator_empilhamento x altura_artigo (encaixa/aninha).
    fator_empilhamento: float = 0.3

    # Fator de enchimento real no plano horizontal (comp x larg), substituindo o
    # floor() teórico de encaixe perfeito. Folgas de manuseio/forma irregular e
    # encaixe imperfeito reduzem o nº de peças por nível face ao máximo
    # geométrico. Aplicado a TODAS as classes (RIGIDO e FLEXIVEL/CONTENTOR) —
    # cobre também o encaixe-de-gume irreal de peças finas postas ao alto pela
    # rotação de orientação (ver boxing.pieces_per_box).
    fill_factor_plano: float = 0.7

    # Encaminhamento para LONGO (cantilever): geometrias que não vão a caixa
    long_geometrias: tuple = ("Comprida",)

    # Dentro de long_geometrias, subtipos que NÃO são material 1D de
    # cantilever (não se apoiam em braços) -> caem para o best-fit normal
    # (S/M/L/PALLET) em vez de ir para a cantilever.
    long_subtipos_excluidos: tuple = ("PAINEL_GRANDE", "VOLUMOSO_OVERSIZE")

    # Filtro geométrico de admissão à cantilever (boxing.cabe_em_cantilever).
    # Estar marcado "Comprida" na folha não chega: peças volumosas não cabem no
    # nível nem se apoiam nos braços. Devem espelhar a tipologia real de
    # cantilever da folha «Racks» (por defeito: nível 0.6 m, braço 0.6 m).
    cantilever_alt_nivel_m: float = 0.6     # menor cota tem de caber aqui
    cantilever_braco_m: float = 0.6         # cota média tem de caber no apoio
    cantilever_esbelteza_min: float = 3.0   # maior/média: perfil, não volume

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

    # Transbordo máximo tolerado ao pousar uma peça EM CIMA da palete, como
    # fração do lado maior da palete. Aplica-se só no fallback do boxing (peça
    # que não cabe em nenhuma unidade de carga mas assenta na footprint).
    #
    # Porquê: `_assenta_na_palete` testava só as duas MENORES cotas -- respondia
    # a "equilibra-se em cima?" e ignorava quanto a maior cota fica em voo. As
    # laterais GFRP (2.47 m) saíam como 1/palete com **1.27 m a pender para
    # fora**, mais do que a própria palete mede. Isso não é só estética: uma
    # palete assim NÃO é armazenável em rack -- o rack_capacity conta-a como
    # ocupando 1.20 m, quando na prática colide com as vizinhas ou invade o
    # corredor. Era subdimensionamento silencioso.
    #
    # 0.25 = tolera até 1.50 m numa palete de 1.20 m. Apanha as laterais (2.47 m)
    # e o tejadilho (1.54 m) -> vão para o chão; deixa em palete o firewall
    # (+1.7 cm) e os painéis (+11 cm), que são transbordos irrelevantes.
    # Valor alto (ex.: 10.0) desliga o critério (comportamento anterior).
    pallet_transbordo_max_pct: float = 0.25

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
    # nível, sem palete nem caixa -- como no LONGO/cantilever, a capacidade é de
    # outro tipo (footprint do nível + empilhamento, não unidades de carga).
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
    oversize_folga_altura_m: float = 0.05

    # Máximo de MÓDULOS de rack empilhados (racks inteiros uns por cima dos
    # outros na mesma posição de chão), por tipologia cujo nome contenha uma
    # destas chaves. Não confundir com RackType.niveis, que são as prateleiras
    # DENTRO de um módulo (input da folha «Racks», não se mexe).
    #
    # Porquê: uma prateleira leve é servida por operador a pé, e empilhar
    # módulos põe stock fora do alcance (com altura_armazem_m=10 e um prumo de
    # 2.10 m caberiam 4 módulos = 12 prateleiras em coluna). Ao travar, o stock
    # que subiria espalha-se por MAIS posições de chão ao lado -- mais área,
    # mas tudo acessível. O porta-paletes não entra: é servido por empilhador.
    #
    # Chave = substring do nome da tipologia (case-insensitive); valor = máx. de
    # módulos. Tipologias não listadas não têm limite próprio (só a altura).
    max_modulos_por_tipologia: dict = field(
        default_factory=lambda: {"prateleira": 2})

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

"""Parâmetros e decisões de negócio. Mudar estes valores muda resultados."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    # caminhos
    master_data: Path = Path("dados/master_data_classificado_v3.xlsx")
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
    oversize_folga_altura_m: float = 0.05

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

# Contexto para o Claude Code

## O que é
Ferramenta de **dimensionamento de capacidade** de um armazém. A partir do
stock máximo de cada artigo, determina o repacking em unidades de carga, o nº de
racks e a área de chão necessária.

## Unidades
**Metros e m³ em todo o lado** (dados e código). Os campos terminam em `_m` /
`_m2` / `_m3`. Não introduzir milímetros.

## Pipeline (3 passos)
0. **Autonomia mínima (`boxing.stock_para_autonomia`)** — decide `stock_max_un`
   a partir de `Peças_por_carro` e `Config.autonomia_minima_carros` (10 carros).
   Ver "Decisão fechada: stock de autonomia mínima é algorítmico" abaixo.
1. **Repacking (`boxing.py`)** — por SKU, escolher a unidade de carga e contar a
   partir de `stock_max_un`. Catálogo de unidades = **dado**; escolha/contagem =
   **output**.
2. **Racking (`racking.py`)** — dadas as tipologias de rack (input), calcular
   quantos racks de cada uma guardam todas as unidades.
3. **Área (`area.py`)** — footprints dos racks + corredores → m² totais.
4. **Zonas funcionais (`area.resumo_area_total`)** — soma a área de racks +
   corredores (Passo 3) + zonas funcionais (input direto, folha «Zonas») +
   margem estrutural → área bruta total do edifício.

## Tipos de carga (`LoadType`)
- **S / M / L** — caixas de repacking (catálogo na folha «Caixas»).
- **MEIA** — meia-palete (0.8 × 0.6 m, mesma altura útil da PALLET): mais uma
  entrada do catálogo «Caixas», candidata entre L e PALLET no best-fit.
- **PALLET** — a palete é **mais uma entrada do catálogo** (BoxType): para o
  packer é um bin (footprint × altura de empilhamento + peso). Não há dataclass
  `Pallet` nem `load_pallet` separados.
- **OVERSIZE** — não assenta em nenhuma unidade de carga do catálogo (nem na
  footprint da palete). Ver "Decisão fechada: OVERSIZE ao CHÃO" e "... em rack
  wide-span" abaixo.

## Altura dos racks
`RackType.alt_nivel_m` é a **altura útil POR NÍVEL** (não o total do prumo). O
teste vertical em `rack_capacity` compara a altura exterior da caixa com
`alt_nivel_m`. Total do prumo = `alt_nivel_m × niveis`.
Com «Alturas_Niveis_m» (alturas heterogéneas) o motor usa
`racking.altura_nivel`/`altura_ate_niveis` e `alt_nivel_m` é só o máximo, de
referência. No Rack Porta-Paletes essas alturas são ÚTEIS (deck + carga,
passo − viga − folga); o passo físico só serve para o 3D
(`racking.cotas_fisicas_niveis`). Ver "níveis de 1,8 m" abaixo.

## Fronteira INPUT vs OUTPUT
- INPUT : geometria do artigo (m), `Peças_por_carro`, `Stock_máx_un` (só
  referência, ver Passo 0), catálogo «Caixas» (S/M/L+PALLET), tipologias «Racks».
- OUTPUT: `stock_max_un` decidido (Passo 0), tipo de carga por SKU, nº de
  unidades, nº de racks, área.

## FORA de âmbito
Desenho de níveis de rack (os racks já vêm com níveis e altura por nível
definidos). Não reintroduzir.

Slotting, COI/ABC e otimização de picking estiveram aqui até 2026-11-05 e foram
**reabertos por pedido explícito** (alocar componentes a posições do arranjo e
prever o picking). Ver "Procura de picking por ciclo de reposição" abaixo --
o dimensionamento (Passos 0-3) continua sem saber nada de picking.

## Decisões já fechadas
- PALLET é entrada do catálogo «Caixas» (não dataclass separado).
- `alt_nivel_m` = altura por nível.
- Convenção de unidades: metros / m³.
- **Orientação / aninhagem do artigo**: ver "Decisão fechada: motor alinhado com
  o dimensionamento_linha" abaixo (`ALTURA_OVERRIDE` + `EIXO_ANINHAGEM` substituem
  a antiga `Orientação_permitida`; `fill_factor_plano` = 0.80, todas as classes).
- **Zona** (área fora dos racks: receção, expedição, equipamento, staging, ...)
  é input direto (`Comp_m × Larg_m`, folha «Zonas», sem SKU/stock associado) —
  não replica as fórmulas de fluxo de receção de uma folha de referência
  (evita depender de dados de encomendas que não existem no master data). A
  área de zonas soma-se à área total **à parte** dos corredores de racks: o
  `layout.py` não muda. `Config.margem_estrutural_pct` (0 =
  desligado) aplica-se sobre racks + corredores + zonas para chegar à área
  bruta total (`area.resumo_area_total`). No layout 3D, as zonas só recebem
  coordenadas para não sobrepor o bloco de racks (`layout._layout_zonas`);
  não entram em `largura_total_m`/`profundidade_total_m`/`area_total_m2`.

- **Regra de escolha de caixa**: minimizar o nº de unidades de carga
  (`boxing.choose_load` — `ceil(stock_max_un / pecas_por_unidade)`, empate →
  caixa mais pequena). Validado: é o que minimiza chão; estratégias de volume
  mínimo dão blocos maiores. O enum `BoxStrategy` (MIN_WASTE / SMALLEST_FIT
  nunca implementados) e o `min_fill_ratio` foram removidos do `config.py` —
  eram código morto que parecia configurável. Não reintroduzir.
- Capacidade do rack: calculada a partir das dimensões (não dada em posições).

## Decisão fechada: cantilever/LONGO removida (2026-09)
O tipo de carga LONGO e a cantilever (`racking.rack_capacity_long`,
`boxing.cabe_em_cantilever`, `Config.long_geometrias`/`long_subtipos_excluidos`/
`cantilever_*`, `RackType.comp_braco_m`, `Article.subtipo_longo`) foram
**removidos por completo** do código — decisão de negócio: já não se usa
cantilever no armazém. Não reintroduzir sem pedido explícito.

Artigos com `geometria = "Comprida"` seguem agora sempre o best-fit normal
(S/M/L/MEIA/PALLET/OVERSIZE), como já acontecia de facto com os dados reais:
os 16 SKUs assim marcados já falhavam o filtro geométrico da cantilever
(`cabe_em_cantilever`) e caíam no best-fit — a remoção não muda nenhum
resultado nos dados atuais, só o código morto que já não era exercitado.
`Article.geometria` mantém-se (é só informativo agora, mostrado na UI).

## Decisão fechada: motor alinhado com o dimensionamento_linha (2026-09-30)
O motor de boxing + alocação + leitura do master data foi alinhado **exatamente**
com o `dimensionamento_linha` (fork posterior deste projeto). Verificado: com o
mesmo master data e config, as decisões de caixa (445/445 SKUs) e a alocação aos
racks (posições, caixas, peças) são idênticas nos dois motores. O que NÃO veio
da linha: bordo de linha, layout/3D da linha, comboio logístico, UI «Caixas &
Racks» (e as rotas `/api/caixas-especiais`).

O que mudou no armazém:
- **`ALTURA_OVERRIDE`** (x/y/z/vazio) substitui `Orientação_permitida`
  (Livre/Base fixa/Não deitar): força essa cota como vertical; vazio = livre
  (testam-se as 3). `Article.orientacao` -> `Article.altura_override`;
  `boxing.pode_deitar` -> `boxing.eixos_possiveis`.
- **Aninhagem separada de empilhável**: `Empilhamento.ANINHAVEL` deixou de
  existir (só `NAO_EMPILHAVEL`/`EMPILHAVEL`). A coluna **`EIXO_ANINHAGEM`**
  (x/y/z/vazio, `Article.eixo_aninhagem`) diz em que cota a peça aninha na
  anterior, vertical OU no plano (`boxing.pecas_no_eixo`, fator 0.3).
- **`fill_factor_plano` 0.70 -> 0.80**.
- **Tolerância de palete**: `Config.pallet_tolerancia_lateral_m` (0.10 m por
  lado em PALLET/MEIA, em ambas as cotas do plano) substitui
  `pallet_transbordo_max_pct` e `_assenta_na_palete`. Não há fallback «1/palete»:
  se não coube em nenhuma unidade (PALLET/MEIA já com tolerância) é **OVERSIZE**.
- **`CAIXA_FORCADA` / `PECAS_CAIXA_FORCADA`** (opcionais, por SKU): exceção
  manual ao best-fit; com quantidade, é usada tal e qual (sem fill factor nem
  arredondar à centena).
- **OVERSIZE2 / folha «Caixas Especiais»** (carrinhos): portado, mas **desligado**
  no armazém (`Config.caixas_especiais_no_armazem = False`; só a linha o usa).
  Testado (2026-09-30) com os 6 carrinhos da linha: não compensa — cada carrinho
  ocupa mais chão do que os racks que liberta (área bruta 319 -> ~497 m²). Se
  alguma vez for ligado: carrinhos partilhados contam o MÁXIMO entre os SKUs
  (`boxing.carrinhos_fisicos`, não a soma), ficam ao chão sem empilhar
  (`Config.carrinhos_em_rack = False`) e o layout trata-os como fiada do bloco,
  o que exagera os corredores.
- `RackType.alturas_niveis_m` (coluna opcional «Alturas_Niveis_m» em «Racks»):
  alturas de nível heterogéneas; vazio = uniforme como sempre.
- `load_zones` lê por NOME de coluna; cotas em texto com vírgula são aceites.
- **Desvio deliberado face à linha — arrumação alinhada no nível**
  (`racking._empacotar_alinhado`): antes do packer de rotação livre, tenta-se
  uma grelha em que cada footprint distinta tem UMA orientação. O packer livre
  misturava 0.6x0.8 e 0.8x0.6 no mesmo nível (5 meias-paletes, frentes
  desalinhadas); a grelha dá 6 (3 x 2). Se não couber, cai no packer livre,
  por isso nunca perde capacidade. Efeito: área bruta 319,3 -> 308,0 m².
- **«Armazém 3D» gera sozinho** ao abrir o separador (como a «Linha 3D»): corre
  «Gerar racks» se ainda não há racks e renderiza com o arranjo atual; só volta
  a renderizar se o arranjo ou os parâmetros mudaram (`gerar3DAutomatico`). Depois
  do 1.º render, um `setInterval` atualiza o 3D em segundo plano quando o arranjo
  fica parado ~1 s (sem mudar de separador); o botão «Renderizar 3D» está
  escondido (o JS ainda o aciona).
  Tabela de Artigos igual à do `linha.html` (Estação, Aninhável = EIXO_ANINHAGEM,
  Caixa forçada).
- **Leitura do master data com o Excel aberto**: se o ficheiro estiver bloqueado
  (PermissionError), `io_excel._abrir_workbook` lê uma cópia temporária (o que
  está gravado em disco, sem alterações por guardar).
- **Mantido só no armazém** (a linha não tem): `pallet_ultimo_recurso` (SIA) —
  agora, quando ligado, a PALLET só é testada (já com tolerância) se nenhuma
  caixa mais pequena couber, e só depois vem OVERSIZE; multi-cliente
  (`Config.for_client`), Supabase (migração `0006`), ocupação, aviso de chaves
  de substring sem correspondência.

**Master data**: `dados/master_data_classificado_v3.xlsx` migrado — «Orientação_
permitida» renomeada para `ALTURA_OVERRIDE`; colunas `EIXO_ANINHAGEM`,
`CAIXA_FORCADA`, `PECAS_CAIXA_FORCADA` acrescentadas; `ALTURA_OVERRIDE`,
`Empilhável`, `EIXO_ANINHAGEM`, `CAIXA_FORCADA` e `PECAS_CAIXA_FORCADA` copiados
por SKU do master data da linha. Backup: `dados/master_data_classificado_v3.
xlsx.pre-motor-linha.bak`. Dimensões e `Peças_por_carro` do armazém NÃO foram
tocadas (há 6 SKUs com cotas e 2 com `Peças_por_carro` diferentes entre os dois
ficheiros — rever à mão qual é o correto).

## Decisão fechada: OVERSIZE ao CHÃO (block stacking)
Quando **não há** tipologia wide-span na folha «Racks», os OVERSIZE são arrumados
**em pilha no chão** (`Config.oversize_ao_chao`, default True) em vez de ficarem
só em aviso. O rack tem prioridade: o chão é a alternativa quando são poucas
referências — montar uma tipologia inteira para 3 SKUs não se justifica.

`area.area_oversize_chao` calcula a área a partir das **cotas + stock**, não de um
valor fixo na folha «Zonas» — assim acompanha o master data em vez de envelhecer.
A peça assenta deitada e empilham-se peças **do mesmo SKU**; vale o menor de
`oversize_chao_altura_max_m` (1.8, alcance) e `oversize_chao_peso_max_kg` (1000,
carga sobre a peça de baixo). O acesso entra como `oversize_chao_acesso_pct`
(0.45) porque **não há corredores medidos pelo layout** — sem isso ficava
subcontado.

Nos dados atuais (3 SKUs, 15 unidades): **38.56 m²**. A `BCA011` domina — 7 peças
de 1.37 m de altura que não empilham entre si, 3.1 m² de chão cada. As duas planas
(0.17 m) são travadas pelo **peso**, não pela altura (863 e 935 kg por pilha).

### As pilhas são uma FIADA DO BLOCO de racks
`layout.gerar_layout` dispõe-nas como mais uma tipologia: alinhadas à mesma
largura, com corredor de transição, a seguir à última fiada de racks. O bloco é um
só retângulo contínuo (um bloco no canvas, não dois) e rodam/deslocam-se com ele.

Consequências na contabilidade — **cuidado com a dupla contagem**:
- `floor_area(posicoes, rack_types, pilhas_chao)` inclui o footprint das pilhas em
  `area_racks_m2`, sob a chave `"Armazenagem ao chão"`. É área OCUPADA; sem isto o
  layout contava-as (para medir corredores = envolvente − ocupado) mas o resumo
  não, e os m² apareciam como corredor.
- `ResumoAreaTotal.area_oversize_chao_m2` é **INFORMATIVO e NÃO entra no total**:
  o footprint já vem em `area_racks_m2` e o espaço em volta em
  `area_corredores_m2`. Somá-lo contava duas vezes.
- A ordem no pipeline inverteu-se: **Passo 3b (layout) antes do Passo 3
  (floor_area)**, porque o footprint das pilhas só existe depois de posicionadas.

**No 3D**: `models.PilhaChao` + `index.html/desenharPilhasChao` — peças
sobrepostas com marcação amarela no pavimento, sem estrutura (não há rack).
Entram no `ajustarCenaECamera`, senão as paredes eram desenhadas a cortá-las.
Verificado nas 4 rotações.

## Decisão fechada: OVERSIZE em rack wide-span
As peças que não assentam na footprint da palete (`LoadType.OVERSIZE`) deixaram de
ficar só em aviso: passam a ser dimensionadas numa **wide-span (bulk rack)**.

**Referência da indústria**: *wide span / longspan shelving* — construção de
porta-paletes com aplicação de estante, para carga **não paletizada carregada à
mão**. Standards: profundidades 450–1200 mm (métrico) ou até 60″ = 1524 mm; vãos
1200/1500/1800/2400/2700 mm; 500–1000 kg/nível. O porta-paletes normal **não
serve**: só vai a 48″ (1219 mm) de profundidade e as peças precisam de ~1.38 m.

Tipologia proposta (a acrescentar à folha «Racks»): **vão 2.70 × prof 1.50 m,
4 níveis de 1.60 m** (prumo 6.4 m). Dá 4 racks para as 35 unidades.

Como funciona («a própria rack é a superfície», sem palete nem caixa):
- `racking.rack_capacity_oversize` — a peça assenta **DEITADA** (menor cota = a
  altura; painéis não se põem ao alto), as duas maiores no plano nas 2
  orientações. Empilha peças **do mesmo SKU** dentro do nível.
- **O peso trava antes da altura**: uma peça de 216 kg empilharia 9× em 1.55 m
  úteis mas só 4× com 1000 kg/nível (`Config.oversize_peso_max_nivel_kg`).
- Admissão por **substring do nome** da tipologia (`Config.oversize_racks =
  ("wide","bulk")`) ou `OVERSIZE` na coluna «Aceita» — evitou-se inventar coluna
  nova. Sem wide-span na folha, volta ao aviso de antes (nunca conta 1/palete).

Cuidado ao ler os resultados: um item colocado é um **stack**, não uma peça —
contar `len(caixas)` dá 16, mas `sum(pecas_nesta_caixa)` dá as 35 reais.

## Decisão fechada: pilar do porta-paletes só conta no espaço de chão
`Larg_m` da folha «Racks» (2.7 no porta-paletes) é o **vão livre entre pilares**
— evidência: 3 paletes de 0.8 m = 2.4 m deixam 0.3 m de folgas, e 2.7 m é a
travessa standard. Os pilares não estavam contados em lado nenhum (passo entre
módulos = `larg_m`, footprint = 3.24 m²).

`Config.pilar_rack_m` (`{"porta-paletes": 0.09}` no `sia.json`; chave = substring
do nome) preenche `RackType.pilar_m` em `data_access.load_rack_types` (Excel e
Supabase). Efeito, só no **chão**: `RackType.larg_total_m` (= vão + pilar) é o
passo no layout (`layout.py`, fila manual, racks fantasma), a largura da fila
manual na sandbox (`index.html`, via `larg_total_m` de `/api/racks`) e a área
(`area_chao_m2`: 3.24 → 3.348 m²; um `Footprint_m2` explícito também leva os
pilares). A **capacidade** (`racking.py`) continua a usar `larg_m` — somar o
pilar ao vão inflacionava paletes/caixas.
- **0.09 m confirmado pelo utilizador**: o mesmo pilar em todos os módulos, **partilhado**
  entre vizinhos, e as pontas da fila têm o seu (lado «solto»). Logo uma fila de
  k módulos mede **k × vão + (k+1) × pilar** — cada módulo leva 1 pilar
  (`larg_total_m`) e a fila leva +1 de **fecho**. O fecho entra na largura das
  fiadas (`_decompor_em_fiadas`, `_melhor_num_por_fiada`, `largura_max_m`), no
  comprimento do corredor, na fila manual da sandbox (`n × total + pilar`) e na
  área (`WarehouseLayout.fechos_pilar_m2` → `area.floor_area`; sem isso o
  envolvente contava-o como corredor). SIA, dados reais: bloco 10.8 → 11.2 m,
  racks 96.1 → 99.4 m².
- **Fiadas encostadas partilham o pilar** (`layout._partilha_pilar`): duas fiadas
  CONSECUTIVAS da mesma tipologia na mesma prateleira do shelf packing encostam-se
  e a junção tem 1 pilar só — o de 9 cm sozinho existe apenas no lado SOLTO da
  fila. Tipologias diferentes não partilham. (Antes contava-se 1 fecho por fiada.)
- A Prateleira Leve (1.80 m) está **fora**: assume-se que a medida já é total.
- Fica por modelar o espaço entre costas das filas duplas (hoje `2 × prof`).

## Decisão fechada: máximo de módulos empilhados
`Config.max_modulos_por_tipologia` trava quantos **módulos** — racks inteiros
uns por cima dos outros na mesma posição de chão — uma tipologia pode empilhar.
Chave = substring do nome (case-insensitive). **Por omissão é vazio `{}`** (e
também no `sia.json`): decisão de usar sempre o máximo de altura disponível.
O `{"prateleira": 2}` descrito abaixo era o default antigo; o mecanismo
continua disponível para voltar a travar uma tipologia.

**Não confundir com `RackType.niveis`**, que são as prateleiras DENTRO de um
módulo (input da folha «Racks», não se mexe). São dois eixos verticais distintos:
com `altura_armazem_m=10`, uma Prateleira Leve de 2.10 m de prumo empilhava 4
módulos = 12 prateleiras em coluna.

Porquê: a prateleira leve é servida por operador **a pé** — empilhar módulos põe
stock fora de alcance. O porta-paletes não é travado (empilhador). Quando o
limite morde, `_tentar_colocar_em_posicao` só empilha enquanto
`num_blocos < max_blocos`, logo o stock que subiria **espalha-se por mais
posições de chão ao lado** — que é o comportamento pretendido, e já existia.

Verificado nos dados atuais (altura 10 m): distribuição dos módulos nas 22
prateleiras passa de `{1:19, 2:1, 3:1, 4:1}` para `{1:16, 2:6}`, com as **mesmas
1239 caixas** — nada se perde. As posições de chão não aumentaram porque havia
folga (19 posições usavam 1 dos 2 módulos permitidos); com stock maior, aumentam.

O limite só pode REDUZIR: `min(cabem_na_altura, teto)` — nunca inventa módulos
que o pé-direito não permite.

## Decisão fechada: `pecas_por_carro` é FLOAT, não int
`Article.pecas_por_carro` é **fracionário por natureza**: `0.5` = 1 peça a cada 2
carros (par esquerdo/direito), `0.03` = 1 a cada ~33. Na base atual há **194 SKUs
com valor fracionário**, 138 deles a `0.5`.

Era `int` e o `int()` da leitura truncava. Consequência silenciosa: os 160 SKUs
com `0 < pecas_por_carro < 1` viravam `0`, e como
`num_unidades_picking = ceil(0 / n) = 0`, **deixavam de pedir zona de picking sem
qualquer aviso** — o aviso só dispara para quem pediu e não conseguiu. Passavam de
745 para 589 SKUs com presença em picking. Também se perdia a fração em 1.5, 3.5,
17.85.

Só há um consumo aritmético (`racking.py`, `num_unidades_picking`) e o `math.ceil`
já trata o float corretamente. Não voltar a pôr `int()` na leitura.

## Decisão fechada: forma do bloco = perímetro mínimo (não área mínima)
`layout._larguras_alinhadas` escolhe a largura-alvo por **perímetro mínimo**
(bloco compacto, próximo do quadrado), com a área como desempate. O
`_melhor_num_por_fiada` (caso de uma só tipologia) já fazia o mesmo.

Antes era área mínima, e premiava blocos absurdamente alongados. Numa base real:
**5.49 × 48.20 m (264.6 m², razão 9:1)** contra **20.13 × 16.50 m (332.1 m²,
1.2:1)** — a "melhor" forma era uma tira pouco mais larga que um corredor.

Duas razões para a mudança:
- **A área menor era em parte artefacto**: menos filas paralelas = menos
  corredores transversais contados no envolvente, ou seja premiava-se ter MENOS
  acessos.
- **Filas curtas são percurso de picking a menos** — 48 m de fila obrigam a
  percorrer 48 m para chegar ao fim.

Custa área (+21% no exemplo). Quem quiser a forma alongada impõe-na com
`Config.largura_max_m`.

## Decisão fechada: tipologias diferentes podem partilhar a mesma banda (shelf packing) -- SÓ quando o perímetro melhora
`layout._larguras_alinhadas`/`_ks_com_teto` (alinhar TODAS as tipologias a uma
única largura-alvo comum, cada uma sempre numa banda inteira só sua, empilhadas
em profundidade) foram **substituídos** por `_fiadas_tipologia` +
`_empacotar_tipologias` (shelf packing entre FIADAS de tipologias diferentes).

Antes, uma tipologia mais estreita (ex.: Prateleira Leve, 1.83 m) alinhada a
uma mais larga (ex.: Rack Porta-Paletes, 2.7 m) desperdiçava a diferença de
largura ao longo de **toda a profundidade** da tipologia estreita — chão vazio
ao lado, nunca aproveitado por outra tipologia.

**Decisão explícita (perguntado ao utilizador)**: o critério continua a ser o
PERÍMETRO mínimo do bloco (bloco compacto, perto do quadrado — ver "forma do
bloco = perímetro mínimo" abaixo), não "misturar sempre que dá para encaixar".
Testámos os dois: para 12 Prateleira Leve + 21 Rack Porta-Paletes, "encostar
tudo" dá 21.69 x 7.8 m = 169 m² (MENOS área) mas 58.98 m de perímetro (forma
mais alongada); manter cada tipologia na sua fiada dá 16.2 x 12.0 m = 194 m²
(mais área) mas só 56.4 m de perímetro (mais compacto). O utilizador escolheu
explicitamente manter o perímetro mínimo — não forçar a mistura só porque
"encaixa". A mistura acontece, mas só quando reduz mesmo o perímetro.

Como funciona:
1. **Cada tipologia decompõe-se nas suas FIADAS DUPLAS** (`_fiadas_tipologia`):
   o k (posições por fiada) continua a ser o perímetro-mínimo da tipologia
   INTEIRA de sempre (`_melhor_num_por_fiada`, agora com `k_max` opcional para
   capar por `largura_max_m`), mas cada fiada vira um item PRÓPRIO com a sua
   largura REAL (a última fiada de uma tipologia tipicamente usa menos que k)
   -- é decompor ao nível da FIADA, não da tipologia inteira, que permite
   testar misturas como "1 fiada larga + 1 fiada estreita de outra tipologia".
2. **Essas fiadas são arrumadas em "prateleiras" por First-Fit Decreasing
   Height (FFDH)**: ordena por profundidade decrescente e cada fiada tenta a
   PRIMEIRA prateleira já aberta onde ainda caiba (não só a última) — sem
   isto, uma prateleira que abre com uma fiada larga e não enche nunca mais
   recebia nada, mesmo que uma fiada mais estreita (de outra tipologia, mais
   à frente na lista) coubesse na sobra.
3. A largura-alvo das prateleiras (`_largura_alvo_shelf`) testa como
   candidatos TODAS as somas de subconjuntos das larguras das fiadas (não só
   somas cumulativas por profundidade decrescente, que perdem combinações
   tipo "1 fiada larga + 1 estreita" quando não são adjacentes nessa ordem)
   e escolhe por PERÍMETRO mínimo do envolvente resultante (empate por área
   menor). Nº de fiadas é sempre pequeno, 2^n é barato; acima de 16 cai para
   as somas cumulativas. `largura_max_m` continua TETO (nunca agrava o livre).
4. O corredor de transição entre prateleiras consecutivas usa o MAIOR
   corredor entre as duas (mesmo critério que já existia entre tipologias).

**Refinamento: ajuste fino ENTRE tipologias (`_ajustar_decomposicoes`).** Cada
tipologia começa na sua forma perímetro-mínimo sozinha, mas testa-se também
trocar por formas mais fragmentadas (`_candidatos_decomposicao`: k=1 máxima
fragmentação, k=n uma única fila) SE isso reduzir o perímetro GLOBAL do
bloco — às vezes uma tipologia pior sozinha encaixa nos buracos deixados por
outra. Busca gulosa (troca uma tipologia de cada vez, 2 passagens); nunca
piora, a forma sozinha é sempre uma das opções testadas. Caso real: com
`altura_armazem_m` a reduzir a Prateleira Leve a poucas posições de chão,
esta troca encaixa-as nos espaços-fantasma deixados pela Rack Porta-Paletes
(k=4, sobra 1 posição de largura), eliminando a fiada própria da Prateleira
Leve por completo.

**Efeito medido** (dados atuais, com o critério de perímetro a decidir se
compensa misturar): área bruta total v2 264 → **238 m² (−10%)**, v3 332 →
**244 m² (−26%)** — o nº de racks não muda (vem do stock, intocado); o ganho
vem de fiadas (inteiras ou fragmentadas) que preenchem a sobra de outra
tipologia SEM alongar o bloco. Com `altura_armazem_m` ligado (empilhamento de
módulos) o ganho é maior ainda — ver "Empilhamento de módulos de rack".

**Refinamento: a fiada FINAL (resto) escolhe o seu próprio k.** As fiadas
CHEIAS de uma tipologia usam o k perímetro-mínimo da tipologia inteira, mas a
última fiada -- quando sobra menos que uma fiada cheia (2k, ou k nas
emparelhadas) -- reotimiza-se sozinha (`_melhor_num_por_fiada` sobre só esse
resto). Herdar o k da maioria distorcia essa sobra: 9 posições sobrantes com
k=6 davam sempre "6 à frente, 3 atrás"; sozinhas, preferem "5 à frente, 4
atrás" (mais equilibrado, perímetro menor). Nunca piora — é só mais uma forma
testada para o resto, nunca imposta às fiadas cheias.

**Bug corrigido**: `_melhor_num_por_fiada` assumia sempre frente+trás (2×k por
fiada), mesmo para tipologias emparelhadas (`profundidade_inclui_par=True`,
só têm o lado "único", k por fiada — ver `racking.com_par_oversize`). Estava
"adormecido" no código antigo (`_larguras_alinhadas` já tratava isto à parte,
e `_melhor_num_por_fiada` só era chamada com k já escolhido); ativou-se agora
que esta função escolhe o k diretamente para TODAS as tipologias, incluindo
emparelhadas — sem o parâmetro `par_incluido`, uma emparelhada de 2 unidades
partia-se em 2 fiadas de 1 (pior) em vez de ficar 1 fiada de 2 (melhor).

**Espaços livres / racks fantasma (Passo 4)**: `EspacoLivre` só tem uma
origem agora — o resto de uma prateleira que nenhuma fiada ocupou (`fila_idx`
negativo sintético, `lado="prateleira"`), atribuído à tipologia que fecha essa
prateleira. `gerar_racks_fantasma` não mudou: continua a estender a MESMA
tipologia do espaço. Não reintroduzir a antiga `_espacos_livres_por_fiada`
(removida): essa via media o "livre" contra a largura TOTAL do bloco, o que
deixou de fazer sentido quando o chão ao lado já pode pertencer a outra
tipologia.

## Decisão fechada: picking manual nunca fica fragmentado (Config.picking_manual_racks)
Pedido explícito do utilizador: tipologias de picking MANUAL (operador a pé --
default `("prateleira",)`, substring case-insensitive no nome, mesmo padrão de
`oversize_racks`/`max_modulos_por_tipologia`) continuam livres para
**partilhar prateleira** (shelf packing) com outras tipologias -- isso não
muda -- mas as SUAS PRÓPRIAS posições nunca podem ficar espalhadas por bandas
do bloco não-adjacentes. Picking manual e movimentação de paletes são
operações servidas por pessoas/equipamento diferentes; um operador a pé não
deve ter de atravessar o armazém para chegar à "outra metade" da sua zona.

**Porquê era preciso**: o shelf packing normal decompõe cada tipologia em
FIADAS soltas (`_fiadas_tipologia`) e deixa o FFDH (`_empacotar_por_shelf`)
distribuí-las livremente por "prateleiras" (bandas Y) -- incluindo a fiada
"resto" (mais rasa, com o seu próprio k) de uma tipologia, que podia acabar
numa prateleira DIFERENTE das fiadas cheias da mesma tipologia, com fiadas de
outra tipologia entaladas no meio. Confirmado com dados sintéticos (7
Prateleira Leve + 8 Porta-Paletes + 10 Rack C): sem correção, a Prateleira
Leve aparecia em dois grupos Y desconexos.

**Como funciona** (`layout._bloco_tipologia_unico`): para as tipologias que
casam `picking_manual_racks`, decompõe-se nas suas fiadas (mesma lógica de
sempre, incl. o resto com o seu próprio k) mas depois EMPILHAM-SE dentro de
UM SÓ chunk atómico (com os corredores internos entre as suas próprias fiadas
preservados) antes de entrar no shelf packing global -- o FFDH já não pode
espalhar esse chunk por prateleiras diferentes, só decidir com quem o
partilha (ou não) na prateleira onde calhar. As restantes tipologias
continuam a decompor-se em fiadas soltas e a ir a `_ajustar_decomposicoes`
normalmente. `_largura_alvo_shelf`/`_empacotar_tipologias` não mudaram --
recebem só mais um chunk na lista.

Efeito colateral aceite: uma tipologia de picking manual perde a otimização
"a fiada resto encaixa numa sobra de outra tipologia" -- fica sempre um
retângulo próprio, o que pode custar uma fração de compactação face ao ótimo
absoluto sem a restrição. É o trade-off pedido (operação > alguns m²).

## Decisão fechada: envelope do bloco de racks
`Config.largura_max_m` / `profundidade_max_m` (0 = sem limite) limitam o bloco de
**racks + corredores** — não as zonas funcionais, que continuam à parte.

É restrição de **forma, não de capacidade**: o nº de racks continua a vir do
stock (Passo 2 intocado). A profundidade é **consequência livre** — por isso
`profundidade_max_m` é um teto de **aviso**, não um corte. Se não couber,
`WarehouseLayout.avisos` diz quanto falta (m e m²) e os avisos sobem ao
Excel/frontend; nunca se deixa stock de fora em silêncio.

**`largura_max_m` é um TETO, não um alvo** (`layout._ks_com_teto`): parte-se
sempre da forma que minimiza a área (`_larguras_alinhadas`) e só se REDUZ o k das
tipologias que excedam o limite. Dar um teto acima do ótimo livre **não muda
nada** — nos dados atuais, teto de 15 m mantém o bloco em 8.10 × 55.6 m.

Houve aqui uma versão que enchia as filas ATÉ à largura dada: com teto de 15 m e
prateleiras de 1.83 m metia 8 posições (14.64 m) quando o ótimo eram 4 (7.32 m),
ou seja **piorava** a área ao "respeitar" um limite que nunca era violado. Um teto
que agrava o resultado quando não é violado não é um teto. Não reintroduzir.

O `/api/render-layout` **tem de receber o mesmo envelope** do `/api/gerar`: sem
isso recalculava a forma livre e depois rodava-a com as dimensões do bloco do
canvas (`_roda_rect`), espremendo as posições num retângulo que não era o delas.

Não confundir com `altura_armazem_m`, que é benigno (só reduz empilhamento);
largura/comprimento podem tornar o stock inarmazenável.

## Decisão fechada: stock de autonomia mínima é algorítmico (Passo 0)
`Stock_máx_10un_a_utilizar` (master data v2) era calculado à MÃO fora do código
(autonomia mínima de 10 carros, arredondado para cima para encher a caixa/palete
escolhida) e colado como INPUT — o pipeline não sabia que essa decisão tinha sido
tomada, só consumia o número já pronto. Passou a ser **Passo 0**
(`boxing.stock_para_autonomia`, chamado por `main.run` antes do Passo 1):

```
stock_teórico = ceil(Config.autonomia_minima_carros × Peças_por_carro)   # 10 carros
(load_type, ppu) = escolha_de_caixa(stock_teórico)                       # mesmo best-fit do Passo 1
capacidade_prática = capacidade_reportada(load_type, ppu)                # teto + arredonda à centena
nº_unidades = ceil(stock_teórico / capacidade_prática)
stock_max_un = capacidade_prática × nº_unidades                          # arredonda p/ cima, enche a última caixa
```

`Article.stock_max_un` deixa de vir do master data: é sempre decidido por este
passo. `Article.stock_atual_un` (`Stock_máx_un` bruto) fica só como referência
nos resultados (sheet Autonomia) — **não** entra na fórmula nem é feito
`max(atual, teórico)`: o stock atual pode ser MAIOR que o teórico e mesmo assim
o stock usado vem do teórico (ex.: SKT01.BBA231 tem 100 un. atuais mas usa 98,
porque a autonomia de 10 carros só precisa de 40 e a caixa M enche a 98).

Reverse-engenheirado e validado por comparação byte-a-byte com o comportamento
antigo: os `stock_max_un` resultantes batem **735/735** com a antiga
`Stock_máx_10un_a_utilizar` da v2 (716 pela fórmula + 19 por
`Config.stock_autonomia_override_sku`, ajustes manuais que não seguiam a
fórmula e foram preservados explicitamente em vez de perdidos na migração).

`warehouse/cenario_autonomia.py` (escala o stock para outra autonomia-alvo,
sem tocar no Excel) e as três rotas de `web.py` que chamam `load_articles`
(`/api/articles`, `/api/gerar`, `/api/render-layout`) chamam
`boxing.aplicar_stock_autonomia` logo a seguir a `load_articles`, antes de
qualquer escala por `carros_alvo` — sem isso ficariam a dimensionar a partir do
`Stock_máx_un` bruto (placeholder que `load_articles` agora devolve).

### Ficheiros que o pipeline escreve
`io_excel.write_results` (o `resultados.xlsx`: folhas Lógica, Resumo, Detalhe,
Autonomia, Validação, Avisos) tem **dois** call sites e ambos têm de continuar a
produzir a mesma folha: `main.run` (CLI) e `/api/gerar` (botão «Gerar racks»).
Até 2026-09 só o CLI é que o escrevia, e quem usava o browser ficava sem o
Excel todo — só com o `pecas_tipo_caixa.xlsx`, que é outra coisa (cópia da
folha «Peças» + coluna `Tipo_Caixa`). O helper que achata as posições para a
folha «Detalhe» vive em `io_excel.flatten_posicoes_para_detalhe` precisamente
por ter os dois consumidores.

Diferenças legítimas entre os dois: o `/api/gerar` usa a altura/picking/margem
do formulário (`local_cfg`), o CLI usa as do cliente; e com `carros_alvo > 0` o
`/api/gerar` passa `autonomia_resultados=None`, porque o rasto do Passo 0 é
anterior ao reescalamento (a folha Autonomia sai na mesma, com "-" nas colunas
do Passo 0 em vez de números que não batem certo).

A escrita está dentro de `try/except OSError`: o modo de falha normal é o
ficheiro estar aberto no Excel (`PermissionError`), e isso **não** pode rebentar
o `/api/gerar` — vira aviso e `resultados_excel: false` na resposta, que é o que
esconde o link de download na UI.

## Em aberto
- 16 SKUs sem `Peso_kg` no master data (#N/A) — lidos como 0.0 na leitura
  (peso desconhecido não corta capacidade); falta confirmar se é o critério
  certo ou se esses SKUs precisam de tratamento à parte.

## Decisão fechada: corredores
O `aisle_factor` (fator de corredor agregado, % da footprint) foi **removido** —
era código morto desde que `layout.py` passou a dispor os racks fisicamente. A
área de corredores é **medida** (retângulo envolvente − footprint dos racks) e as
larguras vêm de `RackType.corredor_m` (folha «Racks»), com `CORREDOR_DEFAULT_M`
(2.5 m) e `CORREDOR_TRANSICAO_M` (3.0 m) como omissões. Não reintroduzir: um
fator único não distingue corredor de empilhador (2.5 m) de picking manual
(1.2 m), e subestimava — nos dados atuais os corredores dão ~108% da footprint,
não 60%.

## Decisão fechada: o armazém HERDA a unidade de movimentação da linha (SIA, 2026-10-14)
A unidade de movimentação (tipo de carga) de cada SKU é decidida pela LINHA
(`dimensionamento_linha`, bordo de linha: Passo 0 para 1 carro + Passo 1) e vai
ser imposta aos fornecedores. `Config.unidade_da_linha` (CLIENT-SPECIFIC, `true`
no `sia.json`): `boxing.choose_load` devolve `boxing.unidade_da_linha`, que
reproduz o cálculo da linha com o mesmo motor (`autonomia_minima_carros_linha`
= 1, sem `pallet_ultimo_recurso`, que a linha não tem). O armazém só decide
QUANTAS unidades guarda (Passo 0 com os 10 carros). Com isto ligado,
`pallet_ultimo_recurso` só conta para os SKUs que não herdam.

**Exceção:** SKUs que na linha vão em CARRINHO (folha «Caixas Especiais»,
copiada do master data da linha) não herdam — não há carrinhos no armazém;
seguem o best-fit próprio. `io_excel.com_carrinhos_da_linha(cfg)` preenche
`Config.skus_carrinho_linha` em `main.run`, `cenario_autonomia` e nas rotas do
`web.py`.

**Pré-requisito:** as folhas «Peças», «Caixas» e «Caixas Especiais» do master
data do armazém têm de ser cópia das da linha (sincronizadas em 2026-10-14;
backups `*.pre-sync-linha-2026-10-14.bak` e `*.pre-caixas-especiais.bak`).
«Racks» e «Zonas» são próprias de cada projeto.

Validado contra o código da linha (`bordo_linha.calcular_bordo_linha`): tipo de
carga E peças por unidade iguais em 428/428 SKUs fora de carrinho. Nos SKUs
herdados as peças por unidade são a capacidade PRÁTICA da linha
(`capacidade_reportada`: teto/centena/override/PECAS_CAIXA_FORCADA) -- é com
essa quantidade que o fornecedor entrega, por isso é ela que dimensiona (decisão
do utilizador, 2026-10-14). Não mudou nenhum nº de caixas face à geométrica:
o Passo 0 já arredondava o stock à capacidade prática. Efeito medido (SIA, config do cliente):
área de racks 90,1 -> 95,7 m², área bruta 418,1 -> 461,8 m²; MEIA 129 -> 98,
PALLET 108 (igual), muitas mais caixas XS/S/M (S 52 -> 221, XS 233 -> 423).

## Decisão fechada: `pallet_ultimo_recurso` (SIA, 2026-09) — caixas pequenas em vez de paletes
`Config.pallet_ultimo_recurso` (`bool`, default `False`, CLIENT-SPECIFIC —
vive em `dados/clients/<nome>.json`, não em `config.py`): quando `True`, a
PALLET deixa de competir no best-fit de `boxing.choose_load` — só entra pelo
mesmo fallback que já existia para OVERSIZE (peça não cabe em nada mais
pequeno → tenta a palete → senão OVERSIZE). Nunca perde stock: um SKU só
continua em PALLET se não couber fisicamente em nenhuma caixa mais pequena.

Porquê: decisão de negócio da SIA, não uma correção ao algoritmo — caixas
L/MEIA/M/S/XS são mais fáceis e seguras de movimentar à mão/porta-paletes do
que uma palete cheia, e o armazém é grande o suficiente para absorver o
custo de área. Testado exaustivamente antes de ligar (conversa com o
consultor, comparando PALLET→L/MEIA com e sem forçar stock a recalcular):
**mais espaço, não menos** — 672→815 unidades de carga, 35→47 racks,
298,3→348,3 m² (+16,8%). É um trade-off consciente (chão por
movimentabilidade), validado com o cliente antes de ligar, não um bug.

Junto com isto, `Aceita` do "Rack Porta-Paletes" na folha «Racks» do master
data da SIA deixou de incluir `L` (agora só `PALLET, MEIA`) — força toda a
caixa L a ir só para a "Prateleira Leve" (único rack que ainda aceita L).
É edição de DADOS, não de código — `Prateleira Leve.Aceita` já incluía `L`,
por isso nenhuma alteração ao motor de alocação foi precisa, só a este valor
na folha «Racks». Backup do ficheiro antes desta edição:
`dados/archive/master_data_classificado_v3.xlsx.pre-L-removal.bak`.

Não reintroduzir PALLET a competir livremente para a SIA sem confirmar de
novo com o cliente — a mudança é deliberada, o algoritmo em si não mudou de
opinião sobre o que minimiza chão (continua documentado acima, `Regra de
escolha de caixa`).

## Decisão fechada: níveis de 1,8 m com pé-direito útil de 4,6 m (SIA, 2026-10-27)
Substitui a versão do mesmo dia com `1.8,1.8,1.0` e caixas no porta-paletes.

**Parâmetros**: pé-direito útil 4,6 m (já sem a folga dos sprinklers, não se
desconta mais nada). Porta-paletes com passo de 1,8 m e 3 níveis (vigas a
0 / 1,8 / 3,6 m), viga 0,09 m, folga de manuseio 0,10 m em todos os níveis, deck
da palete 0,144 m (Ext_Alt − Int_Alt). Convenção: abertura = passo − viga
(também no topo: 4,6 − 2×1,8 − 0,09); altura útil (deck + carga) = abertura −
folga → **1,61 / 1,61 / 0,81 m** (carga máx. 1,466 / 1,466 / 0,666).

**Folha «Racks»** (backup `dados/master_data_classificado_v3.xlsx.pre-niveis-1-61m.bak`):
- **Rack Porta-Paletes**: Níveis 3, `Alt_nivel_m` 1.61 (= máximo, referência),
  `Alturas_Niveis_m` = `1.61,1.61,0.81`, «Aceita» só **`PALLET, MEIA`**: não há
  caixas nos porta-paletes. A emparelhada (`com_par_oversize`) herda aceita +
  OVERSIZE e as alturas.
- **Prateleira Leve**: **1,80 × 0,60 m — medida DEFINITIVA** (2026-10-28, decisão
  do utilizador; **não "corrigir" para 1,83**: esse valor foi posto como correção
  do 1,80 e voltou a ser revertido). Vive só na folha «Racks» do master data
  (nenhum valor hardcoded no código; os testes usam 1,8 nas fixtures). Backup
  anterior: `dados/master_data_classificado_v3.xlsx.pre-prateleira-1-8m.bak`.
  Os exemplos históricos com 1,83 m nesta nota (ex. «8 posições, 14.64 m»)
  são resultados datados e ficam como estão. Os Excel de `dados/archive/` e
  os `dados/cenarios/` também guardam 1,83 (histórico/saídas antigas) e não se
  mexem; `resultados.xlsx` é saída e refaz-se na próxima geração.
  **um módulo de 6 níveis** de 0,70 m (prumo 4,2 m) por posição de chão —
  `blocos_empilhados` = floor(4,6 / 4,2) = 1. Não são "2 módulos de 3 níveis".
- O loader lê `Alturas_Niveis_m` por cabeçalho e rebenta se a contagem não bater
  com Níveis (`io_excel.load_rack_types`).

**Folha «Caixas»**: PALLET e MEIA com `Int_Alt_m` 1.466 e `Ext_Alt_m` 1.61 (eram
1,5 / 1,644; Volume acompanha). Uma palete cheia de 1,644 m deixa de caber em
qualquer nível — aceite. Mexe no Passo 0: menos peças por palete.

**Altura REAL da palete/meia** (`boxing.altura_real_unidade`): deck + carga das
peças que lá vão — a capacidade prática (`capacidade_reportada`, a de «peças
nesta caixa»), orientação de `pieces_per_box` (mais peças; empate → carga mais
baixa), `fill_factor_plano` + tolerância lateral, camadas = ceil(peças /
peças_por_plano) até ao máximo, altura = camadas × cota (eixo de aninhagem
vertical: cota × (1 + (camadas − 1) × fator)). `allocate_to_racks` põe-na no
`_ItemParaColocar.alt_m`, e `_empacotar_nivel` aceita paletes baixas no 3.º
nível. O filtro de tipologias (`rack_capacity`) e o `boxes_per_stack` continuam
com a nominal. Folha Validação: «Altura real da unidade (m)» e «Cabe no 3.º
nível (≤ 0,81)». Pesos: nenhum SKU de palete/meia tem peso; as alturas são
majorantes, não se inventa peso.

**Ordem de colocação**: área decrescente e, na mesma área, as mais ALTAS
primeiro (sort estável). As altas ficam com os níveis 1 e 2 e as baixas enchem o
3.º. Medido: 12 ordens aleatórias dos SKUs dão sempre o mesmo nº de módulos.

**`niveis_efetivos`** com `alturas_niveis_m` conta a soma acumulada das alturas
úteis: 1,61 + 1,61 + 0,81 = 4,03 ≤ 4,6 → 3 níveis (floor(4,6 / 1,61) dava 2).

**OVERSIZE na emparelhada**: a estimativa (`_oversize_plano_altura`,
`rack_capacity_oversize`) assume `alt_nivel_m` uniforme (1,61). A colocação real
respeita cada nível (`_empacotar_nivel`): verificado, nenhum stack fica num
nível mais baixo do que ele. **Sem 2.ª folga** (decisão do utilizador): nas
tipologias com `Alturas_Niveis_m` não se desconta `oversize_folga_altura_m`
(0,05), porque a folga já está nos 1,61. Sem isto, os SIDE BODY LH/RH (1,582 m
de pé, `ALTURA_OVERRIDE` = x, 20 peças) ficavam fora do dimensionamento.

**Stock nunca some em silêncio**: `allocate_to_racks` dá aviso com SKU e nº de
unidades de qualquer item que não caiba em nenhum nível compatível (antes era
descartado sem aviso).

**Saídas**: `resumo_ocupacao` traz `por_nivel` por tipologia (unidades por
nível, também na UI e na folha «Ocupação por nível»). 3D: `_serializar_layout`
manda `cotas_niveis_m` + `altura_m` FÍSICOS (`racking.cotas_fisicas_niveis`:
passo = útil + `Config.folga_manuseio_m` + `Config.viga_m`): vigas a 1,8 e 3,6 m,
prateleira com 6 níveis de 0,7 m.

O pé-direito (`altura_armazem_m = 4.6`) **não vem do Excel nem do JSON do
cliente**: passa-se no formulário da UI ou em `--altura 4.6`.

**Medido** (altura 4,6, 10 carros, sem picking):

| | `sia.json` (herda a linha, palete último recurso) | motor da linha (`unidade_da_linha` e `pallet_ultimo_recurso` a False) |
|---|---|---|
| Rack Porta-Paletes | 16 módulos | 19 |
| Emparelhada | 4 | 4 |
| Prateleira Leve | 17 módulos de 6 níveis, todos cheios | 12 (67 níveis com caixas: 11 cheios + 1 com 1 nível) |
| Caixas na prateleira | 108 L, 423 XS, 221 S, 124 M | 84 L, 233 XS, 52 S, 52 M |
| Paletes + meias | 206 un / 37 SKUs | 218 un / 59 SKUs |
| Cabem no 3.º nível | 113 un / 14 SKUs | 125 un / 21 SKUs |
| Autonomia (vs 1,5 / 1,644) | só WHEEL RIM 11,2 → 10,0 | 4 SKUs (COLUMN BOTTOM COVER 72 → 63, SINGLE MOTOR CN MOTOR 45 → 42, WHEEL RIM 11,2 → 10,0, WHEEL PLASTIC COVER 33 → 32); nenhum muda de tipo; mínimo 10 carros |

A referência externa de 2026-10-27 (20 + 4 porta-paletes, 12 prateleiras, 212 un
/ 57 SKUs) foi feita com o motor da linha: bate com a coluna da direita, a menos
de 1 módulo de porta-paletes e 2 SKUs / 6 un de palete (master data diferente).
Área bruta com o `sia.json`: 416,5 → **442,4 m²**. Os racks descem de 106,9 para
96,4 m², mas o bloco passa de 16,2 × 13,2 para 10,8 × 22,2 m: as caixas saíram do
porta-paletes, e a prateleira (antes vazia) tem agora fiadas e corredores próprios.

## Decisão fechada: procura de picking por ciclo de reposição (`procura.py`, 2026-11-05)
Pedido explícito: alocar as peças às racks do arranjo e dar uma previsão e um
mapa de calor de picking, de acordo com o dimensionamento do livro do comboio
(`comboio bulk + armazem 23-10.xlsx`, no repo `dimensionamento_linha`). Esta é
a 1.ª fase: **que SKUs saem do armazém, em que ciclo, unidade e quantidade**.
A alocação (fase 2), os tempos de picking (fase 3) e o separador na UI (fase
4) estão nas secções seguintes.

**Os pressupostos vêm do livro, não do código** (decisão do utilizador:
"pega nos pressupostos de movimentações, tempos, etc. do excel"):
`Config.pressupostos_picking` (no `sia.json`, caminho relativo à raiz do repo)
aponta para o livro; `procura.carregar_pressupostos` lê só VALORES (o livro tem
de estar gravado pelo Excel, com os cálculos em cache). PFEP e «Consumo SKU»
por nome de cabeçalho; parâmetros por endereço, como nas fórmulas do livro
(«Parâmetros» C10/C11/C49, «Cenário» C12/C13 e a secção 5, coluna F = partida
usada; «Kits» C4).

**Fórmulas reproduzidas** (folhas «Esvaziamentos», «Entregas caixas», «7b
Voltas», «Bulk»): esvaziadas(t) = INT(dias completos × emb/dia + Σ embalagens
dos consumos da ref. até à hora de t + nível inicial); caixas usam t − atraso
de reposição (coluna BN do PFEP), paletes/carrinhos não; entregas = diferença
entre voltas consecutivas do mesmo tipo; o bulk tira a ref. das voltas e
entrega as embalagens de um dia no fim do dia. **Validado a 1 carro/dia
(`validar_contra_excel`, também em `tests/test_procura.py` contra o livro
real): 60/60 voltas de caixas e 60/60 de paletes iguais à «7b Voltas» por
unidade, as mesmas 50 linhas no bulk, bulk por dia igual à folha «Bulk» nos
30 dias.** Médias por volta: 10,07 caixas, 0,92 paletes e meias, 0,58
carrinhos (= «Arm Picking + Preparação», secção A).

**Respostas do utilizador às questões em aberto (2026-11-05):**
- Os 219 SKUs sem PFEP são **consumíveis**: "temos de ver a autonomia e
  assumir". Assumido (`procura.ASSUNCOES`): unidade = a do armazém, consumo
  regular (peças/carro × carros/dia), caixa reposta na volta seguinte do seu
  tipo, bulk pela mesma regra. A 1 carro/dia só 19 dos 219 têm alguma saída
  em 30 dias (as caixas duram meses). **Supersedido**: desde a fase 3 vão por
  omissão numa reposição semanal à parte (ver "tempos de picking" abaixo).
- Velocidades/tempos: **parametrizáveis, por omissão os do Excel** (fase 3).
- Posições **fixas** por SKU (fase 2).
- Carros por dia: **parâmetro** (`Config.carros_por_dia`, `--carros`), por
  omissão 1. Tem de ser inteiro: com N carros, cada carro demora tempo/dia ÷ N
  e os consumos acontecem à mesma fração desse tempo (o dia de 1 carro
  comprimido) -- EXTRAPOLAÇÃO, não validável contra o livro (que é de 1
  carro/dia). O horário das voltas não muda com N.

**Ligação ref. do PFEP -> SKU do armazém** (`mapa_skus`): o PFEP usa às vezes
só o código (`BEN10.CDF005`). Primeiro nome igual, depois o código entre os
SKUs que nenhuma ref. levou pelo nome inteiro (caso `SKT10.BGB003`, ver
docstring). Nos dados de hoje: 226/226 refs ligadas, sem aviso. As unidades do
PFEP batem com as do armazém (herdadas da linha) em 208 das 228 linhas; as 20
diferenças são os 16 carrinhos (no armazém seguem o best-fit, ver "HERDA a
unidade") e 4 paletes: `FCC101`/`FCC103` (Europalete de 1 no PFEP, OVERSIZE
no armazém) e `BCF001` / `SKT10.BGB003_____CMS,FR RR` (Europalete de 50 / 138
no PFEP, palete de 15 / 6 no armazém -- já divergiam do motor da linha antes;
o pick sai com a unidade do PFEP, por isso a alocação tem de tratar estas duas
como "várias paletes do armazém por entrega" ou o PFEP tem de ser corrigido).

**Carrinhos e kits**: um carrinho entregue = 1 visita a cada referência do
carrinho, com as peças dela (PFEP «Qtd por embalagem»); kit no bulk = 1 visita
por referência, com «Unidades por kit». Kits nas voltas regulares não são
modelados (dão aviso; hoje não há nenhum).

Saída: `python -m warehouse.procura [--carros N] [--dias D] [--out x.xlsx]` ->
`Config.output_procura` (`dados/procura_picking.xlsx`, ignorado pelo git):
folhas Leia-me (parâmetros + assunções), Picks (cada saída), Por volta, Por SKU
(visitas/unidades/peças por dia -- o que a alocação vai usar), Validação, Avisos.

## Decisão fechada: alocação às racks do ARRANJO (`alocacao.py`, 2026-11-05)
Fase 2 do pedido acima. **Aloca às racks que estão no canvas nesse momento**
(bloco de racks gerado e/ou filas manuais do sandbox) -- pedido explícito:
"vai alocar as peças às racks que estiverem no arranjo em determinado momento,
não vai gerar um layout novo". Posições **fixas** por SKU (resposta do
utilizador). As regras estão em `alocacao.REGRAS` (também vão para o Excel,
folha Leia-me): mudar uma regra é mudar o código E esse texto.

**Geometria = a do `/api/render-layout`**: `alocacao.run` chama-o com o MESMO
pedido (`AlocacaoRequest` herda `ArrangeRequest`: pé-direito, envelope,
`carros_alvo` mudam o bloco) e lê `layout` + `racks_manuais`, já rodados e
normalizados -- as coordenadas batem com o 3D. O render passou a anotar cada
posição com `bloco_id`/`bloco_nome` (chaves novas, o 3D ignora-as). Id da
posição: `<bloco_id>|<lado>|<índice>` (filas manuais) ou
`racking|<tipologia>|<índice>` (bloco gerado); endereço legível
`<nome do bloco> frente/trás · M03 · N2`, com os módulos numerados ao longo
do eixo comprido da fila. Ponto de carga = centro da zona com «comboio» no
nome (hoje «Zona armazenamento comboio»), ou `ponto_carga` no pedido.

**Mesmas unidades do dimensionamento**: `racking.preparar_itens` foi EXTRAÍDO
de `allocate_to_racks` sem mudar nada (verificado: `resultados.xlsx` igual
folha a folha, 124 testes iguais) -- unidade herdada da linha, stack de caixas,
altura REAL das paletes/meias (o 3.º nível do porta-paletes, 0,81 m úteis, só
leva o que lá cabe: no bloco dimensionado 62 unidades, a mais alta com 0,75 m).

**Algoritmo em duas etapas, e porquê** (medido no bloco dimensionado, 32
posições, prateleiras a 100 %):
- Arrumar por frequência unidade a unidade (COI puro) fragmentava os níveis:
  ficavam de fora 11 OVERSIZE + 2 M, que o dimensionamento arruma. Não usar.
- **1.ª etapa = o packer do dimensionamento**, com as posições por distância
  ao ponto de carga: tipologias por footprint crescente (a palete tenta o
  porta-paletes antes do par emparelhado, o único sítio das OVERSIZE -- por
  custo puro as paletes enchiam os pares e ficavam de fora 8 a 30 OVERSIZE),
  posição a posição, níveis de baixo para cima, itens por área decrescente.
  Como as posições vazias de uma tipologia são iguais, dá a MESMA arrumação do
  dimensionamento: **0 por alocar no bloco dimensionado, stock de todos os SKUs
  igual** (teste de integração).
- **2.ª etapa = trocas que não tiram nada do arranjo**: (a1) posições
  INTEIRAS entre posições iguais (mesma tipologia e nº de níveis), a de mais
  visitas para a mais perto; (a2) dentro de cada posição, níveis com a mesma
  altura útil, o de mais visitas em baixo; (b) unidades com a mesma forma
  (tipo + footprint), a de mais visitas no slot mais barato onde caiba em
  altura -- `_atribuir_por_altura` garante a viabilidade com uma condição de
  contagem por classe de altura (Hall): sem ela, uma palete baixa muito
  procurada roubava o nível 1 e as altas ficavam sem lugar (teste
  `test_palete_alta_nunca_vai_para_o_nivel_de_081`).
- Trocar níveis SOLTOS em vez de posições inteiras ganhava mais (14 % contra
  13,5 %) mas partia os SKUs que ocupam vários níveis (57 SKUs em mais de 1
  posição, até 6). Com posições inteiras + contiguidade: 22 SKUs em mais de 1
  posição (só a 1.ª etapa: 45), custo ponderado −13,5 % (rot 0) / −17 % (rot 90)
  face à 1.ª etapa; classe A a 23 m em média, C a 30 m.
- **Contiguidade** (`Config.alocacao_tolerancia_contiguo_min`, 0,1 min; era
  `..._m` = 5 m antes da fase 3): na troca por forma, a unidade seguinte de um
  SKU fica numa posição onde ele já está se lá houver lugar até 0,1 min mais
  caro -- sem isto as 10 paletes de `BCB001` ficavam em 3 posições de 2 filas.

**Custo de um nível**: era |dx| + |dy| + base do nível × 2 m/m; desde a fase 3
é `tempos.custo_visita_min` (minutos de uma visita: ida e volta pela grelha ÷
velocidade do equipamento + 30 s acima de 1,6 m) -- ver a secção seguinte. Os
números de 13,5 % / 22 SKUs acima foram medidos com o custo antigo. Frequência =
`procura.taxas_por_sku` (em regime: visitas/dia = embalagens/dia limitadas ao
nº de voltas desse ciclo; nunca a contagem dos 30 dias simulados, que perde os
SKUs de autonomia longa). Classes ABC por visitas (80 / 95 %).

**Fixações** (`Config.alocacao_fixacoes`, `dados/alocacao_fixacoes.json`,
`{sku: endereço}`, ignorado pelo git): entram primeiro e a 2.ª etapa não as
mexe; endereço inexistente ou que não leva tudo -> aviso. **Incremental**
(`--incremental`/`incremental: true`, estado em `Config.alocacao_estado`,
gravado com `--guardar`/`guardar: true`): guarda a geometria EXATA de cada
unidade (posição, nível, x, y, comp, larg, nº de caixas) e repõe-na tal e qual
se ainda couber -- só se perde o que deixou de caber. Guardar só posição +
nível e voltar a empacotar mudava 50 SKUs sem nada ter mudado (um nível cheio
não volta a fechar com outra ordem). Reaplicado sem mudanças: 0 SKUs mudam.

Nada some em silêncio: `NaoAlocado` com motivo ("o arranjo não tem nenhuma
rack que a aceite (…)", "sem espaço…"). Ex.: o arranjo antigo do sandbox
(`Downloads/arranjo_layout.json`, sem par emparelhado) deixa as 46 OVERSIZE
de fora com esse motivo.

Saídas: `python -m warehouse.alocacao arranjo_layout.json --altura 4.6
[--carros N] [--incremental] [--guardar]` ou `POST /api/alocacao` (resposta
com posições, níveis e caixas para o heatmap e a vista de frente da fase 4)
-> `Config.output_alocacao` (`dados/alocacao.xlsx`, `GET /api/download/
alocacao`): Leia-me (parâmetros, custo ponderado, regras), Localizações (1
linha por unidade), Por SKU, Por posição (visitas/dia com escala de cor),
Não alocados, Avisos. Testes: `tests/test_alocacao.py`.

## Decisão fechada: tempos de picking pela localização (`tempos.py`, 2026-11-05)
Fase 3. Respostas do utilizador: **1 viagem por caixa** (como o livro),
**calibrar ao livro**, velocidades por omissão 0,8 m/s a pé / 1,5 order-picker
/ 2,0 empilhador (ASSUNÇÃO), **+30 s acima de 1,6 m** de topo de nível.

`tempo de um pick = manuseio + 2 × percurso ÷ velocidade + extra de altura`:
- **Percurso** = Dijkstra numa grelha de 0,25 m (`Config.picking_grelha_m`)
  do arranjo -- racks e obstáculos são paredes (obstáculo rodado = AABB), zonas
  são chão; 8 vizinhos sem cortar esquinas; chega-se a uma posição pelos LADOS
  COMPRIDOS (nunca pela cabeceira). Posição fechada -> distância em planta +
  aviso. Do ponto de carga (zona «comboio»). Mesma distância na alocação.
- **Equipamento**: `Config.picking_equipamento` por substring da tipologia
  (prateleira -> order-picker, porta-paletes -> empilhador); peças de
  carrinho e de kit sempre a pé. Sem velocidade de elevação: os 30 s do livro
  («Arm Picking», linha 56) já são o extra de altura (contaria duas vezes).
- **Manuseio calibrado**: manuseio = tempo do livro − deslocação média de
  REFERÊNCIA (só refs do PFEP, pesada pelos picks dos dias simulados), por
  tipo (caixa 2,5 / palete 3 / 1.ª peça de carrinho 3 / kit 2). Grava-se SÓ a
  deslocação de referência (`Config.tempos_calibracao`,
  `dados/tempos_calibracao.json`, no .gitignore) -- se um tempo do livro mudar
  o manuseio acompanha. **A calibração não se refaz sozinha**: o 1.º arranjo
  corrido é a referência e os seguintes comparam-se com ele (`--calibrar` /
  `calibrar: true` para mudar de referência). Calibrar a cada corrida apagava
  a diferença entre arranjos (média sempre = livro). Medido: com a calibração
  do bloco dimensionado (comboio encostado), pôr o comboio 30 m do outro lado
  leva a sobra de 34 para −12 min.

**Preparação por volta** = fórmulas do livro («7b Voltas» BB/BC, «Bulk» AB)
com a parte de picking trocada: caixa `C84 − G56` (esvaziar + carregar) + o
picking pela localização; palete `C89 − G70` + picking; carrinho `C90` +
1.ª peça de cada ref. pela localização + peças a mais `C103`; carruagens
`C87`/`C94`, fixo `C88`/`C95`; bulk com as viagens do livro (carruagens e fixo
por viagem, kit `C92`). Carruagens: as da «7b» (com vazios) a 1 carro/dia;
com outros carros/dia, só as cheias (aproximação, com aviso).

**Gantt do armazém** (`tempos.gantt`) = regras da folha «Gantt»: pior volta de
cada posição do dia nos dias simulados; 1 trem em avanço; margem
preparação × (1/85 % − 1) em branco a seguir; a volta parte quando a
preparação acaba; bulk depois da última regular (C102 = Não): paletes, margem
(**se uma pausa de trabalho cair dentro dela, a pausa faz de margem** -- sem
esta regra dava 51,9 em vez de 46,8), caixas, margem, 1.ª volta de amanhã +
margem. **Validado a 1 carro/dia, 30 dias: 60/60 + 60/60 voltas = «7b» BB/BC,
bulk 214,25 min/dia = «Bulk» AB nos 30 dias, sobra 46,76 min e 0 atrasadas =
«Gantt» C7/C14** (com outro nº de dias o Gantt dá "não aplicável").

**Resultado no bloco dimensionado** (comboio encostado, calibrado aí), com os
consumíveis nas voltas: livro sobra 46,8 min; modelo 35,1 min e **1 volta
atrasada 0,8 min (não encaixa)**; modelo sem consumíveis 44,6 min, encaixa.
Quem tirava o arranjo do Gantt eram os **consumíveis** (o livro não os tem:
+1,8 min por volta de caixas em média), não a localização.

**Decisão do utilizador (2026-11-05): consumíveis em reposição SEMANAL à
parte, "por agora"** (`Config.consumiveis_reposicao = "semanal"`, `"voltas"`
volta ao comportamento anterior; `Config.consumiveis_periodo_dias = 5`, 1
semana de dias úteis, ASSUNÇÃO). Ciclo `procura.SEMANAL`: no início do dia
1, 6, 11, … leva as caixas que esvaziaram desde a anterior; fora do comboio e
do bulk; frequência = 1 visita por semana no máximo. Preparação de cada
reposição (`tempos._prep_semanal`) = unidades × (esvaziar + picking pela
localização) + lista e verificação de uma volta de caixas (C88), e **não entra
no Gantt diário**: verifica-se se a pior (÷ 85 %) cabe na sobra do dia.
Consumível que gaste mais de 1 unidade por semana -> aviso (o bordo de linha
tem de aguentar a semana; hoje, a 1 carro/dia, nenhum). Resultado: Gantt do
modelo 44,5 min de sobra, 0 atrasadas (**encaixa**); 5 reposições em 30 dias,
média 19,4 min, pior 26,6 (31,3 com a margem) < 44,5 -> cabe. **Estes números
são dos 30 dias do livro; com o horizonte de 1 ano (secção seguinte) mudam.**

## Decisão fechada: horizonte de 1 ano, Gantt dia a dia, validação a 30 dias à parte (2026-11-05)
O utilizador questionou "dias a simular" como parâmetro ("não faz grande
sentido"). Tinha razão: era o «Parâmetros»!C49 do livro, cujo 30 é um limite
técnico (300 linhas da «7b»), e o resultado dependia dele -- com nível inicial 0
(as 228 linhas do PFEP) todas as caixas arrancam cheias e as referências com
autonomias 2 / 2,5 / 3 / 5 / 10 / 15 / 20 / 30 carros esvaziam juntas no dia 31
(1.ª volta de caixas com 32 caixas contra 11,7 de média; repete-se a 61, 121,
181…). Não há "ciclo completo": cada referência repete-se no seu período (1 a
1600 dias) e o m.m.c. dá ~4e16 dias.

- **`Config.simulacao_dias = 250`** (1 ano de dias úteis), fora da UI e da
  API (o campo «Dias a simular» e `dias` do `AlocacaoRequest` saíram; o
  `--dias` das CLIs também). `alocacao.run(dias=...)` fica só para testes.
- **Validação contra o livro numa corrida à parte com os 30 dias dele**
  (`tempos.avaliar(..., res_proc_validacao)`; `procura.run` idem) -- continua
  4/4 OK.
- **Gantt dia a dia** (`tempos.gantt_por_dia`): o Gantt de CADA dia (as
  preparações desse dia + a 1.ª volta do dia seguinte), além da pior volta de
  cada posição do dia (como o livro, agora com "(pior: dia N)" no nome). A
  reposição semanal compara-se com a sobra do PRÓPRIO dia. Saídas: folha
  «Gantt por dia», bloco «Dia a dia» na folha «Tempos», `tempos.por_dia` e
  `tempos.dias` na API, linha "Dias que não encaixam" no painel.

**Medido (bloco dimensionado, 1 carro/dia, tempos do livro):**

| | prep. média | caixas/dia | dias que não encaixam | pior sobra |
|---|---|---|---|---|
| 30 dias (livro) | 296,2 min (= «Comparação» D38) | 20,1 | 0 | 47,7 |
| 250 dias | 304,7 | 23,5 (+17 %) | 71 (28 %) | 0,9 |
| 250 dias, níveis iniciais desfasados (fração por linha, ângulo dourado) | 305,5 | 23,7 | 98 (39 %) | 40,9 |

- **Os 30 dias do livro são o ARRANQUE, mais leve do que o regime**: as
  caixas de autonomia longa ainda não esvaziaram (+17 % de caixas por dia em
  regime). As médias do livro (10,07 caixas por volta, 296 min/dia) estão
  por baixo.
- **O que falha é SEMPRE a volta «Caixas 2»** (partida forçada aos 75 min,
  «Cenário» E63; a preparação só começa quando a «Paletes 1» parte, aos 42 min
  -> 33 min de janela). Atrasos pequenos: 58 dos 71 dias < 5 min, 4 dias ≥ 10
  min. O fim do dia aguenta (pior sobra 0,9 min). Desfasar tira os picos (pior
  sobra 40,9) mas espalha a carga e há MAIS dias com a «Caixas 2» atrasada --
  não resolve; o horário das voltas é que é apertado. Por decidir pelo
  utilizador.
- Com os tempos pela localização (calibrados no bloco): 69 dias não encaixam,
  pior dia 180. A **reposição semanal** em 1 ano: 49 reposições, média 39 min,
  pior 128 min, não cabe na sobra do dia em 7 semanas -- os consumíveis também
  arrancam todos cheios e esvaziam juntos ao fim de meses.

Saída: folhas «Tempos» (Gantt livro / modelo / sem consumíveis, preparação
média, calibração, validação, Gantt tarefa a tarefa, assunções) e «Tempos por
volta» no `alocacao.xlsx`; «Por posição» ganha «Picking (min/dia)» com escala
de cor; `/api/alocacao` devolve `tempos` e `picking_min_dia` por posição
(`calibrar`, `dias` no pedido). Testes: `tests/test_tempos.py` + validação
contra o livro no teste de integração de `tests/test_alocacao.py`.

## Decisão fechada: separador «Alocação & Picking» (fase 4, 2026-11-05)
Pedido explícito: "um separador novo, com todas as funcionalidades
necessárias... carregar numa rack e ver a rack de frente e as peças em cada
posição". Só apresentação -- todos os números vêm do `/api/alocacao`.

- **Mesmo arranjo do 3D**: o corpo do pedido do «Renderizar 3D» passou para
  uma função única, `pedidoArranjo()` (index.html, logo a seguir a
  `canvasBlocks`: blocos do canvas + Cubo + altura/picking/envelope/
  carros_alvo), usada pelo próprio render E exposta em `window` para o
  separador -- os dois nunca divergem. `window.temArranjo()` diz se há racks.
- **Código à parte**: `warehouse/static/alocacao.js` (módulo carregado no fim
  do index.html), para não crescer mais o script principal (~3400 linhas). O
  HTML da secção e o CSS (`.aloc-*`) estão no index.html.
- **Planta**: SVG em metros (viewBox = envolvente de posições, zonas,
  obstáculos, Cubo e ponto de carga), nas coordenadas do render
  (`para_json` passou a devolver `planta`: zonas, obstáculos, Cubo). Mapa de
  calor por posição: minutos de picking/dia (omissão), visitas/dia, classe ABC
  (melhor classe presente) ou ocupação em planta; escala branco -> amarelo ->
  vermelho igual à do Excel. Ponto de carga a azul. Pesquisa de SKU destaca as
  posições e filtra a tabela.
- **Vista de frente** (clique numa rack): níveis à escala nas cotas FÍSICAS
  (`base_m`) com a altura útil tracejada (vê-se o 0,81 m do 3.º nível do
  porta-paletes), caixas/paletes à largura (`comp_m`, ao longo do vão) e
  altura reais, cor pela classe; as de trás (`y_m` > 5 % da profundidade)
  mais claras e sem rótulo. Rótulo = código curto (sem BEN10./SKT10., até ao
  1.º "_" ou espaço -- há SKUs sem «_____») cortado à largura. Por baixo, o
  conteúdo por nível (SKU, unidade, nº, peças, classe, visitas/dia).
- **Painéis**: Gantt (livro / modelo / sem consumíveis, sobra e
  atrasadas, ENCAIXA/NÃO ENCAIXA), reposição semanal (CABE/NÃO CABE),
  validação contra o livro, resumo (alocadas, por alocar com motivos, ABC,
  deslocação ponderada), avisos; tabela de localizações (clique seleciona a
  rack); link para o Excel. Parâmetros: carros/dia, dias, incremental,
  guardar como estado, recalibrar. **Supersedido em parte** (painéis, tabela
  de localizações, campo «dias») -- ver "ecrã reformulado" abaixo.

Verificado no browser com o bloco gerado (altura 4,6): 1152 unidades em 32
posições, nada por alocar, Gantt 46,8 / 44,5 min (encaixa), vista de frente de
prateleira (6 níveis) e de porta-paletes (3.º nível só paletes baixas),
pesquisa `BCB001` -> 10 paletes numa posição; «Armazém 3D» sem alteração (render
200, 89,81 m²); consola sem erros. **A calibração dos tempos gravada no teste
foi apagada**: o 1.º arranjo que o utilizador alocar passa a ser a referência.

## Decisão fechada: ecrã reformulado -- calor de todas as movimentações, cronograma do dia (2026-11-05)
Decisões do utilizador: **os atrasos da volta «Caixas 2» são aceites**; o ecrã
passa a ser planta pequena (esquerda) + rack de frente e componentes (direita)
+ faixa de indicadores + **o ano e o cronograma do dia** em baixo, em vez da
tabela de localizações ("já sai num Excel"). Para analisar os dias de rotura.

- **Calor de todas as movimentações** (modo por omissão do mapa): o CHÃO com
  as passagens/dia de todas as idas e voltas de picking
  (`tempos.percursos`: cada visita a uma posição desce o gradiente da mesma
  grelha dos tempos até ao ponto de carga, ×2; células de 0,5 m;
  `planta.percursos` na API; cor pela raiz para o ponto de carga não esmagar o
  resto) e as RACKS com visitas/dia de todos os ciclos (voltas, bulk,
  reposição semanal). As zonas são chão: os percursos atravessam-nas na
  diagonal se for mais curto (assunção da fase 3).
- **O Gantt passou a ser calculado tarefa a tarefa** (`tempos._dia`: o
  armazém E o comboio), e `gantt()` / `gantt_por_dia()` / `cronograma()`
  usam essa função -- uma fonte só. Comboio: volta regular parte à hora
  planeada ou quando a preparação acaba e dura o «t volta máx.» do livro
  («Cenário» col. I); viagem do bulk parte quando está pronta (preparação do
  modelo repartida pelas viagens na proporção da «Bulk» AB), o(s) posto(s)
  acabaram («Bulk» F) e o comboio está livre, ou à hora forçada («Bulk» AE);
  dollys (`«7c Relatório»` E29) no fim. **Bate com a folha «Gantt»** com os
  tempos do livro: viagens 14:09 / 14:31 / 14:53 (atravessa a pausa, 15:24) /
  15:25, comboio acaba 16:05, armazém acaba 16:03 (`fim_trabalho`, sem a
  margem final; a sobra inclui-a, 46,76).
- **Horas de relógio**: turno «Cenário» C53/D53, pausas G54:G56 (início em min
  de trabalho) e E54:E56 (duração). Numa fronteira de pausa, uma tarefa que
  começa aí começa depois, uma que acaba aí acaba antes (`_relogio(depois=)`);
  as barras partem-se nas pausas (`_segmentos`).
- **`GET /api/alocacao/dia/{dia}`** (a última alocação fica em
  `web._ultima_alocacao`): tarefas do dia (armazém e comboio, horas, atraso,
  nota) e os picks de cada preparação (SKU, unidade, nº, peças, posto,
  endereços, tempo). Os picks do bulk vão para a viagem cujo posto bate
  («Bulk» H); a 1.ª volta de hoje mostra os picks no comboio (foi preparada
  ontem); «Preparar … de amanhã» mostra os do dia seguinte. A resposta do
  POST deixou de levar todas as voltas (614 KB; estão no Excel).
- **O ano**: um quadrado por dia útil (colunas = semanas): verde encaixa,
  âmbar = só voltas atrasadas (aceites), vermelho = sobra < 0 ou a reposição
  semanal não cabe (`por_dia.semanal_cabe`). Pior dia, próximo vermelho, ◀ ▶.
  Ao alocar abre-se o pior dia e a posição com mais movimentações.
- Exemplo lido no ecrã (bloco dimensionado): 9 dias vermelhos, 60 âmbar; o
  pior, dia 180, rebenta por «Preparar Caixas 1 de amanhã» com 32 caixas --
  amanhã é o dia 181, um dos picos periódicos (31, 61, 121, 181…).

## Decisão fechada: incremental, estado e calibração AUTOMÁTICOS -- sem opções (2026-11-05)
Pedido do utilizador: "as 3 opções não deviam ser opções". Saíram da UI, da API
(`AlocacaoRequest`) e das CLIs `--incremental`/`--calibrar`; o que se dizia
acima sobre elas (fase 2 e 3) fica supersedido por isto:

- **Incremental = consequência**: `alocacao.assinatura_logica(cfg)` (hash de
  `ALGORITMO_VERSAO` + os parâmetros que decidem onde fica cada coisa:
  velocidades, equipamento, altura limite e extra, tolerância de
  contiguidade, grelha, regra dos consumíveis -- NÃO os carros/dia nem a BOM,
  que são entradas). Estado guardado com a mesma assinatura -> incremental;
  sem estado -> de raiz E guarda-se sozinho; assinatura diferente (ou estado no
  formato antigo) -> de raiz. **Quem mudar as regras de alocação no código
  tem de subir `ALGORITMO_VERSAO`**, senão o incremental repõe arrumações
  feitas com as regras antigas.
- **Guardar estado só com alterações**: o estado (`versao: 2`) guarda a
  geometria + a BOM (`impressao_bom`: sku -> unidade, peças/unidade, nº de
  unidades, peças/carro) + a assinatura. `comparar_bom` dá novos / removidos /
  alterados; `res.estado.pode_guardar` só quando há alterações à BOM ou a
  lógica mudou. UI: botão «Guardar como estado» que só aparece aí
  (`POST /api/alocacao/guardar-estado`, 409 se não houver nada); CLI
  `--guardar` idem. Mudar o ARRANJO não oferece guardar (pedido: só a BOM):
  as unidades cujas posições deixaram de existir voltam a arrumar-se, de forma
  determinística, em cada corrida.
- **Calibração automática no armazém dimensionado**
  (`calibracao_automatica`): a referência deixou de ser "o 1.º arranjo
  corrido" e passou a ser o bloco de racks + zonas como o «Gerar racks» os põe
  (`web.blocos_canvas_por_omissao`, extraído de `/api/gerar` para os dois
  usarem a mesma grelha de zonas). Reproduzível e igual para todos os
  arranjos. Refaz-se sozinha quando muda alguma coisa de que depende (chave:
  lógica, BOM, tempos do livro, racks, zonas, pé-direito/envelope/carros_alvo,
  carros/dia) -- `tempos_calibracao.json` guarda a chave; senão lê-se
  ("lida"/"refeita" em `res.estado.calibracao`). Custa uma alocação a mais só
  quando se refaz.

Verificado: API (sem estado -> de raiz + guardado; lógica mudada -> de raiz +
pode guardar; guardar 200, outra vez 409; a seguir incremental, 0 mudanças,
calibração lida) e browser (1.ª corrida de raiz / refeita, 2.ª incremental /
lida, botão escondido). O estado do teste no browser foi apagado.

## Decisão fechada: Zona de Espera, caminhos reais, mapa do dia, cronograma encadeado (D2, 2026-11-05)
Pedido do utilizador (4 pontos), o que se dizia acima sobre o cronograma
"à hora do livro" fica supersedido por isto:

- **Destino do picking = Zona de Espera** (`Config.picking_zona_destino =
  "ZONA DE ESPERA"`), não o comboio. `alocacao.ponto_de_carga` procura primeiro
  o nome EXATO (strip/lower) e só depois por substring -- "espera" apanhava
  "Espera para Armazenagem". Se a zona não existir no arranjo: aviso e cai na
  zona do comboio. Entra em `assinatura_logica` (mudar o destino refaz de raiz).
- **Caminhos não atravessam racks**: a grelha (`tempos.construir_grelha`) incha
  cada obstáculo em `folga = picking_passagem_min_m / 2` (0,8 m -> 0,4 m), por
  isso frestas mais estreitas que 0,8 m não são caminho; a descida do Dijkstra
  não corta cantos. `tempos.rotas()` dá o calor fino do chão e uma polilinha
  por posição (`planta.caminhos`). As zonas contam como chão pisável.
- **Mapa do dia**: ao escolher um dia a planta passa a mostrar só os caminhos
  desse dia (`cronograma.movimentos_dia`, espessura ~ nº de idas); ao abrir uma
  tarefa, só os dela (`tarefa.movimentos`). A posição de cada pick sai de uma
  atribuição FIFO unidade->posição (`ResultadoTempos.posicoes_pick`).
  «Ver o ano» / «Ver o dia N» alternam.
- **Cronograma encadeado, sem horas fixas** (`tempos._dia`, inspirado no Gantt
  do «comboio bulk + armazem 23-10 D2.xlsx», com crítica):
  - voltas na ordem do livro (C1 P1 C2 P2 ...); 1 trem em avanço: a preparação
    seguinte começa quando a anterior + margem acaba E a volta anterior já
    partiu; margem = 85 % (dur / 0,85) no armazém e também no comboio (o comboio
    só pega na seguinte depois de início + dur / 0,85 da anterior);
  - bulk: caixas primeiro (D2, `Config.bulk_caixas_primeiro`), margem entre
    blocos; viagens do bulk por ordem de disponibilidade (pronto + posto pronto),
    SEM partidas forçadas; dollys no fim;
  - **o comboio nunca é dividido por uma pausa** (`_encaixar_comboio`): se
    atravessaria, passa para depois dela (repetido se cair noutra); a
    preparação do armazém PODE ser interrompida pela pausa;
  - um dia "não encaixa" se o armazém ou o comboio passam do turno, ou se a
    reposição semanal não cabe. Deixou de haver "voltas atrasadas" (âmbar).
  - Simplificações conscientes: a procura de cada volta continua a ser a
    calculada às horas planeadas do livro (aproximação: encadear desloca as
    partidas uns minutos); a regra D2 "antes da pausa só se acabar antes dela
    com a preparação atrasada pela margem" ficou só "não atravessa".
  - `validar_contra_livro`: o Gantt passou a "não comparável" (o livro usa horas
    fixas); 7b e Bulk continuam a validar 1:1.

Resultado com dados reais (bloco do «Gerar racks», 4,6 m, 1 carro/dia, 250
dias): 0 células de caminho dentro de racks; 6 dias vermelhos (96, 101, 126,
151, 171, 201), todos pela reposição semanal; armazém e comboio nunca passam
do turno; pior dia 180: sobra 12,9 min, armazém acaba 16:30, comboio 16:20,
viagem Paletes 3 empurrada para 15:15 (depois da pausa); 107 idas a 29
posições. Browser: 2250 tarefas de comboio em 250 dias, 0 atravessam pausa;
filtro por tarefa ok; consola limpa. Testes: 155 passam.

## Decisão fechada: as unidades de um SKU ficam JUNTAS (contiguidade, `ALGORITMO_VERSAO` 2026-11-05.2)
Pedido do utilizador: uma rota do bulk de caixas ia a módulos de prateleira
de filas diferentes para o mesmo SKU. Causa medida no bloco dimensionado:
a 1.ª etapa (FFD por distância) metia a unidade seguinte no buraco livre mais
perto, muitas vezes noutra fila (27 de 401 SKUs de prateleira); a 2.ª etapa
piorava (trocas de posições inteiras posição a posição e trocas de unidades
só olhavam para "a mesma posição").

- **Vizinhança** (`alocacao.vizinhanca`): afastamento 1 = módulo ao lado na
  mesma fila OU o módulo em frente do mesmo corredor (`_frente_a_frente`:
  sobreposição ≥ metade e corredor de 0,5 a 4 m -- costas com costas NÃO é
  vizinho, é outro corredor); 2 = dois módulos ao lado ou o do lado do da
  frente. Só na mesma tipologia e fisicamente perto (o nº de módulo de uma
  fila do bloco salta da prateleira para o porta-paletes).
- **1.ª etapa** (`junto`): a unidade seguinte tenta o módulo do SKU, depois os
  vizinhos, e só depois o resto por distância. Continua a caber tudo.
- **2.ª etapa (a1)** (`_trocar_posicoes`): componentes de posições ligadas por
  SKUs que formam módulos seguidos de uma fila mudam-se em BLOCO para módulos
  seguidos livres; ordem por visitas/posição, mas um bloco só fica num sítio se
  os blocos de vários módulos que faltam ainda couberem. Componentes que não
  são módulos seguidos (ex.: ligadas pelo módulo da frente) ficam onde estão.
- **2.ª etapa (b)**: a caixa seguinte fica na mesma posição ou num vizinho se
  lá houver lugar até `alocacao_tolerancia_contiguo_min` (0,1 min) mais caro
  do que o mais barato.

Resultado (bloco dimensionado, 4,6 m, 1 carro/dia): SKUs de prateleira não
contíguos (pela vizinhança acima) 6 -> 1; tudo continua a caber; deslocação
ponderada 79,9 -> 84,1 min/dia (+5 %: o preço de não espalhar). Com tolerância
0,5 min (aceita o +30 s de altura) ficam 13 SKUs em >1 módulo em vez de 39, a
85,4 min/dia -- não adotado. Em todas as tipologias sobram 9 não contíguos,
quase todos paletes (porta-paletes costas com costas, ou normal + emparelhada).
Rota do bulk de caixas: 5 conjuntos de posições em 60 dias (a rotação FIFO
entre os módulos vizinhos do SKU, aceite pelo utilizador). O estado guardado
pelo utilizador às 15:22 fica com a lógica antiga: a próxima alocação é de
raiz e oferece «Guardar como estado». Testes: 158 passam (3 novos; os 2 de
alocação falham com o código antigo).

## Decisão fechada: referência = livro «… 23-10 D2 auto» e cronograma igual à folha «Gantt» (2026-11-05)
Pedido do utilizador: o novo Excel `comboio bulk + armazem 23-10 D2 auto.xlsx`
passa a ser a referência (`sia.json` `pressupostos_picking`; os testes com
dados reais também). A leitura não mudou de sítio; a procura continua 1:1
(7b 60/60 + 60/60, Bulk igual nos 30 dias). O livro passou a ter as regras do
cronograma encadeado (voltas regulares «o mais cedo possível», horário
calculado por repetição e gravado em «Cenário» E63/E82/E83 -- é isso que
`tl.partidas` lê, por isso a procura de cada volta já usa as horas reais). O
que mudou no `tempos._dia` para bater com ele:

- **Regra das pausas** (`_encaixar_comboio`, «Parâmetros» C107, «Gantt» Y33 e
  X67:X77), igual para voltas regulares, viagens do bulk e dollys: só fica
  antes da próxima pausa se máx.(início, pronto + preparação × (1/85 % − 1)) +
  duração ÷ 85 % acabar antes dela; senão parte no fim da pausa. (Antes:
  só "não atravessa".)
- **Viagens do bulk e dollys em intervalos**: podem ir num intervalo do comboio
  entre voltas regulares (regresso de uma -> partida da seguinte) se lá
  couberem sem a atrasar («Gantt» V67:V77); senão depois do regresso da última.
- **Dollys** só depois das saídas das estações C (`tl.dolly_rel` = «Gantt» E77
  = máx. das «Saídas C» com «Sim»), não depois do posto pronto das viagens.
- **`horario=True`** (só na validação): a volta regular não parte antes da
  hora gravada no livro -- o livro usa O = máx.(horário, pronto, regresso da
  anterior) e o horário gravado pode ser > o mais cedo possível (36,25 contra
  31,75 na Paletes 1: o cálculo por repetição só atrasa). Na simulação dia a
  dia fica `False` (parte mal possa -- regra do utilizador, sem horas fixas).
- **Validação contra a folha «Gantt» de volta** (`validar_contra_livro`, com os
  tempos do livro e a pior volta de cada posição nos 30 dias): sobra do
  armazém (C7) 41,18 = 41,18; sobra do mizu (C9) 88,70 = 88,70; partida das 4
  voltas regulares (O32:O35), das 4 viagens do bulk e dos dollys (K67:K77) 9
  de 9 iguais. Com o livro 23-10 antigo (horas fixas, partidas forçadas) esta
  validação falha -- é esperado.

Resultado (arranjo do «Gerar racks», 4,6 m, 1 carro/dia, 250 dias): 7 dias
vermelhos (36, 96, 101, 126, 151, 171, 201), TODOS pela reposição semanal;
armazém e comboio nunca passam do turno; 0 tarefas do comboio atravessam uma
pausa; 1.ª viagem do bulk às 13:00 em 189 dias (até 13:08 nos outros); pior
dia 30 (sobra 5,5 min, armazém acaba 16:36, comboio 15:31). REAR END
(BEN10.FCC103) continua com 0 emb/h e fora do bulk no PFEP do livro: é
arrumado mas nunca tem picking (coluna «Ciclos» vazia) -- a confirmar no livro.
Testes: 160 passam.

## Decisão fechada: zona de bulk e percurso único do bulk de caixas (`ALGORITMO_VERSAO` 2026-11-05.3)
Pedido do utilizador: a rota do bulk de caixas ia a módulos diferentes no
dia 1 e no dia 2 e deixava módulos sem visitas no caminho -- "não devíamos
agrupar o bulk perto, visto que sai todos os dias?". Causa medida: o custo de
uma visita tem +30 s acima de 1,6 m (N3-N6 das prateleiras: 1,18-1,47 min)
e a diferença de distância entre o módulo mais perto e o mais longe é só
~0,3 min (N1-N2: 0,68-0,97) -- o algoritmo preferia um N1/N2 longe a um N3
perto, e o bulk ficava nos níveis baixos de 16 dos 17 módulos, misturado com
voltas e consumíveis. Além disso o modelo por caixa (uma ida e volta cada,
como o livro) não valoriza ter o bulk junto, mas o bulk de caixas prepara-se
DE UMA VEZ (34 caixas, ~98 min) num percurso com o carrinho.

- **Zona de bulk** (`Config.alocacao_zona_bulk = True`, `alocacao.zona_do_sku`:
  bulk -> voltas -> reposição semanal -> sem picking, pelos `Taxa.ciclos`): na
  1.ª etapa o bulk arruma-se primeiro (FFD dentro da zona), nas posições mais
  perto do destino; a 2.ª etapa não mistura zonas (posições inteiras ordenam-se
  por zona e depois por visitas; as unidades só se trocam com unidades da mesma
  zona). Dentro da zona, o mais frequente nos níveis baixos.
- **Tolerância de contiguidade 0,1 -> 1,0 min**: com a zona, a troca de
  unidades partia 5 SKUs por módulos não vizinhos; com 1,0 (na prática "sempre
  junto": nas prateleiras a diferença máxima é ~0,8 min) ficam 0, e SKUs em
  >1 módulo 31 -> 19, por +0,3 min/dia de deslocação ponderada.
- **Percurso único** (`tempos.Percursos`, `rt.percursos`, criado em
  `alocacao.run`): para as preparações do bulk de CAIXAS, o cronograma dá
  `percurso` = {metros, paragens, pontos, ordem, idas_voltas_m}: sai do destino,
  passa pela frente de cada posição e volta (mesma grelha/passagem/acessos dos
  tempos; vizinho mais perto + 2-opt; Dijkstras em cache). As PALETES não: o
  empilhador leva uma de cada vez, aí as idas e voltas são o percurso real.
  O TEMPO não muda (continua o por caixa calibrado ao livro) -- o percurso é um
  indicador. UI: ao abrir a tarefa, a planta desenha o percurso (roxo) e a
  legenda e a tabela dão metros e paragens.

Resultado (arranjo do «Gerar racks», 250 dias), antes -> depois: bulk de
caixas em 16 -> 10 módulos (os 10 mais perto); paragens/dia 13 -> 7,2;
percurso 99,7 -> 92,1 m/dia (o grosso são os ~35 m de cada lado até à Zona de
Espera); idas e voltas por caixa 2526 -> 2250 m; paletes (idas e voltas)
Paletes 1 509 -> 503, Paletes 3 516 -> 479 m; deslocação ponderada 84,1 ->
84,4 min/dia; tudo cabe; 0 SKUs não contíguos. Do dia 1 para o 2 nenhum SKU
do bulk de caixas muda de módulo neste arranjo. Testes: 162 passam.

## Decisão fechada: regras das viagens do utilizador -- horário fixo das voltas e 13:00 do bulk (2026-11-05)
O utilizador corrigiu o pedido anterior ("começar as viagens logo às 13h" e
"sem horas fixas") com as regras de outra análise do livro. Comparadas com o
`tempos._dia`, batiam todas menos duas, agora iguais:

- **Voltas regulares: o MESMO horário todos os dias**, o do livro («Cenário»
  E63/E82/E83, calculado o mais cedo possível para o pior dos 30 dias):
  O = máx.(horário, pronto, comboio livre + 85 %, regra das pausas). Num dia
  mais pesado a volta sai depois do horário = atraso (`Tarefa.atraso`,
  `atrasadas`/`atraso_max` do dia, nota «atrasada: …»). `Config.
  voltas_horario_do_livro = True` (False = cada dia parte mal possa, a regra
  anterior). Supersede o "encadeado puro" das voltas regulares das decisões
  acima; o armazém continua encadeado (1 trem em avanço, 85 %).
- **Bulk: hora mínima explícita** `tl.bulk_hora_min` = «Parâmetros» C104 (225
  min de trabalho = 13:00) e ordem no armazém `tl.bulk_caixas_primeiro` =
  C106, lidas do livro (saiu `Config.bulk_caixas_primeiro`). A «Bulk» F já
  incluía a hora mínima e a margem na linha (C105 = 0); agora é explícito.
- Iguais desde antes: Caixas 1 às 8:00 (preparada na véspera); ordem C1 P1 C2
  P2; postos por viagem (Caixas: todos; Paletes 1: C2, C1, C4, P1; Paletes 2:
  C5; Paletes 3: C3, do «Bulk»); caixas antes das paletes; cada viagem só
  depois de preparada e 85 % depois da anterior; dollys depois da última
  viagem (+85 %) e da última estação C com dolly (C1', 15:21:30); pausas com
  folga (C107).

Resultado (250 dias): validação contra a folha «Gantt» 9 de 9; 1.ª viagem do
bulk 13:05-13:15 (no pior dia dos 30 do livro: Caixas 1 13:16, Paletes 1
13:51, igual ao livro); 9 dias vermelhos: 7 pela reposição semanal e 2 (30 e
180) porque o ARMAZÉM passa do turno (sobra −11,9 e −9,6 min) -- com as voltas
encadeadas não passava em nenhum (pior sobra +5,5): o horário fixo é calculado
para o pior dos 30 dias do livro, mais leves do que os 250 (arranque), e com 1
trem em avanço uma partida mais tarde atrasa a preparação seguinte. 8 dias com
voltas atrasadas (máx. 26,9 min, dia 192). Testes: 163 passam.
O utilizador escolheu manter o horário do livro (pior dos 30 dias), aceitando
os 2 dias em que o armazém passa do turno.

## Decisão fechada: o mais frequente perto -- 1.ª etapa por frequência e trocas aos pares (`ALGORITMO_VERSAO` 2026-11-05.4)
Pedido do utilizador: no mapa de calor (visitas/dia por posição) havia racks
mais escuras mais longe. Medido: a 1.ª etapa arrumava por ÁREA (FFD, como o
dimensionamento) -- as caixas grandes iam para as posições de perto qualquer
que fosse a frequência -- e a troca de posições inteiras em bloco (a1) quase
nunca se aplicava, porque a contiguidade liga as posições umas às outras
(componentes que não são módulos seguidos de uma fila ficam fixas). Nos
porta-paletes a correlação custo × visitas era nula ou ao contrário (arranjo
do utilizador −0,09; «Gerar racks» +0,69 = mais longe, mais visitas).

- **1.ª etapa por frequência** dentro de cada zona (visitas/dia por unidade
  guardada, decrescente; FFD de desempate). Se deixar alguma unidade de fora,
  `alocar` repete por área (`_alocar(por_frequencia=False)`) e fica com a que
  deixa menos de fora (aviso).
- **2.ª etapa (c)** `_trocas_aos_pares`, no fim: (1) reparação -- um SKU que a
  1.ª etapa partiu (os vizinhos já estavam cheios) junta-se trocando a posição
  solta com uma igual ao lado do resto dele, mesmo que custe um pouco mais;
  (2) melhoria -- troca o conteúdo de duas posições inteiras iguais sempre que
  baixa Σ visitas × custo, sem partir nenhum SKU (nº de blocos pela
  vizinhança não aumenta) e sem misturar zonas (mesma zona de topo, ou uma
  vazia). Repete até não haver ganho.

Resultado (correlação custo × visitas por tipologia; inversões = pares em que
a posição mais longe tem claramente mais visitas):

| arranjo | antes | depois |
|---|---|---|
| utilizador, prateleiras | −0,52 · 14 inv. | −0,78 · 8 inv. |
| utilizador, porta-paletes | −0,09 · 46 inv. | −0,63 · 1 inv. |
| «Gerar racks», prateleiras | −0,64 · 19 inv. | −0,83 · 10 inv. |
| «Gerar racks», porta-paletes | +0,69 · 14 inv. | −0,71 · 5 inv. |

Deslocação ponderada: utilizador 46,3 -> 46,6 min/dia (só as trocas aos pares
davam 45,2, com o mapa pior: escolhida a versão que põe o mais frequente
perto); «Gerar racks» 84,4 -> 83,2. Não contíguos (todas as tipologias):
utilizador 5 -> 2, «Gerar racks» 6 -> 7 (paletes). As inversões que sobram
são sobretudo de altura: subir acima de 1,6 m custa +30 s, mais do que a
diferença de distância entre módulos, por isso um nível baixo um pouco mais
longe pode ser mais barato do que um alto perto -- o mapa por posição soma os
níveis e não mostra isso (o modo «minutos» mostra o custo real).
No arranjo do utilizador (5 m, 10 carros): 46 SKUs OVERSIZE (70 unidades)
ficam por alocar -- o arranjo não tem «Rack Porta-Paletes 2 (emparelhada)».
Bulk de caixas: percurso 40,9 m/dia, 8,7 paragens. Testes: 165 passam.
(Correção, 2026-11-09: as "46 OVERSIZE" eram 46 PILHAS de 7 SKUs = 70 peças,
10 carros × 7: FLOOR PANEL, FRONT END, REAR END, SIDE BODY LH/RH, ROOF 4W,
HEADLINER.)

## Decisão fechada: OVERSIZE em racks normais com dois corredores (`ALGORITMO_VERSAO` 2026-11-09.1)
Pedido do utilizador: "as oversize podem entrar em racks normais, desde que
tenham dois corredores, como se fosse uma emparelhada". O arranjo dele só tem
«Rack Porta-Paletes» simples (frente/trás em filas duplas) e prateleiras -- os
7 SKUs OVERSIZE (46 pilhas) ficavam por alocar.

- `alocacao.posicoes_em_par`: numa fila DUPLA de porta-paletes
  (`Config.oversize_par_racks`), cada par frente + trás costas com costas
  (sobreposição ≥ 90 %, folga ≤ 5 cm) com acesso pelo chão às DUAS faces
  (`tempos._celula_acesso` na grelha com a passagem mínima) dá uma posição
  virtual «<fila> frente+trás» (`par|<frente>|<trás>`), tipologia sintética
  «<base> (par frente+trás)» (profundidade × 2) que só aceita OVERSIZE.
  `com_pares` junta-as às posições, distâncias (face mais perto) e candidatos
  das OVERSIZE (DEPOIS das emparelhadas dedicadas, se houver). Em `run` e na
  calibração. `Config.alocacao_oversize_em_pares = True`.
- `_alocar(pares=...)`: as OVERSIZE arrumam-se primeiro; um nível ocupado no
  par reserva o mesmo nível nos dois módulos reais e vice-versa
  (`area_livre = -1`, também no incremental); pares e módulos com nível
  reservado ficam fora das trocas de posições/níveis (a reserva é física).
- UI: o par só se desenha (contorno tracejado roxo, clicável) quando tem carga;
  `para_json` marca `par`. Endereço no Excel: «… frente+trás · M08 · N1».
- Cache: o browser corria o `alocacao.js` antigo depois de uma atualização;
  agora o servidor manda `Cache-Control: no-cache` em tudo o que não é
  `/api/` e o `index.html` pede `alocacao.js?v=<data>` (subir a data quando
  mudar o JS).

Resultado no arranjo do utilizador (5 m, 10 carros, guardado em
`dados/arranjo_utilizador_2026-11-05.json`): 0 por alocar; as 70 peças em 4
pares (filas duplas 1 e 2, M07/M08, os mais perto da Zona de Espera); 0
conflitos par × módulo real; deslocação ponderada 46,6 -> 50,7 min/dia (mais
70 peças com picking diário: o FRONT END, os SIDE BODY... vão no bulk); 1 dia
vermelho em 250 (reposição semanal), armazém nunca passa do turno, pior dia
30 com 34,8 min de sobra. «Gerar racks»: as OVERSIZE continuam nas
emparelhadas dedicadas (5 pares criados, não usados). Testes: 166 passam.

### Visualização dos pares e escala do mapa (2026-11-09)
O utilizador abriu «Rack Porta-Paletes (fila 1) trás · M08» e viu os 3 níveis
«vazio» -- estavam reservados pelas OVERSIZE do par, que estão registadas na
posição virtual, e o mapa só desenhava o contorno do par. Corrigido só na
apresentação (a alocação não muda):

- `para_json`: cada nível de um módulo real reservado por um par leva `par`
  = {id, endereco, caixas, visitas_dia, prof_m} do nível do par; o módulo real
  soma metade das visitas e do picking do par (`visitas_par_dia`).
- `alocacao.js`: vista de frente desenha as OVERSIZE do par (tracejado roxo,
  título "ocupa a frente e a trás") e a tabela lista-as com «↔» e «reservado
  pelo par frente+trás»; ocupação (metade da área do par), classe, pesquisa de
  SKU e tooltip contam com elas; no modo do dia o módulo real soma as idas ao
  par.
- **Escala por tipologia** nos modos do ano (visitas/dia e minutos/dia): com uma
  escala única os porta-paletes (máx. 3,5 visitas/dia) ficavam quase brancos ao
  lado das prateleiras (máx. 9,96). Ocupação em 0-100 %; o modo do dia continua
  com escala única (conta idas). A legenda dá o máximo de cada tipologia.

Validado na fila 1 do arranjo do utilizador: M01-M03 vazios de facto (o stock
cabe nos mais perto), M04-M06 com paletes, M07 N1-N2 e M08 N1-N3 reservados
pelos pares (OVERSIZE), M07 N3 com paletes. Testes: 167 passam.
Em aberto (decisão do utilizador): OVERSIZE por frequência como o resto, ou
ao fundo de propósito -- hoje vão primeiro e ficam com os pares mais perto.

## Convenções
- Python 3.11+, dataclasses, type hints, deps em `requirements.txt`.
- Domínio em pt-PT. Packing: `rectpack` (2D, footprint por nível de rack).
  `py3dbp`/`ortools`/`pandas` estiveram listados em `requirements.txt` mas
  nunca foram importados em código nenhum — removidos (ver
  MIGRATION_LOG.md, Phase 6). Se algum dia forem mesmo precisos (ex.:
  minimização de racks por CP-SAT), voltam quando o código que os usa for
  escrito, não antes.
- Pipeline em `main.run()` legível: 1 → 2 → 3. Testes: `python -m pytest`.

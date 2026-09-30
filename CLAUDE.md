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

## Fronteira INPUT vs OUTPUT
- INPUT : geometria do artigo (m), `Peças_por_carro`, `Stock_máx_un` (só
  referência, ver Passo 0), catálogo «Caixas» (S/M/L+PALLET), tipologias «Racks».
- OUTPUT: `stock_max_un` decidido (Passo 0), tipo de carga por SKU, nº de
  unidades, nº de racks, área.

## FORA de âmbito
Slotting, COI/ABC, otimização de picking, desenho de níveis de rack (os racks já
vêm com níveis e altura por nível definidos). Não reintroduzir.

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
  a renderizar se o arranjo ou os parâmetros mudaram (`gerar3DAutomatico`).
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

## Decisão fechada: máximo de módulos empilhados
`Config.max_modulos_por_tipologia` (default `{"prateleira": 2}`) trava quantos
**módulos** — racks inteiros uns por cima dos outros na mesma posição de chão —
uma tipologia pode empilhar. Chave = substring do nome (case-insensitive).

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

## Convenções
- Python 3.11+, dataclasses, type hints, deps em `requirements.txt`.
- Domínio em pt-PT. Packing: `rectpack` (2D, footprint por nível de rack).
  `py3dbp`/`ortools`/`pandas` estiveram listados em `requirements.txt` mas
  nunca foram importados em código nenhum — removidos (ver
  MIGRATION_LOG.md, Phase 6). Se algum dia forem mesmo precisos (ex.:
  minimização de racks por CP-SAT), voltam quando o código que os usa for
  escrito, não antes.
- Pipeline em `main.run()` legível: 1 → 2 → 3. Testes: `python -m pytest`.

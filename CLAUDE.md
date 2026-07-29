# Contexto para o Claude Code

## O que é
Ferramenta de **dimensionamento de capacidade** de um armazém. A partir do
stock máximo de cada artigo, determina o repacking em unidades de carga, o nº de
racks e a área de chão necessária.

## Unidades
**Metros e m³ em todo o lado** (dados e código). Os campos terminam em `_m` /
`_m2` / `_m3`. Não introduzir milímetros.

## Pipeline (3 passos)
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
- **LONGO** — material comprido (perfis, tubos, vedantes). **Não é caixa**: vai
  direto para cantilever. O boxing encaminha para LONGO os artigos cuja
  geometria está em `cfg.long_geometrias` (por defeito "Comprida"), com
  `pecas_por_unidade = 1`. A capacidade da cantilever (`rack_capacity_long`) é de
  outro tipo: metros lineares / itens por braço, não footprint.

## Altura dos racks
`RackType.alt_nivel_m` é a **altura útil POR NÍVEL** (não o total do prumo). O
teste vertical em `rack_capacity` compara a altura exterior da caixa com
`alt_nivel_m`. Total do prumo = `alt_nivel_m × niveis`.

## Fronteira INPUT vs OUTPUT
- INPUT : geometria do artigo (m), stock máximo, catálogo «Caixas» (S/M/L+PALLET),
  tipologias «Racks».
- OUTPUT: tipo de carga por SKU, nº de unidades, nº de racks, área.

## FORA de âmbito
Slotting, COI/ABC, otimização de picking, desenho de níveis de rack (os racks já
vêm com níveis e altura por nível definidos). Não reintroduzir.

## Decisões já fechadas
- Tipo LONGO criado; "Comprida" → cantilever, sem caixa.
- PALLET é entrada do catálogo «Caixas» (não dataclass separado).
- `alt_nivel_m` = altura por nível.
- Convenção de unidades: metros / m³.
- **Orientação do artigo** passou a ser usada no boxing (`pieces_per_box`):
  «Livre» (default, ~96% dos SKUs) permite qualquer das 3 cotas como altura
  (`boxing.pode_deitar`); «Base fixa»/«Não deitar» travam o Z medido de pé.
  Reduz paletes (~−31% só por densidade). O fator de enchimento do plano
  (`fill_factor_plano`, ex-`fill_factor_rigido`) aplica-se agora a TODAS as
  classes (era só RIGIDO) — cobre o encaixe-de-gume irreal de peças finas
  deitadas.
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

## Decisão fechada: transbordo máximo ao pousar em palete
`Config.pallet_transbordo_max_pct` (0.25) limita quanto uma peça pode pender da
palete no fallback do boxing. Acima disso é **OVERSIZE** → armazenagem ao chão.

Porquê: `_assenta_na_palete` testava só as duas MENORES cotas — respondia a
"equilibra-se em cima?" e ignorava o transbordo. As laterais GFRP (2.47 m) saíam
como 1/palete com **1.27 m em voo** de uma palete de 1.20 m, mais do que a palete
mede. Não é estética: uma palete assim **não é armazenável em rack** — o
`rack_capacity` conta-a como ocupando 1.20 m quando colide com as vizinhas.

O critério real acabou por ser a **altura útil da palete**, não a tolerância: uma
peça fina e comprida pode ser posta DE PÉ e aí não transborda nada (1.45 × 1.0 ×
0.2 assenta em 0.2 × 1.0 com 1.45 m de altura, passa mesmo com tolerância zero).
Por isso `_assenta_na_palete` testa as 3 orientações e exige que a cota vertical
caiba em `int_alt_m` — sem isso, aceitava-se pôr uma lateral de 2.47 m de pé.

A tolerância aplica-se SÓ à cota maior do plano; alargá-la a ambas seria inventar
uma palete maior em vez de tolerar que a peça penda de uma ponta.

Efeito nos dados atuais: 3 SKU passam a OVERSIZE (2 laterais + tejadilho),
−5 posições de porta-paletes. **A área bruta SOBE** (719 → 741 m²) porque as
peças em palete ocupavam 1 palete cada mas eram contadas como 1.20 × 0.80; ao
chão empilham 4 (laterais) e 13 (tejadilho), mas o footprint real é maior e agora
é medido honestamente. O ganho é de correção, não de área.

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

Como funciona (mesmo padrão do LONGO/cantilever — «a própria rack é a
superfície», sem palete nem caixa):
- `racking.rack_capacity_oversize` — a peça assenta **DEITADA** (menor cota = a
  altura; painéis não se põem ao alto), as duas maiores no plano nas 2
  orientações. Empilha peças **do mesmo SKU** dentro do nível.
- **O peso trava antes da altura**: uma peça de 216 kg empilharia 9× em 1.55 m
  úteis mas só 4× com 1000 kg/nível (`Config.oversize_peso_max_nivel_kg`).
- Admissão por **substring do nome** da tipologia (`Config.oversize_racks =
  ("wide","bulk")`) ou `OVERSIZE` na coluna «Aceita» — evitou-se inventar coluna
  nova. Sem wide-span na folha, volta ao aviso de antes (nunca conta 1/palete).
- **Coexiste com a cantilever**: LONGO (1D esbelto, apoiado em braços) e OVERSIZE
  (volume plano pousado) são problemas geométricos distintos.

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

## Convenções
- Python 3.11+, dataclasses, type hints, deps em `requirements.txt`.
- Domínio em pt-PT. Packing: `py3dbp` (3D) e `rectpack` (2D); `ortools` só para
  refinamento de minimização de racks.
- Pipeline em `main.run()` legível: 1 → 2 → 3. Testes: `python -m pytest`.

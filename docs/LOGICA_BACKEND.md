# Lógica do backend — dimensionamento de armazém

Este documento descreve, passo a passo, tudo o que o algoritmo faz: dos dados
de entrada até à área total do armazém e ao modelo 3D. O código vive no pacote
`warehouse/`; cada secção indica o módulo correspondente.

**Convenção de unidades: metros / m² / m³ em todo o lado** (dados e código).

---

## 1. Visão geral do pipeline

```
master_data.xlsx
      │
      ▼
[1] BOXING (boxing.py)      por SKU: que unidade de carga e quantas?
      │  BoxAssignment (tipo, peças/unidade, nº unidades)
      ▼
[2] RACKING (racking.py)    onde guardar cada caixa? (packer 2D real)
      │  PosicaoChao → blocos → níveis → caixas colocadas
      ▼
[3] ÁREA (area.py)          footprint dos racks
[3b] LAYOUT (layout.py)     filas duplas + corredores → bloco de racks
      │
      ▼
resultados.xlsx / web (canvas de arranjo + 3D)
```

- **INPUT** (dado, nunca calculado): geometria dos artigos, stock máximo,
  peças por carro, catálogo de caixas «Caixas», tipologias «Racks», zonas
  funcionais «Zonas».
- **OUTPUT** (calculado): tipo de carga por SKU, nº de unidades, alocação a
  racks, nº de racks, áreas.

---

## 2. Dados de entrada (`io_excel.py`)

Ficheiro por defeito: `dados/master_data_classificado_v1.xlsx`.

### Folha «Peças» → `Article`
Leitura **por posição de coluna** (ordem fixa; os cabeçalhos têm problemas de
encoding, por isso não se procura por nome):

| # | Coluna | Campo | Notas |
|---|--------|-------|-------|
| 0 | SKU | `sku` | linha ignorada se vazio |
| 1 | Descrição | `descricao` | |
| 2–4 | Comprimento/Largura/Altura_m | `comp_m/larg_m/alt_m` | metros |
| 5 | Peso_kg | `peso_kg` | `#N/A`/vazio → 0.0 (peso desconhecido não corta) |
| 6 | Stock_máx_un | `stock_max_un` | motor do dimensionamento |
| 7 | Peças_por_carro | `pecas_por_carro` | **obrigatório**; erro se faltar |
| 8 | Orientação_permitida | `orientacao` | |
| 9 | Empilhável | `empilhamento` | NAO_EMPILHAVEL / EMPILHAVEL / ANINHAVEL |
| 10 | Fragilidade | `fragilidade` | |
| 11 | Geometria | `geometria` | "Comprida" → candidato a LONGO |
| 12 | Volume_unit_m³ | (não lido) | |
| 13 | Subtipo_LONGO | `subtipo_longo` | PAINEL_GRANDE / VOLUMOSO_OVERSIZE |
| 14 | Classe | `classe` | RIGIDO / FLEXIVEL_CONTENTOR |

### Folha «Caixas» → `BoxType` (catálogo de unidades de carga)
Cada linha é uma unidade de carga: **S, M, L, MEIA (meia-palete 0.8×0.6) e
PALLET (1.2×0.8)**. A palete e a meia-palete são entradas normais do catálogo
— para o algoritmo são "caixas sem paredes".
- **INT** (interior): envelope útil onde as peças entram.
- **EXT** (exterior): ocupação real no rack (inclui o deck da palete).
- `peso_max_kg`: carga máxima da unidade.

### Folha «Racks» → `RackType`
- `larg_m × prof_m`: footprint do módulo no chão.
- `alt_nivel_m`: altura útil **POR NÍVEL** (não o total do prumo).
  Altura total do prumo = `alt_nivel_m × niveis`.
- `niveis`: nº de prateleiras/níveis do módulo.
- `aceita`: tipos de carga compatíveis (ex.: "PALLET, L, MEIA").
- `comp_braco_m`: comprimento do braço (só cantilever; 0 = N/A).
- `corredor_m`: largura do corredor de serviço desta tipologia (0 → 2.5 m).

### Folha «Zonas» → `Zona`
Zonas funcionais (receção, expedição, staging, ...) com dimensão direta
`comp_m × larg_m` — **input puro**, sem SKU/stock associado. Não passam pelo
algoritmo; somam-se à área no fim e posicionam-se manualmente no canvas.

---

## 3. Passo 1 — Boxing / repacking (`boxing.py`)

Para cada SKU decide **uma** unidade de carga e conta quantas são precisas
para o stock máximo.

### 3.1 Encaminhamento para LONGO (cantilever)
Se `geometria ∈ cfg.long_geometrias` (por defeito "Comprida") **e** o
`subtipo_longo` não está excluído (`PAINEL_GRANDE`, `VOLUMOSO_OVERSIZE` não
são material 1D de braços) → `LoadType.LONGO`, com `pecas_por_unidade = 1` e
`num_unidades = stock_max_un`. Não há caixa.

### 3.2 Peças por unidade de carga — `pieces_per_box()`
Modelo de envelope 3D com três cortes:

1. **Orientação da peça** (coluna `Orientação_permitida`): se permite deitar
   (tudo menos «Base fixa»/«Não deitar»), testam-se as **3 escolhas de cota
   vertical** e fica a que mais peças rende — não se força o Z medido como
   altura. «Base fixa»/«Não deitar» mantêm o Z medido de pé. Para cada escolha
   de altura, o **plano horizontal** (as outras 2 cotas vs. interior da caixa)
   usa `floor(dim_caixa / dim_peça)` por eixo, testando as 2 orientações no
   plano; fica a melhor. O resultado é multiplicado por `fill_factor_plano =
   0.7` (folgas de manuseio/encaixe imperfeito — o floor() assume encaixe
   perfeito, que não é realista), arredondado para baixo (mínimo 1). Aplica-se
   a **todas as classes** (RIGIDO e FLEXIVEL/CONTENTOR).
2. **Eixo vertical** — `pieces_in_stack()` conforme a coluna Empilhável:
   - `NAO_EMPILHAVEL` → 1 peça de altura, sempre.
   - `EMPILHAVEL` → `floor(altura_int / altura_peça)` (altura cheia somada).
   - `ANINHAVEL` → a 1ª peça ocupa a altura inteira; cada peça extra
     acrescenta só `fator_empilhamento × altura_peça` (encaixa na anterior),
     com `fator_empilhamento = 0.3`:
     `n = floor((altura_int/altura_peça − 1)/0.3) + 1`.
3. **Peso**: `floor(peso_max_caixa / peso_peça)`. Peso 0/desconhecido não corta.

`peças_por_unidade = min(plano × vertical, corte_por_peso)`.

### 3.3 Escolha da unidade — `choose_load()`
Minimiza o **nº de unidades de carga**: testa todas as candidatas
**S → M → L → MEIA → PALLET** onde caiba ≥ 1 peça e escolhe a que minimiza
`ceil(stock_max_un / peças_por_unidade)`. Em **empate ganha a mais pequena**
(ordem de teste). Se a peça não cabe em nenhuma → PALLET com 1 peça/palete
(fallback para oversize).

> Nota validada com dados reais: minimizar o nº de unidades também minimiza a
> área de chão, porque as unidades grandes preenchem exatamente os slots dos
> racks (o desperdício fica *dentro* da caixa, não no chão). Estratégias de
> "mínimo volume" foram testadas e dão armazéns maiores.

**Output**: `BoxAssignment(sku, load_type, pecas_por_unidade, num_unidades)`.
No fluxo web é também escrito `dados/pecas_tipo_caixa.xlsx` — a folha «Peças»
original + coluna `Tipo_Caixa`.

---

## 4. Passo 2 — Racking / alocação (`racking.py`)

### 4.1 Capacidade de um rack
- **Caixas** — `rack_capacity(rack, box)`: por nível, envelope 2D
  `floor(larg_rack/dim1) × floor(prof_rack/dim2)` na melhor das 2 orientações
  da caixa (dimensões EXT); valida `ext_alt ≤ alt_nivel_m`.
  Total = por_nível × níveis.
- **LONGO** — `rack_capacity_long(rack, comp_artigo)`: cantilever,
  `floor(comp_braço / comp_artigo) × níveis` (metros lineares por braço, não
  footprint).

### 4.2 Estimativa agregada — `racks_needed()`
Resumo rápido (para UI/relatório; **não** é a verdade da alocação): agrupa as
unidades por tipo de carga e cada tipo escolhe o rack compatível com **menor
m² de chão por unidade** (`best_rack_for_load`, considerando módulos
empilháveis se `altura_armazem_m > 0`). Tipos sem rack viável geram aviso e
ficam de fora.

### 4.3 Empilhamento vertical de caixas (stacks)
Antes de colocar, as caixas de cada SKU são agrupadas em **stacks**:
`caixas_por_stack = floor(altura_nível_mínima / ext_alt_caixa)`, onde a altura
mínima considera apenas racks onde a caixa **cabe de facto**
(`aceita` **e** `rack_capacity > 0` — aceitar não chega). Cada stack vira um
item único com altura combinada; ocupa **uma** posição 2D no nível.
`num_boxes_in_stack` regista quantas caixas físicas o stack contém.

### 4.4 Picking (`altura_picking_m`, `pecas_por_carro`)
Com o modo picking ativo (`altura_picking_m > 0`):
- `altura_picking_m` é a altura **máxima de alcance** do operador. Um nível é
  **de picking** se o seu **topo** (base + altura do nível) fica dentro de
  `altura_picking_m` — o nível inteiro tem de estar ao alcance, por isso conta
  o limite superior, não a base (ex.: nível de 1.8 m num porta-paletes com
  picking a 2 m é picking; o 2.º nível, topo a 3.6 m, já não).
- Por SKU, as primeiras `ceil(pecas_por_carro / pecas_por_unidade)` caixas são
  marcadas "precisam de picking" — têm de ficar em níveis de picking, **sem
  fallback** (antes de desistir, o algoritmo esgota todas as posições e abre
  novas). As restantes preferem reserva mas podem usar picking livre.
- Se for estruturalmente impossível, a caixa vai para reserva e o SKU entra
  no aviso "sem zona de picking disponível".
- Com `altura_picking_m = 0` **nenhuma** caixa é marcada (senão todas
  falhariam a colocação estrita e gerariam avisos falsos).

### 4.5 Alocação real — `allocate_to_racks()`
A fonte da verdade. Algoritmo:

1. Cria os itens (stacks) de todos os SKUs e ordena por **área de footprint
   decrescente** (first-fit-decreasing — caixas grandes primeiro aproveitam
   melhor o espaço).
2. Para cada item, tenta por esta ordem:
   1. A posição onde o SKU **já tem presença de picking** (agrupa o SKU).
   2. Todas as posições já abertas, de todas as tipologias compatíveis
      (ordenadas por footprint crescente).
   3. Abrir uma **posição de chão nova**.
   4. Último recurso (só itens de picking): aceitar reserva + aviso.
3. "Caber" num nível é testado com o **rectpack** (packer 2D real): corre-se o
   packer com todas as caixas já confirmadas + a nova, e só se aceita se o
   lote inteiro couber. Tipos de carga diferentes **misturam-se livremente**
   no mesmo nível quando a tipologia os aceita.
4. **Módulos empilhados**: se `altura_armazem_m > 0`, cada posição de chão
   pode empilhar módulos inteiros na vertical até
   `floor(altura_armazem / (alt_nivel × niveis))` — decidido posição a
   posição, não imposto à tipologia.

**Output**: lista de `PosicaoChao` (posição de chão → blocos → níveis →
`CaixaColocada` com coordenadas x/y reais dentro do nível).

---

## 5. Passo 3 — Área (`area.py`)

- `floor_area()`: **footprint de racks** = Σ (footprint unitário × nº de
  posições de chão) por tipologia. Módulos empilhados partilham o footprint —
  só o chão conta.
- `resumo_area_total()`: área bruta =
  `racks + corredores (do layout) + zonas funcionais + margem estrutural`,
  onde a margem é `margem_estrutural_pct × (racks+corredores+zonas)`.

---

## 6. Passo 3b — Layout físico (`layout.py`)

Transforma as posições de chão num **bloco retangular de racks** com
coordenadas reais:

1. **Filas duplas costas-com-costas**: as posições de cada tipologia formam
   pares de filas encostadas; entre pares há um corredor de serviço com a
   largura `corredor_m` da tipologia (0 → 2.5 m). Cada corredor serve duas
   frentes (a fila de trás de um par e a da frente do seguinte) — é a
   configuração de densidade máxima.
2. **Largura-alvo comum** (`_larguras_alinhadas`): em vez de cada tipologia
   otimizar o seu próprio retângulo (que deixava cantos mortos ao lado das
   tipologias pequenas), testa-se como largura-alvo todos os múltiplos
   `k × larg_m` de cada tipologia e escolhe-se a combinação que **minimiza a
   área do retângulo envolvente do bloco inteiro**. Todas as tipologias
   enchem filas até essa largura.
3. **Transição entre tipologias**: o corredor entre o bloco de uma tipologia
   e a seguinte é `max(corredor_A, corredor_B)` — serve os dois lados, manda o
   requisito mais largo.
4. **Zonas** (só no fluxo CLI completo): dispostas por shelf-packing numa
   banda própria antes dos racks, com 3 m de transição; não entram nas
   dimensões do bloco de racks.

**Output**: `WarehouseLayout` — posições com (x, y), dimensões do envolvente
(`largura_total_m × profundidade_total_m`), área total do bloco e área de
corredores (= envolvente − racks).

---

## 7. Fluxo web (`web.py`)

1. **`POST /api/gerar`** — corre os passos 1–3b e devolve:
   - o **bloco de racks** como um retângulo (dimensões do envolvente do
     layout) + as zonas, prontos para o canvas ("Arranjar Layout");
   - totais de unidades por tipo de carga, requisitos por tipologia, avisos;
   - escreve `dados/pecas_tipo_caixa.xlsx` (download em
     `GET /api/download/pecas-caixas`).
2. **Canvas** (frontend): o utilizador arrasta o bloco de racks e as zonas
   (grelha de 0.25 m); os corredores entre blocos são o espaço que ele
   deixar. Zonas rodam em passos de 45°; o bloco de racks em passos de 90°.
3. **`POST /api/render-layout`** — recebe o arranjo manual e devolve o 3D:
   - recalcula o pipeline e **desloca** as posições de rack para onde o bloco
     foi posto no canvas (rotação de 90/180/270° transforma exatamente todas
     as posições e caixas em torno do centro do bloco);
   - zonas com as posições/rotações do canvas;
   - **área total = retângulo envolvente** de todos os blocos colocados.

Endpoints de dados (`/api/articles`, `/api/boxes`, `/api/racks`,
`/api/zones`) são **só leitura** — o master data edita-se diretamente no
Excel.

---

## 8. Parâmetros (`config.py`)

| Parâmetro | Default | Efeito |
|---|---|---|
| `fator_empilhamento` | 0.3 | altura extra por peça ANINHAVEL |
| `fill_factor_plano` | 0.7 | fator de enchimento no plano (todas as classes) |
| `long_geometrias` | ("Comprida",) | encaminhamento para LONGO |
| `long_subtipos_excluidos` | PAINEL_GRANDE, VOLUMOSO_OVERSIZE | não vão a cantilever |
| `altura_armazem_m` | 0 | pé-direito p/ empilhar módulos (0 = desligado) |
| `altura_picking_m` | 0 | teto da zona de picking (0 = desligado) |
| `margem_estrutural_pct` | 0 | paredes/pilares sobre a área útil |

**Onde mexer para mudar resultados:** dimensões de caixas e corredores →
Excel («Caixas», «Racks»); alturas de picking/armazém → UI web ou CLI
(`--picking`, `--altura`); fatores de enchimento/empilhamento → `config.py`.

---

## 9. Decisões fechadas e limitações conhecidas

- A palete e a meia-palete são entradas do catálogo, não casos especiais.
- `alt_nivel_m` é altura por nível; total do prumo = nível × níveis.
- Minimizar o nº de unidades é a regra certa para minimizar chão (validado:
  alternativas de volume mínimo dão blocos maiores). O enum `BoxStrategy` e o
  `min_fill_ratio` foram removidos do `config.py` — as outras estratégias nunca
  passaram de nomes.
- Stacks só juntam caixas **do mesmo SKU**; a altura do stack usa o nível mais
  baixo entre racks onde a caixa cabe (garante que o stack cabe em qualquer
  colocação possível).
- O envolvente do bloco de racks está tipicamente a <1% do mínimo teórico
  para a configuração de corredores dada; as alavancas reais de área são a
  largura dos corredores e o crescimento em altura (níveis/módulos).
- Fora de âmbito (não reintroduzir): slotting, COI/ABC, otimização de picking,
  desenho de níveis de rack.

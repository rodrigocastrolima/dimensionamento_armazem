# Handoff — altura das caixas M/L e inclinação de peças (para testar na LINHA)

Destinatário: agente Claude a trabalhar no `dimensionamento_linha`.
Origem: estudos feitos no `dimensionamento_armazem` (2026-10-07). **Nada disto está
implementado em nenhum dos dois projetos** — foram estudos com monkeypatch em scripts
temporários. O utilizador quer ver o IMPACTO na linha ANTES de qualquer alteração.

## 0. Regras de trabalho com este utilizador
- **Explicar e medir primeiro, implementar só quando ele pedir.** Já disse várias vezes
  «calma» quando se avançou sem pedido. Estudos = scripts temporários (scratchpad), sem
  tocar no código nem no master data.
- Português de Portugal.
- Ficheiros `.py` em CRLF; master data em OneDrive/SharePoint (pode estar aberto no Excel
  Online por outras pessoas → ler de uma cópia temporária; nunca gravar sem backup).

## 1. Contexto e decisão de arquitetura (importante)
- O motor de boxing (`boxing.choose_load`, `pieces_per_box`) é **o mesmo código** nos dois
  projetos (alinhado em 2026-09-30, 445/445 decisões idênticas).
- A folha «Peças» do master data do armazém foi tornada **idêntica à da linha** hoje
  (backup do armazém: `dados/master_data_classificado_v3.xlsx.pre-pecas-linha.bak`).
  A linha é a fonte de verdade das peças.
- **Decisão do utilizador:** a unidade de movimentação é decidida pela LINHA (lógica do
  bordo de linha: Passo 0 para `autonomia_minima_carros_linha` = 1 carro + mínimo kanban
  `kanban_min_caixas_linha` = 2) e vai ser **imposta aos fornecedores**. O armazém vai
  depois **herdar** essa unidade (só calcula quantas unidades para 10 carros). Isto ainda
  NÃO foi feito no armazém — primeiro afina-se a linha.
- Consequência: tamanhos de caixa e inclinação têm de ser avaliados **na linha**
  (bordo de linha + flow racks), não no armazém. Um ganho no armazém pode não existir na
  linha (stock de 1 carro é muito menor; a caixa escolhida é outra).
- Pendente em paralelo (não implementado): regra «caixas primeiro» (MEIA/PALLET só se
  nenhuma caixa XS/S/M/L levar ≥1 peça). Ver secção 6.

## 2. Mudança de tamanho de caixa proposta
Catálogo atual (folha «Caixas», metros, interior / exterior):

| Caixa | int C×L×A | ext A | peso máx |
|---|---|---|---|
| XS | 0.28 × 0.13 × 0.11 | 0.12 | 10 kg |
| S  | 0.40 × 0.30 × 0.24 | 0.25 | 15 kg |
| M  | 0.58 × 0.38 × **0.64** | **0.65** | 30 kg |
| L  | 0.88 × 0.53 × **0.67** | **0.68** | 30 kg |

Proposta do utilizador (as M e L são altas demais para caixas de armazém):
**M → 0.40 m de altura exterior (int 0.39), L → 0.50 m (int 0.49)**. Planta (C×L) e peso
não mudam. Variante também estudada: L → 0.60 m.

Restrição dura do utilizador: **não pode aumentar o nº de paletes** (MEIA/PALLET).

Resultados no ARMAZÉM (autonomia 10 carros, cliente SIA, altura armazém 5 m, picking 0,
master data anterior à sincronização de hoje — servem só de ordem de grandeza):

| Cenário | MEIA | PALLET | M | L | Área racks | Área bruta |
|---|---|---|---|---|---|---|
| base | 144 | 106 | 44 | 51 | 130.9 | 476.1 |
| M40/L50 | **147 (+3)** | 106 | 24 | 68 | 132.0 | 476.1 |
| M40/L60 | 144 | 106 | 24 | 71 | 132.0 | 476.1 |
| inclinação (alturas atuais) | 140 | 106 | 47 | 37 | 126.5 | 474.8 |
| M40/L50 + inclinação | 143 | 106 | 24 | 65 | 127.6 | 474.8 |
| M40/L60 + inclinação | 140 | 106 | 24 | 60 | 127.6 | 474.8 |

Leitura: com M40/L50 sozinho, 3 SKUs deixam de caber na L e sobem para MEIA (viola a
restrição). Com inclinação, M40/L50 fica abaixo da base. Os 3 SKUs que obrigavam a MEIA
eram Dash front panel, Condenser e Column bottom cover (forçá-los a L com
CAIXA_FORCADA foi outra hipótese estudada).

**O que falta: o mesmo estudo na linha** — e a linha tem um efeito que o armazém não tem:
a altura da caixa entra na altura dos níveis dos **flow racks** (`linha.alocar_por_canais`,
`RackType.alturas_niveis_m`). Caixas M/L mais baixas podem permitir mais níveis por
flow rack → menos estantes / menos metros de linha. Medir isso.

## 3. Inclinação de peças (só em CAIXA)
Ideia: tombar a peça num ângulo α dentro da caixa, encostada à parede, em fila.
**Só em caixa (XS/S/M/L), nunca em MEIA/PALLET** — palete não tem paredes onde apoiar
(regra do utilizador).

Fórmula usada no estudo (peça com cotas ordenadas: L = maior das duas no plano de
inclinação, t = espessura, w = cota ao longo da largura da caixa; testam-se as 3 escolhas
de w, as 2 orientações da caixa e α = 5°…85° de 5 em 5):
```
altura ocupada   = L·sinα + t·cosα          ≤ altura interior da caixa
pegada na fila   = L·cosα + t·sinα          ≤ comprimento interior (H)
passo entre peças = t / sinα
peças por fila   = floor((H − pegada) / passo) + 1
filas lado a lado = floor(W / w)
peças/caixa      = floor(peças por fila × filas × fill_factor_plano 0.80), limitado por peso
```
Usa-se o máximo entre esta contagem e a do `pieces_per_box` normal. Implementação de
referência (função `ppu_tilt` e wrapper `com_tilt`):
`C:/Users/Kaizen/AppData/Local/Temp/estudo3.py`.

**Limitação conhecida do estudo:** `ppu_tilt` ignora `ALTURA_OVERRIDE` (testa todas as
orientações). Vários dos SKUs aprovados abaixo têm override — ver decisão em aberto 7.2.
Caso do shock absorber: o «inclinado» era na prática a peça EM PÉ (α≈90°), não uma
inclinação real; dá as mesmas 22 peças que a orientação livre.

### Validação visual do utilizador (artifact com as caixas NOVAS, M40/L50)
**Podem inclinar (5):**

| SKU | Cotas (m) | Override | Empilh. | Atual sem→com incl. | Novo (M40/L50) sem→com incl. |
|---|---|---|---|---|---|
| BEN10.CCC505 INTERMEDIATE SHAFT ASSY | 0.70×0.03×0.05 | — | não | L 13 pç → M 38 pç | L 13 → L 68 pç |
| BEN10.GJA101_C.2 SEAT BACK FOAM & TRIM,RR | 0.153×0.479×0.489 | **x** | não | L×10 (1 pç) → L×3 (4 pç) | L×10 (1) → L×5 (2 pç) |
| BEN10.GMA101 REAR VIEW MIRROR | 0.055×0.113×0.218 | **x** | sim | M 44 pç → S 12 pç | M 28 → S 12 pç |
| SKT10.DDA005 SINGLE MOTOR CN DD DRIVESHAFT ASSY | 0.088×0.088×0.7115 | **y** | não | MEIA×2 (8) → L×1 (24) | MEIA×2 (8) → L×2 (9) |
| SKT10.EDA101 VCU SINGLE MOTOR | 0.154×0.162×0.042 | **z** | sim | M 60 → S 11 | M 36 → S 11 |

**Não podem (6)** — excluir: BEN10.GBC105 INFERIOR SIDE COVER, BEN10.GCA615 HVAC BLOWER,
BEN10.GCB135 AIR DUCT 1, BEN10.GDA101 CONSOLE DASH BOTTON TRIM,
SKT10.CBB601 / CBB602 LOWER CONTROL ARM FR LH/RH.

Não avaliados pelo utilizador (estavam no artifact): FMC103/FMC105 window sweepers (sem
ganho com as caixas novas) e CBE601/CBE602 shock absorbers (não é inclinação, é em pé).
Nota: os sweepers mudaram entretanto para `Peças_por_carro` = 1 (eram 0.1 no armazém).

Os números acima são para 10 carros (armazém). **Na linha (1 carro + kanban 2) a caixa
escolhida pode ser outra e o ganho pode desaparecer** — por isso o utilizador pede o
teste na linha.

## 4. O que se pede ao agente da linha (por esta ordem, sem implementar)
1. **Baseline da linha** com o master data atual: por SKU com estação, tipo de carga e nº
   de unidades do bordo de linha (`bordo_linha.calcular_bordo_linha`), totais por tipo,
   e o resultado dos flow racks / layout da linha (nº de estantes, níveis, metros).
2. **Impacto das alturas M40/L50** (e variante L60) **antes de mudar**: mesmo cálculo com o
   catálogo alterado em memória (`dataclasses.replace` no BoxType M/L: `ext_alt_m`,
   `int_alt_m = ext − 0.01`). Reportar: SKUs que mudam de caixa (de→para, peças/caixa),
   SKUs que passam para MEIA/PALLET (restrição: **zero novas paletes**), efeito nos flow
   racks (níveis por estante, nº de estantes). E o mesmo para o ARMAZÉM herdado se for
   rápido (unidades da linha × 10 carros).
3. **Impacto da inclinação** só nos 5 SKUs aprovados, só em XS/S/M/L, com caixas atuais e
   com caixas novas: caixa e peças/caixa na linha, com e sem inclinação.
4. Apresentar ao utilizador em tabelas curtas e PERGUNTAR antes de alterar código ou
   master data.

## 5. Se/quando for implementado (proposta, não decidido)
- Inclinação como **exceção explícita por SKU** no master data (ex.: coluna
  `INCLINAVEL` = s/vazio), nunca automática para todas as peças; aplicada só a
  `LoadType` em `_CAIXAS`; manter a mesma lógica nos dois projetos (motor partilhado).
- Alturas das caixas = edição da folha «Caixas» (dado, não código), nos DOIS master data.

## 6. Contexto «caixas primeiro» (estudado, não implementado)
Utilizador prefere SEMPRE caixa a palete (meia ou inteira). No armazém (10 carros):
MEIA 144 → 97, caixas 404 → 503, PALLET 106 inalterado, área bruta 476 → 562 m²
(sobretudo forma do bloco/corredores; com M40/L50: MEIA 117, 502 m²). Proposta de
implementação: patamares em `choose_load` — caixas → MEIA (→ PALLET se
`pallet_ultimo_recurso`) → OVERSIZE; script pronto mas não aplicado:
`.../scratchpad/patch_caixas_primeiro.py` (sessão do armazém). Na linha não existe
`pallet_ultimo_recurso` (só no armazém, SIA).

## 7. Decisões em aberto (perguntar ao utilizador)
1. Alturas finais das caixas: M40/L50 ou M40/L60?
2. Inclinação vs `ALTURA_OVERRIDE`: 4 dos 5 SKUs aprovados têm override. A inclinação
   deve poder ignorar o override (o override existe por razão física/qualidade?) ou só
   inclinar respeitando o eixo vertical forçado?
3. A inclinação vale para a linha, para o armazém, ou para ambos (o armazém vai herdar a
   unidade da linha, por isso em princípio decide-se na linha).

## 8. Ficheiros úteis
- Motor: `warehouse/boxing.py` (`choose_load`, `pieces_per_box`, `stock_para_autonomia`),
  `warehouse/bordo_linha.py`, `warehouse/linha.py`, `warehouse/config.py`
  (`autonomia_minima_carros_linha`, `kanban_min_caixas_linha`).
- Estudos (temporários, só leitura): `C:/Users/Kaizen/AppData/Local/Temp/estudo3.py`
  (alturas + inclinação), `estudo4.py` (caixas primeiro), `tilt_data.json` (dados do
  artifact de inclinação), `estudo3.json` / `estudo4.json` (resultados).

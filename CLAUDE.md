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

## Tipos de carga (`LoadType`)
- **S / M / L** — caixas de repacking (catálogo na folha «Caixas»).
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

## Em aberto (ver `config.py`, "A CONFIRMAR")
- Regra de escolha de caixa (`BoxStrategy`): default = minimizar nº de caixas.
- Capacidade do rack: calculada a partir das dimensões (não dada em posições).
- Área: fator de corredor agregado (refinar para filas/corredores reais).
- 16 SKUs sem `Peso_kg` no master data (#N/A) — tratar na leitura.

## Convenções
- Python 3.11+, dataclasses, type hints, deps em `requirements.txt`.
- Domínio em pt-PT. Packing: `py3dbp` (3D) e `rectpack` (2D); `ortools` só para
  refinamento de minimização de racks.
- Pipeline em `main.run()` legível: 1 → 2 → 3. Testes: `python -m pytest`.

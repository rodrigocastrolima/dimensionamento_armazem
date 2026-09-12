# Dimensionamento de Armazém

A partir do **stock máximo** de cada artigo, calcula quanto armazém é preciso:

```
stock máximo  ──(1) repacking──►  S / M / L / PALLET  ou  OVERSIZE
              ──(2) racking ──►   nº de racks por tipologia
              ──(3) área ────►    área de chão (m²)
```

Unidades em metros / m³. Não faz slotting/picking — é dimensionamento de capacidade.

## Estrutura
```
warehouse/
  models.py     dataclasses (INPUT vs OUTPUT); LoadType = S/M/L/PALLET/OVERSIZE
  config.py     parâmetros e regras de negócio
  io_excel.py   ler master data / escrever resultados
  boxing.py     Passo 1 — escolher unidade + contar
  racking.py    Passo 2 — racks necessários (rectpack)
  area.py       Passo 3 — área de chão
  main.py       orquestra o pipeline (CLI)
dados/          master_data.xlsx e resultados.xlsx
tests/          testes mínimos
```

## Correr
```bash
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt
python -m warehouse.main --data dados/master_data.xlsx --out dados/resultados.xlsx
```

## Ordem sugerida de implementação
`io_excel.load_articles` / `load_catalog` / `load_rack_types`
→ `boxing.pieces_per_box` → `boxing.choose_load`
→ `racking.rack_capacity` → `racking.racks_needed`
→ `io_excel.write_results`.

O Passo 3 (área) e a aritmética de contagem já estão feitos.

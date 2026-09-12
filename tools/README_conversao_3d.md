# Conversão do modelo 3D do BEN (FBX → GLB) — ABANDONADO, scripts removidos

Este pipeline tentava converter o CAD real do BEN (FBX pesado, para render
offline, fornecido pelo fabricante) num GLB leve para o viewer web. **Não
produz um modelo utilizável** (ver medições abaixo) e **os scripts
(`fbx_para_glb.py`, `reenquadrar_glb.py`) foram removidos do repositório**
— ficam só no histórico do git (`git log --all -- tools/fbx_para_glb.py`)
para quem precisar de os recuperar como ponto de partida.

**O viewer usa o BEN procedural** (`criarCarroProcedural()` em
`warehouse/static/carro.js`) — um modelo desenhado à mão em Three.js com as
proporções certas (ver `BEN_DIM` no mesmo ficheiro), não uma conversão do
CAD. Não há `ben.glb` nenhum instalado e o `carro.js` já não carrega
nenhum. Este ficheiro fica como registo do que foi tentado e porquê não
funcionou, para ninguém repetir a mesma tentativa sem saber que já foi
medida e descartada.

## Como corria (histórico — os scripts já não existem)

- **Pré-requisitos**: Blender 5.2 LTS; o FBX do fornecedor em `BEN 3D/`
  (gitignored — 1,89 GB, nunca versionado).
- `fbx_para_glb.py <entrada.fbx> <saida.glb> <alvo_triangulos>` (variável
  `MIN_PECA_M` — descarta peças abaixo desta dimensão, em metros) fazia o
  bake principal, ~35 min.
- `reenquadrar_glb.py entrada.glb saida.glb [limiar_m]` cortava a
  geometria-lixo distante, centrava a footprint na origem e assentava a
  base em Z=0 — passo obrigatório depois da conversão, não opcional (ver
  "a bounding box vem envenenada" abaixo).

**Porquê:** o GLB sai em farrapos, e não é afinação — é aritmética.

Medido no bake de 2026-07-28 (300 k triângulos, dimensões e enquadramento
já corretos):

| | arestas de bordo | componentes desconexos |
|---|---|---|
| FBX de origem (peças maiores) | 0,12–0,19 (são) | — |
| GLB produzido | **0,62** | **90 752** (39 992 com 1 só triângulo) |

308 peças com ~103 M triângulos para um orçamento de 300 k dá um **ratio de
decimação de 0,3%**; peças individuais de 3,4 M polígonos ficam com ~10 k.
Colapsar uma malha CAD a 0,3% não a simplifica, desintegra-a. O `MIN_TRIS=150`
por peça não protege contra isto, e subir o orçamento não resolve em escala útil
para um viewer web — faltavam ordens de grandeza, não 2×.

A cena aparecia também quase preta. Não era o material (`baseColor` estava em
0,72/0,75/0,79): triângulos soltos têm normais aleatórias, metade virada para
dentro, e não há superfície para captar luz. O escuro era sintoma, não causa.

**Se o CAD real for algum dia indispensável:** retopologia manual em Blender, ou
pedir ao fornecedor uma versão low-poly de visualização. Não é trabalho de
script.

**O que se aproveitou:** as cotas do CAD real —
**2,51 × 1,72 × 1,56 m** (comp × larg × alt), um microcarro pequeno e alto.
Estão em `BEN_DIM` no `carro.js` e são o alvo das proporções do procedural.

### O bake sozinho NÃO chega: a bounding box vem envenenada

O GLB que sai do `fbx_para_glb.py` mede **34,04 × 1,72 × 34,04 m**. Não é a
escala que está errada — é que **2 polígonos** (4 vértices, de 300 000) ficam a
~24 m do corpo. 99,9% do modelo cabe a 3,2 m do centro; esses dois esticam a
caixa 10×. Como o enquadramento usa a bounding box, o resultado seria um BEN
minúsculo no canto de um vazio de 34 m.

O `remover_outliers()` do conversor **não apanha isto**: filtra por OBJETO
(centro da bounding box de cada malha) e o lixo está dentro da mesma malha que
a carroçaria. O corte por VÉRTICE está no `reenquadrar_glb.py` — daí ser um
passo obrigatório, não um extra.

Por arrumar (não urgente): portar esse corte para o `fbx_para_glb.py`, depois
de `juntar_malhas()`, para o conversor deixar de depender do segundo passo.

**Alternativa em aberto:** ficar pelo carro procedural (`carro.js`,
~3 k triângulos, silhueta correta do BEN). Para um elemento decorativo de zona
pode chegar.

## Notas de implementação (porquê, não o quê)

- **Luzes FBX** — o importador do Blender 5.2 rebenta
  (`'CyclesLightSettings' object has no attribute 'cast_shadow'`). O import é
  tudo-ou-nada, por isso **uma luz** impediria a geometria toda de entrar. Não
  há opção para as ignorar → repõe-se o atributo antes de importar.
- **Decimar peça a peça, antes de juntar** — juntar primeiro dá uma malha de
  ~76 M vértices e o Decimate rebenta (`EXCEPTION_ACCESS_VIOLATION`): precisa
  de várias vezes a malha em RAM e a máquina tem 15 GB.
- **`make_single_user(obdata=True)`** — o CAD reutiliza a mesma malha em várias
  instâncias (simetrias) e o Blender recusa aplicar modificadores a dados
  multi-user.
- **Coordenadas em mm** — CAD vem em milímetros; converte-se para metros.
- **Z-up → Y-up** — o GLB sai Z-up (Blender) e o viewer é Y-up; a rotação de
  -90° em X é aplicada no `carro.js`, ao carregar.

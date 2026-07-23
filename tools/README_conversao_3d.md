# Conversão do modelo 3D do BEN (FBX → GLB)

Converte o CAD do BEN (FBX pesado, para render offline) num GLB leve para o
viewer web. **Não corre sozinho** — é um bake manual, demora ~35 min.

## Pré-requisitos
- **Blender 5.2 LTS** em `C:\Program Files\Blender Foundation\Blender 5.2\`
  (instalado via `winget install BlenderFoundation.Blender`).
- O FBX em `BEN 3D/` (gitignored — 1,89 GB, não versionar).

## Como correr

```bash
MIN_PECA_M=0.12 "/c/Program Files/Blender Foundation/Blender 5.2/blender.exe" \
  --background --factory-startup --python tools/fbx_para_glb.py -- \
  "BEN 3D/2025 - ben exterior for anim (1).fbx" \
  warehouse/static/models/ben.glb \
  300000
```

Argumentos: `<entrada.fbx> <saida.glb> <alvo_triangulos>`
Variável: `MIN_PECA_M` — descarta peças abaixo desta dimensão (m).

Como demora, convém correr em background e ver o log:

```bash
... > /tmp/ben_conv.log 2>&1 &
grep fbx2glb /tmp/ben_conv.log | tail
```

## Se só for preciso reenquadrar (segundos, sem reconverter)

```bash
"/c/Program Files/Blender Foundation/Blender 5.2/blender.exe" \
  --background --factory-startup --python tools/reenquadrar_glb.py -- \
  entrada.glb saida.glb
```

Centra a footprint na origem e assenta a base em Z=0.

## Estado atual — POR VALIDAR

O `warehouse/static/models/ben.glb` em disco é a **v1 (133 k tri)** e está
**fragmentado**: a silhueta lê-se bem, mas as superfícies estão estilhaçadas.

**Causa:** decimação demasiado agressiva por peça. As 110 peças grandes
(>0,5 m) ficaram com **127 triângulos em média** (56 delas com <50). Abaixo de
~150 triângulos uma peça de carroçaria deixa de ser superfície e vira
triângulos soltos.

**Correções já no script, ainda NÃO executadas** (é o bake que falta correr):
1. `remover_pecas_pequenas()` — descarta o que é interno e nunca se vê
   (parafusos, cablagem). Liberta o orçamento para a carroçaria: cada painel
   passa a receber 20-30× mais triângulos pelo mesmo peso final.
2. `MIN_TRIS = 150` (era 12) — piso por peça, para nenhuma se desfazer.
3. Margem de 1,6× **removida** — obrigava a esmagar cada peça muito abaixo do
   necessário; a passagem final só cortava o total, o estrago já estava feito.
4. `material_unico()` — os materiais do CAD vinham quase pretos
   (`baseColor ≈ 0.0004`), o que tornava a cena ilegível.
5. `_dim_reais()` — mede pelos vértices em mundo. O `obj.dimensions` pode estar
   desatualizado após modificadores e deu um falso "34 m" num modelo de 2,5 m.

**Alternativa em aberto:** ficar pelo carro procedural (`carro.js`,
~3 k triângulos, silhueta correta do BEN). Para um elemento decorativo de zona
pode chegar, e não tem nenhum destes problemas.

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

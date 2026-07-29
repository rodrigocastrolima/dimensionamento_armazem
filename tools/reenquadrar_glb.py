"""Centra/assenta um GLB já convertido, descartando geometria-lixo distante.

Corre em segundos, sem repetir os ~30 min da conversão FBX->GLB.

Porque existe o corte de outliers: o CAD do BEN traz um par de triângulos
degenerados muito longe do corpo (no bake de 2026-07-28 eram 4 vértices em 2
polígonos, de 300 000). Não se vêem, mas envenenam a bounding box -- o modelo
media 34 x 1.72 x 34 m quando o BEN real cabe em ~2.5 x 1.6 x 1.4 m. Como o
enquadramento (centrar a footprint, assentar a base) é feito por essa caixa, o
resultado era um BEN minúsculo encostado ao canto de um vazio de 34 m.

O `remover_outliers()` do fbx_para_glb.py não apanha isto: filtra por OBJETO
(centro da bounding box de cada malha) e este lixo está dentro da mesma malha
que a carroçaria. Aqui corta-se por VÉRTICE, que é a granularidade do problema.
"""
import bpy, sys, os, statistics
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
if len(argv) < 2:
    raise SystemExit("uso: ... -- <entrada.glb> <saida.glb> [limiar_m]")
src, out = argv[0], argv[1]
# Meia-largura da caixa aceite em torno da MEDIANA dos vértices. 6 m é folgado
# para um veículo de ~2.5 m e não toca no corpo (p99.9 fica a ~3.2 m do centro).
LIMIAR_M = float(argv[2]) if len(argv) > 2 else 6.0

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
malhas = [o for o in bpy.data.objects if o.type == "MESH"]
print("malhas:", len(malhas))
bpy.ops.object.select_all(action="DESELECT")
for o in malhas:
    o.select_set(True)
bpy.context.view_layer.objects.active = malhas[0]
if len(malhas) > 1:
    bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active


def bounds():
    vs = [obj.matrix_world @ v.co for v in obj.data.vertices]
    mn = Vector((min(v.x for v in vs), min(v.y for v in vs), min(v.z for v in vs)))
    mx = Vector((max(v.x for v in vs), max(v.y for v in vs), max(v.z for v in vs)))
    return mn, mx


mn, mx = bounds()
print("antes:  %.2f x %.2f x %.2f m" % (mx.x - mn.x, mx.y - mn.y, mx.z - mn.z))

# --- corte de outliers por vértice -----------------------------------------
# Apaga-se o POLÍGONO inteiro que tenha algum vértice fora da caixa: deixar o
# polígono com parte dos vértices apagados criaria geometria degenerada.
co = [obj.matrix_world @ v.co for v in obj.data.vertices]
cx = statistics.median(v.x for v in co)
cy = statistics.median(v.y for v in co)
cz = statistics.median(v.z for v in co)


def longe(i):
    v = co[i]
    return (abs(v.x - cx) > LIMIAR_M or abs(v.y - cy) > LIMIAR_M
            or abs(v.z - cz) > LIMIAR_M)


maus = [p.index for p in obj.data.polygons if any(longe(i) for i in p.vertices)]
if maus:
    total = len(obj.data.polygons)
    # segurança: se o "lixo" for uma fatia grande, o limiar está errado para
    # este modelo -- aborta em vez de mutilar a carroçaria.
    if len(maus) > total * 0.02:
        raise SystemExit(
            f"ABORTADO: {len(maus)} de {total} poligonos ficariam fora de "
            f"+-{LIMIAR_M} m da mediana ({100*len(maus)/total:.1f}%). "
            f"O limiar nao serve para este modelo -- confirme as dimensoes.")
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    alvo = [bm.faces[i] for i in maus]
    bmesh.ops.delete(bm, geom=alvo, context="FACES")
    # vértices que ficaram órfãos depois de remover as faces
    soltos = [v for v in bm.verts if not v.link_faces]
    if soltos:
        bmesh.ops.delete(bm, geom=soltos, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    print(f"outliers: {len(maus)} poligono(s) removido(s) "
          f"(fora de +-{LIMIAR_M} m da mediana), {len(soltos)} vertice(s) orfao(s)")
    mn, mx = bounds()
    print("depois do corte: %.2f x %.2f x %.2f m" % (mx.x - mn.x, mx.y - mn.y, mx.z - mn.z))
else:
    print("outliers: nenhum")

# --- enquadramento ----------------------------------------------------------
# centrar footprint na origem e assentar no chao (Z up no Blender)
ctr = (mn + mx) / 2
obj.location.x -= ctr.x
obj.location.y -= ctr.y
obj.location.z -= mn.z
bpy.context.view_layer.update()
bpy.ops.object.transform_apply(location=True)

mn2, mx2 = bounds()
print("final:  min", [round(x, 2) for x in mn2], "max", [round(x, 2) for x in mx2])
print("dimensao final: %.2f x %.2f x %.2f m" % (mx2.x - mn2.x, mx2.y - mn2.y, mx2.z - mn2.z))

tris = sum(len(p.vertices) - 2 for p in obj.data.polygons)
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB",
    export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=6,
    export_apply=True, export_cameras=False, export_lights=False)
print(f"OK: {tris:,} tri | {os.path.getsize(out)/1e6:.2f} MB")

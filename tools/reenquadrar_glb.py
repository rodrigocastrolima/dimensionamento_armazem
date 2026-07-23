import bpy, sys, os
from mathutils import Vector
argv=sys.argv[sys.argv.index("--")+1:]
src,out=argv[0],argv[1]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
malhas=[o for o in bpy.data.objects if o.type=="MESH"]
print("malhas:",len(malhas))
bpy.ops.object.select_all(action="DESELECT")
for o in malhas: o.select_set(True)
bpy.context.view_layer.objects.active=malhas[0]
if len(malhas)>1: bpy.ops.object.join()
obj=bpy.context.view_layer.objects.active
# bounds reais dos vertices em mundo
vs=[obj.matrix_world @ v.co for v in obj.data.vertices]
mn=Vector((min(v.x for v in vs),min(v.y for v in vs),min(v.z for v in vs)))
mx=Vector((max(v.x for v in vs),max(v.y for v in vs),max(v.z for v in vs)))
print("bounds reais:", [round(x,2) for x in mn], [round(x,2) for x in mx])
print("dimensao real: %.2f x %.2f x %.2f m" % (mx.x-mn.x, mx.y-mn.y, mx.z-mn.z))
# centrar footprint na origem e assentar no chao (Z up no Blender)
ctr=(mn+mx)/2
obj.location.x -= ctr.x; obj.location.y -= ctr.y; obj.location.z -= mn.z
bpy.context.view_layer.update()
bpy.ops.object.transform_apply(location=True)
vs=[obj.matrix_world @ v.co for v in obj.data.vertices]
mn2=Vector((min(v.x for v in vs),min(v.y for v in vs),min(v.z for v in vs)))
mx2=Vector((max(v.x for v in vs),max(v.y for v in vs),max(v.z for v in vs)))
print("depois: min",[round(x,2) for x in mn2],"max",[round(x,2) for x in mx2])
tris=sum(len(p.vertices)-2 for p in obj.data.polygons)
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB",
    export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=6,
    export_apply=True, export_cameras=False, export_lights=False)
print(f"OK: {tris:,} tri | {os.path.getsize(out)/1e6:.2f} MB")

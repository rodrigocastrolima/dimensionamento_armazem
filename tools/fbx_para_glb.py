"""Converte o FBX pesado do BEN (CAD, ~105 M triangulos) num GLB leve para web.

Corre DENTRO do Blender, nao no Python do sistema:

    blender --background --factory-startup --python tools/fbx_para_glb.py -- \
        "BEN 3D/2025 - ben exterior for anim (1).fbx" \
        warehouse/static/models/ben.glb 120000

Passos: importa o FBX -> junta tudo -> Decimate ate ao orcamento de triangulos
-> limpa camaras/luzes -> assenta no chao e centra -> exporta GLB (Draco).

Nota: o import de 1.89 GB e a parte lenta e pesada em RAM. O Decimate corre
depois, sobre a malha ja unida, que e a forma que da reducao mais estavel.
"""
import bpy
from mathutils import Vector
import sys
import os
import time


def log(msg):
    print(f"[fbx2glb] {msg}", flush=True)


def limpar_cena():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _corrigir_bug_luzes_fbx():
    """O importador FBX do Blender 5.2 rebenta ao criar luzes:
    'CyclesLightSettings' object has no attribute 'cast_shadow'
    (a propriedade foi removida do Cycles, mas o addon ainda lhe escreve).

    Como o import e all-or-nothing, uma unica luz no ficheiro impede a
    importacao da geometria toda. Nao ha opcao para ignorar luzes, por isso
    devolve-se o atributo em falta -- so precisa de aceitar a escrita, o valor
    e irrelevante: as luzes sao descartadas logo a seguir (remover_nao_malhas).
    """
    try:
        import cycles.properties as cp
    except Exception:
        return
    cls = getattr(cp, "CyclesLightSettings", None)
    if cls is None or hasattr(cls, "cast_shadow"):
        return
    try:
        import bpy.props as bprops
        cls.cast_shadow = bprops.BoolProperty(name="cast_shadow", default=True)
        log("workaround aplicado: CyclesLightSettings.cast_shadow")
    except Exception as e:
        log(f"aviso: nao foi possivel aplicar o workaround das luzes ({e})")


def remover_nao_malhas():
    """Camaras, luzes e empties da cena de render nao interessam para a web."""
    for obj in list(bpy.data.objects):
        if obj.type != "MESH":
            bpy.data.objects.remove(obj, do_unlink=True)


def juntar_malhas():
    malhas = [o for o in bpy.data.objects if o.type == "MESH"]
    if not malhas:
        raise SystemExit("[fbx2glb] ERRO: nenhuma malha importada")
    bpy.ops.object.select_all(action="DESELECT")
    for o in malhas:
        o.select_set(True)
    bpy.context.view_layer.objects.active = malhas[0]
    if len(malhas) > 1:
        bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def contar_tris(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def decimar(obj, alvo_tris):
    atual = contar_tris(obj)
    log(f"triangulos antes do decimate: {atual:,}")
    if atual <= alvo_tris:
        log("ja abaixo do orcamento; sem decimate")
        return atual
    ratio = max(min(alvo_tris / atual, 1.0), 0.00001)
    log(f"decimate ratio = {ratio:.6f}  (alvo {alvo_tris:,})")
    mod = obj.modifiers.new(name="dec", type="DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    bpy.ops.object.modifier_apply(modifier=mod.name)
    depois = contar_tris(obj)
    log(f"triangulos depois: {depois:,}")
    return depois


def remover_pecas_pequenas(min_dim_m=0.12):
    """Descarta componentes pequenos (parafusos, clips, eletronica interna).

    Num modelo CAD de veiculo a esmagadora maioria das pecas e interna e NUNCA
    se ve de fora, mas consome o orcamento de triangulos -- obrigando a esmagar
    a carroçaria ate esta deixar de ser uma superficie (foi o que aconteceu:
    110 pecas grandes ficaram com 127 triangulos em media, em farrapos).
    Ficando so com o que se ve, cada painel recebe 20-30x mais triangulos pelo
    mesmo peso final.
    """
    malhas = [o for o in bpy.data.objects if o.type == "MESH"]
    fora = []
    for o in malhas:
        d = o.dimensions
        if max(d.x, d.y, d.z) < min_dim_m:
            fora.append(o)
    for o in fora:
        bpy.data.objects.remove(o, do_unlink=True)
    log(f"pecas pequenas removidas (<{min_dim_m} m): {len(fora)} "
        f"-> restam {len(malhas) - len(fora)}")


def material_unico(obj, cor=(0.72, 0.75, 0.79, 1.0)):
    """Um so material claro. Os materiais do CAD vinham quase pretos
    (baseColor ~0.0004), o que tornava a cena ilegivel."""
    obj.data.materials.clear()
    mat = bpy.data.materials.new(name="BEN_body")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = cor
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.42
        if "Metallic" in bsdf.inputs:
            bsdf.inputs["Metallic"].default_value = 0.25
    obj.data.materials.append(mat)


def decimar_por_peca(alvo_tris):
    """Decima cada malha para a sua quota do orcamento, proporcional ao tamanho.

    Peças minusculas (parafusos, clips) recebem um minimo para nao desaparecerem
    por completo -- num modelo de carro sao irrelevantes vistas de fora, mas
    apagá-las abriria buracos na silhueta.
    """
    malhas = [o for o in bpy.data.objects if o.type == "MESH"]
    total = 0
    for o in malhas:
        total += sum(len(p.vertices) - 2 for p in o.data.polygons)
    log(f"decimate por peca: {len(malhas)} malhas, {total:,} triangulos")
    if total <= alvo_tris:
        return

    # Modelos CAD reutilizam a mesma malha em varias instancias (simetrias,
    # parafusos repetidos). O Blender recusa aplicar modificadores a dados
    # multi-user, por isso da-se a cada objeto uma copia propria da sua malha.
    partilhadas = sum(1 for o in malhas if o.data.users > 1)
    if partilhadas:
        log(f"  {partilhadas} objeto(s) com malha partilhada -> a tornar unicos")
        bpy.ops.object.select_all(action="DESELECT")
        for o in malhas:
            o.select_set(True)
        bpy.context.view_layer.objects.active = malhas[0]
        # single_user com obdata=True copia as malhas partilhadas
        bpy.ops.object.make_single_user(object=True, obdata=True)

    # Sem margem: a margem de 1.6x que aqui existia obrigava a esmagar cada
    # peca muito abaixo do necessario, e a passagem final so cortava o total --
    # o estrago ja estava feito.
    ratio_global = alvo_tris / total
    # Piso ALTO por peca: abaixo de ~150 triangulos uma peca de carroçaria
    # deixa de ser uma superficie e vira triangulos soltos (o efeito
    # "fragmentos" visto no viewer). Peças pequenas ficam intactas.
    MIN_TRIS = 150
    feitas = 0
    falhas = []
    for i, o in enumerate(malhas):
        tris = sum(len(p.vertices) - 2 for p in o.data.polygons)
        if tris <= MIN_TRIS:
            continue
        alvo_peca = max(int(tris * ratio_global), MIN_TRIS)
        if alvo_peca >= tris:
            continue
        mod = o.modifiers.new(name="dec", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = alvo_peca / tris
        bpy.ops.object.select_all(action="DESELECT")
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
            feitas += 1
        except Exception as e:
            # nao deixar uma peca problematica parar a conversao inteira
            falhas.append(o.name[:40])
            try:
                o.modifiers.remove(mod)
            except Exception:
                pass
        if (i + 1) % 200 == 0:
            log(f"  {i+1}/{len(malhas)} malhas processadas")
    novo = sum(sum(len(p.vertices) - 2 for p in o.data.polygons)
               for o in bpy.data.objects if o.type == "MESH")
    log(f"decimate por peca concluido ({feitas} malhas): {total:,} -> {novo:,} tri")
    if falhas:
        log(f"  {len(falhas)} peca(s) nao decimadas (ficam em detalhe cheio): "
            f"{', '.join(falhas[:5])}{' ...' if len(falhas) > 5 else ''}")


def remover_outliers(limiar_mult=3.0):
    """Descarta malhas muito afastadas do grosso do modelo.

    Cenas de animação trazem lixo (helpers, geometria de fundo, objetos
    esquecidos longe da origem) que estica a bounding box -- no BEN dava
    34 km de comprimento. Usa-se a MEDIANA das distancias a origem como
    referencia robusta e corta-se o que estiver muito para la disso.
    """
    import statistics
    malhas = [o for o in bpy.data.objects if o.type == "MESH"]
    if len(malhas) < 5:
        return
    dists = []
    for o in malhas:
        # CENTRO DA GEOMETRIA em mundo, não a translação do objeto: em CAD as
        # peças costumam ter translação (0,0,0) e as coordenadas embutidas nos
        # vértices, por isso usar matrix_world.translation não detetava nada.
        bb = [o.matrix_world @ Vector(c) for c in o.bound_box]
        cx = sum(v.x for v in bb) / 8.0
        cy = sum(v.y for v in bb) / 8.0
        cz = sum(v.z for v in bb) / 8.0
        dists.append((cx ** 2 + cy ** 2 + cz ** 2) ** 0.5)
    med = statistics.median(dists)
    if med <= 0:
        return
    limite = max(med * limiar_mult, 1e-6)
    fora = [o for o, d in zip(malhas, dists) if d > limite]
    for o in fora:
        bpy.data.objects.remove(o, do_unlink=True)
    if fora:
        log(f"outliers removidos: {len(fora)} malha(s) a mais de {limite:.1f} da origem")


def _dim_reais(obj):
    """Dimensoes a partir dos VERTICES em mundo. obj.dimensions pode estar
    desatualizado apos modificadores/transformacoes -- foi o que deu um falso
    '34 m' quando o modelo media 2.5 m."""
    vs = [obj.matrix_world @ v.co for v in obj.data.vertices]
    if not vs:
        return (0.0, 0.0, 0.0)
    xs = [v.x for v in vs]; ys = [v.y for v in vs]; zs = [v.z for v in vs]
    return (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def normalizar_escala(obj):
    """CAD costuma vir em MILIMETROS. Se o modelo for absurdamente grande para
    um veiculo, converte para metros (o viewer trabalha em metros)."""
    bpy.context.view_layer.update()
    d = max(_dim_reais(obj))
    if d > 100:            # nenhum veiculo tem >100 m
        fator = 0.001
        obj.scale = (fator, fator, fator)
        bpy.ops.object.transform_apply(scale=True)
        log(f"escala convertida mm -> m (dimensao maxima era {d:.0f})")


def assentar_e_centrar(obj):
    """Origem no centro da footprint, base a y=0 (convencao do viewer)."""
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
    obj.location = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    zs = [(obj.matrix_world @ v.co).z for v in obj.data.vertices]
    obj.location.z -= min(zs)
    bpy.context.view_layer.update()


def dimensoes(obj):
    bpy.context.view_layer.update()
    return _dim_reais(obj)


def main():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(argv) < 2:
        raise SystemExit("uso: ... -- <entrada.fbx> <saida.glb> [alvo_tris]")
    fbx, out = argv[0], argv[1]
    alvo = int(argv[2]) if len(argv) > 2 else 120000

    t0 = time.time()
    limpar_cena()

    log(f"a importar {fbx} ({os.path.getsize(fbx)/1e9:.2f} GB) -- demora")
    _corrigir_bug_luzes_fbx()
    # use_image_search=False: evita varrer o disco atras de texturas (lento e
    # inutil aqui, o GLB leva material simples).
    bpy.ops.import_scene.fbx(
        filepath=fbx,
        use_image_search=False,
        use_custom_props=False,
        ignore_leaf_bones=True,
    )
    log(f"import concluido em {time.time()-t0:.0f}s; objetos: {len(bpy.data.objects)}")

    remover_nao_malhas()
    remover_outliers()
    remover_pecas_pequenas(min_dim_m=float(os.environ.get("MIN_PECA_M", "0.12")))

    # Decimar PEÇA A PEÇA antes de juntar. Juntar primeiro cria uma malha de
    # ~76 M vertices e o Decimate rebenta (EXCEPTION_ACCESS_VIOLATION): as
    # estruturas de edge-collapse precisam de varias vezes a malha em RAM.
    # Peca a peca, cada decimate e pequeno e a memoria mantem-se controlada.
    decimar_por_peca(alvo)

    obj = juntar_malhas()
    log(f"malhas unidas -> 1 objeto ({len(obj.data.vertices):,} vertices)")

    # passagem final: corrige o excesso acumulado do arredondamento por peca
    decimar(obj, alvo)
    material_unico(obj)
    normalizar_escala(obj)
    assentar_e_centrar(obj)
    x, y, z = dimensoes(obj)
    log(f"dimensoes finais: {x:.2f} x {y:.2f} x {z:.2f} m")

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=out,
        export_format="GLB",
        export_draco_mesh_compression_enable=True,
        export_draco_mesh_compression_level=6,
        export_apply=True,
        export_cameras=False,
        export_lights=False,
    )
    log(f"GLB escrito: {out} ({os.path.getsize(out)/1e6:.2f} MB)")
    log(f"TOTAL {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

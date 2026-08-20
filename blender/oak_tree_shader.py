"""
tree_shade.py -- Procedural Oak Bark/Foliage Shading + Baking (Blender 4.5)
Script 2 of 2 in the Oak LOD pipeline. Run this AFTER tree_gen.py, in the same
.blend (it expects Oak_LOD0/1/2 objects and Bark/Foliage/Billboard placeholder
materials to already exist -- material names match tree_gen.py exactly: "Bark",
"Foliage", "Billboard").

What this builds:
  - "Bark" material: Ridged Multifractal furrows + Voronoi plate-cracking, a
    noise-jittered base->canopy height gradient, sparse large irregular BURLS,
    dense small KNOTS (both presence-gated so density and shape size are
    independent controls), amber sap rings around knots, fake cavity/AO
    darkening, and a 3-way seasonal color blend (spring/summer/fall). Uses
    Object-space texture coordinates (not Generated) so noise detail isn't
    compressed across the whole joined branch+foliage mesh's bounding box.
  - "Foliage" material: a procedural leaf-CLUSTER cutout (noise-distorted
    Voronoi cells punched into alpha, not a flat colored square), per-leaf
    tonal variation, per-instance tint variance, translucency, seasonal blend.
  - Bakes Albedo/Normal/Roughness for both materials in Cycles (1024px,
    32 samples), saves PNGs next to the .blend, and rewires the baked images
    into the Principled BSDF so the viewport shows the final baked result.

Because branch UVs are an analytic cylindrical projection (same formula on
every LOD) and foliage clumps deliberately share overlapping 0-1 UV space,
ONE bake of each material -- done here on Oak_LOD0 -- is reused automatically
by Oak_LOD1/Oak_LOD2 too, since they share the same material datablocks.
LOD3 is skipped entirely (disabled in tree_gen.py, to be rebuilt later).

Run inside Blender's Scripting tab, or headless:
    blender --background --python tree_shade.py

NOTE ON THE FROZEN-BLENDER FEELING: baking runs synchronously and blocks the
UI by design -- Blender WILL look unresponsive for the duration of each bake.
Watch the console: you should see a "Baking ... pass" message before each
bake starts and a "... pass complete" message after it finishes. If those
messages are appearing (even minutes apart) and your CPU usage is high,
it's working, not frozen.
"""

import bpy
import os
from mathutils import Vector

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

SEASON = 1.0          # 0.0 = spring, 1.0 = summer, 2.0 = fall -- set this BEFORE
                       # running the script. Changing it after baking has no
                       # effect: baked images are static, and the shader gets
                       # rewired to read them instead of the live procedural
                       # graph at the end of main(). To get a different
                       # season's textures, change SEASON and re-run the
                       # whole script -- it'll bake a new, separately-named
                       # set rather than overwriting the previous one.
BARK_BAKE_SIZE = 1024
FOLIAGE_BAKE_SIZE = 1024
BAKE_SAMPLES = 32
BAKE_MARGIN_PX = 8

BAKE_TARGET_LOD = 0   # which LOD object to bake from (highest UV density)

BILLBOARD_CAPTURE_SIZE = 1024
BILLBOARD_AO_DISTANCE = 0.4
BILLBOARD_AO_SAMPLES = 8
BILLBOARD_MARGIN = 1.08   # extra headroom so the silhouette isn't clipped at frame edges

SEASON_LABELS = {0: "Spring", 1: "Summer", 2: "Fall"}


def season_label(season_value):
    return SEASON_LABELS[max(0, min(2, round(season_value)))]

# ---------------------------------------------------------------------------
# NODE HELPERS
# ---------------------------------------------------------------------------

def new_node(nt, type_, location=(0, 0), **kwargs):
    n = nt.nodes.new(type_)
    n.location = location
    for k, v in kwargs.items():
        setattr(n, k, v)
    return n


def link(nt, out_socket, in_socket):
    nt.links.new(out_socket, in_socket)


def add_season_driver(scene, value_node):
    """Drive a material's Season Value node from one shared scene property,
    so both materials stay in sync when you tweak it. Always overwrites
    scene['oak_season'] with the current SEASON constant -- a previous
    version only set it if the property didn't already exist yet, which
    meant every run after the first silently ignored SEASON entirely and
    kept reusing whatever value got saved into the .blend the first time."""
    scene["oak_season"] = SEASON
    fcurve = value_node.outputs[0].driver_add("default_value")
    drv = fcurve.driver
    drv.type = 'AVERAGE'
    var = drv.variables.new()
    var.name = "season"
    var.type = 'SINGLE_PROP'
    var.targets[0].id_type = 'SCENE'
    var.targets[0].id = scene
    var.targets[0].data_path = '["oak_season"]'


def seasonal_mix(nt, fac_socket, season_value_node, spring, summer, fall, base_x, base_y):
    """Builds 3 ColorRamps (one per season) all reading fac_socket, then
    blends between them based on season_value_node (0=spring,1=summer,2=fall).
    spring/summer/fall are each a list of (position, color) tuples -- use 2
    stops for a simple gradient or more for a richer multi-hue ramp (e.g.
    red -> orange -> purple for fall instead of one flat 2-color blend).
    Returns the final Color output socket."""
    ramps = []
    for i, stops in enumerate((spring, summer, fall)):
        r = new_node(nt, 'ShaderNodeValToRGB', (base_x, base_y - i * 180))
        elements = r.color_ramp.elements
        while len(elements) < len(stops):
            elements.new(0.5)
        while len(elements) > len(stops):
            elements.remove(elements[-1])
        for el, (pos, col) in zip(elements, stops):
            el.position = pos
            el.color = (*col, 1.0)
        link(nt, fac_socket, r.inputs['Fac'])
        ramps.append(r)

    mix_sp_su = new_node(nt, 'ShaderNodeMixRGB', (base_x + 220, base_y - 90))
    mix_sp_su.use_clamp = True
    clamp1 = new_node(nt, 'ShaderNodeClamp', (base_x + 40, base_y - 420))
    clamp1.inputs['Min'].default_value = 0.0
    clamp1.inputs['Max'].default_value = 1.0
    link(nt, season_value_node.outputs[0], clamp1.inputs['Value'])
    link(nt, clamp1.outputs['Result'], mix_sp_su.inputs['Fac'])
    link(nt, ramps[0].outputs['Color'], mix_sp_su.inputs['Color1'])
    link(nt, ramps[1].outputs['Color'], mix_sp_su.inputs['Color2'])

    mix_final = new_node(nt, 'ShaderNodeMixRGB', (base_x + 440, base_y - 180))
    mix_final.use_clamp = True
    subtract = new_node(nt, 'ShaderNodeMath', (base_x + 40, base_y - 480))
    subtract.operation = 'SUBTRACT'
    subtract.inputs[1].default_value = 1.0
    link(nt, season_value_node.outputs[0], subtract.inputs[0])
    clamp2 = new_node(nt, 'ShaderNodeClamp', (base_x + 220, base_y - 480))
    clamp2.inputs['Min'].default_value = 0.0
    clamp2.inputs['Max'].default_value = 1.0
    link(nt, subtract.outputs['Value'], clamp2.inputs['Value'])
    link(nt, clamp2.outputs['Result'], mix_final.inputs['Fac'])
    link(nt, mix_sp_su.outputs['Color'], mix_final.inputs['Color1'])
    link(nt, ramps[2].outputs['Color'], mix_final.inputs['Color2'])

    return mix_final.outputs['Color']


# ---------------------------------------------------------------------------
# BARK MATERIAL
# ---------------------------------------------------------------------------

def build_bark_material(scene):
    print("Building Bark material node graph...", flush=True)
    mat = bpy.data.materials["Bark"]
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = new_node(nt, 'ShaderNodeOutputMaterial', (1600, 0))
    bsdf = new_node(nt, 'ShaderNodeBsdfPrincipled', (1300, 0))
    link(nt, bsdf.outputs['BSDF'], out.inputs['Surface'])

    # Object-space coordinates, NOT Generated: Generated normalizes 0-1 across
    # the WHOLE joined mesh's bounding box (trunk + wide canopy spread), which
    # compresses all noise/knot detail into a sliver of that range and reads
    # as flat/under-detailed. Object space uses real local meters, so every
    # Scale value below maps to actual bark-frequency detail regardless of
    # how wide the canopy makes the object's overall bounding box.
    tex_coord = new_node(nt, 'ShaderNodeTexCoord', (-1600, 0))
    mapping = new_node(nt, 'ShaderNodeMapping', (-1400, 0))
    mapping.inputs['Scale'].default_value = (3.0, 3.0, 3.0)
    link(nt, tex_coord.outputs['Object'], mapping.inputs['Vector'])

    season_val = new_node(nt, 'ShaderNodeValue', (-1600, 900))
    season_val.outputs[0].default_value = SEASON
    add_season_driver(scene, season_val)

    sep_xyz = new_node(nt, 'ShaderNodeSeparateXYZ', (-1400, -350))
    link(nt, tex_coord.outputs['Object'], sep_xyz.inputs['Vector'])

    # ragged, noise-perturbed base -> canopy transition instead of a clean ramp
    edge_noise = new_node(nt, 'ShaderNodeTexNoise', (-1400, -550))
    edge_noise.inputs['Scale'].default_value = 8.0
    link(nt, mapping.outputs['Vector'], edge_noise.inputs['Vector'])
    edge_jitter = new_node(nt, 'ShaderNodeMath', (-1200, -550), operation='SUBTRACT')
    edge_jitter.inputs[1].default_value = 0.5
    link(nt, edge_noise.outputs['Fac'], edge_jitter.inputs[0])
    edge_jitter_scaled = new_node(nt, 'ShaderNodeMath', (-1050, -550), operation='MULTIPLY')
    edge_jitter_scaled.inputs[1].default_value = 0.4
    link(nt, edge_jitter.outputs[0], edge_jitter_scaled.inputs[0])
    height_jittered = new_node(nt, 'ShaderNodeMath', (-900, -450), operation='ADD')
    link(nt, sep_xyz.outputs['Z'], height_jittered.inputs[0])
    link(nt, edge_jitter_scaled.outputs[0], height_jittered.inputs[1])

    height_ramp = new_node(nt, 'ShaderNodeValToRGB', (-700, -350))
    height_ramp.color_ramp.elements[0].position = 0.0
    height_ramp.color_ramp.elements[0].color = (0.09, 0.10, 0.06, 1.0)   # mossy dark base
    height_ramp.color_ramp.elements[1].position = 1.0
    height_ramp.color_ramp.elements[1].color = (0.42, 0.30, 0.18, 1.0)   # lighter upper bark
    link(nt, height_jittered.outputs[0], height_ramp.inputs['Fac'])

    # --- macro furrows: Ridged Multifractal noise reads as deep bark grooves ---
    noise_macro = new_node(nt, 'ShaderNodeTexNoise', (-1200, 250))
    try:
        noise_macro.noise_type = 'RIDGED_MULTIFRACTAL'
    except (AttributeError, TypeError):
        pass  # falls back to default fBM on older API -- still functions fine
    noise_macro.inputs['Scale'].default_value = 10.0
    noise_macro.inputs['Detail'].default_value = 6.0
    noise_macro.inputs['Roughness'].default_value = 0.65
    link(nt, mapping.outputs['Vector'], noise_macro.inputs['Vector'])
    macro_as_color = new_node(nt, 'ShaderNodeValToRGB', (-1000, 250))
    macro_as_color.color_ramp.elements[0].color = (0, 0, 0, 1)
    macro_as_color.color_ramp.elements[1].color = (1, 1, 1, 1)
    link(nt, noise_macro.outputs['Fac'], macro_as_color.inputs['Fac'])

    # --- plate cracking ---
    plate_voronoi = new_node(nt, 'ShaderNodeTexVoronoi', (-1200, 30), voronoi_dimensions='3D')
    plate_voronoi.inputs['Scale'].default_value = 9.0
    link(nt, mapping.outputs['Vector'], plate_voronoi.inputs['Vector'])
    plate_ramp = new_node(nt, 'ShaderNodeValToRGB', (-1000, 30))
    plate_ramp.color_ramp.elements[0].position = 0.0
    plate_ramp.color_ramp.elements[0].color = (0.5, 0.5, 0.5, 1.0)
    plate_ramp.color_ramp.elements[1].position = 0.18
    plate_ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
    link(nt, plate_voronoi.outputs['Distance'], plate_ramp.inputs['Fac'])

    grain_combine = new_node(nt, 'ShaderNodeMixRGB', (-800, 130), blend_type='MULTIPLY')
    grain_combine.inputs['Fac'].default_value = 0.5
    link(nt, macro_as_color.outputs['Color'], grain_combine.inputs['Color1'])
    link(nt, plate_ramp.outputs['Color'], grain_combine.inputs['Color2'])

    base_mix = new_node(nt, 'ShaderNodeMixRGB', (-600, -100), blend_type='MULTIPLY')
    base_mix.inputs['Fac'].default_value = 0.55
    link(nt, height_ramp.outputs['Color'], base_mix.inputs['Color1'])
    link(nt, grain_combine.outputs['Color'], base_mix.inputs['Color2'])

    # --- burls: sparse, large, irregular. Gated by a per-cell "presence" test
    # (Voronoi Color -> Red channel -> threshold) so only a handful of cells
    # actually become a burl, rather than every cell getting a soft one. ---
    burl_warp_noise = new_node(nt, 'ShaderNodeTexNoise', (-1200, 550))
    burl_warp_noise.inputs['Scale'].default_value = 3.0
    link(nt, mapping.outputs['Vector'], burl_warp_noise.inputs['Vector'])
    burl_warp_scale = new_node(nt, 'ShaderNodeVectorMath', (-1050, 550), operation='SCALE')
    burl_warp_scale.inputs['Scale'].default_value = 0.6
    link(nt, burl_warp_noise.outputs['Color'], burl_warp_scale.inputs[0])
    burl_warp_add = new_node(nt, 'ShaderNodeVectorMath', (-900, 550), operation='ADD')
    link(nt, mapping.outputs['Vector'], burl_warp_add.inputs[0])
    link(nt, burl_warp_scale.outputs['Vector'], burl_warp_add.inputs[1])

    burl_voronoi = new_node(nt, 'ShaderNodeTexVoronoi', (-700, 550), voronoi_dimensions='3D')
    burl_voronoi.inputs['Scale'].default_value = 1.6
    burl_voronoi.inputs['Randomness'].default_value = 1.0
    link(nt, burl_warp_add.outputs['Vector'], burl_voronoi.inputs['Vector'])

    burl_presence_split = new_node(nt, 'ShaderNodeSeparateColor', (-500, 650))
    link(nt, burl_voronoi.outputs['Color'], burl_presence_split.inputs['Color'])
    burl_presence = new_node(nt, 'ShaderNodeMath', (-350, 650), operation='GREATER_THAN')
    burl_presence.inputs[1].default_value = 0.90   # ~10% of cells become burls -- sparse
    link(nt, burl_presence_split.outputs['Red'], burl_presence.inputs[0])

    burl_shape = new_node(nt, 'ShaderNodeValToRGB', (-500, 450))
    burl_shape.color_ramp.elements[0].position = 0.0
    burl_shape.color_ramp.elements[0].color = (1, 1, 1, 1)
    burl_shape.color_ramp.elements[1].position = 0.11
    burl_shape.color_ramp.elements[1].color = (0, 0, 0, 1)
    link(nt, burl_voronoi.outputs['Distance'], burl_shape.inputs['Fac'])

    burl_mask = new_node(nt, 'ShaderNodeMixRGB', (-200, 550), blend_type='MULTIPLY')
    burl_mask.inputs['Fac'].default_value = 1.0
    link(nt, burl_shape.outputs['Color'], burl_mask.inputs['Color1'])
    link(nt, burl_presence.outputs['Value'], burl_mask.inputs['Color2'])

    # --- small frequent knots: same technique, denser + gentler threshold ---
    knot_voronoi = new_node(nt, 'ShaderNodeTexVoronoi', (-700, 750), voronoi_dimensions='3D')
    knot_voronoi.inputs['Scale'].default_value = 15.0
    knot_voronoi.inputs['Randomness'].default_value = 1.0
    link(nt, mapping.outputs['Vector'], knot_voronoi.inputs['Vector'])

    knot_presence_split = new_node(nt, 'ShaderNodeSeparateColor', (-500, 850))
    link(nt, knot_voronoi.outputs['Color'], knot_presence_split.inputs['Color'])
    knot_presence = new_node(nt, 'ShaderNodeMath', (-350, 850), operation='GREATER_THAN')
    knot_presence.inputs[1].default_value = 0.55   # ~45% of cells -- frequent
    link(nt, knot_presence_split.outputs['Red'], knot_presence.inputs[0])

    knot_shape = new_node(nt, 'ShaderNodeValToRGB', (-500, 750))
    knot_shape.color_ramp.elements[0].position = 0.0
    knot_shape.color_ramp.elements[0].color = (1, 1, 1, 1)
    knot_shape.color_ramp.elements[1].position = 0.05
    knot_shape.color_ramp.elements[1].color = (0, 0, 0, 1)
    link(nt, knot_voronoi.outputs['Distance'], knot_shape.inputs['Fac'])

    knot_mask = new_node(nt, 'ShaderNodeMixRGB', (-200, 750), blend_type='MULTIPLY')
    knot_mask.inputs['Fac'].default_value = 1.0
    link(nt, knot_shape.outputs['Color'], knot_mask.inputs['Color1'])
    link(nt, knot_presence.outputs['Value'], knot_mask.inputs['Color2'])

    combined_knot_mask = new_node(nt, 'ShaderNodeMixRGB', (0, 650), blend_type='LIGHTEN')
    combined_knot_mask.inputs['Fac'].default_value = 1.0
    link(nt, burl_mask.outputs['Color'], combined_knot_mask.inputs['Color1'])
    link(nt, knot_mask.outputs['Color'], combined_knot_mask.inputs['Color2'])

    knot_color = new_node(nt, 'ShaderNodeRGB', (0, 400))
    knot_color.outputs[0].default_value = (0.05, 0.03, 0.02, 1.0)
    knot_darken = new_node(nt, 'ShaderNodeMixRGB', (250, 200), blend_type='MULTIPLY')
    link(nt, base_mix.outputs['Color'], knot_darken.inputs['Color1'])
    link(nt, knot_color.outputs[0], knot_darken.inputs['Color2'])
    link(nt, combined_knot_mask.outputs['Color'], knot_darken.inputs['Fac'])

    # --- sap: thin glossy amber ring just outside each small knot's edge ---
    sap_shape = new_node(nt, 'ShaderNodeValToRGB', (-500, 950))
    sap_shape.color_ramp.elements[0].position = 0.0
    sap_shape.color_ramp.elements[0].color = (1, 1, 1, 1)
    sap_shape.color_ramp.elements[1].position = 0.09   # wider than knot_shape -> ring around it
    sap_shape.color_ramp.elements[1].color = (0, 0, 0, 1)
    link(nt, knot_voronoi.outputs['Distance'], sap_shape.inputs['Fac'])

    sap_ring = new_node(nt, 'ShaderNodeMixRGB', (-200, 950), blend_type='SUBTRACT')
    link(nt, sap_shape.outputs['Color'], sap_ring.inputs['Color1'])
    link(nt, knot_shape.outputs['Color'], sap_ring.inputs['Color2'])
    sap_mask = new_node(nt, 'ShaderNodeMixRGB', (0, 950), blend_type='MULTIPLY')
    sap_mask.use_clamp = True
    link(nt, sap_ring.outputs['Color'], sap_mask.inputs['Color1'])
    link(nt, knot_presence.outputs['Value'], sap_mask.inputs['Color2'])

    sap_color = new_node(nt, 'ShaderNodeRGB', (250, 950))
    sap_color.outputs[0].default_value = (0.45, 0.22, 0.05, 1.0)
    sap_applied = new_node(nt, 'ShaderNodeMixRGB', (500, 350))
    link(nt, knot_darken.outputs['Color'], sap_applied.inputs['Color1'])
    link(nt, sap_color.outputs[0], sap_applied.inputs['Color2'])
    link(nt, sap_mask.outputs['Color'], sap_applied.inputs['Fac'])

    # --- seasonal tint ---
    season_color = seasonal_mix(
        nt, height_jittered.outputs[0], season_val,
        spring=[(0.0, (0.16, 0.16, 0.08)), (1.0, (0.44, 0.33, 0.20))],
        summer=[(0.0, (0.09, 0.10, 0.06)), (1.0, (0.42, 0.30, 0.18))],
        fall=[(0.0, (0.14, 0.09, 0.05)), (1.0, (0.46, 0.28, 0.15))],
        base_x=-1400, base_y=-750,
    )
    seasoned = new_node(nt, 'ShaderNodeMixRGB', (750, 250), blend_type='MULTIPLY')
    seasoned.inputs['Fac'].default_value = 0.35
    link(nt, sap_applied.outputs['Color'], seasoned.inputs['Color1'])
    link(nt, season_color, seasoned.inputs['Color2'])

    # --- fake cavity/AO: darken valleys directly, since bark grooves here are
    # shader-only bump (not real displacement), so a geometric AO bake would
    # see almost nothing. Reusing the same macro+crack value that drives the
    # bump keeps the "shading" and the "occlusion" visually consistent. ---
    cavity_range = new_node(nt, 'ShaderNodeMapRange', (750, -50))
    cavity_range.inputs['To Min'].default_value = 0.55
    cavity_range.inputs['To Max'].default_value = 1.0
    link(nt, grain_combine.outputs['Color'], cavity_range.inputs['Value'])
    final_color = new_node(nt, 'ShaderNodeMixRGB', (1000, 150), blend_type='MULTIPLY')
    final_color.inputs['Fac'].default_value = 1.0
    link(nt, seasoned.outputs['Color'], final_color.inputs['Color1'])
    link(nt, cavity_range.outputs['Result'], final_color.inputs['Color2'])

    # --- bump: furrows -> cracks -> knot ring-swirl, stacked ---
    bump_furrow = new_node(nt, 'ShaderNodeBump', (250, -300))
    bump_furrow.inputs['Strength'].default_value = 0.5
    bump_furrow.inputs['Distance'].default_value = 0.06
    link(nt, noise_macro.outputs['Fac'], bump_furrow.inputs['Height'])

    bump_crack = new_node(nt, 'ShaderNodeBump', (450, -300))
    bump_crack.inputs['Strength'].default_value = 0.3
    bump_crack.inputs['Distance'].default_value = 0.02
    link(nt, plate_voronoi.outputs['Distance'], bump_crack.inputs['Height'])
    link(nt, bump_furrow.outputs['Normal'], bump_crack.inputs['Normal'])

    knot_ring_scale = new_node(nt, 'ShaderNodeMath', (-200, 100), operation='MULTIPLY')
    knot_ring_scale.inputs[1].default_value = 40.0
    link(nt, knot_voronoi.outputs['Distance'], knot_ring_scale.inputs[0])
    knot_ring_sine = new_node(nt, 'ShaderNodeMath', (0, 100), operation='SINE')
    link(nt, knot_ring_scale.outputs[0], knot_ring_sine.inputs[0])
    knot_ring_height = new_node(nt, 'ShaderNodeMath', (250, 0), operation='MULTIPLY')
    link(nt, knot_ring_sine.outputs[0], knot_ring_height.inputs[0])
    link(nt, knot_presence.outputs['Value'], knot_ring_height.inputs[1])

    bump_knot = new_node(nt, 'ShaderNodeBump', (650, -300))
    bump_knot.inputs['Strength'].default_value = 0.45
    bump_knot.inputs['Distance'].default_value = 0.015
    link(nt, knot_ring_height.outputs[0], bump_knot.inputs['Height'])
    link(nt, bump_crack.outputs['Normal'], bump_knot.inputs['Normal'])

    # --- roughness: uniform mature bark, glossy dip at sap rings ---
    rough_range = new_node(nt, 'ShaderNodeMapRange', (250, -450))
    rough_range.inputs['To Min'].default_value = 0.72
    rough_range.inputs['To Max'].default_value = 0.9
    link(nt, grain_combine.outputs['Color'], rough_range.inputs['Value'])
    sap_roughness = new_node(nt, 'ShaderNodeRGB', (250, -600))
    sap_roughness.outputs[0].default_value = (0.18, 0.18, 0.18, 1.0)
    rough_final = new_node(nt, 'ShaderNodeMixRGB', (500, -450))
    link(nt, sap_mask.outputs['Color'], rough_final.inputs['Fac'])
    link(nt, rough_range.outputs['Result'], rough_final.inputs['Color1'])
    link(nt, sap_roughness.outputs[0], rough_final.inputs['Color2'])

    link(nt, final_color.outputs['Color'], bsdf.inputs['Base Color'])
    link(nt, bump_knot.outputs['Normal'], bsdf.inputs['Normal'])
    link(nt, rough_final.outputs['Color'], bsdf.inputs['Roughness'])

    print("  Bark material done.", flush=True)
    return {
        "material": mat, "node_tree": nt,
        "color_socket": final_color.outputs['Color'],
        "bump_height_socket": bump_knot.outputs['Normal'],
        "roughness_socket": rough_final.outputs['Color'],
        "knot_mask_socket": combined_knot_mask.outputs['Color'],
    }


# ---------------------------------------------------------------------------
# FOLIAGE MATERIAL
# ---------------------------------------------------------------------------

def build_foliage_material(scene):
    print("Building Foliage material node graph...", flush=True)
    mat = bpy.data.materials["Foliage"]
    mat.use_nodes = True
    mat.blend_method = 'HASHED'
    nt = mat.node_tree
    nt.nodes.clear()

    out = new_node(nt, 'ShaderNodeOutputMaterial', (1300, 0))
    bsdf = new_node(nt, 'ShaderNodeBsdfPrincipled', (1000, 0))
    link(nt, bsdf.outputs['BSDF'], out.inputs['Surface'])

    uv_map = new_node(nt, 'ShaderNodeUVMap', (-1100, 0))

    season_val = new_node(nt, 'ShaderNodeValue', (-1100, 600))
    season_val.outputs[0].default_value = SEASON
    add_season_driver(scene, season_val)

    obj_info = new_node(nt, 'ShaderNodeObjectInfo', (-1100, -300))

    # --- leaf-cluster cutout: noise-distorted Voronoi cells punched into alpha ---
    edge_noise = new_node(nt, 'ShaderNodeTexNoise', (-900, 200))
    edge_noise.inputs['Scale'].default_value = 14.0
    edge_noise.inputs['Detail'].default_value = 4.0
    link(nt, uv_map.outputs['UV'], edge_noise.inputs['Vector'])

    warp_add = new_node(nt, 'ShaderNodeVectorMath', (-700, 100), operation='ADD')
    warp_scale = new_node(nt, 'ShaderNodeVectorMath', (-800, 200), operation='SCALE')
    warp_scale.inputs['Scale'].default_value = 0.18
    link(nt, edge_noise.outputs['Color'], warp_scale.inputs[0])
    link(nt, uv_map.outputs['UV'], warp_add.inputs[0])
    link(nt, warp_scale.outputs['Vector'], warp_add.inputs[1])

    leaf_voronoi = new_node(nt, 'ShaderNodeTexVoronoi', (-500, 100),
                             voronoi_dimensions='2D', feature='F1')
    leaf_voronoi.inputs['Scale'].default_value = 9.0
    leaf_voronoi.inputs['Randomness'].default_value = 1.0
    link(nt, warp_add.outputs['Vector'], leaf_voronoi.inputs['Vector'])

    alpha_ramp = new_node(nt, 'ShaderNodeValToRGB', (-300, 100))
    alpha_ramp.color_ramp.elements[0].position = 0.0
    alpha_ramp.color_ramp.elements[0].color = (1.0, 1.0, 1.0, 1.0)   # leaf interior = opaque
    alpha_ramp.color_ramp.elements[1].position = 0.34
    alpha_ramp.color_ramp.elements[1].color = (0.0, 0.0, 0.0, 1.0)   # gap between leaves = cut out
    link(nt, leaf_voronoi.outputs['Distance'], alpha_ramp.inputs['Fac'])

    # --- per-leaf tonal variation using the Voronoi cell's random color ---
    tone_ramp = new_node(nt, 'ShaderNodeValToRGB', (-300, -150))
    tone_ramp.color_ramp.elements[0].color = (0.10, 0.05, 0.02, 1.0)
    tone_ramp.color_ramp.elements[1].color = (0.55, 0.55, 0.55, 1.0)
    link(nt, leaf_voronoi.outputs['Color'], tone_ramp.inputs['Fac'])

    # seasonal blend driven by the same Voronoi distance field as the alpha
    # cutout, so seasonal color and leaf shape stay spatially consistent.
    # Multi-stop ramps: spring gets a coppery-red new-growth tint fading to
    # green (real oaks flush reddish before greening up), fall spans red ->
    # orange -> purple/plum instead of a flat 2-color gradient.
    season_color = seasonal_mix(
        nt, leaf_voronoi.outputs['Distance'], season_val,
        spring=[(0.0, (0.78, 0.10, 0.05)), (0.4, (0.90, 0.68, 0.08)), (1.0, (0.35, 0.55, 0.14))],
        summer=[(0.0, (0.08, 0.28, 0.06)), (1.0, (0.24, 0.46, 0.12))],
        fall=[(0.0, (0.65, 0.10, 0.03)), (0.3, (0.88, 0.42, 0.03)),
              (0.65, (0.92, 0.68, 0.08)), (1.0, (0.75, 0.35, 0.04))],
        base_x=-300, base_y=-500,
    )

    # per-clump hue variance: a SECOND seasonal ramp keyed by obj_info.Random
    # (stable per clump instance, not per leaf-shape-pixel like the ramp
    # above) so different clumps across the canopy land at different points
    # along the range instead of all showing the same uniform gradient --
    # this is what actually reads as "throughout the leaves" variety rather
    # than one smooth blend.
    variant_color = seasonal_mix(
        nt, obj_info.outputs['Random'], season_val,
        spring=[(0.0, (0.82, 0.12, 0.06)), (0.5, (0.88, 0.62, 0.06)), (1.0, (0.38, 0.52, 0.16))],
        summer=[(0.0, (0.08, 0.28, 0.06)), (1.0, (0.24, 0.46, 0.12))],
        fall=[(0.0, (0.60, 0.09, 0.03)), (0.5, (0.85, 0.45, 0.03)), (1.0, (0.88, 0.62, 0.07))],
        base_x=-300, base_y=-1150,
    )
    season_color_final = new_node(nt, 'ShaderNodeMixRGB', (150, -300))
    season_color_final.inputs['Fac'].default_value = 0.5
    link(nt, season_color, season_color_final.inputs['Color1'])
    link(nt, variant_color, season_color_final.inputs['Color2'])

    tone_mix = new_node(nt, 'ShaderNodeMixRGB', (0, -100), blend_type='MULTIPLY')
    tone_mix.inputs['Fac'].default_value = 0.6
    link(nt, season_color_final.outputs['Color'], tone_mix.inputs['Color1'])
    link(nt, tone_ramp.outputs['Color'], tone_mix.inputs['Color2'])

    # per-instance variance so clumps across the tree don't look identical
    instance_tint = new_node(nt, 'ShaderNodeMixRGB', (250, -100), blend_type='MULTIPLY')
    instance_tint.inputs['Fac'].default_value = 0.5
    tint_ramp = new_node(nt, 'ShaderNodeValToRGB', (0, -350))
    tint_ramp.color_ramp.elements[0].color = (0.75, 0.75, 0.75, 1.0)
    tint_ramp.color_ramp.elements[1].color = (1.15, 1.15, 1.15, 1.0)
    link(nt, obj_info.outputs['Random'], tint_ramp.inputs['Fac'])
    link(nt, tone_mix.outputs['Color'], instance_tint.inputs['Color1'])
    link(nt, tint_ramp.outputs['Color'], instance_tint.inputs['Color2'])

    # --- translucency for backlit leaf look ---
    translucent = new_node(nt, 'ShaderNodeBsdfTranslucent', (500, -250))
    link(nt, instance_tint.outputs['Color'], translucent.inputs['Color'])
    leaf_mix_shader = new_node(nt, 'ShaderNodeMixShader', (750, 0))
    leaf_mix_shader.inputs['Fac'].default_value = 0.25
    link(nt, bsdf.outputs['BSDF'], leaf_mix_shader.inputs[1])
    link(nt, translucent.outputs['BSDF'], leaf_mix_shader.inputs[2])
    nt.links.remove(bsdf.outputs['BSDF'].links[0])
    link(nt, leaf_mix_shader.outputs['Shader'], out.inputs['Surface'])

    bump = new_node(nt, 'ShaderNodeBump', (250, 250))
    bump.inputs['Strength'].default_value = 0.25
    link(nt, leaf_voronoi.outputs['Distance'], bump.inputs['Height'])

    link(nt, instance_tint.outputs['Color'], bsdf.inputs['Base Color'])
    link(nt, alpha_ramp.outputs['Color'], bsdf.inputs['Alpha'])
    link(nt, bump.outputs['Normal'], bsdf.inputs['Normal'])
    bsdf.inputs['Roughness'].default_value = 0.55

    print("  Foliage material done.", flush=True)
    return {
        "material": mat, "node_tree": nt,
        "color_socket": instance_tint.outputs['Color'],
        "alpha_socket": alpha_ramp.outputs['Color'],
        "normal_socket": bump.outputs['Normal'],
    }


# ---------------------------------------------------------------------------
# BAKING
# ---------------------------------------------------------------------------

def create_bake_image(name, size, non_color=False):
    img = bpy.data.images.new(name, size, size, alpha=True)
    img.colorspace_settings.name = 'Non-Color' if non_color else 'sRGB'
    return img


def add_image_node(nt, image, location):
    node = nt.nodes.new('ShaderNodeTexImage')
    node.image = image
    node.location = location
    return node


def set_active_image(nt, node):
    for n in nt.nodes:
        n.select = False
    node.select = True
    nt.nodes.active = node


def bake_pass(obj, bake_type, targets, use_pass_color_only=False):
    """targets: list of (node_tree, image_node) to bake into simultaneously."""
    print(f"Baking {bake_type} pass ({len(targets)} image(s))... this can take "
          f"a while with no further output until it finishes -- that's Cycles "
          f"working, not Blender being frozen.", flush=True)

    for nt, node in targets:
        set_active_image(nt, node)

    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = BAKE_SAMPLES
    scene.render.bake.margin = BAKE_MARGIN_PX
    scene.cycles.bake_type = bake_type
    if use_pass_color_only:
        scene.render.bake.use_pass_direct = False
        scene.render.bake.use_pass_indirect = False
        scene.render.bake.use_pass_color = True

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.bake(type=bake_type)
    print(f"  {bake_type} pass complete.", flush=True)


def save_image(img, out_dir, filename):
    img.filepath_raw = os.path.join(out_dir, filename)
    img.file_format = 'PNG'
    img.save()
    print(f"  Saved {filename}", flush=True)


# ---------------------------------------------------------------------------
# LOD3 BILLBOARD CAPTURE
# ---------------------------------------------------------------------------

def bake_knot_mask(bark, out_dir):
    """Bakes combined_knot_mask -- an intermediate node inside the bark
    graph, not something wired to a BSDF input -- as its own standalone
    grayscale texture. Standard bake types (DIFFUSE/NORMAL/ROUGHNESS) only
    capture what's actually driving the Principled BSDF, so getting an
    arbitrary intermediate node out requires the same swap_output trick used
    for billboard capture: temporarily route it through Emission -> Output,
    bake type EMIT (a literal dump of whatever's wired to the surface), then
    restore the real material. This separate texture is what lets the Unity
    shader reposition JUST the knots per tree instance, independently of the
    base bark pattern."""
    print("Baking bark knot mask (for Unity per-instance knot variation)...", flush=True)
    nt = bark["node_tree"]

    img = create_bake_image("Oak_Bark_KnotMask", BARK_BAKE_SIZE, non_color=True)
    img_node = add_image_node(nt, img, (-1400, -1500))
    set_active_image(nt, img_node)

    emission = new_node(nt, 'ShaderNodeEmission', (-1200, -1500))
    link(nt, bark["knot_mask_socket"], emission.inputs['Color'])
    out_node, orig_socket = swap_output(nt, emission.outputs['Emission'])

    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = BAKE_SAMPLES
    scene.render.bake.margin = BAKE_MARGIN_PX
    scene.cycles.bake_type = 'EMIT'

    lod0 = bpy.data.objects.get(f"Oak_LOD{BAKE_TARGET_LOD}")
    bpy.ops.object.select_all(action='DESELECT')
    lod0.select_set(True)
    bpy.context.view_layer.objects.active = lod0
    bpy.ops.object.bake(type='EMIT')

    restore_output(nt, out_node, orig_socket)

    save_image(img, out_dir, "Oak_Bark_KnotMask.png")
    print("  Knot mask bake complete.", flush=True)


def compute_world_bbox(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)


def make_ortho_camera(name, location, look_at, ortho_scale):
    cam_data = bpy.data.cameras.new(name)
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = ortho_scale
    cam_obj = bpy.data.objects.new(name, cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.location = location
    direction = (look_at - location)
    cam_obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    return cam_obj


def build_capture_shader(nt, albedo_node, alpha_socket=None):
    """Unlit-albedo-times-AO shader, used ONLY as a temporary swap during
    billboard capture. Matters because LOD0-2 already display flat baked
    albedo (no real-time lighting reacting to it) -- if the billboard were
    captured under normal Cycles lighting instead, it would visibly mismatch
    LOD0-2's flat look right at the LOD transition distance. AO adds a bit
    of depth without introducing directional lighting that could mismatch."""
    ao_node = new_node(nt, 'ShaderNodeAmbientOcclusion', (-1200, -1250))
    ao_node.samples = BILLBOARD_AO_SAMPLES
    ao_node.inputs['Distance'].default_value = BILLBOARD_AO_DISTANCE
    link(nt, albedo_node.outputs['Color'], ao_node.inputs['Color'])

    emission = new_node(nt, 'ShaderNodeEmission', (-1000, -1250))
    link(nt, ao_node.outputs['Color'], emission.inputs['Color'])

    if alpha_socket is None:
        return emission.outputs['Emission']

    transparent = new_node(nt, 'ShaderNodeBsdfTransparent', (-1000, -1350))
    mix = new_node(nt, 'ShaderNodeMixShader', (-800, -1300))
    link(nt, transparent.outputs['BSDF'], mix.inputs[1])
    link(nt, emission.outputs['Emission'], mix.inputs[2])
    link(nt, alpha_socket, mix.inputs['Fac'])
    return mix.outputs['Shader']


def swap_output(nt, new_socket):
    out_node = next(n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL')
    orig_link = out_node.inputs['Surface'].links[0] if out_node.inputs['Surface'].links else None
    orig_socket = orig_link.from_socket if orig_link else None
    if orig_link:
        nt.links.remove(orig_link)
    link(nt, new_socket, out_node.inputs['Surface'])
    return out_node, orig_socket


def restore_output(nt, out_node, orig_socket):
    for l in list(out_node.inputs['Surface'].links):
        nt.links.remove(l)
    if orig_socket is not None:
        link(nt, orig_socket, out_node.inputs['Surface'])


def capture_billboard_views(bark, foliage, bark_albedo_node, foliage_albedo_node, out_dir):
    """Renders LOD0 from the front, transparent background, unlit+AO
    (matching LOD0-2's flat baked look). Single view only -- LOD3 is a
    single quad now, not crossed, since a crossed billboard doubles the
    trunk visibly from oblique angles (fine for puffy foliage, obviously
    wrong for a hard vertical line like a trunk)."""
    lod0 = bpy.data.objects.get(f"Oak_LOD{BAKE_TARGET_LOD}")
    if lod0 is None:
        print("  [WARN] LOD0 not found -- skipping billboard capture.", flush=True)
        return None

    print("Capturing billboard front view (unlit + AO, transparent bg)...",
          flush=True)

    xmin, xmax, ymin, ymax, zmin, zmax = compute_world_bbox(lod0)
    width_x = xmax - xmin
    width_y = ymax - ymin
    height = zmax - zmin
    center = Vector(((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2))
    reach = max(width_x, width_y, height) * 3 + 5   # camera distance, generous margin

    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = BAKE_SAMPLES
    prev_film_transparent = scene.render.film_transparent
    prev_res_x, prev_res_y = scene.render.resolution_x, scene.render.resolution_y
    prev_color_mode = scene.render.image_settings.color_mode
    prev_file_format = scene.render.image_settings.file_format

    scene.render.film_transparent = True
    scene.render.resolution_x = BILLBOARD_CAPTURE_SIZE
    scene.render.resolution_y = BILLBOARD_CAPTURE_SIZE
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'

    bark_out_node, bark_orig = swap_output(
        bark["node_tree"], build_capture_shader(bark["node_tree"], bark_albedo_node))
    foliage_out_node, foliage_orig = swap_output(
        foliage["node_tree"],
        build_capture_shader(foliage["node_tree"], foliage_albedo_node,
                              foliage_albedo_node.outputs['Alpha']))

    # Widest of the two horizontal extents, so the canopy isn't clipped
    # regardless of whether the tree happens to be wider along X or Y.
    ortho_scale = max(width_x, width_y, height) * BILLBOARD_MARGIN
    # Aim target's X is forced to 0 (the trunk's actual axis, guaranteed by
    # generate_skeleton() always starting there) rather than using the
    # bounding-box center's X, which can drift off-axis if the canopy grew
    # asymmetrically. Since the camera itself is also at X=0, this keeps the
    # view direction purely along +Y with zero horizontal skew -- otherwise
    # the trunk would land slightly off-center in the image, which matters a
    # lot here since the same image gets reused on both crossed quads and
    # needs to sit exactly on their shared rotation axis to read as one
    # continuous trunk instead of two offset ones.
    aim = Vector((0, center.y, center.z))
    cam_obj = make_ortho_camera("_BillboardCam", Vector((0, ymin - reach, center.z)),
                                 aim, ortho_scale)
    cam_data = cam_obj.data
    scene.camera = cam_obj
    path = os.path.join(out_dir, "Oak_Billboard.png")
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    print(f"  Captured billboard view: {path}", flush=True)
    bpy.data.objects.remove(cam_obj, do_unlink=True)
    if cam_data.users == 0:
        bpy.data.cameras.remove(cam_data)

    restore_output(bark["node_tree"], bark_out_node, bark_orig)
    restore_output(foliage["node_tree"], foliage_out_node, foliage_orig)

    scene.render.film_transparent = prev_film_transparent
    scene.render.resolution_x, scene.render.resolution_y = prev_res_x, prev_res_y
    scene.render.image_settings.color_mode = prev_color_mode
    scene.render.image_settings.file_format = prev_file_format

    print("  Billboard capture complete.", flush=True)
    return path


def build_billboard_materials(image_path):
    """Simple Principled-BSDF material for the single billboard quad -- the
    captured image driving Base Color + Alpha, high roughness (matte, no
    gloss on a flat cutout), no normal map needed."""
    if image_path is None:
        return
    mat = bpy.data.materials["Billboard"]
    mat.use_nodes = True
    mat.blend_method = 'HASHED'
    nt = mat.node_tree
    nt.nodes.clear()

    out = new_node(nt, 'ShaderNodeOutputMaterial', (600, 0))
    bsdf = new_node(nt, 'ShaderNodeBsdfPrincipled', (300, 0))
    link(nt, bsdf.outputs['BSDF'], out.inputs['Surface'])
    bsdf.inputs['Roughness'].default_value = 0.8

    img = bpy.data.images.load(image_path)
    img_node = new_node(nt, 'ShaderNodeTexImage', (0, 0))
    img_node.image = img
    link(nt, img_node.outputs['Color'], bsdf.inputs['Base Color'])
    link(nt, img_node.outputs['Alpha'], bsdf.inputs['Alpha'])
    print(f"  Built Billboard material from {os.path.basename(image_path)}", flush=True)


def export_fbx(out_dir):
    """Export the OakTree root empty + all Oak_LODx children as ONE fbx.
    Hidden objects (LOD3 -- currently disabled pending a rebuild) are
    excluded by Blender's FBX exporter even when selected, so this
    temporarily un-hides everything under the root for the export and
    restores the previous hide state afterward."""
    root = bpy.data.objects.get("OakTree")
    if root is None:
        print("  [WARN] 'OakTree' root empty not found -- skipping FBX export. "
              "Run tree_gen.py in this .blend first.", flush=True)
        return

    export_objs = [root] + list(root.children)
    print(f"Exporting FBX: {[o.name for o in export_objs]}", flush=True)

    prev_state = {}
    for obj in export_objs:
        prev_state[obj.name] = (obj.hide_viewport, obj.hide_render, obj.hide_get())
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)

    bpy.ops.object.select_all(action='DESELECT')
    for obj in export_objs:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = root

    fbx_path = os.path.join(out_dir, "Oak.fbx")
    try:
        bpy.ops.export_scene.fbx(
            filepath=fbx_path,
            use_selection=True,
            object_types={'EMPTY', 'MESH'},
            mesh_smooth_type='FACE',
            add_leaf_bones=False,
            bake_anim=False,
            axis_forward='-Z',
            axis_up='Y',
            # Bakes the Z-up -> Y-up axis conversion directly into vertex
            # data instead of leaving it as a rotation on each object's
            # transform node. Without this, normal scene placement in Unity
            # looks correct (the renderer handles the node rotation fine),
            # but Unity Terrain's tree-painting system builds its own
            # instancing matrices and does NOT respect a non-identity
            # rotation on the prefab root -- that mismatch is exactly what
            # causes painted trees to land rotated 90 degrees and lying down.
            bake_space_transform=True,
        )
        print(f"  Exported FBX: {fbx_path}", flush=True)
    except RuntimeError as e:
        print(f"  [ERROR] FBX export failed: {e}. Check that the FBX "
              f"import/export add-on is enabled (Edit > Preferences > "
              f"Add-ons).", flush=True)

    # restore hide state -- LOD3 goes back to hidden in the viewport, per
    # the earlier request to keep it disabled until it's rebuilt
    for obj in export_objs:
        hv, hr, hg = prev_state[obj.name]
        obj.hide_viewport = hv
        obj.hide_render = hr
        obj.hide_set(hg)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    scene = bpy.context.scene
    print(f"Baking for season: {season_label(SEASON)} (SEASON={SEASON})", flush=True)
    bark = build_bark_material(scene)
    foliage = build_foliage_material(scene)

    lod0 = bpy.data.objects.get(f"Oak_LOD{BAKE_TARGET_LOD}")
    if lod0 is None:
        raise RuntimeError(
            f"Oak_LOD{BAKE_TARGET_LOD} not found -- run tree_gen.py first in this .blend."
        )
    print(f"Baking against {lod0.name} ({len(lod0.data.polygons)} faces), "
          f"bark={BARK_BAKE_SIZE}px, foliage={FOLIAGE_BAKE_SIZE}px, "
          f"samples={BAKE_SAMPLES}. Starting albedo/normal/roughness passes now.",
          flush=True)

    bark_albedo_img = create_bake_image("Oak_Bark_Albedo", BARK_BAKE_SIZE)
    bark_normal_img = create_bake_image("Oak_Bark_Normal", BARK_BAKE_SIZE, non_color=True)
    bark_rough_img = create_bake_image("Oak_Bark_Roughness", BARK_BAKE_SIZE, non_color=True)

    foliage_albedo_img = create_bake_image("Oak_Foliage_Albedo", FOLIAGE_BAKE_SIZE)
    foliage_normal_img = create_bake_image("Oak_Foliage_Normal", FOLIAGE_BAKE_SIZE, non_color=True)

    bark_albedo_node = add_image_node(bark["node_tree"], bark_albedo_img, (-1400, -900))
    bark_normal_node = add_image_node(bark["node_tree"], bark_normal_img, (-1600, -900))
    bark_rough_node = add_image_node(bark["node_tree"], bark_rough_img, (-1800, -900))

    foliage_albedo_node = add_image_node(foliage["node_tree"], foliage_albedo_img, (-1300, -900))
    foliage_normal_node = add_image_node(foliage["node_tree"], foliage_normal_img, (-1500, -900))

    # --- bake albedo (color-only diffuse pass, ignores lighting) ---
    bake_pass(lod0, 'DIFFUSE',
              [(bark["node_tree"], bark_albedo_node),
               (foliage["node_tree"], foliage_albedo_node)],
              use_pass_color_only=True)

    # --- bake normal (reads whatever's wired into each BSDF's Normal input) ---
    bake_pass(lod0, 'NORMAL',
              [(bark["node_tree"], bark_normal_node),
               (foliage["node_tree"], foliage_normal_node)])

    # --- bake roughness (bark only -- foliage roughness is a flat constant) ---
    bake_pass(lod0, 'ROUGHNESS', [(bark["node_tree"], bark_rough_node)])

    blend_path = bpy.data.filepath
    out_dir = os.path.dirname(blend_path) if blend_path else bpy.app.tempdir

    bake_knot_mask(bark, out_dir)

    label = season_label(SEASON)
    save_image(bark_albedo_img, out_dir, f"Oak_Bark_Albedo_{label}.png")
    save_image(bark_normal_img, out_dir, f"Oak_Bark_Normal_{label}.png")
    save_image(bark_rough_img, out_dir, f"Oak_Bark_Roughness_{label}.png")
    save_image(foliage_albedo_img, out_dir, f"Oak_Foliage_Albedo_{label}.png")
    save_image(foliage_normal_img, out_dir, f"Oak_Foliage_Normal_{label}.png")

    # --- rewire baked images into the live shaders so the viewport shows
    #     the FINAL baked result, per spec ---
    bnt = bark["node_tree"]
    bsdf = next(n for n in bnt.nodes if n.type == 'BSDF_PRINCIPLED')
    normal_map = new_node(bnt, 'ShaderNodeNormalMap', (-1000, -900))
    link(bnt, bark_albedo_node.outputs['Color'], bsdf.inputs['Base Color'])
    link(bnt, bark_normal_node.outputs['Color'], normal_map.inputs['Color'])
    link(bnt, normal_map.outputs['Normal'], bsdf.inputs['Normal'])
    link(bnt, bark_rough_node.outputs['Color'], bsdf.inputs['Roughness'])

    fnt = foliage["node_tree"]
    fbsdf = next(n for n in fnt.nodes if n.type == 'BSDF_PRINCIPLED')
    f_normal_map = new_node(fnt, 'ShaderNodeNormalMap', (-900, -900))
    link(fnt, foliage_albedo_node.outputs['Color'], fbsdf.inputs['Base Color'])
    link(fnt, foliage_albedo_node.outputs['Alpha'], fbsdf.inputs['Alpha'])
    link(fnt, foliage_normal_node.outputs['Color'], f_normal_map.inputs['Color'])
    link(fnt, f_normal_map.outputs['Normal'], fbsdf.inputs['Normal'])

    billboard_image_path = capture_billboard_views(
        bark, foliage, bark_albedo_node, foliage_albedo_node, out_dir)
    if billboard_image_path:
        build_billboard_materials(billboard_image_path)
        billboard = bpy.data.objects.get("Oak_LOD3")
        if billboard:
            billboard.hide_viewport = False
            billboard.hide_render = False
            print("  LOD3 billboard finalized and unhidden.", flush=True)
    else:
        print("  [WARN] Billboard capture incomplete -- LOD3 stays hidden.", flush=True)

    if bpy.context.screen:
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.type = 'MATERIAL'

    print(f"Bake complete. Images saved to: {out_dir}")

    export_fbx(out_dir)

    if blend_path:
        bpy.ops.wm.save_mainfile()


if __name__ == "__main__":
    main()

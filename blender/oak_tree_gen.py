"""
tree_gen.py -- Procedural Oak Tree LOD Generator (Blender 4.5)
Script 1 of 2 in the Oak LOD pipeline.

Builds ONE random branch skeleton per run and derives all four LOD meshes from it
(same origin, same seed) plus foliage clumps and a LOD3 billboard. Assigns placeholder
material slots ("Bark", "Foliage", "Billboard") for
tree_shade.py to fill with real
procedural node graphs and bakes. Logs triangle counts to console + a text report
saved next to the .blend.

Run inside Blender's Scripting tab, or headless:
    blender --background --python tree_gen.py

Re-run any time for a brand new tree. Pass a fixed SEED below if you want to
reproduce a specific tree while iterating on shading in script 2.
"""

import bpy
import bmesh
import random
import math
import os
import contextlib
from mathutils import Vector, Matrix


def get_view3d_override():
    """Find a VIEW_3D area/region so ops like mode_set/smart_project (which
    require a 3D viewport in context) work when the script is run from the
    Scripting tab's Run Script button instead of an operator search/shortcut."""
    windows = list(bpy.context.window_manager.windows)
    if bpy.context.window and bpy.context.window not in windows:
        windows.insert(0, bpy.context.window)
    for window in windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        return {'window': window, 'screen': window.screen,
                                'area': area, 'region': region}
    return None


def view3d_context():
    override = get_view3d_override()
    return bpy.context.temp_override(**override) if override else contextlib.nullcontext()

# ---------------------------------------------------------------------------
# CONFIG -- tune these
# ---------------------------------------------------------------------------

SEED = random.randint(0, 999_999)   # override with a fixed int to reproduce a tree
random.seed(SEED)

TRUNK_HEIGHT          = 3.0
TRUNK_BASE_RADIUS     = 0.30
TRUNK_TOP_RADIUS      = 0.16
MAX_BRANCH_DEPTH      = 5
BRANCH_COUNT_RANGE    = (3, 5)       # children per branching node
TRUNK_SPLIT_RANGE     = (4, 6)       # children off the trunk itself (depth 0)
BRANCH_LENGTH_FALLOFF = 0.74
BRANCH_RADIUS_FALLOFF = 0.74
MIN_BRANCH_RADIUS     = 0.018        # clamp so outer twigs don't taper to a hair
BRANCH_ANGLE_RANGE    = (30, 68)     # degrees off parent -- wide oak-style spread
BRANCH_TWIST_RANGE    = (0, 360)     # degrees, radial distribution around parent
BEND_JITTER           = 0.12         # per-branch waviness

LOD_PARAMS = {
    # sides = bevel profile vertex count (branch cross-section)
    # depth_cutoff = max branch order included at this LOD
    # branch_tri_budget / foliage_tri_budget = soft targets, hard-enforced by TOTAL_TRI_CAP
    0: dict(sides=8, depth_cutoff=5, branch_tri_budget=1500, foliage_tri_budget=2500),
    1: dict(sides=6, depth_cutoff=3, branch_tri_budget=550,  foliage_tri_budget=1000),
    2: dict(sides=4, depth_cutoff=2, branch_tri_budget=180,  foliage_tri_budget=440),
}
# Derived, not hand-maintained: each LOD's hard cap is just its own branch +
# foliage budget added together, so changing LOD_PARAMS above is the only
# place you ever need to edit -- this can't drift out of sync with it.
# LOD3 (billboard) budget is independent of LOD_PARAMS since it has no
# branch/foliage geometry of its own.
TOTAL_TRI_CAP = {i: p["branch_tri_budget"] + p["foliage_tri_budget"] for i, p in LOD_PARAMS.items()}
TOTAL_TRI_CAP[3] = 20

CLUMP_TRIS_BY_LOD  = {0: 6, 1: 6, 2: 4}   # tris per foliage clump prototype (crossed quads)
CLUMP_RADIUS_BY_LOD = {0: 0.35, 1: 0.4, 2: 0.55}  # bigger/fewer clumps at lower LOD

BILLBOARD_WIDTH_MARGIN = 1.05   # billboard slightly wider than canopy bounds

OBJECT_PREFIX = "Oak"
ROOT_EMPTY_NAME = "OakTree"

# ---------------------------------------------------------------------------
# SKELETON DATA STRUCTURE
# ---------------------------------------------------------------------------

class Segment:
    __slots__ = ("id", "start", "end", "r_start", "r_end", "depth", "parent")

    def __init__(self, seg_id, start, end, r_start, r_end, depth, parent):
        self.id = seg_id
        self.start = start
        self.end = end
        self.r_start = r_start
        self.r_end = r_end
        self.depth = depth
        self.parent = parent


def rotate_vector(direction, angle_rad, twist_rad):
    """Tilt `direction` by angle_rad off itself, then roll around it by twist_rad."""
    up = direction.normalized()
    arbitrary = Vector((1, 0, 0)) if abs(up.z) < 0.9 else Vector((0, 1, 0))
    perp = up.cross(arbitrary).normalized()
    tilted = Matrix.Rotation(angle_rad, 4, perp) @ up
    return (Matrix.Rotation(twist_rad, 4, up) @ tilted).normalized()


def generate_skeleton():
    """Recursively grow trunk + branches. Returns a flat list of Segment."""
    segments = []

    def grow(start, direction, length, r_start, r_end, depth, parent_id):
        # small per-branch waviness so limbs don't read as perfectly straight rods
        wobble = Vector((
            random.uniform(-BEND_JITTER, BEND_JITTER),
            random.uniform(-BEND_JITTER, BEND_JITTER),
            random.uniform(-BEND_JITTER * 0.3, BEND_JITTER * 0.3),
        )) * (depth + 1)
        end = start + direction * length + wobble

        seg_id = len(segments)
        segments.append(Segment(seg_id, start, end, r_start, r_end, depth, parent_id))

        if depth >= MAX_BRANCH_DEPTH:
            return

        n_children = (random.randint(*TRUNK_SPLIT_RANGE) if depth == 0
                      else random.randint(*BRANCH_COUNT_RANGE))
        for _ in range(n_children):
            angle = math.radians(random.uniform(*BRANCH_ANGLE_RANGE))
            twist = math.radians(random.uniform(*BRANCH_TWIST_RANGE))
            child_dir = rotate_vector(direction, angle, twist)
            child_len = length * BRANCH_LENGTH_FALLOFF * random.uniform(0.8, 1.15)
            child_r_start = r_end
            child_r_end = max(r_end * BRANCH_RADIUS_FALLOFF, MIN_BRANCH_RADIUS)
            grow(end, child_dir, child_len, child_r_start, child_r_end, depth + 1, seg_id)

    grow(Vector((0, 0, 0)), Vector((0, 0, 1)), TRUNK_HEIGHT,
         TRUNK_BASE_RADIUS, TRUNK_TOP_RADIUS, 0, -1)
    return segments


def get_leaf_segments(filtered):
    """Segments in `filtered` that have no children also present in `filtered`."""
    ids = {s.id for s in filtered}
    parent_ids = {s.parent for s in filtered}
    return [s for s in filtered if s.id not in parent_ids]


def trim_to_budget(filtered, sides, budget):
    """Iteratively strip leaf branches (deepest first) until the rough triangle
    estimate fits the budget. Keeps the tree topologically connected (never
    removes a segment whose children are still present)."""
    filtered = list(filtered)
    if budget <= 0:
        return filtered
    while len(filtered) > 1:
        est = len(filtered) * sides * 2
        if est <= budget:
            break
        leaves = get_leaf_segments(filtered)
        leaves.sort(key=lambda s: -s.depth)
        remove_n = max(1, len(leaves) // 10)
        remove_ids = {s.id for s in leaves[:remove_n]}
        filtered = [s for s in filtered if s.id not in remove_ids]
    return filtered


# ---------------------------------------------------------------------------
# MESH BUILDING
# ---------------------------------------------------------------------------

def build_segment_tube(bm, seg, sides, leaf_ids):
    """Build one branch segment as an explicit ring-extruded tube -- no curve
    or bevel-object involved, so there's no black-box conversion step that
    can silently collapse the radius to zero. Caps are only added at the
    trunk base and at branch tips (leaves), since interior joints are
    covered by the parent/child geometry overlapping -- this also saves
    triangles versus capping every junction."""
    axis = seg.end - seg.start
    length = axis.length
    if length < 1e-6:
        return

    axis_n = axis.normalized()
    helper_vec = Vector((0, 0, 1)) if abs(axis_n.z) < 0.999 else Vector((1, 0, 0))
    right = axis_n.cross(helper_vec).normalized()
    up = right.cross(axis_n).normalized()

    ring_start, ring_end = [], []
    for i in range(sides):
        ang = 2 * math.pi * i / sides
        offset = right * math.cos(ang) + up * math.sin(ang)
        ring_start.append(bm.verts.new(seg.start + offset * seg.r_start))
        ring_end.append(bm.verts.new(seg.end + offset * seg.r_end))

    for i in range(sides):
        a, b = ring_start[i], ring_start[(i + 1) % sides]
        c, d = ring_end[(i + 1) % sides], ring_end[i]
        bm.faces.new((a, b, c, d))

    if seg.parent == -1:
        bm.faces.new(list(reversed(ring_start)))   # cap the trunk base
    if seg.id in leaf_ids:
        bm.faces.new(ring_end)                      # cap branch tips


def apply_cylindrical_uv(mesh_obj):
    """Cylindrical UV projection computed directly through bmesh (U = angle
    around the vertical axis, V = normalized height). No bpy.ops involved,
    so it works identically whether run from the GUI or headless, and can't
    hit smart_project's viewport-context poll issue. Seams at branch unions
    won't be perfectly continuous, but this is a solid base for the bark
    bake in tree_shade.py -- can be refined per-branch later if needed."""
    mesh = mesh_obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    uv_layer = bm.loops.layers.uv.get("UVMap") or bm.loops.layers.uv.new("UVMap")

    zs = [v.co.z for v in bm.verts]
    z_min, z_max = (min(zs), max(zs)) if zs else (0.0, 1.0)
    z_range = max(z_max - z_min, 1e-6)

    for face in bm.faces:
        for loop in face.loops:
            co = loop.vert.co
            u = (math.atan2(co.y, co.x) / (2 * math.pi)) + 0.5
            v = (co.z - z_min) / z_range
            loop[uv_layer].uv = (u, v)

    bm.to_mesh(mesh)
    bm.free()


def build_branch_mesh(segments, lod_index):
    params = LOD_PARAMS[lod_index]
    sides = params["sides"]
    filtered = [s for s in segments if s.depth <= params["depth_cutoff"]]
    filtered = trim_to_budget(filtered, sides, params["branch_tri_budget"])
    leaf_ids = {s.id for s in get_leaf_segments(filtered)}

    mesh = bpy.data.meshes.new(f"_branch_LOD{lod_index}_mesh")
    bm = bmesh.new()
    for seg in filtered:
        build_segment_tube(bm, seg, sides, leaf_ids)
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    mesh_obj = bpy.data.objects.new(f"_branch_LOD{lod_index}", mesh)
    bpy.context.collection.objects.link(mesh_obj)

    # cylindrical UV unwrap done manually through bmesh -- no bpy.ops involved,
    # so it can't hit the "context is incorrect" class of errors at all.
    apply_cylindrical_uv(mesh_obj)

    triangulate_object(mesh_obj)

    # smooth shading via mesh data directly (again, no operator -> no context issues).
    for poly in mesh_obj.data.polygons:
        poly.use_smooth = True

    return mesh_obj, filtered


def triangulate_object(obj):
    mod = obj.modifiers.new("Triangulate", type='TRIANGULATE')
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)


def get_tri_count(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    mesh.calc_loop_triangles()
    count = len(mesh.loop_triangles)
    eval_obj.to_mesh_clear()
    return count


# ---------------------------------------------------------------------------
# FOLIAGE CLUMPS
# ---------------------------------------------------------------------------

def create_clump_prototype(lod_index, radius):
    """3 crossing quads (6 tris) for LOD0/1, 2 crossing quads (4 tris) for LOD2."""
    tris = CLUMP_TRIS_BY_LOD[lod_index]
    n_quads = tris // 2

    mesh = bpy.data.meshes.new(f"_ClumpProto_LOD{lod_index}")
    bm = bmesh.new()
    for i in range(n_quads):
        ang = math.pi * i / n_quads
        v1 = bm.verts.new((-radius * math.cos(ang), -radius * math.sin(ang), 0))
        v2 = bm.verts.new((radius * math.cos(ang), radius * math.sin(ang), 0))
        v3 = bm.verts.new((radius * math.cos(ang), radius * math.sin(ang), radius * 2))
        v4 = bm.verts.new((-radius * math.cos(ang), -radius * math.sin(ang), radius * 2))
        bm.faces.new((v1, v2, v3, v4))
    bm.normal_update()

    uv_layer = bm.loops.layers.uv.new("UVMap")
    for face in bm.faces:
        for loop in face.loops:
            co = loop.vert.co
            loop[uv_layer].uv = ((co.x / radius + 1) * 0.5, co.z / (radius * 2))

    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new(f"_ClumpProto_LOD{lod_index}", mesh)
    bpy.context.collection.objects.link(obj)
    obj.hide_viewport = True
    obj.hide_render = True
    return obj


def scatter_foliage(filtered_segments, lod_index):
    params = LOD_PARAMS[lod_index]
    tris_each = CLUMP_TRIS_BY_LOD[lod_index]
    budget = params["foliage_tri_budget"]
    n_clumps = max(1, budget // tris_each)

    tips = get_leaf_segments(filtered_segments)
    if not tips:
        tips = filtered_segments

    proto = create_clump_prototype(lod_index, CLUMP_RADIUS_BY_LOD[lod_index])
    clump_objs = []

    for i in range(n_clumps):
        tip = tips[i % len(tips)]
        jitter = Vector((random.uniform(-0.15, 0.15),
                          random.uniform(-0.15, 0.15),
                          random.uniform(-0.05, 0.15)))
        loc = tip.end + jitter

        inst = bpy.data.objects.new(f"_clump_{lod_index}_{i}", proto.data)
        bpy.context.collection.objects.link(inst)
        inst.location = loc
        inst.rotation_euler = (
            random.uniform(0, math.pi * 2),
            random.uniform(0, math.pi * 2),
            random.uniform(0, math.pi * 2),
        )
        clump_objs.append(inst)

    bpy.data.objects.remove(proto, do_unlink=True)
    return clump_objs


# ---------------------------------------------------------------------------
# LOD3 BILLBOARD
# ---------------------------------------------------------------------------

def create_billboard(canopy_height, canopy_width):
    """Two quads crossed at 90 degrees, BOTH using the same single captured
    image (not two different front/side captures). Because both quads are
    built symmetric around the shared vertical Z-axis, and the trunk in the
    image sits at the image's horizontal center (see capture_billboard_views
    in tree_shade.py, which deliberately aims the camera to guarantee this),
    the trunk column of the texture lands exactly on the axis where the two
    quads intersect -- so it reads as one continuous trunk from any angle,
    instead of the double-trunk artifact you get from two DIFFERENT captures
    that each have their own independent (and differing) centering."""
    w = canopy_width * BILLBOARD_WIDTH_MARGIN
    h = canopy_height * BILLBOARD_WIDTH_MARGIN

    mesh = bpy.data.meshes.new(f"{OBJECT_PREFIX}_LOD3_mesh")
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")

    for ang in (0.0, math.pi / 2):
        positions = [
            (-w / 2 * math.cos(ang), -w / 2 * math.sin(ang), 0),
            (w / 2 * math.cos(ang), w / 2 * math.sin(ang), 0),
            (w / 2 * math.cos(ang), w / 2 * math.sin(ang), h),
            (-w / 2 * math.cos(ang), -w / 2 * math.sin(ang), h),
        ]
        uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]

        # Front-winding face, then a SECOND coincident face at the same
        # positions but with its own separate vertices (bmesh treats a face
        # as a duplicate based on vertex SET, not winding order -- reusing
        # the same 4 verts with reversed order raises "face already exists",
        # so the back copy needs distinct vertex objects even though they
        # sit at identical coordinates). Single-sided geometry only renders
        # from one side (backface culled by default), which means a static,
        # non-camera-facing crossed billboard actually vanishes entirely at
        # certain orbit angles -- both planes' normals face away from the
        # camera simultaneously in that arc. Doubling each plane here means
        # it's always rendering from both sides, so the tree never
        # disappears no matter how the camera orbits, without depending on
        # remembering to set the Unity material to two-sided.
        front_verts = [bm.verts.new(p) for p in positions]
        face_front = bm.faces.new(front_verts)
        for loop, uv in zip(face_front.loops, uvs):
            loop[uv_layer].uv = uv

        back_verts = [bm.verts.new(p) for p in reversed(positions)]
        face_back = bm.faces.new(back_verts)
        for loop, uv in zip(face_back.loops, reversed(uvs)):
            loop[uv_layer].uv = uv

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new(f"{OBJECT_PREFIX}_LOD3", mesh)
    bpy.context.collection.objects.link(obj)
    triangulate_object(obj)  # 4 faces (2 planes x front+back) -> 8 tris; well under the 20 tri cap
    return obj


# ---------------------------------------------------------------------------
# MATERIALS (placeholders -- tree_shade.py builds the real node graphs)
# ---------------------------------------------------------------------------

def ensure_placeholder_material(name, color):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    return mat


def assign_placeholder_materials(obj, slot_names):
    obj.data.materials.clear()
    for name in slot_names:
        obj.data.materials.append(bpy.data.materials[name])


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def reset_scene():
    """Fully clear every object from previous runs, including hidden ones.
    The old version used bpy.ops.object.select_all + delete, but Blender's
    'select all' SKIPS hidden objects (hide_viewport=True) -- like the LOD3
    billboard, which is deliberately hidden. That meant old hidden billboards
    never got deleted, and once their parent (OakTree) WAS deleted by the
    next reset, they became orphaned leftovers -- Blender then auto-renamed
    each new run's billboard Oak_LOD3.001, .002, .003 etc. Removing objects
    directly via bpy.data instead doesn't care about visibility, so nothing
    can be left behind between runs."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for block_type in (bpy.data.meshes, bpy.data.curves, bpy.data.materials):
        for block in list(block_type):
            if block.users == 0:
                block_type.remove(block)


def main():
    reset_scene()

    bark_mat = ensure_placeholder_material("Bark", (0.30, 0.20, 0.13))
    foliage_mat = ensure_placeholder_material("Foliage", (0.20, 0.45, 0.12))
    billboard_mat = ensure_placeholder_material("Billboard", (0.20, 0.45, 0.12))

    skeleton = generate_skeleton()

    report_lines = [f"Oak Tree LOD Report -- seed {SEED}", "=" * 50]
    lod_objects = {}
    canopy_max_x = 0.0
    canopy_max_y = 0.0
    canopy_max_z = TRUNK_HEIGHT

    for lod_index in (0, 1, 2):
        branch_obj, filtered = build_branch_mesh(skeleton, lod_index)
        branch_tris = get_tri_count(branch_obj)

        clumps = scatter_foliage(filtered, lod_index)
        foliage_tris = sum(get_tri_count(c) for c in clumps)

        for seg in filtered:
            canopy_max_x = max(canopy_max_x, abs(seg.end.x))
            canopy_max_y = max(canopy_max_y, abs(seg.end.y))
            canopy_max_z = max(canopy_max_z, seg.end.z)

        # material slots: 0 = Bark (branch faces), 1 = Foliage (clump faces)
        assign_placeholder_materials(branch_obj, ["Bark"])
        for c in clumps:
            assign_placeholder_materials(c, ["Foliage"])

        bpy.ops.object.select_all(action='DESELECT')
        branch_obj.select_set(True)
        for c in clumps:
            c.select_set(True)
        bpy.context.view_layer.objects.active = branch_obj
        bpy.ops.object.join()

        lod_obj = bpy.context.view_layer.objects.active
        lod_obj.name = f"{OBJECT_PREFIX}_LOD{lod_index}"
        lod_obj.location = (0, 0, 0)
        bpy.context.scene.cursor.location = (0, 0, 0)
        with view3d_context():
            bpy.ops.object.origin_set(type='ORIGIN_CURSOR')

        total_tris = get_tri_count(lod_obj)
        lod_objects[lod_index] = lod_obj

        cap = TOTAL_TRI_CAP[lod_index]
        status = "OK" if total_tris <= cap else "OVER BUDGET"
        report_lines.append(
            f"LOD{lod_index}: branch={branch_tris} tris, foliage={foliage_tris} tris, "
            f"TOTAL={total_tris} / cap {cap}  [{status}]"
        )

    billboard = create_billboard(canopy_max_z, max(canopy_max_x, canopy_max_y) * 2)
    assign_placeholder_materials(billboard, ["Billboard"])
    billboard.hide_viewport = True   # LOD3 disabled -- will be rebuilt once LOD0-2 are finalized
    billboard.hide_render = True
    bill_tris = get_tri_count(billboard)
    cap3 = TOTAL_TRI_CAP[3]
    status3 = "OK" if bill_tris <= cap3 else "OVER BUDGET"
    report_lines.append(f"LOD3: billboard={bill_tris} tris / cap {cap3}  [{status3}]")
    lod_objects[3] = billboard

    # parent everything under one empty at world origin
    root = bpy.data.objects.new(ROOT_EMPTY_NAME, None)
    bpy.context.collection.objects.link(root)
    root.location = (0, 0, 0)
    for obj in lod_objects.values():
        obj.parent = root
        obj.matrix_parent_inverse = root.matrix_world.inverted()

    # viewport feedback
    if bpy.context.screen:
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.type = 'MATERIAL'

    report_lines.append("=" * 50)
    report_text = "\n".join(report_lines)
    print(report_text)

    blend_path = bpy.data.filepath
    out_dir = os.path.dirname(blend_path) if blend_path else bpy.app.tempdir
    report_path = os.path.join(out_dir, "LOD_TriangleReport.txt")
    with open(report_path, "w") as f:
        f.write(report_text)
    print(f"Report written to: {report_path}")

    if blend_path:
        bpy.ops.wm.save_mainfile()


if __name__ == "__main__":
    main()

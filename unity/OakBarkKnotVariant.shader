// OakBarkKnotVariant.shader
// Unity 2017-compatible Surface Shader.
//
// Adds a SECOND knot layer on top of the baked bark albedo (which already
// has its own fixed knots baked in from Blender). This extra layer's UV is
// offset by a value derived purely from each tree's own world position, so
// every tree instance shows a differently-positioned extra knot pattern --
// with zero additional setup required.
//
// Why world-position-based instead of a per-instance script property: trees
// painted onto Terrain via the Paint Trees system (including the custom
// TerrainTreePainter tool) are rendered through Unity's own internal
// batching, not as individual GameObjects with MonoBehaviours -- there's no
// hook to set a MaterialPropertyBlock per painted tree instance. World
// position is something every instance already has uniquely for free, so
// hashing it into a UV offset works correctly for both terrain-painted
// trees AND ordinary hand-placed prefabs, with no scripting needed either
// way.
//
// SETUP:
//   1. Create a new Material using this shader.
//   2. Albedo         = Oak_Bark_Albedo_<Season>.png  (from tree_shade.py)
//   3. Normal Map     = Oak_Bark_Normal_<Season>.png
//   4. Roughness Map  = Oak_Bark_Roughness_<Season>.png
//   5. Knot Mask      = Oak_Bark_KnotMask.png           (new, separate bake)
//   6. Assign this material to the Bark slot on Oak_LOD0/1/2 (and the tree
//      prototype/prefab used for terrain painting).
//   7. Import the Knot Mask texture with Wrap Mode = Repeat so the offset
//      sampling tiles cleanly instead of clamping at the edges.

Shader "Custom/OakBarkKnotVariant"
{
    Properties
    {
        _MainTex ("Albedo (base knots already baked in)", 2D) = "white" {}
        _BumpMap("Normal Map", 2D) = "bump" { }
_RoughnessMap("Roughness Map (R channel)", 2D) = "white" { }
_KnotMask("Extra Knot Mask (separate bake)", 2D) = "black" { }
_KnotColor("Extra Knot Tint", Color) = (0.05, 0.03, 0.02, 1)
        _KnotStrength("Extra Knot Strength", Range(0, 1)) = 0.5
        _KnotTiling("Extra Knot Tiling", Float) = 1.0
        _VariationScale("Per-Instance Variation Scale", Float) = 0.15
        _Metallic("Metallic", Range(0, 1)) = 0.0
    }
    SubShader
    {
        Tags { "RenderType"="Opaque" }
        LOD 200

        CGPROGRAM
        #pragma surface surf Standard fullforwardshadows
        #pragma target 3.0

        sampler2D _MainTex;
sampler2D _BumpMap;
sampler2D _RoughnessMap;
sampler2D _KnotMask;
fixed4 _KnotColor;
half _KnotStrength;
float _KnotTiling;
float _VariationScale;
half _Metallic;

struct Input
{
    float2 uv_MainTex;
    float2 uv_BumpMap;
};

// Deterministic hash: same input always gives the same output, so
// a given tree's extra knot pattern stays fixed (doesn't shimmer
// or change frame to frame), but different world positions give
// different-looking results.
float2 Hash2D(float2 p)
{
    p = float2(dot(p, float2(127.1, 311.7)), dot(p, float2(269.5, 183.3)));
    return frac(sin(p) * 43758.5453123);
}

void surf(Input IN, inout SurfaceOutputStandard o)
{
    fixed4 baseColor = tex2D(_MainTex, IN.uv_MainTex);

    // unity_ObjectToWorld's translation column -- this tree
    // instance's world position, correctly unique per instance
    // even under Unity's terrain tree batching/instancing.
    float2 worldSeed = float2(unity_ObjectToWorld[0].w, unity_ObjectToWorld[2].w);
    float2 offset = Hash2D(worldSeed * _VariationScale);

    float2 knotUV = IN.uv_MainTex * _KnotTiling + offset;
    fixed knotMask = tex2D(_KnotMask, knotUV).r * _KnotStrength;

    fixed3 finalAlbedo = lerp(baseColor.rgb, _KnotColor.rgb, knotMask);

    o.Albedo = finalAlbedo;
    o.Normal = UnpackNormal(tex2D(_BumpMap, IN.uv_BumpMap));
    o.Smoothness = 1.0 - tex2D(_RoughnessMap, IN.uv_MainTex).r;
    o.Metallic = _Metallic;
    o.Alpha = 1.0;
}
ENDCG
    }
    FallBack "Diffuse"
}

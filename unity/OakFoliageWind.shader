// OakFoliageWind.shader
// Unity 2017-compatible Surface Shader.
//
// Wind sway for the foliage clumps: each little leaf-clump card sways as a
// whole rigid unit (not individual leaf tips bending -- that would need a
// per-vertex sway-weight baked into vertex colors back in Blender before
// the clumps get randomly rotated/instanced, which the current pipeline
// doesn't produce). Every clump gets its own phase offset derived from
// world position, so clumps on the same tree -- and different trees --
// don't all sway in unison. Since Bark uses a totally separate shader with
// no vertex displacement, the trunk/branches stay rigid automatically just
// by this only being assigned to the Foliage material slot.
//
// SETUP:
//   1. Create a new Material using this shader.
//   2. Albedo    = Oak_Foliage_Albedo_<Season>.png  (RGB + alpha cutout)
//   3. Normal    = Oak_Foliage_Normal_<Season>.png
//   4. Assign to the Foliage slot on Oak_LOD0/1/2 and the tree prototype
//      used for terrain painting.
//   5. Tune _WindStrength for how aggressive the sway looks -- that's the
//      main "how windy does this feel" knob.

Shader "Custom/OakFoliageWind"
{
    Properties
    {
        _MainTex ("Albedo (RGB) Alpha (A)", 2D) = "white" {}
        _BumpMap("Normal Map", 2D) = "bump" { }
_Cutoff("Alpha Cutoff", Range(0, 1)) = 0.5
        _Metallic("Metallic", Range(0, 1)) = 0.0
        _Smoothness("Smoothness", Range(0, 1)) = 0.2

        [Header(Wind)]
        _WindStrength("Wind Strength (Aggressiveness)", Range(0, 2)) = 0.3
        _WindSpeed("Wind Speed", Range(0, 10)) = 1.5
        _WindDirection("Wind Direction (World X, Z)", Vector) = (1, 0, 0, 0)
        _WindTurbulence("Turbulence (secondary faster flutter)", Range(0, 2)) = 0.5
        _WindVariationScale("Per-Clump Phase Variation", Float) = 0.2
    }
    SubShader
    {
        Tags { "RenderType"="TransparentCutout" "Queue"="AlphaTest" }
        LOD 200
        Cull Off   // clumps are thin crossed cards -- need both faces visible

        CGPROGRAM
        #pragma surface surf Standard vertex:vert alphatest:_Cutoff addshadow
        #pragma target 3.0

        sampler2D _MainTex;
sampler2D _BumpMap;
half _Metallic;
half _Smoothness;

half _WindStrength;
half _WindSpeed;
float4 _WindDirection;
half _WindTurbulence;
float _WindVariationScale;

struct Input
{
    float2 uv_MainTex;
    float2 uv_BumpMap;
};

float Hash1(float2 p)
{
    return frac(sin(dot(p, float2(127.1, 311.7))) * 43758.5453123);
}

void vert(inout appdata_full v)
{
    float3 worldPos = mul(unity_ObjectToWorld, v.vertex).xyz;

    // Per-INSTANCE phase (this whole tree) plus a per-VERTEX phase
    // (varies by position within the tree, so individual clumps on
    // the same tree land on different points of the sway cycle
    // instead of all moving as one rigid block) -- both derived
    // purely from world position, so no vertex colors or extra
    // per-instance data are needed.
    float2 instanceSeed = float2(unity_ObjectToWorld[0].w, unity_ObjectToWorld[2].w);
    float instancePhase = Hash1(instanceSeed) * 6.2831853;
    float vertexPhase = Hash1(worldPos.xz * _WindVariationScale) * 6.2831853;
    float phase = instancePhase + vertexPhase;

    float mainSway = sin(_Time.y * _WindSpeed + phase) * _WindStrength;
    float turbulence = sin(_Time.y * _WindSpeed * 3.7 + phase * 2.3)
                        * _WindStrength * _WindTurbulence * 0.35;
    float totalSway = mainSway + turbulence;

    float3 windDirWorld = normalize(float3(_WindDirection.x, 0.0, _WindDirection.y) + 1e-5);
    float3 windDirObject = mul((float3x3)unity_WorldToObject, windDirWorld);

    v.vertex.xyz += windDirObject * totalSway;
}

void surf(Input IN, inout SurfaceOutputStandard o)
{
    fixed4 c = tex2D(_MainTex, IN.uv_MainTex);
    o.Albedo = c.rgb;
    o.Alpha = c.a;
    o.Normal = UnpackNormal(tex2D(_BumpMap, IN.uv_BumpMap));
    o.Metallic = _Metallic;
    o.Smoothness = _Smoothness;
}
ENDCG
    }
    FallBack "Transparent/Cutout/Diffuse"
}

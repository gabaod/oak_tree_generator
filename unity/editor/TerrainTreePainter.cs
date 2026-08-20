// TerrainTreePainter.cs
// Unity 2017-compatible Editor tool.
//
// IMPORTANT: this script must live inside a folder literally named "Editor"
// somewhere under Assets (e.g. Assets/Editor/TerrainTreePainter.cs), or Unity
// will try to compile it into the runtime build and fail, since it uses
// UnityEditor APIs.
//
// Open it via the menu: Tools > Terrain > Tree Painter (Min Spacing)
//
// This mimics Unity's built-in "Paint Trees" terrain tool (Brush Size,
// Density) but adds a Min Spacing slider (2-20m) that's actually enforced:
// while you hold the mouse button and drag across the terrain, each brush
// "stamp" tries several random points inside the brush circle and only
// places a tree if it's at least Min Spacing away from every other tree
// already on the terrain (including ones placed earlier in the same
// stroke) -- unlike the built-in tool, which has no such control and
// clusters trees together while dragging.
//
// Written for C# 4.0 compatibility: no string interpolation, no
// expression-bodied members, no null-conditional operators, no LINQ.

using UnityEngine;
using UnityEditor;
using System.Collections.Generic;

public class TerrainTreePainter : EditorWindow
{
    private Terrain targetTerrain;
    private int prototypeIndex = 0;

    private float brushSize = 8f;      // matches the "Brush Size" feel of the built-in tool
    private float density = 0.5f;      // 0..1, matches the "Density" feel of the built-in tool
    private float minSpacing = 4f;     // the actual new control: 2-20m enforced minimum distance

    private float minScale = 0.9f;
    private float maxScale = 1.1f;
    private bool randomRotation = true;

    private bool isPainting = false;
    private Vector3 lastPaintPos;

    [MenuItem("Tools/Terrain/Tree Painter (Min Spacing)")]
    public static void ShowWindow()
    {
        TerrainTreePainter window = GetWindow<TerrainTreePainter>("Tree Painter");
        window.minSize = new Vector2(280f, 380f);
    }

    private void OnEnable()
    {
        if (targetTerrain == null && Terrain.activeTerrain != null)
        {
            targetTerrain = Terrain.activeTerrain;
        }
        SceneView.onSceneGUIDelegate += OnSceneGUI;
    }

    private void OnDisable()
    {
        SceneView.onSceneGUIDelegate -= OnSceneGUI;
    }

    private void OnGUI()
    {
        EditorGUILayout.LabelField("Target Terrain", EditorStyles.boldLabel);
        targetTerrain = (Terrain)EditorGUILayout.ObjectField(targetTerrain, typeof(Terrain), true);

        if (targetTerrain == null)
        {
            EditorGUILayout.HelpBox("Assign a Terrain (or select one in the scene) to begin.", MessageType.Info);
            return;
        }

        TerrainData data = targetTerrain.terrainData;
        if (data.treePrototypes == null || data.treePrototypes.Length == 0)
        {
            EditorGUILayout.HelpBox(
                "This terrain has no Tree Prototypes yet. Add your tree prefab under " +
                "the Terrain's Paint Trees tab (Edit Trees > Add Tree) first -- this " +
                "tool paints using prototypes already registered on the terrain, same " +
                "as the built-in tool.", MessageType.Warning);
            return;
        }

        string[] names = new string[data.treePrototypes.Length];
        for (int i = 0; i < names.Length; i++)
        {
            GameObject prefab = data.treePrototypes[i].prefab;
            names[i] = (prefab != null) ? prefab.name : ("Prototype " + i);
        }
        prototypeIndex = Mathf.Clamp(prototypeIndex, 0, names.Length - 1);
        prototypeIndex = EditorGUILayout.Popup("Tree Prototype", prototypeIndex, names);

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("Brush", EditorStyles.boldLabel);
        brushSize = EditorGUILayout.Slider("Brush Size", brushSize, 1f, 60f);
        density = EditorGUILayout.Slider("Density", density, 0f, 1f);

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("Spacing", EditorStyles.boldLabel);
        minSpacing = EditorGUILayout.Slider("Min Spacing (m)", minSpacing, 2f, 20f);
        EditorGUILayout.HelpBox(
            "No tree will be placed closer than this to any other tree, even while " +
            "dragging fast. This is the control the built-in Paint Trees tool doesn't have.",
            MessageType.None);

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("Variation", EditorStyles.boldLabel);
        EditorGUILayout.MinMaxSlider("Scale Range", ref minScale, ref maxScale, 0.5f, 2f);
        EditorGUILayout.LabelField("  " + minScale.ToString("F2") + " - " + maxScale.ToString("F2"));
        randomRotation = EditorGUILayout.Toggle("Random Y Rotation", randomRotation);

        EditorGUILayout.Space();
        EditorGUILayout.HelpBox(
            "Hold Left Mouse Button and drag over the terrain in the Scene view to paint. " +
            "Release to stop.", MessageType.Info);

        EditorGUILayout.Space();
        if (GUILayout.Button("Remove All Trees On This Terrain"))
        {
            if (EditorUtility.DisplayDialog("Remove All Trees",
                "This will remove every tree instance currently on " + targetTerrain.name + ". Continue?",
                "Remove All", "Cancel"))
            {
                Undo.RegisterCompleteObjectUndo(data, "Clear Trees");
                data.treeInstances = new TreeInstance[0];
                EditorUtility.SetDirty(data);
            }
        }
    }

    private void OnSceneGUI(SceneView sceneView)
    {
        if (targetTerrain == null)
        {
            return;
        }

        // Claims the mouse for this tool instead of letting Scene view treat
        // a plain left-drag as a selection rectangle.
        int controlID = GUIUtility.GetControlID(FocusType.Passive);
        HandleUtility.AddDefaultControl(controlID);

        Event e = Event.current;
        Ray ray = HandleUtility.GUIPointToWorldRay(e.mousePosition);
        RaycastHit hit;
        bool hitTerrain = Physics.Raycast(ray, out hit, 10000f);

        if (hitTerrain && hit.collider.gameObject != targetTerrain.gameObject)
        {
            hitTerrain = false;
        }

        if (hitTerrain)
        {
            Handles.color = new Color(0.1f, 1f, 0.3f, 0.9f);
            Handles.DrawWireDisc(hit.point, Vector3.up, brushSize);
            Handles.color = new Color(1f, 0.55f, 0f, 0.6f);
            Handles.DrawWireDisc(hit.point, Vector3.up, minSpacing);
            sceneView.Repaint();
        }

        if (e.type == EventType.MouseDown && e.button == 0 && !e.alt && hitTerrain)
        {
            isPainting = true;
            lastPaintPos = hit.point - new Vector3(minSpacing * 2f, 0f, 0f); // force first stamp
            PaintAt(hit.point);
            e.Use();
        }
        else if (e.type == EventType.MouseDrag && e.button == 0 && !e.alt && isPainting && hitTerrain)
        {
            float movedSinceLastStamp = Vector3.Distance(
                new Vector3(hit.point.x, 0f, hit.point.z),
                new Vector3(lastPaintPos.x, 0f, lastPaintPos.z));

            // throttles stamps while dragging fast -- without this, a fast
            // drag would call PaintAt() dozens of times per second
            if (movedSinceLastStamp >= brushSize * 0.35f)
            {
                PaintAt(hit.point);
            }
            e.Use();
        }
        else if (e.type == EventType.MouseUp && e.button == 0)
        {
            isPainting = false;
        }
    }

    private void PaintAt(Vector3 worldCenter)
    {
        lastPaintPos = worldCenter;

        TerrainData data = targetTerrain.terrainData;
        List<TreeInstance> instances = new List<TreeInstance>(data.treeInstances);

        // density (0..1) controls how many candidate points this stamp
        // tries -- higher density = more attempts = more likely to fill
        // the brush circle up to what min spacing allows.
        int attempts = Mathf.Max(1, Mathf.RoundToInt(density * 14f));
        int placedThisStamp = 0;

        for (int i = 0; i < attempts; i++)
        {
            Vector2 randomOffset = Random.insideUnitCircle * brushSize;
            Vector3 candidate = new Vector3(
                worldCenter.x + randomOffset.x,
                0f,
                worldCenter.z + randomOffset.y);
            candidate.y = targetTerrain.SampleHeight(candidate) + targetTerrain.transform.position.y;

            if (IsFarEnoughFromAllTrees(candidate, instances))
            {
                TreeInstance instance = new TreeInstance();
                instance.position = WorldToNormalized(candidate);
                instance.prototypeIndex = prototypeIndex;
                float scale = Random.Range(minScale, maxScale);
                instance.widthScale = scale;
                instance.heightScale = scale;
                instance.rotation = randomRotation ? Random.Range(0f, Mathf.PI * 2f) : 0f;
                instance.color = Color.white;
                instance.lightmapColor = Color.white;

                instances.Add(instance);
                placedThisStamp++;
            }
        }

        if (placedThisStamp > 0)
        {
            Undo.RegisterCompleteObjectUndo(data, "Paint Trees");
            data.treeInstances = instances.ToArray();
            EditorUtility.SetDirty(data);
        }
    }

    private bool IsFarEnoughFromAllTrees(Vector3 candidateWorld, List<TreeInstance> instances)
    {
        // O(n) check against every existing tree. Fine for interactive
        // painting at typical tree counts (thousands); if you're painting
        // tens of thousands of trees on one terrain and it starts to feel
        // laggy while dragging, that's the spot to optimize with a spatial
        // grid/hash instead of a flat list scan.
        Vector3 terrainPos = targetTerrain.transform.position;
        Vector3 size = targetTerrain.terrainData.size;
        Vector3 candidateFlat = new Vector3(candidateWorld.x, 0f, candidateWorld.z);

        for (int i = 0; i < instances.Count; i++)
        {
            Vector3 existingWorld = new Vector3(
                terrainPos.x + instances[i].position.x * size.x,
                0f,
                terrainPos.z + instances[i].position.z * size.z);

            if (Vector3.Distance(existingWorld, candidateFlat) < minSpacing)
            {
                return false;
            }
        }
        return true;
    }

    private Vector3 WorldToNormalized(Vector3 worldPos)
    {
        Vector3 terrainPos = targetTerrain.transform.position;
        Vector3 size = targetTerrain.terrainData.size;
        float nx = (worldPos.x - terrainPos.x) / size.x;
        float nz = (worldPos.z - terrainPos.z) / size.z;
        float ny = (worldPos.y - terrainPos.y) / size.y;
        return new Vector3(Mathf.Clamp01(nx), Mathf.Max(0f, ny), Mathf.Clamp01(nz));
    }
}

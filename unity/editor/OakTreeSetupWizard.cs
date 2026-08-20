// OakTreeSetupWizard.cs
// Unity 2017-compatible Editor tool. NOT a MonoBehaviour -- this is a pure
// editor-time setup utility (copy files, create materials, build a prefab,
// register a terrain tree prototype). None of that needs to live on a
// GameObject or run per-frame, so an EditorWindow is the correct tool here
// rather than a MonoBehaviour.
//
// IMPORTANT: this script must live inside a folder literally named "Editor"
// somewhere under Assets (e.g. Assets/Editor/OakTreeSetupWizard.cs).
//
// Open it via: Tools > Terrain > Oak Tree Setup Wizard
//
// WHAT IT DOES, given a source folder (wherever tree_gen.py / tree_shade.py
// wrote Oak.fbx and the baked PNGs) and a chosen season:
//   1. Copies Oak.fbx + the season's textures + the knot mask + the
//      billboard texture into your Assets folder and imports them.
//   2. Creates three materials:
//        oak_knot     -> Custom/OakBarkKnotVariant (bark + knot variation)
//        wind_shader  -> Custom/OakFoliageWind (foliage + wind sway)
//        oak_billboard-> Standard shader, Rendering Mode = Cutout
//   3. Instantiates Oak.fbx into the scene hierarchy.
//   4. Assigns oak_knot + wind_shader (Elements 0/1) to Oak_LOD0/1/2, and
//      oak_billboard (Element 0) to Oak_LOD3.
//   5. Saves the whole thing as a new prefab asset.
//   6. Adds that prefab as a Tree Prototype on the Terrain you pick, so
//      Paint Trees (or the custom TerrainTreePainter tool) can use it
//      immediately.
//
// NOTE: there is only ONE billboard texture (Oak_Billboard.png), reused on
// both crossed quads -- an earlier design used separate Front/Side images,
// but that caused a double-trunk artifact and was replaced with a single
// shared image, which is what this wizard expects.
//
// Written for C# 4.0 compatibility: no string interpolation, no
// expression-bodied members, no null-conditional operators.

using UnityEngine;
using UnityEditor;
using System.IO;
using System.Collections.Generic;

public class OakTreeSetupWizard : EditorWindow
{
    private enum Season { Spring, Summer, Fall }

    private string sourceFolder = "";
    private string importFolder = "Assets/OakTree";
    private Season season = Season.Summer;
    private Terrain targetTerrain;

    [MenuItem("Tools/Terrain/Oak Tree Setup Wizard")]
    public static void ShowWindow()
    {
        OakTreeSetupWizard window = GetWindow<OakTreeSetupWizard>("Oak Tree Setup");
        window.minSize = new Vector2(360f, 260f);
    }

    private void OnEnable()
    {
        if (targetTerrain == null && Terrain.activeTerrain != null)
        {
            targetTerrain = Terrain.activeTerrain;
        }
    }

    private void OnGUI()
    {
        EditorGUILayout.LabelField("1. Source Folder", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "The folder where tree_gen.py / tree_shade.py saved Oak.fbx and the " +
            "baked PNGs (usually next to the .blend file).", MessageType.None);
        EditorGUILayout.BeginHorizontal();
        EditorGUILayout.TextField(sourceFolder);
        if (GUILayout.Button("Browse...", GUILayout.Width(80f)))
        {
            string picked = EditorUtility.OpenFolderPanel("Select Blender output folder", sourceFolder, "");
            if (!string.IsNullOrEmpty(picked))
            {
                sourceFolder = picked;
            }
        }
        EditorGUILayout.EndHorizontal();

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("2. Import Into", EditorStyles.boldLabel);
        importFolder = EditorGUILayout.TextField("Assets Folder", importFolder);

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("3. Season", EditorStyles.boldLabel);
        season = (Season)EditorGUILayout.EnumPopup("Season", season);

        EditorGUILayout.Space();
        EditorGUILayout.LabelField("4. Terrain", EditorStyles.boldLabel);
        targetTerrain = (Terrain)EditorGUILayout.ObjectField(
            "Target Terrain", targetTerrain, typeof(Terrain), true);

        EditorGUILayout.Space();
        EditorGUILayout.Space();

        GUI.enabled = !string.IsNullOrEmpty(sourceFolder) && targetTerrain != null;
        if (GUILayout.Button("Generate & Setup", GUILayout.Height(36f)))
        {
            RunSetup();
        }
        GUI.enabled = true;

        if (targetTerrain == null)
        {
            EditorGUILayout.HelpBox("Assign a Terrain to register the finished tree on.", MessageType.Warning);
        }
    }

    private void RunSetup()
    {
        string seasonLabel = season.ToString(); // "Spring" / "Summer" / "Fall" -- matches tree_shade.py's filenames exactly

        // --- required source files ---
        Dictionary<string, string> required = new Dictionary<string, string>();
        required.Add("fbx", "Oak.fbx");
        required.Add("barkAlbedo", "Oak_Bark_Albedo_" + seasonLabel + ".png");
        required.Add("barkNormal", "Oak_Bark_Normal_" + seasonLabel + ".png");
        required.Add("barkRough", "Oak_Bark_Roughness_" + seasonLabel + ".png");
        required.Add("knotMask", "Oak_Bark_KnotMask.png");
        required.Add("foliageAlbedo", "Oak_Foliage_Albedo_" + seasonLabel + ".png");
        required.Add("foliageNormal", "Oak_Foliage_Normal_" + seasonLabel + ".png");
        required.Add("billboard", "Oak_Billboard.png");

        List<string> missing = new List<string>();
        foreach (KeyValuePair<string, string> kv in required)
        {
            string fullPath = Path.Combine(sourceFolder, kv.Value);
            if (!File.Exists(fullPath))
            {
                missing.Add(kv.Value);
            }
        }
        if (missing.Count > 0)
        {
            EditorUtility.DisplayDialog("Missing Files",
                "Couldn't find these files in the source folder:\n\n" + string.Join("\n", missing.ToArray()) +
                "\n\nMake sure tree_gen.py and tree_shade.py have both been run for the '" + seasonLabel +
                "' season, and that the source folder points at their output.", "OK");
            return;
        }

        Shader knotShader = Shader.Find("Custom/OakBarkKnotVariant");
        Shader windShader = Shader.Find("Custom/OakFoliageWind");
        if (knotShader == null || windShader == null)
        {
            EditorUtility.DisplayDialog("Missing Shaders",
                "Couldn't find Custom/OakBarkKnotVariant and/or Custom/OakFoliageWind. " +
                "Make sure OakBarkKnotVariant.shader and OakFoliageWind.shader are already " +
                "somewhere in your project's Assets folder.", "OK");
            return;
        }

        EnsureFolder(importFolder);

        // --- copy files into the project and import them ---
        Dictionary<string, string> assetPaths = new Dictionary<string, string>();
        foreach (KeyValuePair<string, string> kv in required)
        {
            string srcPath = Path.Combine(sourceFolder, kv.Value);
            string destPath = importFolder + "/" + kv.Value;
            File.Copy(srcPath, GetSystemPath(destPath), true);
            assetPaths.Add(kv.Key, destPath);
        }
        AssetDatabase.Refresh();

        // --- fix texture import settings (normal maps, non-color data) ---
        ConfigureTexture(assetPaths["barkAlbedo"], false, false);
        ConfigureTexture(assetPaths["barkNormal"], true, true);
        ConfigureTexture(assetPaths["barkRough"], false, true);
        ConfigureTexture(assetPaths["knotMask"], false, true);
        ConfigureTexture(assetPaths["foliageAlbedo"], false, false);
        ConfigureTexture(assetPaths["foliageNormal"], true, true);
        ConfigureTexture(assetPaths["billboard"], false, false);
        AssetDatabase.Refresh();

        Texture2D barkAlbedo = AssetDatabase.LoadAssetAtPath<Texture2D>(assetPaths["barkAlbedo"]);
        Texture2D barkNormal = AssetDatabase.LoadAssetAtPath<Texture2D>(assetPaths["barkNormal"]);
        Texture2D barkRough = AssetDatabase.LoadAssetAtPath<Texture2D>(assetPaths["barkRough"]);
        Texture2D knotMask = AssetDatabase.LoadAssetAtPath<Texture2D>(assetPaths["knotMask"]);
        Texture2D foliageAlbedo = AssetDatabase.LoadAssetAtPath<Texture2D>(assetPaths["foliageAlbedo"]);
        Texture2D foliageNormal = AssetDatabase.LoadAssetAtPath<Texture2D>(assetPaths["foliageNormal"]);
        Texture2D billboardTex = AssetDatabase.LoadAssetAtPath<Texture2D>(assetPaths["billboard"]);
        GameObject fbxAsset = AssetDatabase.LoadAssetAtPath<GameObject>(assetPaths["fbx"]);

        if (fbxAsset == null)
        {
            EditorUtility.DisplayDialog("Import Failed",
                "Oak.fbx was copied but Unity couldn't load it as a GameObject. " +
                "Check the Console for import errors.", "OK");
            return;
        }

        // --- material 1: oak_knot (bark + knot variation) ---
        Material oakKnot = new Material(knotShader);
        oakKnot.SetTexture("_MainTex", barkAlbedo);
        oakKnot.SetTexture("_BumpMap", barkNormal);
        oakKnot.SetTexture("_RoughnessMap", barkRough);
        oakKnot.SetTexture("_KnotMask", knotMask);
        AssetDatabase.CreateAsset(oakKnot, importFolder + "/oak_knot.mat");

        // --- material 2: wind_shader (foliage + wind sway) ---
        Material windMat = new Material(windShader);
        windMat.SetTexture("_MainTex", foliageAlbedo);
        windMat.SetTexture("_BumpMap", foliageNormal);
        AssetDatabase.CreateAsset(windMat, importFolder + "/wind_shader.mat");

        // --- material 3: oak_billboard (Standard shader, Cutout mode) ---
        Material billboardMat = new Material(Shader.Find("Standard"));
        SetStandardShaderCutout(billboardMat);
        billboardMat.SetTexture("_MainTex", billboardTex);
        AssetDatabase.CreateAsset(billboardMat, importFolder + "/oak_billboard.mat");

        AssetDatabase.SaveAssets();

        // --- instantiate the fbx and assign materials per LOD ---
        GameObject instance = (GameObject)PrefabUtility.InstantiatePrefab(fbxAsset);
        if (instance == null)
        {
            EditorUtility.DisplayDialog("Instantiate Failed",
                "Couldn't instantiate Oak.fbx into the scene.", "OK");
            return;
        }

        AssignMaterials(instance, "Oak_LOD0", new Material[] { oakKnot, windMat });
        AssignMaterials(instance, "Oak_LOD1", new Material[] { oakKnot, windMat });
        AssignMaterials(instance, "Oak_LOD2", new Material[] { oakKnot, windMat });
        AssignMaterials(instance, "Oak_LOD3", new Material[] { billboardMat });

        // --- save the configured instance as a new prefab asset ---
        string prefabPath = importFolder + "/Oak_Prefab.prefab";
#if UNITY_2018_3_OR_NEWER
        GameObject prefabAsset = PrefabUtility.SaveAsPrefabAsset(instance, prefabPath);
#else
        GameObject prefabAsset = PrefabUtility.CreatePrefab(prefabPath, instance, ReplacePrefabOptions.ReplaceNameBased);
#endif
        if (prefabAsset == null)
        {
            EditorUtility.DisplayDialog("Prefab Save Failed",
                "Couldn't save the configured tree as a prefab asset.", "OK");
            return;
        }

        // --- register as a tree prototype on the target terrain ---
        TerrainData terrainData = targetTerrain.terrainData;
        List<TreePrototype> prototypes = new List<TreePrototype>(terrainData.treePrototypes);
        TreePrototype newPrototype = new TreePrototype();
        newPrototype.prefab = prefabAsset;
        newPrototype.bendFactor = 0f; // wind sway is handled by our own shader, not Unity's built-in tree bending
        prototypes.Add(newPrototype);
        terrainData.treePrototypes = prototypes.ToArray();
        terrainData.RefreshPrototypes();
        EditorUtility.SetDirty(terrainData);
        AssetDatabase.SaveAssets();

        Debug.Log("Oak Tree Setup complete: '" + seasonLabel + "' season, materials in " +
            importFolder + ", prefab registered on terrain '" + targetTerrain.name +
            "'. Ready for Paint Trees.");

        EditorUtility.DisplayDialog("Done",
            "Oak tree set up for " + seasonLabel + " and added to " + targetTerrain.name +
            "'s tree prototypes. Open Paint Trees (or the custom Tree Painter tool) and it's ready to use.",
            "Great");
    }

    private void AssignMaterials(GameObject root, string childName, Material[] mats)
    {
        Transform child = root.transform.Find(childName);
        if (child == null)
        {
            Debug.LogWarning(childName + " was not found under " + root.name +
                " -- skipping material assignment for it.");
            return;
        }
        Renderer renderer = child.GetComponent<Renderer>();
        if (renderer == null)
        {
            Debug.LogWarning(childName + " has no Renderer component -- skipping.");
            return;
        }
        renderer.sharedMaterials = mats;
    }

    private void SetStandardShaderCutout(Material mat)
    {
        // Mirrors what Unity's own Standard Shader GUI does when you switch
        // Rendering Mode to Cutout in the Inspector -- setting the texture
        // alone isn't enough, these keywords/blend states are required too.
        mat.SetFloat("_Mode", 1f); // 1 = Cutout
        mat.SetFloat("_Cutoff", 0.5f);
        mat.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.One);
        mat.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.Zero);
        mat.SetInt("_ZWrite", 1);
        mat.DisableKeyword("_ALPHABLEND_ON");
        mat.EnableKeyword("_ALPHATEST_ON");
        mat.DisableKeyword("_ALPHAPREMULTIPLY_ON");
        mat.renderQueue = (int)UnityEngine.Rendering.RenderQueue.AlphaTest;
    }

    private void ConfigureTexture(string assetPath, bool isNormalMap, bool isLinearData)
    {
        TextureImporter importer = AssetImporter.GetAtPath(assetPath) as TextureImporter;
        if (importer == null)
        {
            return;
        }
        if (isNormalMap)
        {
            importer.textureType = TextureImporterType.NormalMap;
        }
        else
        {
            importer.textureType = TextureImporterType.Default;
            importer.sRGBTexture = !isLinearData;
        }
        importer.SaveAndReimport();
    }

    private void EnsureFolder(string assetFolderPath)
    {
        if (AssetDatabase.IsValidFolder(assetFolderPath))
        {
            return;
        }
        string parent = Path.GetDirectoryName(assetFolderPath);
        if (string.IsNullOrEmpty(parent))
        {
            parent = "Assets";
        }
        parent = parent.Replace("\\", "/");
        string folderName = Path.GetFileName(assetFolderPath);

        if (!AssetDatabase.IsValidFolder(parent))
        {
            EnsureFolder(parent);
        }
        AssetDatabase.CreateFolder(parent, folderName);
    }

    private string GetSystemPath(string assetPath)
    {
        // Converts an "Assets/..." path into a full OS path for File.Copy.
        string projectRoot = Application.dataPath.Substring(0, Application.dataPath.Length - "Assets".Length);
        return Path.Combine(projectRoot, assetPath);
    }
}

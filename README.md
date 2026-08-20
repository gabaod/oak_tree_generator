# oak_tree_generator
Creates LOD0-3 versions of an random generated oak tree with materials, sway and knot placement shaders in unity and an auto setup for unity and a tree painter with minimum spacing<br><br>

Blender Setup Usage:<br><br>
1. open up blender and create a new save file as this will determine where all the files will be exported to and your file must be saved first before running this.<br>
2. goto Window -> Toggle System Console so you can see the progression of the scripts as you run them as the second script will take some time baking and exporting<br>
3. In the scripting tab in blender, click the icon for new text up top and paste the contents of oak_tree_gen.py into the window, feel free and rename the file up top from Text.001 etc<br>
4. In the main scripting window under # CONFIG -- tune these  feel free and change the settings, the LOD params is what will make sure its near your max amount of triangles total<br>
  defaults seem to work well for descenders<br>
5. Click the play/run script icon up top and it will generate a tree for you.  If you dont like how it looks reclick the play button over and over until satisfied.<br>
6. Now click the new text up top as you did before and paste the contents of oak_tree_shader.py<br>
7. Under # CONFIG change season to be 0.0 for spring, 1.0 for summer or 2.0 for fall.
8. Click the play icon on this script and it will bake all your pngs and export them for you *NOTE this will take some time as its processing the pngs and you can view status in console window<br>

Unity Setup Usage:<br><br>
1. Add the shader files to your assets in unity<br>
2. Add the contents in editor/ to assets/editor in unity<br>
3. At the top goto Tools -> Terrain -> Oak Tree Setup Wizard<br>
4. Browse to your path that your blender exported everything to, choose the season and assign your terrain and click Generate<br>
5. Now you can paint the trees like normal in blender or use the second script under Tools -> Terrain -> Tree Painter<br>
6. Feel free and change the settings of the materials it created for you to adjust the knot placement and wind settings for the sway.

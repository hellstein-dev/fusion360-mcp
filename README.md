# fusion-mcp

MCP server that lets LLM agents (Claude Desktop, Claude Code, any MCP client) build
models in Autodesk Fusion 360.

It has two parts:

- **FusionMCP add-in** (`fusion_addin/`) runs inside Fusion. It listens on
  `127.0.0.1` only, requires a random token and runs every request on Fusion's main thread.
- **MCP server** (`fusion-mcp`, `src/fusion_mcp/`) is started by the MCP client over
  stdio and forwards tool calls to the add-in.

The add-in publishes its port and token in `~/.fusion-mcp/bridge.json` while it runs.

## Requirements

- Autodesk Fusion 360 on Windows or macOS.
- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11 for the MCP server. The add-in uses
  Fusion's bundled Python and needs no extra packages.

## Install the add-in

Step-by-step guide for any machine, including manual install and troubleshooting:
[INSTALL.md](INSTALL.md).

From this directory (`mcp-plugin/`):

```sh
uv run fusion-mcp install-addin
```

This links `fusion_addin/` into Fusion's AddIns folder as `FusionMCP`:

- Windows: `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\FusionMCP` (a junction)
- macOS: `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/FusionMCP`
  (a symlink)

If links cannot be created on your machine, use `uv run fusion-mcp install-addin --copy`.
It copies the add-in and bundles the protocol package into `FusionMCP/_vendor/`. Re-run it
after every update.

The installer never touches a `FusionMCP` folder it did not create. To remove the add-in,
run `uv run fusion-mcp uninstall-addin`.

Restart Fusion. The add-in starts automatically. Its button is in the **Utilities → Add-ins**
panel and shows the state: `FusionMCP: Running :7432` or `FusionMCP: Stopped`. Click it to
stop or start the bridge. The default port is 7432; if it is busy, the add-in picks a free
port and writes it to the discovery file.

## Connect an MCP client

Print a ready config:

```sh
uv run fusion-mcp print-config                       # Claude Desktop
uv run fusion-mcp print-config --client claude-code  # Claude Code (.mcp.json)
```

The Claude Desktop output looks like this; merge it into `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "fusion360": {
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/to/mcp-plugin", "fusion-mcp"]
    }
  }
}
```

For Claude Code, put the `--client claude-code` output into `.mcp.json` of your project,
or run `claude mcp add fusion360 -- uv run --directory /abs/path/to/mcp-plugin fusion-mcp`.

## Check the connection

Ask the agent to call **`fusion_status`**. With Fusion and the add-in running it returns
the Fusion version, the add-in version, Fusion's Python version, the active document, the
design type and the document units. Otherwise the error starts with `NOT_CONNECTED` and
says what to start. The MCP server re-reads the discovery file on every reconnect, so
restarting Fusion or the add-in does not require restarting the client.

## Tools

| Tool | What it does |
|------|--------------|
| `fusion_status` | Connection check, versions, active document, design type, units |
| `execute_python` | Runs Python inside Fusion (`adsk`, `app`, `ui`, `design`, `root`, `params` in scope) and returns `result`, `stdout`, `stderr` |

Units: tool inputs are millimetres and degrees, and strings are Fusion expressions
(`"10 mm"`, `"width / 2"`). The Fusion API inside `execute_python` works in centimetres
and radians.

Errors come back as tool results with `isError = true` and the text
`"<CODE>: <message>"` plus JSON details (for example a Fusion traceback). This lets the
agent see what went wrong and fix it.

## Sketches

The `sketch_*` tools (`sketch_create`, `sketch_list`, `sketch_get`, `sketch_profiles`,
`sketch_draw`, `sketch_constrain`, `sketch_dimension`, `sketch_dimension_set`,
`sketch_set_construction`, `sketch_delete`, `sketch_transform`, `sketch_offset`,
`sketch_trim`, `sketch_extend`, `sketch_fillet`, `sketch_mirror`, `sketch_pattern`,
`sketch_project`) build fully constrained, parametric 2D sketches. A typical sketch takes
`sketch_create` plus one `sketch_draw` with geometry, constraints and dimensions together.

- **Coordinates are sketch space, mm.** Points are `[x, y]` on the sketch plane (numbers
  only; the `units` parameter of `sketch_draw` can switch to cm/m/in/ft). Fusion picks the
  sketch axes: `sketch_create` and `sketch_get` return `transform = {origin, x_axis,
  y_axis, normal}` (model mm, unit vectors), and model = origin + x·x_axis + y·y_axis.
  On `origin:XY` sketch x/y are model x/y, but on **`origin:XZ` the sketch Y axis is model
  −Z**: a point drawn at y = 10 ends up at z = −10. Read `transform`, or give points in
  model space as `{"model": [x, y, z]}` (projected onto the sketch plane; an off-plane
  point adds a warning).
- **Geometry is approximate, dimensions are exact.** Expressions are refused in
  coordinates (`["w/2", 0]` fails with `INVALID_PARAMS`). Exact and parametric sizes come
  from dimensions: a number is mm (degrees for angles), a string is a Fusion expression
  (`"plate_w * 0.6"`). `parameter_name` names a dimension's parameter, so later dimensions
  and `parameter_update` can use it.
- **Local ids live for one call.** Inside a `sketch_draw` call an item's `id`, and sub-paths
  such as `r.bottom`, `r.center`, `l1.end` or `slot.c0`, can be used by later items,
  constraints and dimensions. `origin` is the sketch origin point. The result `refs` maps
  every local path to a real entity ref. Use those refs in later calls, because the local
  ids are gone after the call.
- **Profile refs.** Fusion profiles have no persistent id, and their order changes whenever
  the sketch changes. A profile is therefore addressed by its sketch and a point inside the
  region: `profile:<sketch token>@<x>,<y>` (sketch mm). Mutating sketch tools and
  `sketch_profiles` return each profile with `ref`, interior `point`, `area_mm2`,
  `centroid`, `bbox` and its outer and inner loops. Pass the ref string to features as the
  profile. It stays valid through moderate dimension changes. If the region has moved away
  from the point, the feature fails with `ENTITY_NOT_FOUND` and the current profiles; call
  `sketch_profiles(contains=[x, y])` to get a fresh ref.
- **Atomicity.** Every mutating sketch tool is all or nothing. On failure, everything it
  created in the sketch is deleted and the changed properties (expressions, construction,
  fixed, positions) are restored. The error names `details.phase`, `index` and `id` of the
  failing item. Fusion cannot undo trim, extend and sketch fillet inside a batch, so
  **`sketch_trim`, `sketch_extend` and `sketch_fillet` take one curve or corner per call**.
  They replace curves, so use the refs they return for the next step. The one exception is
  `sketch_delete`: it validates all refs before deleting anything, but if Fusion itself
  fails in the middle of the batch, the entities already deleted stay deleted
  (`FUSION_API_ERROR` with `details.deleted`).
- Changing a sketch that features already use can break later timeline features. Check
  feature health afterwards (`inspect_tree`).

## Solid features

The `feature_*` tools turn sketches into solids and modify bodies, one parametric timeline
feature per call:

| Tool | What it does |
|------|--------------|
| `feature_extrude` | Extrude profiles, planar faces or sketch texts: one side, two sides or symmetric; distance, to an entity or through all; taper, start offset, thin wall |
| `feature_revolve` | Revolve profiles around a sketch line, construction axis or straight edge: full, angle, two angles or to an entity |
| `feature_sweep` | Sweep profiles along sketch curves or edges, with twist, taper, a guide rail and path share |
| `feature_loft` | Loft through profiles, planar faces and end points, with end conditions, rails or a centerline |
| `feature_rib`, `feature_web` | Thin stiffening walls from open sketch curves, joined to the body |
| `feature_hole` | Simple, counterbore or countersink holes at a point, sketch points, offsets from edges or a circular edge's centre; optional `thread` makes a tapped hole in the same call |
| `feature_thread` | Cosmetic or modeled thread on cylindrical faces (hole or shaft is detected) |
| `feature_thread_options` | Read-only lookup of thread types, sizes, designations, classes and a recommendation for a diameter |
| `feature_fillet` | Constant, variable or chord-length fillets on edges |
| `feature_rule_fillet` | Fillet all edges of faces/features, or the edges between two sets |
| `feature_chamfer` | Equal, two-distance or distance-angle chamfers |
| `feature_shell` | Hollow a body, removing faces (open shell) or closed, inside and/or outside |
| `feature_draft` | Tilt faces by a draft angle about a neutral plane |
| `feature_offset_faces` | Press-pull faces by a signed distance |

- **Units.** A plain number is a length in the tool's `units` (default mm; `cm`, `m`, `in`,
  `ft` also work) or an angle in degrees. Shares of a path, weights and counts are plain
  numbers. A string is a Fusion expression (`"wall * 2"`, `"5 deg"`) and keeps the feature
  driven by user parameters.
- **Where refs come from.** Profiles come from `sketch_draw` / `sketch_profiles`
  (`profile:<sketch>@x,y`) or a spec such as `{"sketch": <ref>, "all": true}`. Sketch
  curves and sketch points (paths, rails, rib curves, hole points) come from the `refs` of
  `sketch_draw`. Faces, edges, vertices and bodies come from `inspect_query` /
  `inspect_describe`. Planes, axes and points come from `construction_plane`,
  `construction_axis`, `construction_point` or `origin:XY|XZ|YZ|X|Y|Z|O`. **Face, edge and
  body refs go stale after later features**, so query them again from the latest model
  instead of reusing old ones.
- **Operations.** Extrude, revolve, sweep and loft take `operation` = `new_body` (default),
  `join`, `cut`, `intersect` or `new_component`. `participant_bodies` limits which bodies
  join/cut/intersect act on. Holes always cut, and ribs and webs always join.
- **Result.** Every feature tool returns `{"feature": {ref, type, name}, "health": {"state":
  ok|warning|error|unknown, "message"}, "created_bodies", "modified_bodies",
  "removed_bodies"}`, plus `component` for `new_component`, `holes` (the count) and
  `thread` for `feature_hole`, and `thread` for `feature_thread`. Body geometry is not
  repeated, so measure it with the `inspect_*` tools.
- **Health and rollback.** Bad input (a missing field for the chosen mode, a wrong entity
  kind, an unknown thread designation with `details.available`) fails with
  `INVALID_PARAMS` before anything changes. A stale profile point fails with
  `ENTITY_NOT_FOUND` and the current profiles. If Fusion computes the feature in the error
  state, the tool fails with `FUSION_API_ERROR` (`details.health`, `details.feature_type`)
  and the timeline is rolled back. A tapped hole whose thread fails leaves neither the hole
  nor the thread. A warning keeps the feature and is reported in `health`. A feature the
  running Fusion API lacks (rib, web, rule or chord fillet on an old version) fails with
  `FUSION_API_ERROR` "... not available" and changes nothing, so use `execute_python`.

Example: an open housing with four counterbored holes and one M5 tapped hole:

```text
sketch_create(plane="origin:XY") -> sketch_draw(rectangle 80 x 50) -> profiles[0].ref
feature_extrude(profiles=[<profile>], side1={"type": "distance", "distance": 20})
feature_fillet(edges=<inspect_query edge, line, direction [0,0,1]>, radius=5)
feature_shell(faces=[<inspect_query face, normal [0,0,-1]>], inside_thickness=3)
sketch_create(plane=<top face>) -> sketch_draw(4 points) -> refs
feature_hole(placement={"type": "sketch_points", "points": [...]}, kind="counterbore",
             diameter=5.5, cb_diameter=9, cb_depth=1.5, extent={"type": "through_all"})
feature_hole(placement={"type": "point", "face": <top face>, "point": [0, 0, 20]},
             diameter=4.2, extent={"type": "through_all"}, thread={"designation": "M5x0.8"})
```

## Body operations & patterns

The `pattern_*` and `body_*` tools copy, combine and reshape bodies. Most of them add one
parametric timeline feature per call:

| Tool | What it does |
|------|--------------|
| `pattern_rectangular` | Copy features, bodies, faces or occurrences along one or two directions (quantity, spacing or extent, symmetric) |
| `pattern_circular` | Copy them around an axis, including the axis of a cylindrical or conical face (quantity, total angle, symmetric) |
| `pattern_path` | Copy them along a path of sketch curves or edges (start share, flip, orientation along the path) |
| `pattern_mirror` | Mirror across a planar face, construction plane or origin plane. `combine` joins mirrored bodies with their originals |
| `body_combine` | Join, cut or intersect tool bodies with a target body (keep tools, new component) |
| `body_split` / `body_split_face` | Split bodies, or faces of a body, with a plane, face, body (or sketch curve for faces) |
| `body_move` | Move bodies, or copies of them (`keep_original`): translate, along an entity, rotate, rotate about a free axis, point to point, rigid 4x4 matrix |
| `body_scale` | Scale about a point (default `origin:O`), uniformly or per axis |
| `body_thicken` | Thicken faces of a surface into a solid |
| `body_boundary_fill` | Make solids from the closed cells that bodies, faces and planes enclose |
| `body_delete_faces` / `body_replace_face` | Delete faces and let Fusion heal the body, or replace faces with a face, plane or surface body |
| `body_rename` / `body_set_visibility` / `body_remove` | Rename, show or hide and set opacity, remove (a Remove feature) or delete bodies |

- **Units.** Lengths and the `translate` / `rotate_about` point and the matrix translation
  are in the tool's `units` (default mm). Angles are in degrees. Quantities, the path
  `start` share (0..1) and scale factors are unitless numbers. Strings are Fusion
  expressions, except in `rotate_about` and `matrix`, which take plain numbers.
- **Input refs.** `entities` of a pattern must all be one kind: feature refs from earlier
  `feature_*` results, body or face refs (c03 `inspect_query`), or occurrence refs.
  Fusion patterns one kind at a time, and a mix fails with `details.kinds`. Directions,
  axes, planes and points come from `origin:X|Y|Z|XY|XZ|YZ|O`, from the c06
  `construction_*` tools, or from edges, faces and vertices of `inspect_query`. The
  `compute` option (`adjust`, `optimized`, `identical`) only applies to feature input.
  Boundary fill cells are never picked by index. You pick them with `cell_points`; a point
  in no cell fails, and `details.cells` lists every cell's centre and volume.
- **Result.** Feature tools return the same result as the `feature_*` tools: `feature`,
  `health`, `created_bodies`, `modified_bodies` and `removed_bodies` (tokens of consumed
  bodies). On top of that:
  - occurrence patterns add `created_occurrences`;
  - `body_split_face` adds the new `faces`;
  - `body_move` with `keep_original` adds `copy`.

  Body tokens change after patterns and combines, so take the refs from the latest result.
  `body_rename`, `body_set_visibility` and `body_remove` return the body refs, the
  visibility state, and the removed refs with `mode_used`.
- **Atomicity.** Bad input fails with `INVALID_PARAMS` before anything changes. A feature
  computed in the error state is rolled back. A copy made for a failed `body_move` is
  removed too. `body_set_visibility` restores every body's previous values if Fusion
  refuses one. `body_remove` in a direct design always deletes the bodies, and Fusion
  cannot undo those deletions.

Example: a symmetric part from overlapping blocks:

```text
feature_extrude(<20 x 20 mm square>, 10 mm) -> created_bodies[0] = cube
pattern_rectangular(entities=[cube], direction1="origin:X", quantity1=3, distance1=15,
                    direction2="origin:Y", quantity2=2, distance2=15)
pattern_mirror(entities=<all bodies>, plane="origin:YZ")
body_combine(target=cube, tools=<the other bodies>)
```

## Surfaces

The `surface_*` tools model with surface bodies (sheets without volume). Each call adds one
parametric timeline feature, except `surface_trim_cells`, which only previews:

| Tool | What it does |
|------|--------------|
| `surface_patch` | Fill a closed boundary (sketch profile, sketch curves or edges) with a surface; continuity `connected` / `tangent` / `curvature` to the edge faces |
| `surface_extend` | Extend free edges of a surface body by a distance (`natural`, `tangent`, `perpendicular`) |
| `surface_trim_cells` | List the cells a trim tool cuts the surfaces into (centre, bbox, area in mm); changes nothing |
| `surface_trim` | Trim with a body, face, plane or sketch curve and remove the cells nearest to given points |
| `surface_untrim` | Remove all, external or internal (hole) loops of faces of surface bodies |
| `surface_stitch` | Stitch surfaces along shared edges within a tolerance (default 0.1 mm); reports `is_solid` |
| `surface_unstitch` | Split faces or bodies (solid or surface) into separate surface bodies |
| `surface_ruled` | Ruled surface from edges or sketch curves: `normal`, `tangent` or along a `direction`, with angle and corner type |
| `surface_offset` | New surface bodies at a signed distance from faces (the faces stay; moving faces of a solid is `feature_offset_faces`) |

- **Surface features from sketches.** `feature_extrude`, `feature_revolve`, `feature_sweep`
  and `feature_loft` take `is_solid` (default `true`). With `is_solid=false` they create a
  surface body: only `new_body` / `new_component`, no `thin` for extrude. Extrude and sweep
  then also accept open sketch curves or edges as the profile (one kind, connected curves
  are chained), e.g. an open line extruded into a wall sheet.
- **Trim cells by points.** Cells of a trim are temporary and have no refs. Call
  `surface_trim_cells` first, then pass `surface_trim` a point (in `units`, default mm) on or
  near each cell to remove, such as a cell centre. A point farther than `max_distance` from
  every cell fails with `INVALID_PARAMS`, and `details.cells` lists the cells. The preview
  always cancels its trim input, so nothing stays half-built.
- **Solid or not.** Every body in a surface result has `is_solid`. `surface_stitch` also
  returns the `is_solid` of the stitched body: `false` means gaps remain, so raise
  `tolerance` or patch the openings. To give a surface a thickness, use `body_thicken`
  (c08); to fill a closed set of surfaces with a solid, use `body_boundary_fill`.
- **Refs.** Edges and faces come from `inspect_query`, curves and profiles from `sketch_*`.
  Surface bodies get new tokens after trim, extend and stitch, so take refs from the latest
  result. Bad input fails with `INVALID_PARAMS` before anything changes, and a feature in
  the error state is rolled back.

Example: a closed box from surfaces:

```text
feature_extrude(profiles=[<20 x 20 mm square>], side1={"type": "distance", "distance": 20},
                is_solid=false)                        -> walls
surface_patch(boundary=[<the square profile>])         -> bottom
surface_patch(boundary=[<the 4 top edges of walls>])   -> top
surface_stitch(surfaces=[walls, bottom, top])          -> is_solid = true
```

## Assemblies

The `assembly_*` tools build multi-part designs: components, occurrences, joints and
interference checks.

| Tool | What it does |
|------|--------------|
| `assembly_list` | Components and occurrences (transform, grounded, light bulb, visibility, joint count) |
| `assembly_new_component` / `assembly_component_from_bodies` | Empty component with a new occurrence (under root or a parent occurrence), or a component made from bodies of one component |
| `assembly_activate` / `assembly_rename_component` | Activate an occurrence (or root); rename a component (returns the new occurrence paths) |
| `assembly_add_occurrence` | Insert another occurrence of a component, referenced or as an independent copy |
| `assembly_transform_occurrence` | Move or rotate an occurrence, or place it by origin and axes; the position is captured with a snapshot |
| `assembly_set_occurrence_state` / `assembly_delete` | Ground, show or hide an occurrence; delete occurrences, joints, joint origins, rigid groups |
| `assembly_create_joint_origin` | Joint origin on a point, face, curve or profile, in the component that owns the geometry |
| `assembly_create_joint` / `assembly_create_as_built_joint` | Rigid, revolute, slider, cylindrical, pin-slot, planar or ball joint (in root) |
| `assembly_set_joint_limits` / `assembly_drive_joint` | Joint limits per degree of freedom; move a joint to values (clamped values are reported) |
| `assembly_create_rigid_group` / `assembly_list_joints` | Lock occurrences together; list joints, as-built joints, joint origins and rigid groups |
| `assembly_analyze_interference` | Overlapping pairs of occurrences or bodies with volume and bounding box |

- **Refs.** Components are `component:<name>`, occurrences `occurrence:<fullPathName>`
  (`occurrence:Box:1`, nested `occurrence:Box:1+Hinge:1`). An entity seen through an
  occurrence is `<native ref>@<occurrence path>` (`AbC=@Lid:1`, `origin:Z@Lid:1`); all
  tools return and accept this form. A native ref of a component inserted exactly once is
  coerced to that occurrence; with several occurrences the error lists the candidates.
  Renaming a component changes its occurrence paths, so old refs stop resolving.
- **Units.** Lengths in mm, angles in degrees; joint values and limits use degrees for
  rotation/pitch/yaw/roll and mm for slides.
- **Atomicity.** Created components, occurrences, joints and groups are removed on failure;
  names, grounding, visibility, transforms, joint values, limits and the active component
  are restored.

Example: a lid on a hinge:

```text
assembly_new_component(name="Box"); assembly_new_component(name="Lid")   # + geometry
assembly_set_occurrence_state(occurrence="occurrence:Box:1", grounded=true)
inspect_query(kind="edge", scope=["occurrence:Box:1"], direction=[1, 0, 0], ...) -> E2=@Box:1
assembly_create_joint(type="revolute", geometry_one={"kind": "curve", "entity": "E1=@Lid:1"},
                      geometry_two={"kind": "curve", "entity": "E2=@Box:1"},
                      motion={"axis": "E2=@Box:1"})
assembly_drive_joint(joint=<joint ref>, values={"rotation": 90})
assembly_analyze_interference(items=["occurrence:Box:1", "occurrence:Lid:1"])
```

## Материалы, экспорт и импорт

Materials, appearances and file exchange:

| Tool | What it does |
|------|--------------|
| `material_list_libraries` | Loaded libraries with their material and appearance counts |
| `material_list` / `appearance_list` | Items of one library, of every library or of the design (`in_design`), with a name `query`, `limit` and `offset` |
| `material_get` | Physical material and appearance of a body, component or occurrence |
| `material_assign` | Physical material (mass, density) for a body, a component, or an occurrence (applied to its component) |
| `appearance_assign` | Appearance for a body, face or occurrence; a library appearance is copied into the design first |
| `appearance_set_color` | Plain RGBA colour (`"#RRGGBB"`, `"#RRGGBBAA"`, `[r, g, b(, a)]`): a design copy of a base appearance, reused when identical |
| `export_cad` | Design, component or occurrence to STEP, IGES, SAT, SMT, F3D archive or USDZ |
| `export_mesh` | Design, component, occurrence or body to STL, 3MF or OBJ (+ `.mtl`); refinement `low`/`medium`/`high`/`custom` |
| `export_sketch_dxf` | A sketch to DXF |
| `import_file` | STEP, IGES, SAT, SMT, F3D (as occurrences/bodies) or STL, OBJ, 3MF (as mesh bodies, `mesh_units`) into a component |

- **Ids, not names.** Material and appearance names are localized and repeat across
  libraries. Take the `id` from `material_list` / `appearance_list`. A name found in several
  places fails with `details.candidates`. Library items are never modified. After
  `material_assign`, check the mass with `inspect_physical_properties`.
- **Paths.** Every path is absolute, and the extension must match the format (`.step`/`.stp`,
  `.iges`/`.igs`, `.sat`, `.smt`, `.f3d`, `.usdz`, `.stl`, `.3mf`, `.obj`, `.dxf`). The tool
  never appends an extension for you. An export also needs the directory to exist; an
  import needs the file to exist. Bad paths fail with `INVALID_PARAMS` and
  `details.reason` set to `not_absolute`, `bad_extension`, `parent_missing`, `is_directory`,
  `exists` or `not_found`. The server checks the path before contacting Fusion.
- **`overwrite`.** An existing file is replaced only with `overwrite=true`. The old file
  (and for OBJ its `.mtl`) is deleted before the export, so if that export then fails, the
  old file is gone. Every export reports the files it wrote with their sizes. An empty or
  missing file is `FUSION_API_ERROR "export failed"`.
- **Exporting F3D is not saving.** `export_cad(format="f3d")` writes a copy of the design to
  a local archive; the document itself stays unsaved and keeps its name. To save the
  document to the cloud project, use `document_save` (c04).
- **Mesh settings.** Custom refinement values are numbers: `surface_deviation` and
  `max_edge_length` in `units` (default mm), `normal_deviation` in degrees, and
  `aspect_ratio`. Giving them without `refinement="custom"` is an error. `file_units` applies
  to STL and OBJ only (3MF returns a warning), and `binary` to STL only.
- **Import.** What was created is reported as refs in `created.occurrences`, `bodies` and
  `mesh_bodies`. An import that produces nothing fails, and in a parametric design its
  timeline items are rolled back. In a direct design, the created objects are deleted.

## Листовой металл

Sheet metal rules, flanges, bends and the flat pattern:

| Tool | What it does |
|------|--------------|
| `sheet_metal_capabilities` | Which sheet metal operations this Fusion API supports (no changes); `sketch_bend` is always `false` |
| `sheet_metal_list_rules` | Design and library rules: thickness, bend radius, gap, relief sizes in mm with expressions, K-factor, relief shape |
| `sheet_metal_create_rule` | Design rule copied from a base rule (default: the library default) with new values |
| `sheet_metal_assign_rule` | Active rule of a component; a library rule is copied into the design first |
| `sheet_metal_convert` | Solid plate body to sheet metal from a planar base face |
| `sheet_metal_base_flange` | Flat sheet metal body from closed profiles |
| `sheet_metal_contour_flange` | Bent body from an open profile (sketch curves / edges), one side, symmetric or two sides |
| `sheet_metal_edge_flange` | Wall on linear edges: height or to-entity, angle, width (full / symmetric / two sided / two offsets), height datum, bend position, mitered corners, flip, bend radius |
| `sheet_metal_list_bends` | Inner / outer bend face pairs with the radius in mm |
| `sheet_metal_unfold` / `sheet_metal_refold` | Unfold all or selected bends about a stationary face; refold an unfold feature |
| `sheet_metal_create_flat_pattern` / `get` / `delete` | The component's flat pattern: size, thickness, bend count, bend angles and directions |
| `sheet_metal_export_flat_pattern` | Flat pattern to DXF (bend / extent lines, file units, splines to polylines) or STEP |

Fusion API coverage:

| Area | API | Status |
|------|-----|--------|
| Rules, active rule, `isSheetMetal` | `SheetMetalRules`, `Component.activeSheetMetalRule`, `BRepBody.isSheetMetal` | stable |
| Flat pattern and DXF export | `Component.createFlatPattern`, `FlatPattern`, `createDXFFlatPatternExportOptions` | stable |
| STEP export of the flat pattern | `createSTEPExportOptionsForFlatPattern` | preview |
| Convert | `BRepBody.convertToSheetMetal` | preview |
| Base / contour / edge flange | `FlangeFeatures.create*FlangeInput` | preview |
| Bends, unfold, refold | `BRepBody.getBendFaces`, `UnfoldFeatures`, `RefoldFeatures` | preview |
| Bend along a sketch line | — | **none** |

- **Check `sheet_metal_capabilities` first.** A tool built on a missing preview API fails
  with `FUSION_API_ERROR` and `details = {"reason": "api_unavailable", "api": "<name>"}`
  before it changes anything. Without the flange API a bracket needs the Fusion UI or
  `execute_python`.
- **No Bend along a sketch line.** The Fusion API has no object for it (`sketch_bend` is
  always `false`). Model a bend with `sheet_metal_edge_flange`, or draw it into the open
  profile of `sheet_metal_contour_flange`.
- **Rules.** A rule is addressed by its name and `source` (`design`, `library`, `all` -
  design first). Numbers are in `units` (default mm); strings are expressions that may use
  `Thickness` (`"Thickness * 1.5"`). Write units in expressions (`"2 mm"`): a rule in inches
  reads bare numbers as inches. Library rules are never changed. A failed call deletes the
  rule it created and restores the previous active rule. Fusion may refuse a rule on a
  component without sheet metal data; then pass `rule` to the first flange tool.
- **Refs change after every flange.** Take edges and faces from the latest result or query
  them again with `inspect_query`.
- **Flat pattern.** One per component, addressed by the component (default: root).
  `sheet_metal_create_flat_pattern` refuses an existing one unless `replace=true`, and
  switches Fusion back to the design workspace afterwards. `replace` is atomic: the tool
  remembers the stationary face (a component attribute) and recreates the old flat pattern
  if the new one fails; a flat pattern made in the UI cannot be restored, so `replace`
  refuses it (`not_restorable`) - delete it with `sheet_metal_delete_flat_pattern` first. Export paths follow the export
  rules above (absolute, `.dxf` / `.step` / `.stp`, `overwrite`).

Bracket recipe:

1. `sheet_metal_capabilities` - check `contour_flange`, `edge_flange`, `flat_pattern`.
2. `sheet_metal_create_rule(name="Steel 2mm", thickness=2, bend_radius="Thickness * 1",
   k_factor=0.44)`.
3. `sketch_create(plane="origin:XY")`, then `sketch_draw` with two lines forming an open L.
4. `sheet_metal_contour_flange(profiles=[<line a>, <line b>], distance=50, rule="Steel 2mm")`.
5. `inspect_query(kind="edge", scope=[<body>])` -> `sheet_metal_edge_flange(edges=[<outer
   edge>], height=20)`.
6. `sheet_metal_create_flat_pattern(stationary_face=<planar face>)` ->
   `sheet_metal_export_flat_pattern(path="C:/out/bracket.dxf")`.

## Формы (T-splines)

Organic shapes from T-spline bodies (the Form environment):

| Tool | What it does |
|------|--------------|
| `form_list` | Form features of the design or a component with their T-spline bodies: control-mesh counts, bbox in mm, the B-Rep body ref |
| `form_create_primitive` | Box, plane, cylinder, sphere, torus or quadball with sizes in mm, face counts, origin / directions and symmetry (`{"x","y","z"}` or `"circular"`), in a new or an existing form |
| `form_create_from_tsm` / `form_get_tsm` | TSM (T-spline mesh) text or a `.tsm` file in and out |
| `form_inspect` | Control-mesh vertices (control and smooth positions), edges (start, end, midpoint, crease) and faces (center) in mm |
| `form_move` | Move vertices or faces picked by points, or the whole body, by a vector |
| `form_crease` | Crease or smooth edges picked by points |
| `form_insert_edge` | Insert edges next to edges of one loop (preview API) |
| `form_convert_to_brep` | Independent B-Rep copy of the form's body (in a base feature), the source hidden |

- **Not parametric.** The Fusion API takes primitive sizes as numbers: expressions and user
  parameters are refused (`INVALID_PARAMS` "not parametric"), and a form does not follow
  parameter changes. A parameter of another shape (e.g. `height` for a sphere) is refused.
- **Points, not indices.** Control-mesh vertices, edges and faces have no stable ids
  (INV-7). The editing tools pick them by points in mm: take the vertex `position`, the edge
  `midpoint` or the face `center` from `form_inspect`. A point with nothing within
  `tolerance` (0.5 mm) or equally near two elements (symmetric meshes) fails with
  `details.nearest` and changes nothing.
- **Atomic edits.** Every selector is resolved before the first change; if a later move or
  crease fails, the earlier ones are undone. The form never stays in edit mode, and its
  B-Rep is regenerated after every call - a body without a B-Rep is an error (rolled back).
- **Convert = copy.** The API has no "Convert" command: `form_convert_to_brep` adds an
  independent copy of the form's B-Rep (later form edits do not change it) and hides the form,
  which stays in the timeline.
- **TSM path for the rest.** Pull, Bridge, Fill Hole, Weld / Unweld, Subdivide, Merge, Thicken,
  soft modification, sketch-driven forms (extrude / revolve / sweep / loft in Form), symmetry
  changes after creation and mesh-to-form conversion have no Fusion API. Read the TSM with
  `form_get_tsm`, edit the text and create the body again with `form_create_from_tsm` (or use
  `execute_python` for what the API does have).
- `form_insert_edge` uses the preview API: without it the tool fails with `FUSION_API_ERROR`
  `reason="api_unavailable"`, nothing changed.

Handle recipe:

1. `form_create_primitive(shape="box", x_size=80, y_size=20, z_size=20, x_faces=4)`.
2. `form_inspect(body=<body>, include=["faces"])` - pick the face centers to pull.
3. `form_move(body=<body>, kind="face", near=[<center>], vector=[0, 0, 15])`.
4. `form_crease(body=<body>, near=[<edge midpoint>])` for a sharp edge.
5. `form_convert_to_brep(body=<body>, name="Handle")` -> shell / fillet the solid as usual.

## Limitations

- **Timeouts.** Every request has a timeout (`execute_python` takes `timeout_s`, default
  60 s). The Fusion API cannot be interrupted, so when a request times out the operation
  keeps running in Fusion. Requests run one at a time, in order. Call `fusion_status`
  before retrying.
- **Atomicity needs a parametric design.** If a tool fails in a parametric design (one with
  a timeline), the timeline items it added are rolled back. A direct-modeling design has no
  timeline, so there is no rollback. The error says so in `details.rollback =
  "unavailable_direct_design"`.
- Local only: the bridge accepts connections from `127.0.0.1` with the token from the
  discovery file. There is no remote access.

## Development

```sh
uv run pytest -q             # unit tests (adsk stubs, fake bridge)
uv run pytest -m fusion      # smoke tests on a live Fusion with the add-in running
uv run ruff check . && uv run ruff format --check . && uv run mypy .
```

### Hot reload of the add-in

Fusion keeps the add-in's modules in `sys.modules`, so an edit to a linked install is not
picked up until the add-in restarts. To load edited handler code into the running add-in:

```sh
uv run fusion-mcp reload-addin   # calls dev.reload, prints reloaded modules and handler count
```

It clears the handler registry, drops the add-in's modules (and `fusion_mcp.protocol.*`) from
`sys.modules`, imports them again (`handlers` first) and points the globals of the modules it
keeps (the entry point's `Context`, say) at the new classes. Exit code is non-zero on error:
an import error in an edited module comes back as `FUSION_API_ERROR` with the traceback
(`status.get` and `dev.reload` keep working); fix the file and run `reload-addin` again. A
few modules hold the running bridge and are never reloaded: the entry point `FusionMCP.py`,
`core/server.py`, `core/dispatcher.py`, `core/registry.py` and `fusion_mcp.protocol.errors` /
`wire` / `discovery`. After changing one of them restart the add-in: Utilities > Add-ins >
FusionMCP > Stop, Run. A change to `handlers/dev.py` itself takes effect on the second
`reload-addin`. `dev.reload` is for development only and is not an MCP tool.

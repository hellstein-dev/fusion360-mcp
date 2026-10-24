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

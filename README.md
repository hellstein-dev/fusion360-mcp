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

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

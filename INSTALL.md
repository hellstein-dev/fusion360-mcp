# Installing fusion-mcp

fusion-mcp has two parts that are installed separately:

1. **FusionMCP add-in** — runs inside Autodesk Fusion and executes the Fusion API.
2. **MCP server** (`fusion-mcp`) — started by your MCP client (Claude Code, Claude Desktop,
   any MCP client) and talks to the add-in over `127.0.0.1`.

All commands below are run from this folder (the one containing `pyproject.toml`).
`<plugin-dir>` means the absolute path of this folder on your machine.

## 1. Requirements

- Autodesk Fusion (Windows or macOS).
- [uv](https://docs.astral.sh/uv/getting-started/installation/). uv installs a suitable
  Python (≥ 3.11) for the MCP server by itself. The add-in uses Fusion's bundled Python and
  needs no extra packages.

Check:

```sh
uv --version
uv run fusion-mcp --version
```

The first `uv run` creates `.venv/` in this folder and installs the server dependencies.

## 2. Install the add-in

### Option A — installer (recommended)

```sh
uv run fusion-mcp install-addin
```

This creates a link named `FusionMCP` in Fusion's add-ins folder pointing to `fusion_addin/`
in this folder (a junction on Windows, a symlink on macOS). Updates to this folder are picked
up after restarting the add-in, no reinstall needed.

If links cannot be created on your machine (permissions, network drive):

```sh
uv run fusion-mcp install-addin --copy
```

`--copy` copies the add-in instead and bundles the protocol package next to it. Re-run it
after every update of this folder.

If Fusion uses a non-standard add-ins folder, pass it explicitly:

```sh
uv run fusion-mcp install-addin --addins-dir "<path to Fusion's API/AddIns folder>"
```

The installer refuses to touch a `FusionMCP` folder it did not create.

### Option B — manual, from inside Fusion

1. In Fusion open **Utilities → Add-ins** (or press `Shift+S`), tab **Add-Ins**.
2. Click the **+** next to *My Add-Ins* and select the `fusion_addin` folder of this plugin.
   Fusion loads add-ins by folder; `FusionMCP.py` and `FusionMCP.manifest` inside it are the
   entry point.

With option B the add-in finds the protocol package through `../src`, so keep `fusion_addin/`
inside this plugin folder — do not move it on its own.

## 3. Start the add-in

1. **Utilities → Add-ins** (`Shift+S`), tab **Add-Ins**.
2. Select **FusionMCP** and click **Run**. Tick **Run on Startup** to start it with Fusion.
3. The **Utilities → Add-ins** panel now shows a button `FusionMCP: Running :<port>`.
   Click it to stop or start the bridge.

While it runs, the add-in writes its port and a random token to
`~/.fusion-mcp/bridge.json` (in your user home folder). The MCP server reads that file, so
nothing needs configuring by hand. The default port is 7432; if it is busy another free port
is used.

## 4. Connect your MCP client

Print a config with the correct absolute path for this machine:

```sh
uv run fusion-mcp print-config                       # Claude Desktop format
uv run fusion-mcp print-config --client claude-code  # Claude Code (.mcp.json) format
```

**Claude Code** — either put the `--client claude-code` output into `.mcp.json` of your
project, or run:

```sh
claude mcp add fusion360 -- uv run --directory <plugin-dir> fusion-mcp
```

**Claude Desktop** — merge the `print-config` output into `claude_desktop_config.json`
(*Settings → Developer → Edit Config*) and restart Claude Desktop:

```json
{
  "mcpServers": {
    "fusion360": {
      "command": "uv",
      "args": ["run", "--directory", "<plugin-dir>", "fusion-mcp"]
    }
  }
}
```

**Other MCP clients** — configure a stdio server with command `uv` and arguments
`run --directory <plugin-dir> fusion-mcp`.

## 5. Check

Ask the agent to call **`fusion_status`**. It returns the Fusion, add-in and Python versions
and the active document. An error starting with `NOT_CONNECTED` means Fusion or the add-in is
not running.

From the command line, the live smoke tests check the whole chain (they create documents
and geometry in Fusion — save your work first):

```sh
uv run pytest -m fusion tests/smoke/test_bridge.py   # connection only
uv run pytest -m fusion                              # everything
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `fusion-mcp: command not found` | Run it through uv: `uv run fusion-mcp ...`, from this folder (or add `--directory <plugin-dir>`). |
| `uv: command not found` | uv is not on `PATH`. Reopen the terminal after installing uv, or use the full path to the `uv` executable. |
| `NOT_CONNECTED` | Fusion is closed or the add-in is stopped. Start it (step 3); `~/.fusion-mcp/bridge.json` must exist. |
| `TIMEOUT` on every call | The add-in accepts the request but Fusion's main thread does not run it. Close modal dialogs in Fusion (including *Scripts and Add-Ins*), then retry. The add-in logs to `~/.fusion-mcp/addin.log`. |
| Add-in does not appear in *Add-Ins* | Restart Fusion after `install-addin`, or add it manually (option B). |
| `FusionMCP failed to start` message box | The text shows the traceback; most often `fusion_addin/` was moved away from `src/` (option B) — use `install-addin --copy` instead. |

## Uninstall

```sh
uv run fusion-mcp uninstall-addin
```

Then remove the `fusion360` entry from your MCP client config.

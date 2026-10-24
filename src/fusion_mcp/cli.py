"""Command-line entry point for the ``fusion-mcp`` console script.

Without a subcommand it runs the MCP server over stdio (what MCP clients launch).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fusion_mcp import __version__

SERVER_KEY = "fusion360"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fusion-mcp",
        description="MCP server for Autodesk Fusion 360. Without a command: serve MCP on stdio.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")

    install = commands.add_parser(
        "install-addin", help="link (or copy) the FusionMCP add-in into Fusion's AddIns folder"
    )
    install.add_argument(
        "--copy",
        action="store_true",
        help="copy instead of linking (bundles fusion_mcp.protocol into _vendor/)",
    )
    install.add_argument("--addins-dir", type=Path, help="override Fusion's AddIns folder")

    uninstall = commands.add_parser("uninstall-addin", help="remove the installed add-in")
    uninstall.add_argument("--addins-dir", type=Path, help="override Fusion's AddIns folder")

    commands.add_parser(
        "reload-addin",
        help="development: hot-reload the running add-in's handler code (dev.reload)",
    )

    config = commands.add_parser("print-config", help="print an MCP client config for this server")
    config.add_argument(
        "--client",
        choices=["claude-desktop", "claude-code"],
        default="claude-desktop",
        help="claude-desktop: claude_desktop_config.json; claude-code: .mcp.json",
    )
    return parser


def run_server() -> int:
    from fusion_mcp.server.app import create_server

    create_server().run("stdio")
    return 0


def client_config(client: str = "claude-desktop", plugin: Path | None = None) -> dict[str, Any]:
    from fusion_mcp.server.install import plugin_dir

    directory = str((plugin if plugin is not None else plugin_dir()).resolve())
    server: dict[str, Any] = {
        "command": "uv",
        "args": ["run", "--directory", directory, "fusion-mcp"],
    }
    if client == "claude-code":
        server = {"type": "stdio", **server}
    return {"mcpServers": {SERVER_KEY: server}}


def _install(args: argparse.Namespace) -> int:
    from fusion_mcp.server.install import InstallError, install

    try:
        target = install(copy=args.copy, addins=args.addins_dir)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    mode = "copied to" if args.copy else "linked at"
    print(f"FusionMCP add-in {mode} {target}")
    print("Restart Fusion (or Utilities > Add-ins > FusionMCP > Run) to load it.")
    return 0


def _uninstall(args: argparse.Namespace) -> int:
    from fusion_mcp.server.install import InstallError, uninstall

    try:
        removed = uninstall(addins=args.addins_dir)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("FusionMCP add-in removed" if removed else "FusionMCP add-in is not installed")
    return 0


def reload_addin(bridge: Any = None) -> int:
    """Call ``dev.reload`` on the running add-in and print the result (c15 design D1).

    Modules that hold the running bridge (entry point, server, dispatcher, registry, context)
    are not reloaded: changing them still needs Utilities > Add-ins > FusionMCP > Stop, Run.
    """
    import asyncio

    from fusion_mcp.protocol.errors import BridgeError
    from fusion_mcp.server.bridge import BridgeClient

    client = bridge if bridge is not None else BridgeClient()

    async def go() -> Any:
        try:
            return await client.call("dev.reload")
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                await close()

    try:
        result = asyncio.run(go())
    except BridgeError as exc:
        print(f"error: {exc.code.value}: {exc.message}", file=sys.stderr)
        if exc.details:
            print(json.dumps(exc.details, indent=2, default=str), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run the CLI. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        return run_server()
    if args.command == "install-addin":
        return _install(args)
    if args.command == "uninstall-addin":
        return _uninstall(args)
    if args.command == "reload-addin":
        return reload_addin()
    if args.command == "print-config":
        print(json.dumps(client_config(args.client), indent=2))
        return 0
    parser.print_help()  # pragma: no cover - every subcommand is dispatched above
    return 2

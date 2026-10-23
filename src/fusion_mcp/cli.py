"""Command-line entry point for the ``fusion-mcp`` console script.

Without a subcommand it runs the MCP server over stdio (what MCP clients launch).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from fusion_mcp import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fusion-mcp",
        description="MCP server for Autodesk Fusion 360. Without a command: serve MCP on stdio.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_subparsers(dest="command", metavar="command")
    return parser


def run_server() -> int:
    from fusion_mcp.server.app import create_server

    create_server().run("stdio")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run the CLI. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        return run_server()
    parser.print_help()  # pragma: no cover - every subcommand is dispatched above
    return 2

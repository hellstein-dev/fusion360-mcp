"""Command-line entry point for the ``fusion-mcp`` console script."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from fusion_mcp import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fusion-mcp",
        description="MCP server for Autodesk Fusion 360.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run the CLI. Returns the process exit code."""
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0

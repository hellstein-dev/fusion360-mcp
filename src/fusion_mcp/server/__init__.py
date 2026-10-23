"""MCP server side: tool definitions and the client that talks to the Fusion add-in bridge.

Runs outside Fusion and may use third-party packages (``mcp``), but never imports
``adsk`` — everything Fusion-specific goes through the bridge via ``fusion_mcp.protocol``.
"""

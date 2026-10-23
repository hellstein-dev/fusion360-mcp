"""Wire protocol shared by the MCP server and the Fusion add-in.

Rule: this package uses the Python standard library ONLY. The Fusion add-in
(``fusion_addin/``) imports it and must run on Fusion's bundled interpreter without
any pip dependencies, so no third-party imports (``mcp``, ``pydantic``, ...) and no
``adsk`` here.
"""

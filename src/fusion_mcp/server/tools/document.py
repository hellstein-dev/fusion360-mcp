"""``document_*`` tools: list, create, open, activate, save and close documents (c04 D9).

Documents are addressed as ``document:file:<id>`` (saved) or ``document:<name>`` (unsaved);
an omitted ``document`` means the active one. Saving and closing happen only on request.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.server.app import call_bridge
from fusion_mcp.server.bridge import Bridge

DesignType = Literal["parametric", "direct"]
DistanceUnits = Literal["mm", "cm", "m", "in", "ft"]
DocumentRef = Annotated[
    str,
    Field(
        min_length=1,
        description="Document ref from document_list: 'document:file:<id>' or 'document:<name>'",
    ),
]
OptionalDocumentRef = Annotated[
    str | None,
    Field(
        min_length=1,
        description="Document ref from document_list; default: the active document",
    ),
]


def _given(**params: Any) -> dict[str, Any]:
    """Only the parameters the agent set: ``None`` means "not given"."""
    return {k: v for k, v in params.items() if v is not None}


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="document_list")
    async def document_list() -> CallToolResult:
        """List the documents open in Fusion.

        Each item: {"ref", "name", "is_active", "is_saved", "is_modified", "file_id",
        "version", "design_type": "parametric"|"direct"|null, "units": "mm"|...|null}.
        `ref` is `document:file:<id>` for a saved document and `document:<name>` for an
        unsaved one; pass it to document_activate / document_save / document_close.
        Other tools always work on the active document.

        Returns {"documents": [...]}.
        """
        return await call_bridge(bridge, "document.list", {})

    @mcp.tool(name="document_activate")
    async def document_activate(document: DocumentRef) -> CallToolResult:
        """Make an open document the active one; every other tool then works on it.

        Two unsaved documents with the same name are ambiguous: the call fails with
        INVALID_PARAMS and `details.candidates`. A ref that matches no open document is
        ENTITY_NOT_FOUND.

        Returns the document's info (see document_list) with "is_active": true.

        Example: document_activate(document="document:file:urn:adsk.wipprod:dm.lineage:abc").
        """
        return await call_bridge(bridge, "document.activate", {"document": document})

    @mcp.tool(name="document_new")
    async def document_new(
        design_type: Annotated[
            DesignType,
            Field(description="parametric (with timeline, default) or direct (no history)"),
        ] = "parametric",
        units: Annotated[
            DistanceUnits, Field(description="Distance display units of the design")
        ] = "mm",
    ) -> CallToolResult:
        """Create a new design document and make it active. It is NOT saved; call
        document_save only when the user asks to save.

        `units` only sets how Fusion displays lengths; numbers passed to tools stay in mm.

        Returns the new document's info (see document_list).

        Example: document_new() -> parametric design in mm.
        """
        return await call_bridge(
            bridge, "document.new", {"design_type": design_type, "units": units}
        )

    @mcp.tool(name="document_open")
    async def document_open(
        file_id: Annotated[
            str | None, Field(min_length=1, description="Cloud data file id (from Fusion)")
        ] = None,
        local_path: Annotated[
            str | None,
            Field(min_length=1, description="Absolute path of a local Fusion archive (.f3d)"),
        ] = None,
    ) -> CallToolResult:
        """Open a document and make it active. Pass exactly one of `file_id` (a cloud file)
        or `local_path` (an existing local .f3d file; other formats such as STEP are not
        opened here).

        A cloud file that is already open is activated instead of opened again
        ("already_open": true).

        Returns the document's info (see document_list) plus "already_open".

        Examples: document_open(file_id="urn:adsk.wipprod:dm.lineage:abc");
        document_open(local_path="C:/parts/bracket.f3d").
        """
        params = _given(file_id=file_id, local_path=local_path)
        return await call_bridge(bridge, "document.open", params)

    @mcp.tool(name="document_save")
    async def document_save(
        document: OptionalDocumentRef = None,
        description: Annotated[str, Field(description="Version description")] = "",
        name: Annotated[
            str | None,
            Field(min_length=1, description="Name for the first save of an unsaved document"),
        ] = None,
        folder_path: Annotated[
            str | None,
            Field(
                min_length=1,
                description="Existing folder in the active project, e.g. 'Parts/Brackets'; "
                "default: the project's root folder",
            ),
        ] = None,
    ) -> CallToolResult:
        """Call ONLY when the user explicitly asked to save the document.

        An already saved document gets a new version (`name`/`folder_path` are rejected:
        no Save As). A never-saved document needs `name` and is saved into the active
        project's root folder or the existing `folder_path` (missing folders are
        ENTITY_NOT_FOUND and never created). The upload to the cloud continues in the
        background ("upload": "pending").

        Returns the document's info (see document_list) plus "upload": "pending".

        Examples: document_save(name="Bracket"); document_save(description="hole moved").
        """
        params = _given(
            document=document, description=description, name=name, folder_path=folder_path
        )
        return await call_bridge(bridge, "document.save", params)

    @mcp.tool(name="document_close")
    async def document_close(
        document: OptionalDocumentRef = None,
        save_changes: Annotated[
            bool, Field(description="Save unsaved changes before closing (saved documents)")
        ] = False,
        discard_changes: Annotated[
            bool, Field(description="Throw unsaved changes away; only if the user said so")
        ] = False,
    ) -> CallToolResult:
        """Call ONLY when the user explicitly asked to close the document.

        A document with unsaved changes is not closed unless exactly one of
        `save_changes` or `discard_changes` is true (INVALID_PARAMS otherwise, the
        document stays open). `save_changes` needs a document that was saved before —
        call document_save with a name first.

        Returns {"closed": {"ref", "type", "name"}, "active": <info of the document that is
        now active> | null}.

        Example: document_close(discard_changes=true).
        """
        params = _given(
            document=document, save_changes=save_changes, discard_changes=discard_changes
        )
        return await call_bridge(bridge, "document.close", params)

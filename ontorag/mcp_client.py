# ontorag/mcp_client.py
"""
Async MCP client for the OntoRAG ontology catalog.

Connects to a remote (or local) MCP server over SSE and exposes the
catalog tools as plain async methods that return dicts.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from mcp.client.sse import sse_client
from mcp.client.session import ClientSession

from ontorag.verbosity import get_logger

_log = get_logger("ontorag.mcp_client")


class OntologyCatalogMCPClient:
    """Thin async wrapper around the ontology-catalog MCP server."""

    def __init__(self, mcp_url: str) -> None:
        self.mcp_url = mcp_url
        _log.info("MCP client target: %s", mcp_url)

    async def _call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Open an SSE session, call one tool, return the parsed JSON result."""
        _log.debug("MCP call_tool: %s(%s)", name, arguments or {})
        async with sse_client(self.mcp_url) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments or {})

        if result.isError:
            text = ""
            for block in result.content:
                if hasattr(block, "text"):
                    text += block.text
            _log.debug("MCP tool error: %s", text)
            return {"error": text}

        # Parse the first text content block as JSON; fall back to raw text
        for block in result.content:
            if hasattr(block, "text"):
                try:
                    parsed = json.loads(block.text)
                    _log.debug("MCP result: %s keys", list(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__)
                    return parsed
                except (json.JSONDecodeError, TypeError):
                    return {"raw": block.text}

        return {}

    # ── Tool wrappers ────────────────────────────────────────────────

    async def list_ontologies(self) -> Dict[str, Any]:
        return await self._call_tool("list_ontologies")

    async def inspect_ontology(self, slug: str) -> Dict[str, Any]:
        return await self._call_tool("inspect_ontology", {"slug": slug})

    async def search_classes(self, query: str) -> Dict[str, Any]:
        return await self._call_tool("search_classes", {"query": query})

    async def search_properties(self, query: str) -> Dict[str, Any]:
        return await self._call_tool("search_properties", {"query": query})

    async def compose(self, slugs: List[str], target_namespace: str = "") -> Dict[str, Any]:
        args: Dict[str, Any] = {"slugs": slugs}
        if target_namespace:
            args["target_namespace"] = target_namespace
        return await self._call_tool("compose", args)

    async def add_ontology(
        self,
        slug: str,
        ttl_content: str,
        label: str = "",
        description: str = "",
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return await self._call_tool("add_ontology", {
            "slug": slug,
            "ttl_content": ttl_content,
            "label": label,
            "description": description,
            "tags": tags or [],
        })

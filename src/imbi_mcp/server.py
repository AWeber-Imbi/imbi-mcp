"""Imbi MCP server.

Auto-generates MCP tools from the Imbi API's OpenAPI spec.
Forwards the caller's Authorization header to the API for
per-user authentication.
"""

from __future__ import annotations

import logging
import typing

import fastmcp
import httpx
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.providers.openapi import MCPType, RouteMap

import imbi_mcp

if typing.TYPE_CHECKING:
    from fastmcp.utilities.openapi import HTTPRoute

logger = logging.getLogger(__name__)

# OpenAPI operation extension imbi-api stamps on endpoints that must not
# be exposed to AI. Its presence (set to ``False``) hides the operation
# regardless of path or method — the API owns which endpoints are
# sensitive (e.g. project Configuration / SSM Parameter Store).
_AI_TOOL_EXTENSION = 'x-imbi-ai-tool'

# Endpoints that should not be exposed as MCP tools.
_EXCLUDED_ROUTE_MAPS = [
    RouteMap(pattern=r'^/auth/', mcp_type=MCPType.EXCLUDE),
    RouteMap(pattern=r'^/mfa/', mcp_type=MCPType.EXCLUDE),
    RouteMap(pattern=r'^/status/?$', mcp_type=MCPType.EXCLUDE),
    RouteMap(pattern=r'.*/thumbnail/?$', mcp_type=MCPType.EXCLUDE),
]


def _exclude_non_ai_tools(
    route: HTTPRoute, _mcp_type: MCPType
) -> MCPType | None:
    """Exclude operations imbi-api flagged as off-limits for AI.

    Returns ``MCPType.EXCLUDE`` when the operation carries
    ``x-imbi-ai-tool: false`` in the OpenAPI spec, else ``None`` to
    leave the route map decision unchanged.
    """
    if route.extensions.get(_AI_TOOL_EXTENSION) is False:
        return MCPType.EXCLUDE
    return None


# Read-only list endpoints → resources, parameterised GETs →
# resource templates, everything else → tools.
_SEMANTIC_ROUTE_MAPS = [
    RouteMap(
        methods=['GET'],
        pattern=r'.*\{.*\}.*',
        mcp_type=MCPType.RESOURCE_TEMPLATE,
    ),
    RouteMap(
        methods=['GET'],
        pattern=r'.*',
        mcp_type=MCPType.RESOURCE,
    ),
]


async def _inject_auth(request: httpx.Request) -> None:
    """Forward the MCP caller's Authorization header to the API."""
    headers = get_http_headers(include={'authorization'})
    auth = headers.get('authorization')
    if auth:
        request.headers['authorization'] = auth


def create_server(api_url: str) -> fastmcp.FastMCP:
    """Build a FastMCP server from the live Imbi API OpenAPI spec.

    Args:
        api_url: Base URL of the running Imbi API
            (e.g. ``http://localhost:8000``).
    """
    spec_url = f'{api_url.rstrip("/")}/openapi.json'
    logger.info('Fetching OpenAPI spec from %s', spec_url)
    response = httpx.get(spec_url, timeout=30)
    response.raise_for_status()
    spec = response.json()

    client = httpx.AsyncClient(
        base_url=api_url,
        timeout=30,
        event_hooks={'request': [_inject_auth]},
    )

    return fastmcp.FastMCP.from_openapi(
        openapi_spec=spec,
        client=client,
        name='Imbi',
        version=imbi_mcp.version,
        route_maps=[
            *_EXCLUDED_ROUTE_MAPS,
            *_SEMANTIC_ROUTE_MAPS,
        ],
        route_map_fn=_exclude_non_ai_tools,
    )

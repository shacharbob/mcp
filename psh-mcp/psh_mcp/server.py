"""Personal Service Health MCP Server Implementation."""

import logging
import os
import re
from typing import Any, List, Optional, TypedDict

# Google Imports
from google.cloud import asset_v1, servicehealth_v1
from google.protobuf.json_format import MessageToDict
# MCP Imports
from mcp.server import InitializationOptions, Server
from mcp.server.sse import SseServerTransport
from mcp.types import EmbeddedResource, TextContent, Tool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
import uvicorn


# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-psh")


# --- 1. DATA TRANSFORMATIONS (From Step 2) ---


class HealthEvent(TypedDict):
  id: str
  title: str
  state: str
  category: str
  last_updated: str
  impacted_products: List[str]
  timeline: List[dict]
  latest_workaround: Optional[str]


def _format_event_details(event_pb) -> HealthEvent:
  if hasattr(event_pb, "_pb"):
    data = MessageToDict(event_pb._pb)
  else:
    data = event_pb if isinstance(event_pb, dict) else {}

  raw_updates = data.get("updates", [])
  timeline = []
  for update in raw_updates:
    timeline.append({
        "time": update.get("updateTime"),
        "title": update.get("title"),
        "description": update.get("description"),
        "symptom": update.get("symptom"),
        "workaround": update.get("workaround"),
    })
  timeline.sort(key=lambda x: x.get("time", ""), reverse=True)

  products = data.get("impactedProducts", [])
  product_names = [p.get("productName") for p in products]

  return {
      "id": data.get("name"),
      "title": data.get("title"),
      "state": data.get("state"),
      "category": data.get("category"),
      "last_updated": data.get("updateTime"),
      "impacted_products": product_names,
      "timeline": timeline,
      "latest_workaround": timeline[0].get("workaround") if timeline else None,
  }


# --- 2. LOGIC IMPLEMENTATION (From Step 3) ---
async def list_active_events(
    project_id: str, location: str = "global"
) -> list[dict]:
  if not re.match(r"^[a-z0-9-]+$", project_id):
    raise ValueError(
        "Invalid project_id. Must be lowercase alphanumeric with hyphens."
    )

  client = servicehealth_v1.ServiceHealthAsyncClient()
  parent = f"projects/{project_id}/locations/{location}"
  request = servicehealth_v1.ListEventsRequest(
      parent=parent, filter="state = ACTIVE"
  )
  events = []
  count = 0
  async for event in await client.list_events(request=request):
    if count >= 10:
      break
    events.append(_format_event_details(event))
    count += 1
  return events


async def list_org_events(organization_id: str) -> list[dict]:
  client = servicehealth_v1.ServiceHealthAsyncClient()
  parent = f"organizations/{organization_id}/locations/global"
  request = servicehealth_v1.ListOrganizationEventsRequest(
      parent=parent, filter="state = ACTIVE"
  )
  events = []
  count = 0
  async for event in await client.list_organization_events(request=request):
    if count >= 10:
      break
    events.append(_format_event_details(event))
    count += 1
  return events


async def get_event_details(event_name: str) -> dict:
  client = servicehealth_v1.ServiceHealthAsyncClient()
  if "organizationEvents" in event_name:
    request = servicehealth_v1.GetOrganizationEventRequest(name=event_name)
    event = await client.get_organization_event(request=request)
  else:
    request = servicehealth_v1.GetEventRequest(name=event_name)
    event = await client.get_event(request=request)
  return _format_event_details(event)


async def list_projects_without_service_health(scope: str) -> list[str]:
  """Finds projects with Service Health disabled.

  WARNING: This performs a full asset inventory scan (O(N)).
  For large organizations, this may time out or use high memory.
  Returns a list of Project Numbers (e.g. 'projects/12345').
  """
  asset_client = asset_v1.AssetServiceAsyncClient()
  # 1. Get ALL active projects
  all_projects = []
  req_projects = asset_v1.SearchAllResourcesRequest(
      scope=scope,
      query="state=ACTIVE",
      asset_types=["cloudresourcemanager.googleapis.com/Project"],
      read_mask="name",
  )
  async for page in await asset_client.search_all_resources(
      request=req_projects
  ):
    if page.project:
      all_projects.append(page.project)

  # 2. Get projects with Service Health ENABLED
  enabled_projects = set()
  req_enabled = asset_v1.SearchAllResourcesRequest(
      scope=scope,
      query="name:servicehealth.googleapis.com",
      asset_types=["serviceusage.googleapis.com/Service"],
  )
  async for page in await asset_client.search_all_resources(
      request=req_enabled
  ):
    parts = page.name.split("/")
    if "projects" in parts:
      pid = parts[parts.index("projects") + 1]
      enabled_projects.add(f"projects/{pid}")

  return [p for p in all_projects if p not in enabled_projects]


# --- 3. MCP SERVER DEFINITION ---
mcp = Server("PSH-Monitor")


TOOLS_SCHEMA = [
    Tool(
        name="list_active_events",
        description=(
            "List active health events (outages/maintenance) for a project."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "location": {"type": "string", "default": "global"},
            },
            "required": ["project_id"],
        },
    ),
    Tool(
        name="list_org_events",
        description="List active health events across the entire Organization.",
        inputSchema={
            "type": "object",
            "properties": {
                "organization_id": {
                    "type": "string",
                    "description": "Numeric Org ID",
                }
            },
            "required": ["organization_id"],
        },
    ),
    Tool(
        name="get_event_details",
        description=(
            "Get full narrative, timeline, and workarounds for a specific"
            " event."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "event_name": {
                    "type": "string",
                    "description": "Full resource name of the event",
                }
            },
            "required": ["event_name"],
        },
    ),
    Tool(
        name="list_projects_without_service_health",
        description=(
            "Audit an Organization to find projects (returned as Project"
            " Numbers) where Service Health is disabled. WARNING: O(N) scan."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": "Scope to scan, e.g., 'organizations/123'",
                }
            },
            "required": ["scope"],
        },
    ),
]


@mcp.list_tools()
async def list_tools() -> list[Tool]:
  return TOOLS_SCHEMA


@mcp.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
  try:
    if name == "list_active_events":
      result: Any = await list_active_events(**arguments)
    elif name == "list_org_events":
      result = await list_org_events(**arguments)
    elif name == "get_event_details":
      result = await get_event_details(**arguments)
    elif name == "list_projects_without_service_health":
      result = await list_projects_without_service_health(**arguments)
    else:
      raise ValueError(f"Unknown tool: {name}")

    return [TextContent(type="text", text=str(result))]
  except Exception as e:
    logger.error(f"Error executing {name}: {e}")
    return [TextContent(type="text", text=f"Error: {str(e)}")]


# --- 4. STREAMABLE HTTP TRANSPORT ---
sse = SseServerTransport("/mcp")


async def handle_mcp(request: Request):
  if request.method == "GET":
    # Establish SSE Session
    async with sse.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
      await mcp.run(
          read_stream=streams[0],
          write_stream=streams[1],
          initialization_options=InitializationOptions(
              server_name="PSH-Monitor",
              server_version="1.0.0",
              capabilities=mcp.get_capabilities(
                  notification_options=None,
                  experimental_capabilities={},
              ),
          ),
      )
  elif request.method == "POST":
    # 1. Handle the RPC (Write to the Read Stream)
    await sse.handle_post_message(request.scope, request.receive, request._send)
    # 2. Acknowledge the POST request immediately
    return Response(status_code=202)

  return Response(status_code=405)


routes = [Route("/mcp", endpoint=handle_mcp, methods=["GET", "POST"])]
app = Starlette(routes=routes)

if __name__ == "__main__":
  port = int(os.environ.get("PORT", 8080))
  uvicorn.run(app, host="0.0.0.0", port=port)

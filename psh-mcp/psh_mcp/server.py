"""Modernized PSH-MCP Server"""
import logging
import os
from contextvars import ContextVar
from typing import Annotated, Any

# Google Imports
import google.cloud.asset_v1 as asset_v1
import google.cloud.servicehealth_v1 as servicehealth_v1
from google.oauth2.credentials import Credentials
from google.protobuf.json_format import MessageToDict

# FastMCP Imports
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-psh")

# --- AUTH CONTEXT ---
request_auth_token: ContextVar[str | None] = ContextVar("request_auth_token", default=None)

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            request_auth_token.set(token)
        else:
            request_auth_token.set(None)
        
        return await call_next(request)

# Define Middleware upfront
middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    ),
    Middleware(AuthMiddleware)
]

# Initialize
mcp = FastMCP("PSH-Monitor")

# --- DEPENDENCY INJECTION ---

# --- HELPERS ---

def get_token(explicit_token: str | None = None) -> str:
    """Get token from explicit argument or request context."""
    if explicit_token:
        return explicit_token
    
    ctx_token = request_auth_token.get()
    if ctx_token:
        return ctx_token
        
    raise ValueError("Authentication required: No Bearer token provided in header or arguments.")

def create_creds(token: str | None = None) -> Credentials:
    final_token = get_token(token)
    return Credentials(token=final_token)

# --- DATA HELPERS ---

def _format_event_details(event_pb) -> dict:
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
            "workaround": update.get("workaround"),
        })
    timeline.sort(key=lambda x: x.get("time", ""), reverse=True)

    products = data.get("impactedProducts", [])
    product_names = [p.get("productName") for p in products]

    return {
        "id": data.get("name"),
        "title": data.get("title"),
        "state": data.get("state"),
        "last_updated": data.get("updateTime"),
        "impacted_products": product_names,
        "timeline": timeline,
        "latest_workaround": timeline[0].get("workaround") if timeline else None,
    }

# --- TOOLS ---

@mcp.tool()
async def list_active_events(
    project_id: str, 
    location: str = "global",
    token: str = None
) -> list[dict]:
    """List active health events (outages/maintenance) for a project."""
    creds = create_creds(token)
    if not project_id.replace("-", "").isalnum():
         raise ValueError("Invalid project_id. Must be lowercase alphanumeric.")

    client = servicehealth_v1.ServiceHealthAsyncClient(credentials=creds)
    parent = f"projects/{project_id}/locations/{location}"
    request = servicehealth_v1.ListEventsRequest(parent=parent, filter="state = ACTIVE")
    
    events = []
    async for event in await client.list_events(request=request):
        events.append(_format_event_details(event))
        if len(events) >= 10: break
    return events

@mcp.tool()
async def list_org_events(
    organization_id: str,
    token: str = None
) -> list[dict]:
    """List active health events across the entire Organization."""
    creds = create_creds(token)
    client = servicehealth_v1.ServiceHealthAsyncClient(credentials=creds)
    parent = f"organizations/{organization_id}/locations/global"
    request = servicehealth_v1.ListOrganizationEventsRequest(parent=parent, filter="state = ACTIVE")
    
    events = []
    async for event in await client.list_organization_events(request=request):
        events.append(_format_event_details(event))
        if len(events) >= 10: break
    return events

@mcp.tool()
async def get_event_details(
    event_name: str,
    token: str = None
) -> dict:
    """Get full narrative, timeline, and workarounds for a specific event."""
    creds = create_creds(token)
    client = servicehealth_v1.ServiceHealthAsyncClient(credentials=creds)
    if "organizationEvents" in event_name:
        request = servicehealth_v1.GetOrganizationEventRequest(name=event_name)
        event = await client.get_organization_event(request=request)
    else:
        request = servicehealth_v1.GetEventRequest(name=event_name)
        event = await client.get_event(request=request)
    return _format_event_details(event)

@mcp.tool()
async def list_projects_without_service_health(
    scope: str,
    token: str = None
) -> list[str]:
    """Audit an Organization to find projects where Service Health is disabled."""
    creds = create_creds(token)
    asset_client = asset_v1.AssetServiceAsyncClient(credentials=creds)
    
    # 1. Get ALL active projects
    all_projects = []
    req_projects = asset_v1.SearchAllResourcesRequest(
        scope=scope, query="state=ACTIVE",
        asset_types=["cloudresourcemanager.googleapis.com/Project"], read_mask="name"
    )
    async for page in await asset_client.search_all_resources(request=req_projects):
        if page.project: all_projects.append(page.project)

    # 2. Get projects with Service Health ENABLED
    enabled_projects = set()
    req_enabled = asset_v1.SearchAllResourcesRequest(
        scope=scope, query="name:servicehealth.googleapis.com",
        asset_types=["serviceusage.googleapis.com/Service"]
    )
    async for page in await asset_client.search_all_resources(request=req_enabled):
        parts = page.name.split("/")
        if "projects" in parts:
            pid = parts[parts.index("projects") + 1]
            enabled_projects.add(f"projects/{pid}")

    return [p for p in all_projects if p not in enabled_projects]

# --- ENTRYPOINT ---

if __name__ == "__main__":
    import uvicorn
    from contextlib import asynccontextmanager
    
    # Define explicit lifespan to initialize FastMCP
    # REFACTORED: We now rely on uvicorn's lifespan="on" and FastMCP's internal handling.
    # The previous manual wrapper is removed to reduce confusion.
        
    # mcp.http_app() returns a Starlette app, but we need to ensure lifespan is passed
    # Re-wrap or attach lifespan if missing
    app = mcp.http_app(middleware=middleware)
    # FastMCP's http_app ALREADY has a lifespan, but it might depend on how uvicorn calls it.
    # The error "Task group is not initialized" means the startup hook didn't run.
    # We will FORCE it by ensuring uvicorn sees it.
    
    # Debug version
    try:
        from fastmcp import __version__
        print(f"DEBUG: FastMCP Version: {__version__}")
    except ImportError:
        print("DEBUG: FastMCP Version: Unknown")
        
    # Run with explicit lifespan arg to be safe
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), lifespan="on")

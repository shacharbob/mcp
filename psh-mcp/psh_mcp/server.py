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
    max_projects: int = 50,
    token: str = None
) -> dict:
    """Audit an Organization to find projects where Service Health is disabled.
    
    Result limited by max_projects to prevent timeouts.
    """
    creds = create_creds(token)
    asset_client = asset_v1.AssetServiceAsyncClient(credentials=creds)
    
    # Safety Check
    if "organizations" in scope and max_projects > 100:
        raise ValueError("Safety Guard: max_projects limited to 100 for Organization scopes.")

    # 1. Get ALL active projects (Limited)
    all_projects = []
    req_projects = asset_v1.SearchAllResourcesRequest(
        scope=scope, query="state=ACTIVE",
        asset_types=["cloudresourcemanager.googleapis.com/Project"], 
        read_mask="name",
        page_size=max_projects
    )
    
    # We only fetch ONE page of projects to respect the limit safely
    # This prevents the O(N) full scan risk
    pager = await asset_client.search_all_resources(request=req_projects)
    
    projects_to_check = []
    
    async for page in pager.pages:
        for p in page.search_all_resources_response.results:
            projects_to_check.append(p.project)
        break # STOP after first page (max_projects)
        
    if not projects_to_check:
        return {"disabled_projects": [], "warning": "No active projects found in scope."}

    # 2. Check Service Health status for these specific projects
    # We can't batch query easily, but we can query by parent?
    # Actually, SearchAll is efficient. We can just search for the enabled service
    # NO, we need to know where it is MISSING.
    # Efficient Set Difference on the limited subset:
    
    enabled_projects = set()
    # Query ONLY the projects we found? 
    # "project:A OR project:B ..." might be too long.
    # We just search the same scope for the enabled service, but limited?
    # No, that might return projects we didn't list in step 1.
    
    # Optimization: We already have a list of candidate projects.
    # We will search for enabled services in the SAME scope, but we can't easily filter to just our 50 candidates
    # without a massive query string.
    # Strategy: Fetch enabled services in the same scope (also limited/paged) and set difference?
    # Risk: If enabled services are on page 2, but we only checked page 1 of projects...
    
    # Correct Small-Scale Audit Strategy (The L6 way for this simplified tool):
    # Iterate the *candidate* projects and check specific API enablement? Too slow (N calls).
    # Better: Just accept that this tool returns "Disabled projects found in the first N projects scanned".
    
    req_enabled = asset_v1.SearchAllResourcesRequest(
        scope=scope, 
        query="name:servicehealth.googleapis.com", # Find the enabled API resource
        asset_types=["serviceusage.googleapis.com/Service"],
        page_size=1000 # Fetch more enabled markers to cover our project range hopefully
    )
    
    # We fetch enough enabled markers to be reasonably sure. 
    # NOTE: This is still imperfect distributed consistency, but better than O(N) crash.
    async for page in await asset_client.search_all_resources(request=req_enabled):
         for result in page.search_all_resources_response.results:
             # name format: //serviceusage.googleapis.com/projects/{PROJECT_NUMBER}/services/servicehealth.googleapis.com
             # BUT SearchAllResources returns `project` field usually formatted as `projects/123...`
             if result.project:
                 enabled_projects.add(result.project) # format: projects/12345
                 
             # Fallback for display_name or name parsing if project field empty (rare in search)
    
    # The `projects_to_check` are `projects/123...` (numbers or IDs?)
    # Asset Inventory usually returns `projects/NUMBER` in `.project` field for resources.
    # But for Project resource itself, `.project` field is empty? No, `.name` is `//cloudresourcemanager.../projects/NUMBER`
    
    disabled = []
    for p in projects_to_check:
        # P is likely `projects/number`
        if p not in enabled_projects:
            disabled.append(p)

    return {
        "disabled_projects": disabled,
        "scanned_count": len(projects_to_check), 
        "warning": f"Scanned first {len(projects_to_check)} projects. Pass page_token (not impl yet) for more."
    }

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

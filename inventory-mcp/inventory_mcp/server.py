"""Modernized Inventory-MCP Server"""
import logging
import os
from contextvars import ContextVar
from typing import Annotated, Any

# Google Imports
import google.cloud.asset_v1 as asset_v1
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
logger = logging.getLogger("mcp-inventory")

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
mcp = FastMCP("GCP-Inventory")

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

# --- TOOLS ---

@mcp.tool()
async def search_assets(
    query: str, 
    scope: str = "organizations/123456789", 
    asset_types: list[str] = ["compute.googleapis.com/Instance"],
    token: str = None
) -> list[dict]:
    """Search for GCP assets using Cloud Asset Inventory."""
    creds = create_creds(token)
    client = asset_v1.AssetServiceAsyncClient(credentials=creds)
    request = asset_v1.SearchAllResourcesRequest(
        scope=scope,
        query=query,
        asset_types=asset_types,
        page_size=50 # [PERF] Enforce page size
    )
    
    results = []
    # [PERF] Use the async iterator
    async for page in await client.search_all_resources(request=request):
        results.append({
            "name": page.name,
            "asset_type": page.asset_type,
            "display_name": page.display_name,
            "project": page.project,
            "state": page.state,
        })
        if len(results) >= 50: break
    
    return results

# --- ENTRYPOINT ---

if __name__ == "__main__":
    import uvicorn
    # mcp.http_app() returns a fully configured Starlette app
    app = mcp.http_app(middleware=middleware)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), lifespan="on")

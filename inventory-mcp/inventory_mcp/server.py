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
    page_size: int = 50,
    page_token: str = None,
    token: str = None
) -> dict:
    """Search for GCP assets using Cloud Asset Inventory.
    
    Returns:
        dict: {
            "resources": list[dict],
            "next_page_token": str | None
        }
    """
    creds = create_creds(token)
    client = asset_v1.AssetServiceAsyncClient(credentials=creds)
    request = asset_v1.SearchAllResourcesRequest(
        scope=scope,
        query=query,
        asset_types=asset_types,
        page_size=page_size,
        page_token=page_token
    )
    
    results = []
    # Using the raw response to get the token, manual iteration for page
    page_result = await client.search_all_resources(request=request)
    # The async iterator flattens pages. We need strictly ONE page.
    # Actually, the python client usually auto-pages. 
    # To Control page size strictly and get token, we use `byte_stream` or just `pages` property if available?
    # AssetServiceAsyncClient.search_all_resources returns SearchAllResourcesPager
    # We can iterate 'pages' property on it? No, async pager is slightly different.
    
    # Correct Paging in Google Async Client:
    # The `await client.search_all_resources` returns an AsyncPager. 
    # Calling `responses` roughly gives access to pages?
    # Simpler: The client abstracts pages. BUT we want to stop after one page and give the token.
    # Workaround: We rely on the request `page_size`. The client *should* fetch one page if we iterate, 
    # but `async for` will fetch next.
    # We can use `pages` property of the pager.
    
    # Standard Paging Pattern for Async Pagers in Python:
    # async for page in await client.search_all_resources(request=request).pages:
    #     ... process page ...
    #     next_token = page.next_page_token
    #     break
    
    pager = await client.search_all_resources(request=request)
    
    # Correct way to get a single page from AsyncPager
    async for page in pager.pages:
        for resource in page.search_all_resources_response.results:
            results.append({
                "name": resource.name,
                "asset_type": resource.asset_type,
                "display_name": resource.display_name,
                "project": resource.project,
                "state": resource.state,
            })
        
        return {
            "resources": results,
            "next_page_token": page.next_page_token if page.next_page_token else None
        }
    
    # If no pages
    return {"resources": [], "next_page_token": None}

# --- ENTRYPOINT ---

if __name__ == "__main__":
    import uvicorn
    # mcp.http_app() returns a fully configured Starlette app
    app = mcp.http_app(middleware=middleware)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), lifespan="on")

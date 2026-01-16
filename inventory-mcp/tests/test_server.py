import pytest
from unittest.mock import MagicMock, call
from inventory_mcp.server import search_resources

@pytest.mark.asyncio
async def test_search_resources_valid_search(mock_asset_client):
    # Setup mock data
    mock_resource = MagicMock()
    mock_resource.name = "//compute.googleapis.com/projects/p/zones/z/instances/i"
    mock_resource.asset_type = "compute.googleapis.com/Instance"
    mock_resource.location = "us-central1-a"
    mock_resource.display_name = "test-instance"
    
    # Configure mock to return this resource
    mock_asset_client.set_results([mock_resource])
    
    # Execute
    results = await search_resources(
        scope="projects/test-project",
        query="name:test",
        location="us-central1"
    )
    
    # Verify inputs
    mock_asset_client.search_all_resources.assert_called_once()
    call_kwargs = mock_asset_client.search_all_resources.call_args[1]
    request = call_kwargs["request"]
    
    assert request["scope"] == "projects/test-project"
    assert "state=ACTIVE" in request["query"]
    assert "location:us-central1" in request["query"]
    assert "name:test" in request["query"]
    assert request["page_size"] == 50
    
    # Verify output
    assert len(results) == 1
    assert results[0]["name"] == mock_resource.name
    assert results[0]["assetType"] == mock_resource.asset_type

@pytest.mark.asyncio
async def test_search_resources_pagination_limit(mock_asset_client):
    # Setup mock data - 60 items
    mock_items = []
    for i in range(60):
        m = MagicMock()
        m.name = f"resource-{i}"
        m.asset_type = "type"
        m.location = "loc"
        m.display_name = f"display-{i}"
        mock_items.append(m)
        
    mock_asset_client.set_results(mock_items)
    
    # Execute
    results = await search_resources(scope="projects/test")
    
    # Verify limit
    assert len(results) == 50
    assert results[0]["name"] == "resource-0"
    assert results[49]["name"] == "resource-49"

@pytest.mark.asyncio
async def test_search_resources_empty(mock_asset_client):
    # Setup mock data - empty
    mock_asset_client.set_results([])
    
    # Execute
    results = await search_resources(scope="projects/test")
    
    # Verify
    assert len(results) == 0

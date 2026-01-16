import pytest
from unittest.mock import AsyncMock, patch
from google.cloud import asset_v1

@pytest.fixture
def mock_asset_client():
    with patch("inventory_mcp.server.asset_v1.AssetServiceAsyncClient") as MockClient:
        mock_instance = MockClient.return_value
        # Default behavior for search_all_resources: return an empty async iterator
        
        async def async_iter(items):
            for item in items:
                yield item

        # Helper to set return value for search_all_resources
        def set_results(items):
            mock_instance.search_all_resources.return_value = async_iter(items)
            
        mock_instance.set_results = set_results
        # Default empty results
        set_results([])
        
        yield mock_instance



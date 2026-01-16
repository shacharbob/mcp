from unittest.mock import AsyncMock, MagicMock
import pytest


@pytest.fixture
def mock_health_client(mocker):
  """Mocks the ServiceHealthAsyncClient."""
  mock = mocker.patch(
      "psh_mcp.server.servicehealth_v1.ServiceHealthAsyncClient"
  )
  return mock.return_value


@pytest.fixture
def mock_asset_client(mocker):
  """Mocks the AssetServiceAsyncClient."""
  mock = mocker.patch("psh_mcp.server.asset_v1.AssetServiceAsyncClient")
  return mock.return_value


@pytest.fixture
def sample_event():
  """Returns a sample Google Cloud Event Protobuf-like dict."""
  return {
      "name": "projects/123/locations/global/events/event-abc",
      "title": "Packet Loss in us-central1",
      "state": "ACTIVE",
      "updateTime": "2024-01-01T12:00:00Z",
      "updates": [{
          "updateTime": "2024-01-01T11:00:00Z",
          "description": "Investigating issue...",
          "workaround": "Failover to us-east1",
      }],
  }




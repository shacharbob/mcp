from unittest.mock import AsyncMock
from psh_mcp.server import _format_event_details, list_active_events
import pytest


# --- Unit Tests: Data Transformation ---
def test_format_event_details(sample_event):
  """Verifies that raw GCP events are flattened correctly."""
  result = _format_event_details(sample_event)

  assert result["title"] == "Packet Loss in us-central1"
  assert result["latest_workaround"] == "Failover to us-east1"
  assert len(result["timeline"]) == 1
  assert result["timeline"][0]["description"] == "Investigating issue..."


# --- Integration Tests: Logic Flow ---
@pytest.mark.asyncio
async def test_list_active_events_valid(mock_health_client, sample_event):
  """Verifies the tool calls the GCP API with correct filters."""
  # Setup Mock
  mock_iterator = AsyncMock()
  mock_iterator.__aiter__.return_value = [sample_event]
  mock_health_client.list_events.return_value = mock_iterator

  # Execute
  events = await list_active_events(project_id="my-project", location="global")

  # Assert
  assert len(events) == 1
  assert events[0]["title"] == "Packet Loss in us-central1"

  # Verify the API was called with 'state = ACTIVE'
  args, kwargs = mock_health_client.list_events.call_args
  assert "state = ACTIVE" in kwargs["request"].filter


@pytest.mark.asyncio
async def test_list_active_events_security():
  """Verifies input validation blocks malicious project IDs."""
  with pytest.raises(ValueError, match="Invalid project_id"):
    await list_active_events(project_id="malicious; rm -rf /")

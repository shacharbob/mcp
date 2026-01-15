print("Starting test script")
import unittest

from src.server import _format_event_details


class TestTransformation(unittest.TestCase):

    def test_event_parsing(self):
        # Mocking the dictionary that MessageToDict would produce
        mock_event = {
            "name": "projects/123/events/abc",
            "title": "Test Outage",
            "state": "ACTIVE",
            "updateTime": "2024-01-01T12:00:00Z",
            "updates": [
                {
                    "updateTime": "2024-01-01T11:00:00Z",
                    "description": "Investigating...",
                    "workaround": "Do nothing.",
                }
            ],
            "impactedProducts": [{"productName": "Cloud SQL"}],
        }

        # Pass dict directly since our helper handles it
        result = _format_event_details(mock_event)

        self.assertEqual(result["title"], "Test Outage")
        self.assertEqual(result["latest_workaround"], "Do nothing.")
        self.assertEqual(result["impacted_products"], ["Cloud SQL"])
        self.assertEqual(result["impacted_products"], ["Cloud SQL"])
        print("Transformation Test Passed!")


import asyncio
from unittest.mock import MagicMock, patch

# Import the function to test
from src.server import list_projects_without_service_health


class TestAuditor(unittest.TestCase):

    def test_audit_logic(self):
        # We mock the AssetServiceClient
        with patch("src.server.asset_v1.AssetServiceClient") as MockClient:
            mock_instance = MockClient.return_value

            # Mock Response 1: List of Projects
            mock_p1 = MagicMock()
            mock_p1.project = "projects/100"
            mock_p2 = MagicMock()
            mock_p2.project = "projects/200"

            # Mock Response 2: List of Enabled Services (Only Project 100 has it)
            mock_s1 = MagicMock()
            mock_s1.name = "//.../projects/100/services/servicehealth..."

            # Configure side_effect for the two sequential calls
            mock_instance.search_all_resources.side_effect = [
                [mock_p1, mock_p2],
                [mock_s1],
            ]

            # Run the function (it is technically sync in our implementation above)
            # If we made it async, we would need asyncio.run()
            # The asset client code above was sync, so we call directly:
            result = asyncio.run(list_projects_without_service_health("orgs/1"))

            # Expectation: Project 200 is missing the service
            self.assertEqual(result, ["projects/200"])
            print("Audit Logic Test Passed!")


if __name__ == "__main__":
    unittest.main()

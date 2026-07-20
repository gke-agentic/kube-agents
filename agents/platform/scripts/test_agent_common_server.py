import unittest
import os
import sys
from pathlib import Path

# Add parent directory to sys.path
sys.path.insert(0, str(Path(__file__).parent.absolute()))

from agent_common_server import resolve_agent_credentials

class TestResolveAgentCredentials(unittest.TestCase):

    def test_resolve_credentials_success(self):
        os.environ["API_SERVER_KEY"] = "my-secret-key"
        os.environ["PLATFORM_API_URL"] = "my-endpoint:1234"
        endpoint, api_key = resolve_agent_credentials("platform")
        self.assertEqual(endpoint, "my-endpoint:1234")
        self.assertEqual(api_key, "my-secret-key")

    def test_resolve_credentials_unset(self):
        if "API_SERVER_KEY" in os.environ:
            del os.environ["API_SERVER_KEY"]
        with self.assertRaises(ValueError) as ctx:
            resolve_agent_credentials("platform")
        self.assertIn("API_SERVER_KEY is unset", str(ctx.exception))

    def test_resolve_credentials_empty(self):
        os.environ["API_SERVER_KEY"] = ""
        with self.assertRaises(ValueError) as ctx:
            resolve_agent_credentials("platform")
        self.assertIn("API_SERVER_KEY is unset", str(ctx.exception))

    def test_resolve_credentials_whitespace(self):
        os.environ["API_SERVER_KEY"] = "   "
        with self.assertRaises(ValueError) as ctx:
            resolve_agent_credentials("platform")
        self.assertIn("API_SERVER_KEY is unset", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()

import unittest
import os
import sys
from unittest.mock import patch

# Ensure script directory is in path
sys.path.insert(0, os.path.dirname(__file__))

from agent_common_server import resolve_agent_credentials

class TestAgentCommonServer(unittest.TestCase):
    @patch.dict(os.environ, {"API_SERVER_KEY": "secure_key", "PLATFORM_API_URL": "http://platform:8642"})
    def test_resolve_credentials_success(self):
        endpoint, api_key = resolve_agent_credentials("platform")
        self.assertEqual(endpoint, "http://platform:8642")
        self.assertEqual(api_key, "secure_key")

    @patch.dict(os.environ, {"API_SERVER_KEY": ""})
    def test_resolve_credentials_empty_key(self):
        with self.assertRaises(ValueError) as context:
            resolve_agent_credentials("platform")
        self.assertIn("API_SERVER_KEY is unset or empty", str(context.exception))

    @patch.dict(os.environ, {"API_SERVER_KEY": "   "})
    def test_resolve_credentials_whitespace_key(self):
        with self.assertRaises(ValueError) as context:
            resolve_agent_credentials("platform")
        self.assertIn("API_SERVER_KEY is unset or empty", str(context.exception))

    @patch.dict(os.environ, {"API_SERVER_KEY": "none"})
    def test_resolve_credentials_none_literal_key(self):
        with self.assertRaises(ValueError) as context:
            resolve_agent_credentials("platform")
        self.assertIn("API_SERVER_KEY is unset or empty", str(context.exception))

if __name__ == "__main__":
    unittest.main()

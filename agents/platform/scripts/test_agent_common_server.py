import os
import sys
import unittest
from pathlib import Path

# Add the directory containing agent_common_server.py to sys.path so it can be imported.
sys.path.insert(0, str(Path(__file__).parent.absolute()))

# agent_common_server pulls in FastMCP / pydantic / session_manager at import time.
# The credential logic under test needs none of them, so fall back to lightweight
# stubs when the real packages aren't importable (i.e. outside the hermes venv).
try:
    import agent_common_server  # noqa: F401
except Exception:
    import types
    for _name in ("mcp", "mcp.server", "mcp.server.fastmcp", "pydantic", "session_manager"):
        sys.modules[_name] = types.ModuleType(_name)
    sys.modules["mcp.server.fastmcp"].FastMCP = lambda *a, **k: types.SimpleNamespace(
        tool=lambda *a, **k: (lambda f: f), run=lambda: None)
    sys.modules["pydantic"].Field = lambda *a, **k: None
    sys.modules["session_manager"].SessionManager = object
    import agent_common_server  # noqa: F401

from agent_common_server import resolve_agent_credentials


class TestResolveAgentCredentials(unittest.TestCase):
    """API_SERVER_KEY must fail closed — never silently authenticate as a
    guessable literal when the shared secret is unconfigured (MCP-001)."""

    def setUp(self):
        self._saved = os.environ.get("API_SERVER_KEY")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("API_SERVER_KEY", None)
        else:
            os.environ["API_SERVER_KEY"] = self._saved

    def test_raises_when_key_unset(self):
        os.environ.pop("API_SERVER_KEY", None)
        with self.assertRaises(ValueError):
            resolve_agent_credentials("platform")

    def test_raises_when_key_empty(self):
        os.environ["API_SERVER_KEY"] = ""
        with self.assertRaises(ValueError):
            resolve_agent_credentials("platform")

    def test_raises_when_key_whitespace(self):
        os.environ["API_SERVER_KEY"] = "   "
        with self.assertRaises(ValueError):
            resolve_agent_credentials("platform")

    def test_never_falls_back_to_none_literal(self):
        """The regression pin: an unconfigured key must not yield the literal 'none'."""
        os.environ.pop("API_SERVER_KEY", None)
        try:
            _, api_key = resolve_agent_credentials("platform")
        except ValueError:
            return  # failing closed is the correct behavior
        self.assertNotEqual(
            api_key, "none",
            "must never authenticate with the guessable literal 'none'")

    def test_returns_endpoint_and_key_when_set(self):
        os.environ["API_SERVER_KEY"] = "s3cret"
        endpoint, api_key = resolve_agent_credentials("platform")
        self.assertEqual(api_key, "s3cret")
        self.assertIn("8642", endpoint)


if __name__ == "__main__":
    unittest.main()

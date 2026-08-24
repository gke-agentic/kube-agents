import io
import os
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from github_token_refresh import refresh_git_credentials


class GitHubTokenRefreshTest(unittest.TestCase):
    @patch("github_token_refresh.subprocess.run")
    @patch("github_token_refresh.urllib.request.urlopen")
    def test_sandbox_delegates_without_receiving_token(self, urlopen, run):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        urlopen.return_value = response

        with patch.dict(
            os.environ,
            {"CREDENTIAL_PROXY_URL": "http://127.0.0.1:8765"},
            clear=False,
        ):
            token = refresh_git_credentials("owner/repository")

        self.assertEqual("", token)
        run.assert_not_called()
        request = urlopen.call_args.args[0]
        self.assertEqual(
            "http://127.0.0.1:8765/v1/github/refresh", request.full_url
        )

    @patch("github_token_refresh.subprocess.run")
    @patch("github_token_refresh.urllib.request.urlopen")
    def test_missing_minty_scope_config_names_the_gap(self, urlopen, run):
        """A 500 for 'scope ... is not found for repository ...' means the
        repository's own Minty configuration never defined the requested
        scope -- a one-time setup gap, not a fault a retry can fix. The
        raised error should say that plainly, naming the scope and the
        repository, instead of surfacing only the opaque "HTTP 500"."""
        run.return_value = MagicMock()

        def raise_http_error(*_args, **_kwargs):
            # Verbatim shape of the response body Minty actually returns to
            # the caller (as opposed to its own server-side log line, which
            # additionally wraps this in a "failed to locate scope ..."
            # configuration-store error).
            body = (
                b'requested scope "platform-agent-scope" is not found for '
                b'repository "gke-agentic"/"kube-agents"'
            )
            raise urllib.error.HTTPError(
                url="http://token-broker/token",
                code=500,
                msg="Internal Server Error",
                hdrs=None,
                fp=io.BytesIO(body),
            )

        urlopen.side_effect = raise_http_error

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CREDENTIAL_PROXY_URL", None)
            with patch(
                "github_token_refresh.subprocess.run",
                return_value=MagicMock(stdout="fake-oidc-token\n", returncode=0),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    refresh_git_credentials("gke-agentic/kube-agents")

        message = str(ctx.exception)
        self.assertIn("platform-agent-scope", message)
        self.assertIn("gke-agentic/kube-agents", message)
        self.assertIn("configuration gap", message)
        self.assertIn("not a transient failure", message)


if __name__ == "__main__":
    unittest.main()

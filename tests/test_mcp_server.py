import io
import os
import unittest
from unittest.mock import patch

from mcp.server.fastmcp.exceptions import ToolError

from export_tracker import Issue, TrackerClient, TrackerError
from export_wiki import WikiClient, WikiError
from mcp_server import create_server, main
from yandex_mcp import YandexReader


class ServerToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.reader = YandexReader(TrackerClient("private-token", organization_id="org"), WikiClient("private-token", "org"))
        self.server = create_server(self.reader)

    async def test_registered_tool_returns_original_data_as_structured_and_text_results(self):
        with patch.object(self.reader.tracker, "get_issue", return_value=Issue("ABC-1", "Task", "Full text")):
            content, data = await self.server.call_tool("get_tracker_issue", {"issue_key": "ABC-1"})
        self.assertEqual("Full text", data["description"])
        self.assertIn("Full text", content[0].text)

    async def test_tool_errors_redact_tokens_for_all_expected_error_types(self):
        for error_type in (TrackerError, WikiError, OSError, ValueError):
            with self.subTest(error=error_type), patch.object(
                self.reader.tracker, "get_issue", side_effect=error_type("Failed with private-token")
            ):
                with self.assertRaises(ToolError) as raised:
                    await self.server.call_tool("get_tracker_issue", {"issue_key": "ABC-1"})
                self.assertNotIn("private-token", str(raised.exception))
                self.assertIn("[REDACTED]", str(raised.exception))

    async def test_unexpected_error_hides_exception_details(self):
        with patch.object(self.reader.tracker, "get_issue", side_effect=RuntimeError("internal private-token")):
            with self.assertRaisesRegex(ToolError, "Unexpected error while reading Yandex data") as raised:
                await self.server.call_tool("get_tracker_issue", {"issue_key": "ABC-1"})
        self.assertNotIn("internal", str(raised.exception))
        self.assertNotIn("private-token", str(raised.exception))


class ServerMainTest(unittest.TestCase):
    def test_missing_configuration_returns_two_and_does_not_start_server(self):
        stderr = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch("sys.stderr", stderr), patch("mcp_server.create_server") as create:
            self.assertEqual(2, main())
        self.assertIn("YANDEX_TRACKER_OAUTH_TOKEN", stderr.getvalue())
        create.assert_not_called()

    def test_valid_configuration_starts_stdio_without_printing_credentials(self):
        with patch.dict(os.environ, {"YANDEX_TRACKER_OAUTH_TOKEN": "private-token", "IDENTITY_HUB": "example-org"}, clear=True), \
                patch("mcp_server.create_server") as create, patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(0, main())
        create.return_value.run.assert_called_once_with(transport="stdio")
        self.assertEqual("", stdout.getvalue())

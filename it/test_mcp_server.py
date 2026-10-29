import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import AsyncExitStack
from datetime import timedelta
from functools import wraps
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
TOOLS = {
    "get_tracker_issue", "list_tracker_children", "get_tracker_issue_with_children",
    "get_wiki_page", "list_wiki_descendants", "get_wiki_page_with_children",
}


def protocol_test(method):
    @wraps(method)
    def run(self):
        async def scenario():
            async with AsyncExitStack() as stack:
                await self.open_session(stack)
                await method(self)
        asyncio.run(scenario())
    return run


class MCPProtocolTest(unittest.TestCase):
    async def open_session(self, stack):
        self.stack = stack
        self.work = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stderr = self.stack.enter_context(tempfile.TemporaryFile(mode="w+"))
        parameters = StdioServerParameters(
            command=sys.executable, args=[str(ROOT / "it/mcp_fixture.py")], cwd=self.work,
            env={"YANDEX_TRACKER_OAUTH_TOKEN": "fixture-secret", "IDENTITY_HUB": "fixture-org",
                 "PYTHONDONTWRITEBYTECODE": "1"},
        )
        streams = await self.stack.enter_async_context(stdio_client(parameters, errlog=self.stderr))
        self.session = await self.stack.enter_async_context(
            ClientSession(*streams, read_timeout_seconds=timedelta(seconds=10))
        )
        self.initialization = await self.session.initialize()

    async def call(self, name, arguments):
        result = await self.session.call_tool(name, arguments)
        self.assertFalse(result.isError, result.content)
        self.assertIsInstance(result.structuredContent, dict)
        self.assertEqual(result.structuredContent, json.loads(result.content[0].text))
        return result.structuredContent

    @protocol_test
    async def test_exposes_exactly_six_read_only_tools_with_typed_arguments(self):
        self.assertEqual("yandex-reader", self.initialization.serverInfo.name)
        tools = (await self.session.list_tools()).tools
        self.assertEqual(TOOLS, {tool.name for tool in tools})
        for tool in tools:
            self.assertTrue(tool.annotations.readOnlyHint)
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertTrue(tool.annotations.idempotentHint)
            argument = "issue_key" if "tracker" in tool.name else "url"
            self.assertEqual([argument], tool.inputSchema["required"])
            self.assertEqual("string", tool.inputSchema["properties"][argument]["type"])

    @protocol_test
    async def test_reads_one_task_and_lists_all_direct_children_without_disk_output(self):
        issue = await self.call("get_tracker_issue", {"issue_key": "ABC-1"})
        self.assertEqual("ABC-1", issue["key"])
        self.assertEqual("Текст задачи\n" + "полный текст " * 6000, issue["description"])
        self.assertEqual(["Comment ABC-1", "Reply ABC-1"], issue["comments"])
        self.assertEqual([{"id": "42", "name": "api.yaml"}], issue["attachments"])
        self.assertEqual([], issue["children"])
        children = await self.call("list_tracker_children", {"issue_key": "ABC-1"})
        self.assertEqual(["ABC-2", "ABC-4"], [child["key"] for child in children["children"]])
        self.assertEqual([], list(Path(self.work).iterdir()))

    @protocol_test
    async def test_reads_complete_task_tree_across_search_and_comment_pages(self):
        issue = await self.call("get_tracker_issue_with_children", {"issue_key": "ABC-1"})
        self.assertEqual(["ABC-2", "ABC-4"], [child["key"] for child in issue["children"]])
        grandchild = issue["children"][0]["children"][0]
        self.assertEqual("ABC-3", grandchild["key"])
        self.assertEqual(["Comment ABC-3", "Reply ABC-3"], grandchild["comments"])
        self.assertEqual([], grandchild["children"])

    @protocol_test
    async def test_reads_wiki_page_comments_and_all_descendant_pages(self):
        page = await self.call("get_wiki_page", {"url": "https://wiki.yandex.ru/space/root/"})
        self.assertEqual("Полный текст 10\n{{widget}}", page["content"])
        self.assertEqual([100, 101], [comment["id"] for comment in page["comments"]])
        self.assertEqual(100, page["comments"][1]["parent_id"])
        self.assertEqual("Alice", page["owner"])
        self.assertEqual(10, page["attachments"][0]["page_id"])
        descendants = await self.call("list_wiki_descendants", {"url": "https://wiki.yandex.ru/space/root/"})
        self.assertEqual([11, 12, 13], [item["id"] for item in descendants["descendants"]])
        tree = await self.call("get_wiki_page_with_children", {"url": "https://wiki.yandex.ru/space/root/"})
        self.assertEqual(4, len(tree["pages_by_slug"]))
        self.assertEqual(["space/root/missing"], tree["path_only_slugs"])
        grandchild = tree["pages_by_slug"]["space/root/child/grandchild"]
        self.assertEqual("Полный текст 12\n{{widget}}", grandchild["content"])
        self.assertEqual("Ответ Wiki", grandchild["comments"][1]["body"])
        self.assertEqual("space/root/child", tree["parent_by_slug"][grandchild["slug"]])
        self.assertEqual([], list(Path(self.work).iterdir()))

    @protocol_test
    async def test_api_failures_are_tool_errors_and_leave_the_session_usable(self):
        for status in (400, 401, 403, 404, 429, 503):
            with self.subTest(status=status):
                result = await self.session.call_tool("get_tracker_issue", {"issue_key": f"ABC-{status}"})
                self.assertTrue(result.isError)
                self.assertIn(str(status), result.content[0].text)
        self.assertEqual(TOOLS, {tool.name for tool in (await self.session.list_tools()).tools})

    @protocol_test
    async def test_descendant_failure_reports_an_error_instead_of_a_partial_tree(self):
        for tool, arguments in (("get_tracker_issue_with_children", {"issue_key": "ABC-9"}),
                                ("get_wiki_page_with_children", {"url": "https://wiki.yandex.ru/space/broken/"})):
            with self.subTest(tool=tool):
                result = await self.session.call_tool(tool, arguments)
                self.assertTrue(result.isError)
                self.assertIn("403", result.content[0].text)
                self.assertIsNone(result.structuredContent)

    @protocol_test
    async def test_invalid_arguments_return_errors_before_api_calls(self):
        cases = [(tool, {"issue_key": "../secret"} if "tracker" in tool else {"url": "https://evil.test/page"})
                 for tool in TOOLS]
        cases += [("get_tracker_issue", {}), ("get_tracker_issue", {"issue_key": 123}),
                  ("get_wiki_page", {"url": None})]
        for tool, arguments in cases:
            with self.subTest(tool=tool, arguments=arguments):
                result = await self.session.call_tool(tool, arguments)
                self.assertTrue(result.isError)

    @protocol_test
    async def test_expected_and_unexpected_errors_do_not_expose_credentials(self):
        for key in ("ABC-500", "ABC-700"):
            result = await self.session.call_tool("get_tracker_issue", {"issue_key": key})
            self.assertTrue(result.isError)
            self.assertNotIn("fixture-secret", result.model_dump_json())
        self.stderr.flush()
        self.stderr.seek(0)
        self.assertNotIn("fixture-secret", self.stderr.read())


class MCPStartupTest(unittest.TestCase):
    def test_missing_token_exits_with_helpful_stderr_and_empty_stdout(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "mcp_server.py")], env={"PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("YANDEX_TRACKER_OAUTH_TOKEN", result.stderr)
        self.assertEqual("", result.stdout)

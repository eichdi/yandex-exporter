import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from export_tracker import (
    Attachment,
    Issue,
    TrackerClient,
    attachments_from_response,
    load_issue_tree,
    main,
    safe_stem,
    total_page_count,
    write_issue_tree,
)


class IssueExportTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_directory = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_safe_stem_replaces_invalid_characters_and_uses_untitled_summary(self):
        self.assertEqual("ABC-1 — Root_task", safe_stem(Issue("ABC-1", "Root/task", "")))
        self.assertEqual("ABC-2 — untitled", safe_stem(Issue("ABC-2", "   ", "")))

    def test_write_issue_tree_creates_nested_markdown_files(self):
        root = Issue("ABC-1", "Root/task", "Root body", (
            Issue("ABC-2", "Child", "Child body"),
        ))

        write_issue_tree(root, self.output_directory)

        root_directory = self.output_directory / "ABC-1 — Root_task"

        self.assertEqual(
            "# ABC-1 — Root/task\n\n"
            "| Поле | Значение |\n"
            "| --- | --- |\n"
            "| Статус | — |\n"
            "| Тип | — |\n"
            "| Приоритет | — |\n"
            "| Исполнитель | — |\n"
            "| Автор | — |\n"
            "| Создана | — |\n"
            "| Обновлена | — |\n"
            "| Дедлайн | — |\n\n"
            "## Описание\n\nRoot body\n\n"
            "## Комментарии\n\nНет комментариев.\n",
            (root_directory / "ABC-1 — Root_task.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            "# ABC-2 — Child\n\n"
            "| Поле | Значение |\n"
            "| --- | --- |\n"
            "| Статус | — |\n"
            "| Тип | — |\n"
            "| Приоритет | — |\n"
            "| Исполнитель | — |\n"
            "| Автор | — |\n"
            "| Создана | — |\n"
            "| Обновлена | — |\n"
            "| Дедлайн | — |\n\n"
            "## Описание\n\nChild body\n\n"
            "## Комментарии\n\nНет комментариев.\n",
            (root_directory / "Подзадачи" / "ABC-2 — Child" / "ABC-2 — Child.md").read_text(
                encoding="utf-8"
            ),
        )
        self.assertTrue((root_directory / "Вложения").is_dir())
        self.assertTrue((root_directory / "Подзадачи").is_dir())

    def test_write_issue_tree_replaces_existing_root_export_after_summary_change(self):
        stale_file = self.output_directory / "ABC-1 — Previous summary.md"
        stale_file.write_text("stale", encoding="utf-8")
        stale_directory = self.output_directory / "ABC-1 — Previous summary"
        stale_directory.mkdir()
        (stale_directory / "stale.md").write_text("stale", encoding="utf-8")

        write_issue_tree(Issue("ABC-1", "Root", "fresh"), self.output_directory)

        self.assertIn(
            "## Описание\n\nfresh",
            (self.output_directory / "ABC-1 — Root" / "ABC-1 — Root.md").read_text(encoding="utf-8"),
        )
        self.assertFalse(stale_directory.exists())
        self.assertFalse(stale_file.exists())

    def test_write_issue_tree_writes_main_fields_and_comments(self):
        issue = Issue(
            "ABC-1",
            "Root",
            "Description",
            status="Open",
            issue_type="Task",
            priority="Critical",
            assignee="Jane Doe",
            author="John Doe",
            created_at="2026-09-18T10:00:00+0300",
            updated_at="2026-09-18T11:00:00+0300",
            deadline="2026-09-30",
            comments=("First comment", "Second comment"),
        )

        write_issue_tree(issue, self.output_directory)

        content = (self.output_directory / "ABC-1 — Root" / "ABC-1 — Root.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("| Статус | Open |", content)
        self.assertIn("| Дедлайн | 2026-09-30 |", content)
        self.assertIn("### 1\n\nFirst comment", content)
        self.assertIn("### 2\n\nSecond comment", content)

    def test_write_issue_tree_downloads_attachments_with_unique_names(self):
        issue = Issue(
            "ABC-1",
            "Root",
            "",
            attachments=(
                Attachment("1", "report.pdf"),
                Attachment("2", "report.pdf"),
            ),
        )
        downloads = []

        def download(issueKey, attachment):
            downloads.append((issueKey, attachment.id))
            return f"content-{attachment.id}".encode()

        write_issue_tree(issue, self.output_directory, download)

        attachments_directory = self.output_directory / "ABC-1 — Root" / "Вложения"
        self.assertEqual([("ABC-1", "1"), ("ABC-1", "2")], downloads)
        self.assertEqual(b"content-1", (attachments_directory / "report.pdf").read_bytes())
        self.assertEqual(b"content-2", (attachments_directory / "report (2).pdf").read_bytes())


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        return False


class TrackerClientTest(unittest.TestCase):
    def testExpandedAttachmentsUseDisplayAsFileName(self):
        self.assertEqual(
            (Attachment("101", "example.yaml"),),
            attachments_from_response([{
                "self": "https://api.tracker.yandex.net/v3/issues/ABC-123/attachments/101",
                "id": "101",
                "display": "example.yaml",
            }]),
        )

    def setUp(self):
        self.requests = []
        self.responses = [
            {
                "key": "ABC-1",
                "summary": "Root",
                "description": "Root body",
                "status": {"display": "Open"},
                "type": {"display": "Task"},
                "priority": {"display": "High"},
                "assignee": {"display": "Jane Doe"},
                "createdBy": {"display": "John Doe"},
                "createdAt": "2026-09-18T10:00:00+0300",
                "updatedAt": "2026-09-18T11:00:00+0300",
                "deadline": "2026-09-30",
                "attachments": [{"id": "1", "name": "report.pdf"}],
            },
            [{"text": "First comment"}],
            [{"key": "ABC-2", "summary": "First", "description": "First body"}],
            [{"key": "ABC-3", "summary": "Second", "description": "Second body"}],
            {"key": "ABC-2", "summary": "First", "description": "First body"},
            [],
            [],
            {"key": "ABC-3", "summary": "Second", "description": "Second body"},
            [],
            [],
        ]
        self.headers = [
            {},
            {},
            {"X-Total-Pages": "2"},
            {"X-Total-Pages": "2"},
            {},
            {},
            {},
            {},
            {},
            {},
        ]

    def opener(self, request, timeout):
        self.requests.append(request)
        return FakeResponse(self.responses.pop(0), self.headers.pop(0))

    def test_load_issue_tree_fetches_every_search_page_and_descendant(self):
        client = TrackerClient("token", opener=self.opener, organization_id="example-org")

        tree = load_issue_tree(client, "ABC-1")

        self.assertEqual(("ABC-2", "ABC-3"), tuple(child.key for child in tree.children))
        self.assertEqual("Open", tree.status)
        self.assertEqual(("First comment",), tree.comments)
        self.assertEqual((Attachment("1", "report.pdf"),), tree.attachments)
        self.assertEqual("OAuth token", self.requests[0].get_header("Authorization"))
        self.assertEqual(
            "example-org",
            self.requests[0].get_header("X-cloud-org-id"),
        )
        search_urls = [request.full_url for request in self.requests if "_search" in request.full_url]
        self.assertIn("expand=attachments", self.requests[0].full_url)
        self.assertIn("page=1", search_urls[0])
        self.assertIn("page=2", search_urls[1])

    def test_total_page_count_accepts_zero_for_an_empty_search_result(self):
        self.assertEqual(0, total_page_count({"X-Total-Pages": "0"}, "ABC-1"))

    def test_get_comments_follows_all_pages_and_uses_comment_text(self):
        responses = [
            [{"id": 1, "text": "First comment"}],
            [{"id": 2, "text": "Second comment"}],
        ]
        headers = [
            {
                "Link": (
                    '<https://api.tracker.yandex.net/v3/issues/ABC-1/comments?'
                    'perPage=100&id=1>; rel="next"'
                )
            },
            {},
        ]
        requests = []

        def opener(request, timeout):
            requests.append(request.full_url)
            return FakeResponse(responses.pop(0), headers.pop(0))

        comments = TrackerClient("token", opener=opener, organization_id="example-org").get_comments("ABC-1")

        self.assertEqual(("First comment", "Second comment"), comments)
        self.assertEqual(
            [
                "https://api.tracker.yandex.net/v3/issues/ABC-1/comments?perPage=100",
                "https://api.tracker.yandex.net/v3/issues/ABC-1/comments?perPage=100&id=1",
            ],
            requests,
        )


class CommandLineTest(unittest.TestCase):
    def test_main_passes_configured_organization_to_tracker(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"YANDEX_TRACKER_OAUTH_TOKEN": "token", "IDENTITY_HUB": "custom-org"}, clear=True):
                with patch("export_tracker.load_issue_tree", return_value=Issue("ABC-1", "Root", "")) as load:
                    self.assertEqual(0, main(["ABC-1", directory]))
                    self.assertEqual("custom-org", load.call_args.args[0].organization_id)

    def test_main_rejects_missing_or_blank_organization_before_api_access(self):
        for organization in (None, "", " "):
            values = {"YANDEX_TRACKER_OAUTH_TOKEN": "token"}
            if organization is not None:
                values["IDENTITY_HUB"] = organization
            with self.subTest(organization=organization), patch.dict(os.environ, values, clear=True):
                with patch("export_tracker.TrackerClient") as client:
                    self.assertEqual(2, main(["ABC-1"]))
                    client.assert_not_called()

    def test_main_returns_error_when_oauth_token_is_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(2, main(["ABC-1"]))

    def test_main_uses_current_directory_when_output_is_omitted(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory)
            with patch.dict(os.environ, {"YANDEX_TRACKER_OAUTH_TOKEN": "token", "IDENTITY_HUB": "example-org"}, clear=True):
                with patch("export_tracker.load_issue_tree", return_value=Issue("ABC-1", "Root", "")):
                    with patch("export_tracker.Path.cwd", return_value=output_directory):
                        self.assertEqual(0, main(["ABC-1"]))
            self.assertTrue((output_directory / "ABC-1 — Root" / "ABC-1 — Root.md").exists())


if __name__ == "__main__":
    unittest.main()

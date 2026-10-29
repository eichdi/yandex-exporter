import json
import os
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from export_tracker import Attachment, Issue, TrackerClient, TrackerError
from export_wiki import Attachment as WikiAttachment
from export_wiki import Comment, WikiClient, WikiError, WikiPage, WikiTree
from yandex_mcp import YandexReader


class YandexReaderTest(unittest.TestCase):
    def setUp(self):
        self.tracker = Mock(spec=TrackerClient)
        self.wiki = Mock(spec=WikiClient)
        self.reader = YandexReader(self.tracker, self.wiki)
        self.issue = Issue(
            "ABC-1", "Корневая задача", "Full **description**\n" + "текст " * 12000,
            status="Open", assignee="Alice", author="Bob", deadline="2026-12-01",
            comments=("Первый комментарий", "Ответ\nбез сокращений"),
            attachments=(Attachment("42", "api.yaml"),),
        )
        self.page = WikiPage(
            10, "space/root", "Страница", "wysiwyg", "**Исходная разметка**\n{{widget}}",
            {"created_at": "2026-10-28", "nested": {"value": "данные"}}, "Alice",
        )
        self.wiki.get_page_by_slug.return_value = {
            "id": 10, "slug": "space/root", "title": "Страница", "page_type": "wysiwyg",
            "content": "**Исходная разметка**\n{{widget}}",
            "attributes": {"created_at": "2026-10-28", "nested": {"value": "данные"}},
            "owner": {"user": {"display_name": "Alice"}},
        }
        self.wiki.get_comments.return_value = [{
            "id": 100, "body": "Комментарий", "created_at": "2026-10-28T10:00:00Z",
            "author": {"display_name": "Bob"}, "parent_id": 99, "thread_id": 98,
        }]
        self.wiki.get_attachments.return_value = [{
            "id": 20, "name": "doc.pdf", "mimetype": "application/pdf", "is_downloadable": True,
        }]

    def test_issue_preserves_full_content_and_metadata_without_fetching_children(self):
        self.tracker.get_issue.return_value = self.issue
        result = self.reader.get_tracker_issue("ABC-1")
        self.assertEqual(self.issue.description, result["description"])
        self.assertEqual(["Первый комментарий", "Ответ\nбез сокращений"], result["comments"])
        self.assertEqual([{"id": "42", "name": "api.yaml"}], result["attachments"])
        self.assertEqual("2026-12-01", result["deadline"])
        self.assertEqual("Alice", result["assignee"])
        self.assertEqual([], result["children"])
        self.assertEqual(result, json.loads(json.dumps(result)))
        self.tracker.get_child_issues.assert_not_called()
        self.tracker.download_attachment.assert_not_called()

    def test_children_list_returns_only_direct_child_summaries(self):
        self.tracker.get_child_issues.return_value = [Issue("ABC-2", "Child", "large body", status="Open")]
        result = self.reader.list_tracker_children("ABC-1")
        self.assertEqual({"parent_key": "ABC-1", "children": [{
            "key": "ABC-2", "summary": "Child", "status": "Open",
        }]}, result)
        self.tracker.get_issue.assert_not_called()

    def test_complete_issue_tree_includes_grandchildren_and_their_comments(self):
        grandchild = Issue("ABC-3", "Grandchild", "Third level", comments=("Nested comment",))
        self.tracker.get_issue.side_effect = {"ABC-1": self.issue, "ABC-2": Issue("ABC-2", "Child", "Child body"),
                                              "ABC-3": grandchild}.__getitem__
        self.tracker.get_child_issues.side_effect = {
            "ABC-1": [Issue("ABC-2", "Child", "")], "ABC-2": [grandchild], "ABC-3": [],
        }.__getitem__
        result = self.reader.get_tracker_issue_with_children("ABC-1")
        nested = result["children"][0]["children"][0]
        self.assertEqual("ABC-3", nested["key"])
        self.assertEqual("Third level", nested["description"])
        self.assertEqual(["Nested comment"], nested["comments"])
        self.tracker.download_attachment.assert_not_called()

    def test_failed_descendant_read_does_not_return_a_partial_tree(self):
        self.tracker.get_issue.side_effect = [self.issue, TrackerError("HTTP 403")]
        self.tracker.get_child_issues.return_value = [Issue("ABC-2", "Child", "")]
        with self.assertRaisesRegex(TrackerError, "403"):
            self.reader.get_tracker_issue_with_children("ABC-1")

    def test_wiki_page_preserves_source_comments_and_page_specific_attachments(self):
        result = self.reader.get_wiki_page("https://wiki.yandex.ru/space/root/?from=browser#section")
        self.assertEqual("**Исходная разметка**\n{{widget}}", result["content"])
        self.assertEqual("Alice", result["owner"])
        self.assertEqual({"value": "данные"}, result["attributes"]["nested"])
        self.assertEqual("Комментарий", result["comments"][0]["body"])
        self.assertEqual(99, result["comments"][0]["parent_id"])
        self.assertEqual("doc.pdf", result["attachments"][0]["name"])
        self.assertEqual(10, result["attachments"][0]["page_id"])
        self.wiki.get_page_by_slug.assert_called_once_with("space/root")
        self.wiki.get_descendants.assert_not_called()
        self.wiki.download_attachment.assert_not_called()

    def test_wiki_descendants_list_resolves_titles_missing_from_descendant_metadata(self):
        self.wiki.get_descendants.return_value = [
            {"id": 11, "slug": "space/root/child/grandchild"},
        ]
        self.wiki.get_page_by_id.return_value = {
            **self.wiki.get_page_by_slug.return_value,
            "id": 11, "slug": "space/root/child/grandchild", "title": "Внук",
        }
        result = self.reader.list_wiki_descendants("https://wiki.yandex.ru/space/root/")
        self.assertEqual({"root_id": 10, "root_slug": "space/root", "descendants": [
            {"id": 11, "slug": "space/root/child/grandchild", "title": "Внук"},
        ]}, result)
        self.wiki.get_descendants.assert_called_once_with(10)
        self.wiki.get_comments.assert_not_called()
        self.wiki.get_page_by_id.assert_called_once_with(11)

    def test_wiki_tree_preserves_sparse_ancestors_and_nested_content(self):
        child = replace(self.page, id=11, slug="space/root/missing/child", content="Nested text")
        tree = WikiTree("space/root", 10, {self.page.slug: self.page, child.slug: child},
                        {"space/root": None, "space/root/missing": "space/root",
                         child.slug: "space/root/missing"}, ["space/root/missing"])
        with patch("yandex_mcp.load_wiki_tree", return_value=tree) as loader:
            result = self.reader.get_wiki_page_with_children("https://wiki.yandex.ru/space/root/")
        self.assertEqual(10, result["root_id"])
        self.assertEqual("Nested text", result["pages_by_slug"][child.slug]["content"])
        self.assertEqual(["space/root/missing"], result["path_only_slugs"])
        self.assertEqual("space/root/missing", result["parent_by_slug"][child.slug])
        self.assertIsNone(result["parent_by_slug"]["space/root"])
        loader.assert_called_once_with(self.wiki, "space/root")

    def test_invalid_issue_keys_are_rejected_before_requests(self):
        for method in (self.reader.get_tracker_issue, self.reader.list_tracker_children,
                       self.reader.get_tracker_issue_with_children):
            for value in ("", "ABC-1/comments", "../ABC-1", "ABC-1?x=1", " ABC-1", "ABC-1\n"):
                with self.subTest(method=method.__name__, value=value), self.assertRaises(ValueError):
                    method(value)
        self.tracker.get_issue.assert_not_called()
        self.tracker.get_child_issues.assert_not_called()

    def test_invalid_wiki_urls_are_rejected_before_requests(self):
        for method in (self.reader.get_wiki_page, self.reader.list_wiki_descendants,
                       self.reader.get_wiki_page_with_children):
            for value in ("https://example.com/page", "http://wiki.yandex.ru/page", "https://wiki.yandex.ru/",
                          "https://user:password@wiki.yandex.ru/page"):
                with self.subTest(method=method.__name__, value=value), self.assertRaises(WikiError):
                    method(value)
        self.wiki.get_page_by_slug.assert_not_called()


class ConfigurationTest(unittest.TestCase):
    def test_configures_both_clients_with_the_same_organization_and_token(self):
        with patch.dict(os.environ, {"YANDEX_TRACKER_OAUTH_TOKEN": "private-token", "IDENTITY_HUB": "custom-org"}, clear=True):
            reader = YandexReader.from_environment()
        self.assertEqual("custom-org", reader.tracker.organization_id)
        self.assertEqual("custom-org", reader.wiki.organization_id)
        self.assertEqual("private-token", reader.tracker.token)
        self.assertEqual("private-token", reader.wiki.token)

    def test_organization_is_required(self):
        with patch.dict(os.environ, {"YANDEX_TRACKER_OAUTH_TOKEN": "private-token"}, clear=True):
            with self.assertRaisesRegex(ValueError, "IDENTITY_HUB"):
                YandexReader.from_environment()

    def test_missing_or_blank_settings_are_rejected_without_exposing_token(self):
        for values in ({}, {"YANDEX_TRACKER_OAUTH_TOKEN": " "},
                       {"YANDEX_TRACKER_OAUTH_TOKEN": "private-token", "IDENTITY_HUB": " "}):
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True):
                with self.assertRaises(ValueError) as raised:
                    YandexReader.from_environment()
                self.assertNotIn("private-token", str(raised.exception))


class TrackerOrganizationTest(unittest.TestCase):
    def test_custom_organization_is_sent_for_json_and_attachment_requests(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"{}"
        opener = Mock(return_value=response)
        client = TrackerClient("token", opener, organization_id="custom-org")
        client.request_json("/issues/ABC-1")
        client.request_bytes("/issues/ABC-1/attachments/1/file")
        for call in opener.call_args_list:
            self.assertEqual("custom-org", call.args[0].get_header("X-cloud-org-id"))

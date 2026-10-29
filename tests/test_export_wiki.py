import json
import os
import subprocess
import tempfile
import unittest
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from export_wiki import (
    Comment,
    WikiClient,
    WikiError,
    WikiPage,
    load_wiki_tree,
    parse_wiki_url,
    render_summary,
    main,
    publish_tree,
    write_tree,
)


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


class BytesResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        return False


class BrokenBytesResponse(BytesResponse):
    def __init__(self):
        pass

    def read(self):
        raise IncompleteRead(b"partial")


class WikiUrlTest(unittest.TestCase):
    def test_imports_with_the_python3_command(self):
        result = subprocess.run(
            ["python3", "-c", "import export_wiki"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, result.returncode, result.stderr)

    def test_parses_decoded_wiki_path_and_ignores_browser_parameters(self):
        self.assertEqual(
            "раздел/страница",
            parse_wiki_url("https://wiki.yandex.ru/%D1%80%D0%B0%D0%B7%D0%B4%D0%B5%D0%BB/%D1%81%D1%82%D1%80%D0%B0%D0%BD%D0%B8%D1%86%D0%B0/?view=tree#comments"),
        )

    def test_rejects_non_wiki_urls_and_credentials(self):
        for value in (
            "http://wiki.yandex.ru/page",
            "https://example.test/page",
            "https://user:password@wiki.yandex.ru/page",
            "https://wiki.yandex.ru/",
        ):
            with self.subTest(value=value):
                with self.assertRaises(WikiError):
                    parse_wiki_url(value)


class WikiClientTest(unittest.TestCase):
    def test_collects_all_cursor_pages_even_when_a_page_is_empty(self):
        responses = [
            {"results": [{"id": 1, "slug": "first"}], "next_cursor": "one"},
            {"results": [], "next_cursor": "two"},
            {"results": [{"id": 2, "slug": "second"}], "next_cursor": None},
        ]
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(responses.pop(0))

        pages = WikiClient("token", "organization", opener=opener).get_descendants(99)

        self.assertEqual([1, 2], [page["id"] for page in pages])
        self.assertEqual("OAuth token", requests[0].get_header("Authorization"))
        self.assertEqual("organization", requests[0].get_header("X-cloud-org-id"))
        self.assertEqual("100", parse_qs(urlparse(requests[0].full_url).query)["page_size"][0])
        self.assertEqual("one", parse_qs(urlparse(requests[1].full_url).query)["cursor"][0])
        self.assertEqual("two", parse_qs(urlparse(requests[2].full_url).query)["cursor"][0])

    def test_rejects_repeated_cursor_to_prevent_an_endless_export(self):
        responses = [
            {"results": [{"id": 1, "slug": "first"}], "next_cursor": "same"},
            {"results": [{"id": 2, "slug": "second"}], "next_cursor": "same"},
        ]

        def opener(request, timeout):
            return FakeResponse(responses.pop(0))

        with self.assertRaisesRegex(WikiError, "cursor"):
            WikiClient("token", "organization", opener=opener).get_descendants(99)

    def test_downloads_attachment_through_page_specific_api_endpoint(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return BytesResponse(b"file bytes")

        attachment = __import__("export_wiki").Attachment(
            18448666,
            "image.png",
            "",
            "",
            "",
            "image/png",
            True,
            "/space/page/.files/image.png",
            7,
        )

        result = WikiClient("token", "organization", opener=opener).download_attachment(attachment)

        self.assertEqual(b"file bytes", result)
        self.assertEqual(
            "https://api.wiki.yandex.net/v1/pages/7/attachments/18448666/download",
            requests[0].full_url,
        )
        self.assertEqual("OAuth token", requests[0].get_header("Authorization"))

    def test_refuses_attachment_without_a_page_id(self):
        attachment = __import__("export_wiki").Attachment(
            1, "file", "", "", "", "", True, "https://example.test/file", None
        )

        with self.assertRaisesRegex(WikiError, "page ID"):
            WikiClient("token", "organization").download_attachment(attachment)

    def test_retries_an_interrupted_attachment_download(self):
        attempts = 0

        def opener(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return BrokenBytesResponse()
            return BytesResponse(b"complete")

        attachment = __import__("export_wiki").Attachment(
            1, "file", "", "", "", "", True, "", 7
        )

        result = WikiClient("token", "organization", opener=opener).download_attachment(attachment)

        self.assertEqual(b"complete", result)
        self.assertEqual(2, attempts)


class WikiTreeTest(unittest.TestCase):
    def test_preserves_wysiwyg_source_and_renders_comment_metadata(self):
        page = WikiPage(
            id=7,
            slug="space/root",
            title="Root",
            page_type="wysiwyg",
            content="{% tree %}",
            attributes={"created_at": "2026-09-24T10:00:00Z"},
            owner="Ирина",
            comments=(
                Comment(
                    id=9,
                    body="Проверьте цифры",
                    author="Мария",
                    created_at="2026-09-24T11:00:00Z",
                    parent_id=None,
                    thread_id=9,
                    inline_text="Число клиентов",
                    resolve_status="unresolved",
                    is_deleted=False,
                    reactions=("like",),
                ),
            ),
        )

        summary = render_summary(page, [], [])

        self.assertIn("{% tree %}", summary)
        self.assertIn("| ID | 7 |", summary)
        self.assertIn("| Владелец | Ирина |", summary)
        self.assertIn("Мария · 2026-09-24T11:00:00Z", summary)
        self.assertIn("Привязка: Число клиентов", summary)
        self.assertIn("Статус: unresolved", summary)

    def test_creates_path_only_ancestor_for_accessible_descendant(self):
        class Client:
            def get_page_by_slug(self, slug):
                return {"id": 1, "slug": slug, "title": "Root", "page_type": "page", "content": "root"}

            def get_descendants(self, page_id):
                return [{"id": 3, "slug": "space/root/hidden/child"}]

            def get_page_by_id(self, page_id):
                return {"id": 3, "slug": "space/root/hidden/child", "title": "Child", "page_type": "page", "content": "child"}

            def get_comments(self, page_id):
                return []

            def get_attachments(self, page_id):
                return []

        tree = load_wiki_tree(Client(), "space/root")

        self.assertEqual(["space/root/hidden"], tree.path_only_slugs)
        self.assertEqual("space/root/hidden", tree.parent_by_slug["space/root/hidden/child"])
        self.assertEqual("space/root", tree.parent_by_slug["space/root/hidden"])


class WikiWriterTest(unittest.TestCase):
    def test_writes_nested_tree_with_safe_unique_names_and_attachments(self):
        root = WikiPage(1, "space/root", "Корень", "page", "root", {}, "")
        first = WikiPage(
            2,
            "space/root/one",
            "Отчёт/2026",
            "page",
            "first",
            {},
            "",
            attachments=(
                __import__("export_wiki").Attachment(10, "report.pdf", "", "", "", "application/pdf", True),
                __import__("export_wiki").Attachment(11, "../report.pdf", "", "", "", "application/pdf", True),
            ),
        )
        second = WikiPage(3, "space/root/two", "Отчёт/2026", "page", "second", {}, "")
        tree = __import__("export_wiki").WikiTree(
            "space/root",
            1,
            {root.slug: root, first.slug: first, second.slug: second},
            {root.slug: None, first.slug: root.slug, second.slug: root.slug},
            [],
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            counts = write_tree(tree, output, lambda attachment: f"bytes-{attachment.id}".encode())
            root_directory = output / "Корень"
            child_directory = root_directory / "Подстраницы"

            self.assertEqual((3, 0, 2), counts)
            self.assertTrue((root_directory / "Корень — summary.md").is_file())
            self.assertEqual(2, len(list(child_directory.iterdir())))
            self.assertEqual(b"bytes-10", next(child_directory.glob("*/Вложения/report.pdf")).read_bytes())
            self.assertEqual(b"bytes-11", next(child_directory.glob("*/Вложения/_report.pdf")).read_bytes())
            self.assertFalse((output.parent / "report.pdf").exists())

    def test_writes_empty_service_directories_for_every_real_page(self):
        page = WikiPage(1, "space/root", "Root", "page", "", {}, "")
        tree = __import__("export_wiki").WikiTree("space/root", 1, {page.slug: page}, {page.slug: None}, [])

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            write_tree(tree, directory, lambda attachment: b"")
            self.assertTrue((directory / "Root" / "Вложения").is_dir())
            self.assertTrue((directory / "Root" / "Подстраницы").is_dir())


class WikiPublicationTest(unittest.TestCase):
    def test_failed_load_keeps_previous_export_and_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            old_export = output / "Old title"
            old_export.mkdir()
            (old_export / "Old title — summary.md").write_text(
                "<!-- yandex-wiki-export-root-id: 1 -->\nold", encoding="utf-8"
            )
            unrelated = output / "Unrelated"
            unrelated.mkdir()
            (unrelated / "keep.txt").write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(WikiError, "download failed"):
                publish_tree(
                    failing_tree(),
                    output,
                    lambda attachment: (_ for _ in ()).throw(WikiError("download failed")),
                )

            self.assertEqual("<!-- yandex-wiki-export-root-id: 1 -->\nold", (old_export / "Old title — summary.md").read_text(encoding="utf-8"))
            self.assertEqual("keep", (unrelated / "keep.txt").read_text(encoding="utf-8"))

    def test_main_rejects_missing_or_blank_organization_before_api_access(self):
        for organization in (None, "", " "):
            values = {"YANDEX_TRACKER_OAUTH_TOKEN": "token"}
            if organization is not None:
                values["IDENTITY_HUB"] = organization
            with self.subTest(organization=organization), patch.dict(os.environ, values, clear=True):
                with patch("export_wiki.WikiClient") as client:
                    self.assertEqual(2, main(["https://wiki.yandex.ru/space/root/"]))
                    client.assert_not_called()

    def test_main_requires_token_without_printing_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(2, main(["https://wiki.yandex.ru/space/root/"]))


def failing_tree():
    page = WikiPage(
        1,
        "space/root",
        "Root",
        "page",
        "content",
        {},
        "",
        attachments=(__import__("export_wiki").Attachment(2, "file.bin", "", "", "", "", True),),
    )
    return __import__("export_wiki").WikiTree(page.slug, page.id, {page.slug: page}, {page.slug: None}, [])


if __name__ == "__main__":
    unittest.main()

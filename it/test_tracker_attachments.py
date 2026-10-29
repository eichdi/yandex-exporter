import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from export_tracker import Attachment, Issue, TrackerClient, main


class TrackerAttachmentExportTest(unittest.TestCase):
    def testMainDownloadsAttachmentsForTheirOwningIssue(self):
        root = Issue(
            "ABC-123", "Root", "",
            children=(Issue(
                "ABC-124", "Child", "",
                attachments=(Attachment("102", "child.yaml"),),
            ),),
            attachments=(Attachment("101", "example.yaml"),),
        )
        responses = {
            "/issues/ABC-123/attachments/101/example.yaml": b"root content",
            "/issues/ABC-124/attachments/102/child.yaml": b"child content",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"YANDEX_TRACKER_OAUTH_TOKEN": "token", "IDENTITY_HUB": "example-org"}):
                with patch("export_tracker.load_issue_tree", return_value=root):
                    with patch.object(TrackerClient, "request_bytes", side_effect=responses.__getitem__):
                        self.assertEqual(0, main(["ABC-123", directory]))
            rootDirectory = Path(directory) / "ABC-123 — Root"
            self.assertEqual(
                b"root content",
                (rootDirectory / "Вложения" / "example.yaml").read_bytes(),
            )
            self.assertEqual(
                b"child content",
                (rootDirectory / "Подзадачи" / "ABC-124 — Child" / "Вложения" / "child.yaml").read_bytes(),
            )

import base64
import unittest
from dataclasses import replace
from unittest.mock import Mock

from export_tracker import Attachment, Issue, TrackerClient, TrackerError
from export_wiki import WikiClient, WikiError
from yandex_mcp import YandexReader


class YandexAttachmentsTest(unittest.TestCase):
    def setUp(self):
        self.tracker = Mock(spec=TrackerClient)
        self.wiki = Mock(spec=WikiClient)
        self.reader = YandexReader(self.tracker, self.wiki)
        self.tracker.get_issue.return_value = Issue(
            'ABC-1', 'Task', '', attachments=(Attachment('42', 'api.yaml'),))
        self.tracker.download_attachment.return_value = b'openapi: 3.0.0\n'
        self.wiki.get_page_by_slug.return_value = {'id': 10, 'slug': 'space/root', 'title': 'Page', 'page_type': 'wysiwyg', 'content': 'Original'}
        self.wiki.get_attachments.return_value = [
            {'id': 20, 'name': 'file.pdf', 'mimetype': 'application/pdf', 'is_downloadable': True}]
        self.wiki.download_attachment.return_value = b'%PDF-1.4\noriginal\x00\xff'

    def test_tracker_downloads_only_the_selected_attachment_as_text(self):
        result = self.reader.get_tracker_attachment('ABC-1', '42')
        self.assertEqual('openapi: 3.0.0\n', result.content[1].text)
        self.assertEqual('api.yaml', result.structuredContent['name'])
        self.assertIn('ABC-1', result.structuredContent['uri'])
        self.tracker.get_issue.assert_called_once_with('ABC-1')
        self.tracker.download_attachment.assert_called_once_with('ABC-1', Attachment('42', 'api.yaml'))

    def test_wiki_resolves_page_and_preserves_original_file_bytes(self):
        result = self.reader.get_wiki_attachment('https://wiki.yandex.ru/space/root/', 20)
        self.assertEqual(b'%PDF-1.4\noriginal\x00\xff', base64.b64decode(result.content[1].resource.blob))
        self.assertIn('/10/', result.structuredContent['uri'])
        self.wiki.get_page_by_slug.assert_called_once_with('space/root')
        downloaded = self.wiki.download_attachment.call_args.args[0]
        self.assertEqual(10, downloaded.page_id)
        self.assertEqual(20, downloaded.id)
        self.assertEqual('file.pdf', downloaded.name)
        self.wiki.get_comments.assert_not_called()

    def test_wiki_metadata_image_mime_is_used(self):
        self.wiki.get_attachments.return_value[0].update(name='screenshot', mimetype='image/png')
        self.wiki.download_attachment.return_value = b'original image'
        result = self.reader.get_wiki_attachment('https://wiki.yandex.ru/space/root/', 20)
        self.assertEqual('image', result.content[1].type)
        self.assertEqual('image/png', result.content[1].mimeType)
        self.assertEqual(b'original image', base64.b64decode(result.content[1].data))

    def test_tracker_escapes_filename_in_resource_uri(self):
        attachment = Attachment('42', '../my file?#.pdf')
        self.tracker.get_issue.return_value = replace(self.tracker.get_issue.return_value, attachments=(attachment,))
        result = self.reader.get_tracker_attachment('ABC-1', '42')
        self.assertTrue(result.structuredContent['uri'].endswith('..%2Fmy%20file%3F%23.pdf'))
        self.tracker.download_attachment.assert_called_once_with('ABC-1', attachment)

    def test_invalid_tracker_arguments_do_not_access_api(self):
        for key, identifier in (('../ABC-1', '42'), ('ABC-1', ''), ('ABC-1', ' ')):
            with self.subTest(key=key, identifier=identifier), self.assertRaises(ValueError):
                self.reader.get_tracker_attachment(key, identifier)
        self.tracker.get_issue.assert_not_called()
        self.tracker.download_attachment.assert_not_called()

    def test_invalid_wiki_arguments_do_not_access_api(self):
        for url, identifier in (('https://evil.test/page', 20),
                                ('https://wiki.yandex.ru/space/root/', 0),
                                ('https://wiki.yandex.ru/space/root/', -1)):
            with self.subTest(url=url, identifier=identifier), self.assertRaises((ValueError, WikiError)):
                self.reader.get_wiki_attachment(url, identifier)
        self.wiki.get_page_by_slug.assert_not_called()
        self.wiki.download_attachment.assert_not_called()

    def test_unknown_ids_do_not_download_any_file(self):
        with self.assertRaisesRegex(TrackerError, 'not found'):
            self.reader.get_tracker_attachment('ABC-1', '999')
        with self.assertRaisesRegex(WikiError, 'not found'):
            self.reader.get_wiki_attachment('https://wiki.yandex.ru/space/root/', 999)
        self.tracker.download_attachment.assert_not_called()
        self.wiki.download_attachment.assert_not_called()

    def test_wiki_non_downloadable_attachment_is_rejected(self):
        self.wiki.get_attachments.return_value[0]['is_downloadable'] = False
        with self.assertRaisesRegex(WikiError, 'not downloadable'):
            self.reader.get_wiki_attachment('https://wiki.yandex.ru/space/root/', 20)
        self.wiki.download_attachment.assert_not_called()

    def test_download_errors_propagate_without_returning_partial_content(self):
        self.tracker.download_attachment.side_effect = TrackerError('HTTP 403')
        with self.assertRaisesRegex(TrackerError, '403'):
            self.reader.get_tracker_attachment('ABC-1', '42')
        self.wiki.download_attachment.side_effect = WikiError('HTTP 404')
        with self.assertRaisesRegex(WikiError, '404'):
            self.reader.get_wiki_attachment('https://wiki.yandex.ru/space/root/', 20)

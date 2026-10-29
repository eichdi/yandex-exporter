import base64
import unittest

from attachment_content import attachment_result


class AttachmentContentTest(unittest.TestCase):
    def test_recognised_text_files_preserve_full_text(self):
        text = 'Привет\r\n{"key": "value"}\n' + 'original ' * 5000
        for name in ('notes.txt', 'README.MD', 'api.yaml', 'api.yml', 'data.json', 'events.jsonl',
                     'file.xml', 'report.csv', 'data.tsv', 'config.toml', 'app.py', 'app.ts',
                     'query.sql', 'application.properties', '.env', 'Dockerfile', 'Makefile'):
            with self.subTest(name=name):
                result = attachment_result(name, text.encode(), 'attachment://example/1')
                self.assertEqual('text', result.structuredContent['representation'])
                self.assertEqual(name, result.structuredContent['name'])
                self.assertEqual(len(text.encode()), result.structuredContent['size'])
                self.assertEqual('attachment://example/1', result.structuredContent['uri'])
                self.assertEqual('text', result.content[1].type)
                self.assertEqual(text, result.content[1].text)

    def test_text_mime_types_work_without_a_known_extension(self):
        for mimetype in ('text/plain; charset=utf-8', 'application/json', 'application/yaml',
                         'application/problem+json', 'application/example+xml', 'text/x-custom'):
            with self.subTest(mimetype=mimetype):
                result = attachment_result('attachment.custom', b'original text', 'attachment://1', mimetype)
                self.assertEqual('text', result.structuredContent['representation'])
                self.assertEqual('original text', result.content[1].text)

    def test_binary_download_mime_does_not_hide_a_text_filename(self):
        result = attachment_result('notes.md', b'# Title', 'attachment://1', 'application/octet-stream')
        self.assertEqual('# Title', result.content[1].text)

    def test_unicode_boms_decode_without_replacement_or_bom_characters(self):
        for encoding, prefix in (('utf-8-sig', b''), ('utf-16-le', b'\xff\xfe'),
                                 ('utf-16-be', b'\xfe\xff'), ('utf-32-le', b'\xff\xfe\x00\x00'),
                                 ('utf-32-be', b'\x00\x00\xfe\xff')):
            with self.subTest(encoding=encoding):
                result = attachment_result('notes.txt', prefix + 'Привет'.encode(encoding), 'attachment://1')
                self.assertEqual('Привет', result.content[1].text)

    def test_malformed_bom_text_is_preserved_as_original(self):
        for payload in (b'\xff\xfe\x00', b'\x00\x00\xfe\xff\x00'):
            with self.subTest(payload=payload):
                result = attachment_result('notes.txt', payload, 'attachment://1')
                self.assertEqual('file', result.structuredContent['representation'])
                self.assertIn('decode', result.structuredContent['reason'])
                self.assertEqual(payload, base64.b64decode(result.content[1].resource.blob))

    def test_images_use_original_bytes_in_native_image_blocks(self):
        for name, mimetype in (('screenshot.PNG', 'image/png'), ('photo.jpeg', 'image/jpeg'),
                               ('picture.webp', 'image/webp'), ('animation.gif', 'image/gif'),
                               ('diagram.svg', 'image/svg+xml')):
            with self.subTest(name=name):
                payload = b'original image bytes\x00\xff'
                result = attachment_result(name, payload, 'attachment://1')
                self.assertEqual('image', result.structuredContent['representation'])
                self.assertEqual('image', result.content[1].type)
                self.assertEqual(mimetype, result.content[1].mimeType)
                self.assertEqual(payload, base64.b64decode(result.content[1].data))

    def test_image_mime_type_works_for_an_extensionless_file(self):
        result = attachment_result('image', b'image bytes', 'attachment://1', 'image/png')
        self.assertEqual('image', result.content[1].type)
        self.assertEqual('image/png', result.content[1].mimeType)

    def test_other_files_are_original_binary_resources_even_if_bytes_are_ascii(self):
        for name, mimetype in (('document.pdf', 'application/pdf'),
                               ('workbook.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                               ('archive.zip', 'application/zip'), ('file.unknown', 'application/octet-stream')):
            with self.subTest(name=name):
                payload = b'original bytes'
                result = attachment_result(name, payload, 'attachment://1')
                self.assertEqual('file', result.structuredContent['representation'])
                block = result.content[1]
                self.assertEqual('resource', block.type)
                self.assertEqual(mimetype, block.resource.mimeType)
                self.assertEqual('attachment://1', str(block.resource.uri))
                self.assertEqual(payload, base64.b64decode(block.resource.blob))

    def test_undecodable_text_is_preserved_as_original_with_reason(self):
        payload = b'\xff\x80original'
        result = attachment_result('notes.txt', payload, 'attachment://1')
        self.assertEqual('file', result.structuredContent['representation'])
        self.assertIn('decode', result.structuredContent['reason'].lower())
        self.assertEqual(payload, base64.b64decode(result.content[1].resource.blob))

    def test_empty_text_and_empty_binary_files_are_preserved(self):
        text = attachment_result('empty.txt', b'', 'attachment://1')
        self.assertEqual('', text.content[1].text)
        binary = attachment_result('empty.bin', b'', 'attachment://1')
        self.assertEqual(b'', base64.b64decode(binary.content[1].resource.blob))

    def test_compressed_images_are_original_files_without_decompression(self):
        payload = b'\x1f\x8bcompressed original'
        result = attachment_result('diagram.svgz', payload, 'attachment://1')
        self.assertEqual('file', result.structuredContent['representation'])
        self.assertEqual(payload, base64.b64decode(result.content[1].resource.blob))

    def test_non_bytes_download_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'bytes'):
            attachment_result('file.txt', 'not bytes', 'attachment://1')

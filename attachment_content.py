"""Represent original attachment contents as native MCP text, images or files."""

import base64
import codecs
import json
import mimetypes
from pathlib import PurePosixPath

from mcp.types import BlobResourceContents, CallToolResult, EmbeddedResource, ImageContent, TextContent


MIME_TYPES = mimetypes.MimeTypes()
TEXT_SUFFIXES = {
    '.txt', '.md', '.markdown', '.rst', '.yaml', '.yml', '.json', '.jsonl', '.ndjson',
    '.xml', '.csv', '.tsv', '.toml', '.ini', '.cfg', '.conf', '.properties', '.log',
    '.html', '.htm', '.css', '.scss', '.js', '.jsx', '.ts', '.tsx', '.py', '.java',
    '.kt', '.kts', '.go', '.rs', '.c', '.h', '.cpp', '.hpp', '.sh', '.bash', '.zsh', '.sql',
}
TEXT_NAMES = {'.env', '.gitignore', '.dockerignore', 'dockerfile', 'makefile', 'license'}
TEXT_MIME_TYPES = {
    'application/json', 'application/yaml', 'application/x-yaml', 'application/toml',
    'application/xml', 'application/javascript', 'application/x-ndjson',
}
MIME_OVERRIDES = {'.yaml': 'application/yaml', '.yml': 'application/yaml',
                  '.jsonl': 'application/x-ndjson', '.ndjson': 'application/x-ndjson',
                  '.webp': 'image/webp',
                  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                  '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}
BINARY_MIME = 'application/octet-stream'


def attachment_mime(name: str, supplied: str) -> str:
    supplied = supplied.partition(';')[0].strip().lower()
    suffix = PurePosixPath(name).suffix.lower()
    guessed, compression = MIME_TYPES.guess_type(name)
    if compression:
        return BINARY_MIME
    guessed = MIME_OVERRIDES.get(suffix) or guessed
    if supplied and supplied != BINARY_MIME:
        return supplied
    if guessed:
        return guessed
    return 'text/plain' if is_text(name, '') else BINARY_MIME


def is_text(name: str, mimetype: str) -> bool:
    path = PurePosixPath(name.lower())
    return (path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES
            or mimetype.startswith('text/') or mimetype in TEXT_MIME_TYPES
            or mimetype.endswith(('+json', '+xml')))


def decode_text(payload: bytes) -> str:
    for prefix, encoding in ((codecs.BOM_UTF32_LE, 'utf-32'), (codecs.BOM_UTF32_BE, 'utf-32'),
                             (codecs.BOM_UTF16_LE, 'utf-16'), (codecs.BOM_UTF16_BE, 'utf-16')):
        if payload.startswith(prefix):
            return payload.decode(encoding)
    return payload.decode('utf-8-sig')


def attachment_result(name: str, payload: bytes, uri: str, mimetype: str = '') -> CallToolResult:
    """Preserve contents without conversion; undecodable text falls back to original bytes."""
    if not isinstance(payload, bytes):
        raise ValueError('Attachment download did not return bytes')
    mimetype = attachment_mime(name, mimetype)
    metadata = {'name': name, 'mime_type': mimetype, 'size': len(payload), 'uri': uri, 'representation': 'file'}
    if mimetype.startswith('image/'):
        metadata['representation'] = 'image'
        content = ImageContent(type='image', data=base64.b64encode(payload).decode('ascii'), mimeType=mimetype)
    elif is_text(name, mimetype):
        try:
            content = TextContent(type='text', text=decode_text(payload))
            metadata['representation'] = 'text'
        except UnicodeError:
            metadata['reason'] = 'Could not decode text as UTF-8 or BOM-marked Unicode; original bytes returned'
    if metadata['representation'] == 'file':
        content = EmbeddedResource(type='resource', resource=BlobResourceContents(
            uri=uri, mimeType=mimetype, blob=base64.b64encode(payload).decode('ascii')))
    return CallToolResult(content=[TextContent(type='text', text=json.dumps(metadata, ensure_ascii=False)), content],
                          structuredContent=metadata)

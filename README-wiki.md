# Export Yandex Wiki Trees

English | [Русский](README-wiki-RU.md)

The script saves the specified Wiki page, its accessible descendants at all levels, attachments, and comments.

```bash
python3 export_wiki.py "https://wiki.yandex.ru/space/page/" exports/
```

If no output directory is specified, the export is created in the current directory. Set the following environment variables:

```bash
export YANDEX_TRACKER_OAUTH_TOKEN="OAUTH_TOKEN_WITH_WIKI_READ_PERMISSION"
export IDENTITY_HUB="YOUR_ORGANIZATION_ID"
```

Both variables are required. Installation, access setup, and loading a local `.env` file are described in the [README](README.md). Python 3.10+ is required.

Output structure (the service directory names are in Russian):

```text
Page title/
  Page title — summary.md
  Вложения/
  Подстраницы/
```

`Вложения/` contains attachments; `Подстраницы/` contains child pages.

`summary.md` contains the complete current Wiki source text, available metadata, a file list, and comments with authors, dates, reply relationships, and statuses. Special Wiki markup is preserved literally. Interactive elements and revision history are not converted into separate local representations.

On repeated runs, the script replaces only the previous export of the same root page. It downloads the entire tree into a temporary directory first, so an access or download failure preserves the previous completed export.

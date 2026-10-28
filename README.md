# Confluence Space Audit

![tests](https://github.com/GhntSergio/confluence-space-audit/actions/workflows/tests.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-3670A0?style=flat&logo=python&logoColor=ffdd54)
![Confluence Cloud](https://img.shields.io/badge/Confluence-Cloud-172B4D?style=flat&logo=confluence&logoColor=white)

Inventory every space of a **Confluence Cloud** site through the REST API and get an **Excel report** that flags what needs attention before a clean-up, a governance review or a migration.

## Why

On large sites, nobody knows how many spaces are still alive. Before archiving, consolidating or migrating, admins need a factual answer to:

- Which spaces are **empty**?
- Which ones have not been updated for a year?
- How many are **personal** or already **archived**?
- How are permissions spread across spaces?

This script answers those questions in one run.

## What the report contains

| Sheet | Content |
|---|---|
| `Summary` | Totals: spaces, global/personal, archived, empty, inactive, pages |
| `Spaces` | One row per space: key, name, type, status, creation date, page count, last update, days since update, flags |
| `Empty` / `Inactive` / `Archived` | Filtered views ready to share with space owners |

Flags:

- `EMPTY`: no pages
- `INACTIVE`: no page updated for more than `--inactive-days` (default 365)
- `PERSONAL`: personal space
- `ARCHIVED`: archived space

With `--with-permissions`, each row also gets the number of permission entries and distinct groups.

## Quick start

```bash
git clone https://github.com/GhntSergio/confluence-space-audit.git
cd confluence-space-audit
pip install -r requirements.txt

cp .env.example .env   # then fill in your site URL, email and API token
python confluence_space_audit.py --inactive-days 365 --output audit.xlsx
```

Create an API token at <https://id.atlassian.com/manage-profile/security/api-tokens>. The account only needs read access to the spaces you want to audit (a site admin sees everything).

### Options

| Option | Description |
|---|---|
| `--inactive-days N` | Inactivity threshold in days (default: 365) |
| `--output FILE` | Excel file to write (default: `confluence_space_audit_YYYYMMDD.xlsx`) |
| `--exclude-personal` | Skip personal spaces |
| `--exclude-archived` | Skip archived spaces |
| `--with-permissions` | Count permission entries and groups per space (slower) |
| `-v` | Verbose logging |

## How it works

| Need | Endpoint |
|---|---|
| List spaces (cursor pagination) | `GET /wiki/api/v2/spaces` |
| Page count and last update per space | `GET /wiki/rest/api/search` with CQL `space = KEY and type = page order by lastmodified desc` |
| Permissions per space | `GET /wiki/api/v2/spaces/{id}/permissions` |

The client follows `_links.next` cursors and backs off on HTTP 429 using the `Retry-After` header, so it can run on large sites without tripping rate limits.

## Tests

```bash
pip install -r requirements-dev.txt
PYTHONPATH=. python -m pytest -q
```

The tests use fake API responses: no Atlassian site or credentials needed.

## Security

- Credentials stay in `.env`, which is git-ignored. Never commit a real token.
- The script is **read-only**: it never modifies or deletes content.

## License

MIT

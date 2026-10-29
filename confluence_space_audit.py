"""Confluence Cloud space audit.

Inventories every space of a Confluence Cloud site through the REST API and
exports an Excel report flagging empty, inactive, archived and personal spaces.

Usage:
    python confluence_space_audit.py --inactive-days 365 --output audit.xlsx

Credentials are read from environment variables (or a .env file):
    CONFLUENCE_BASE_URL   e.g. https://your-site.atlassian.net
    ATLASSIAN_EMAIL       account email
    ATLASSIAN_API_TOKEN   API token (https://id.atlassian.com/manage-profile/security/api-tokens)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Iterator, Optional

import pandas as pd
import requests

log = logging.getLogger("confluence-space-audit")


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
class ConfluenceClient:
    """Minimal Confluence Cloud client with pagination and rate-limit handling."""

    def __init__(self, base_url: str, email: str, token: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (email, token)
        self.session.headers.update({"Accept": "application/json"})
        self.timeout = timeout

    def _get(self, path_or_url: str, params: Optional[dict] = None, retries: int = 5) -> dict:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        for attempt in range(retries):
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                log.warning("Rate limited, retrying in %ss", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Too many retries for {url}")

    def iter_spaces(self, include_archived: bool = True) -> Iterator[dict]:
        """Yield all spaces using the v2 cursor-based pagination."""
        params = {"limit": 250}
        if not include_archived:
            params["status"] = "current"
        data = self._get("/wiki/api/v2/spaces", params=params)
        while True:
            yield from data.get("results", [])
            next_link = data.get("_links", {}).get("next")
            if not next_link:
                break
            data = self._get(f"{self.base_url}{next_link}")

    def cql_count_and_latest(self, space_key: str) -> tuple[int, Optional[str]]:
        """Return (number of pages, ISO date of the last modified content) via CQL."""
        cql = f'space = "{space_key}" and type = page order by lastmodified desc'
        data = self._get("/wiki/rest/api/search", params={"cql": cql, "limit": 1})
        total = int(data.get("totalSize", 0))
        results = data.get("results", [])
        last_modified = results[0].get("lastModified") if results else None
        return total, last_modified

    def permission_summary(self, space_id: str) -> tuple[int, int]:
        """Return (number of permission entries, number of distinct groups)."""
        entries, groups = 0, set()
        data = self._get(f"/wiki/api/v2/spaces/{space_id}/permissions", params={"limit": 250})
        while True:
            for perm in data.get("results", []):
                entries += 1
                principal = perm.get("principal", {})
                if principal.get("type") == "group":
                    groups.add(principal.get("id"))
            next_link = data.get("_links", {}).get("next")
            if not next_link:
                break
            data = self._get(f"{self.base_url}{next_link}")
        return entries, len(groups)


# --------------------------------------------------------------------------- #
# Audit logic
# --------------------------------------------------------------------------- #
@dataclass
class SpaceAudit:
    key: str
    name: str
    type: str
    status: str
    created_at: Optional[str]
    page_count: int
    last_modified: Optional[str]
    days_since_update: Optional[int]
    permission_entries: Optional[int] = None
    group_count: Optional[int] = None
    flags: list[str] = field(default_factory=list)

    def to_row(self) -> dict:
        row = asdict(self)
        row["flags"] = ", ".join(self.flags)
        return row


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_flags(space: SpaceAudit, inactive_days: int) -> list[str]:
    flags = []
    if space.status == "archived":
        flags.append("ARCHIVED")
    if space.type == "personal":
        flags.append("PERSONAL")
    if space.page_count == 0:
        flags.append("EMPTY")
    elif space.days_since_update is not None and space.days_since_update > inactive_days:
        flags.append("INACTIVE")
    return flags


def audit_space(client: ConfluenceClient, raw: dict, inactive_days: int,
                with_permissions: bool, now: datetime) -> SpaceAudit:
    key = raw.get("key", "")
    page_count, last_modified = client.cql_count_and_latest(key)
    last_dt = _parse_date(last_modified)
    days = (now - last_dt).days if last_dt else None

    space = SpaceAudit(
        key=key,
        name=raw.get("name", ""),
        type=raw.get("type", ""),
        status=raw.get("status", ""),
        created_at=raw.get("createdAt"),
        page_count=page_count,
        last_modified=last_modified,
        days_since_update=days,
    )
    if with_permissions:
        space.permission_entries, space.group_count = client.permission_summary(raw["id"])
    space.flags = compute_flags(space, inactive_days)
    return space


def build_summary(df: pd.DataFrame) -> pd.DataFrame:
    def count(flag: str) -> int:
        return int(df["flags"].str.contains(flag).sum()) if not df.empty else 0

    return pd.DataFrame(
        [
            ("Total spaces", len(df)),
            ("Global spaces", int((df["type"] == "global").sum()) if not df.empty else 0),
            ("Personal spaces", count("PERSONAL")),
            ("Archived spaces", count("ARCHIVED")),
            ("Empty spaces", count("EMPTY")),
            ("Inactive spaces", count("INACTIVE")),
            ("Total pages", int(df["page_count"].sum()) if not df.empty else 0),
        ],
        columns=["Metric", "Value"],
    )


def export_excel(df: pd.DataFrame, output: str) -> None:
    df = df.copy()
    for col in ("page_count", "days_since_update", "permission_entries", "group_count"):
        if col in df:
            df[col] = df[col].astype("Int64")  # keep integers even when some values are missing
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        build_summary(df).to_excel(writer, sheet_name="Summary", index=False)
        df.to_excel(writer, sheet_name="Spaces", index=False)
        for flag in ("EMPTY", "INACTIVE", "ARCHIVED"):
            subset = df[df["flags"].str.contains(flag)] if not df.empty else df
            subset.to_excel(writer, sheet_name=flag.title(), index=False)
        for sheet in writer.sheets.values():
            for column in sheet.columns:
                width = max(len(str(c.value or "")) for c in column) + 2
                sheet.column_dimensions[column[0].column_letter].width = min(width, 60)
            sheet.freeze_panes = "A2"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit Confluence Cloud spaces and export an Excel report.")
    p.add_argument("--inactive-days", type=int, default=365,
                   help="Flag spaces with no page update for more than N days (default: 365)")
    p.add_argument("--output", default=f"confluence_space_audit_{datetime.now():%Y%m%d}.xlsx",
                   help="Output Excel file")
    p.add_argument("--exclude-personal", action="store_true", help="Skip personal spaces")
    p.add_argument("--exclude-archived", action="store_true", help="Skip archived spaces")
    p.add_argument("--with-permissions", action="store_true",
                   help="Also count permission entries and groups per space (slower)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    load_env_file()

    missing = [v for v in ("CONFLUENCE_BASE_URL", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN")
               if not os.environ.get(v)]
    if missing:
        log.error("Missing environment variables: %s (see .env.example)", ", ".join(missing))
        return 1

    client = ConfluenceClient(os.environ["CONFLUENCE_BASE_URL"],
                              os.environ["ATLASSIAN_EMAIL"], os.environ["ATLASSIAN_API_TOKEN"])
    now = datetime.now(timezone.utc)
    rows = []
    for raw in client.iter_spaces(include_archived=not args.exclude_archived):
        if args.exclude_personal and raw.get("type") == "personal":
            continue
        log.info("Auditing space %s", raw.get("key"))
        rows.append(audit_space(client, raw, args.inactive_days, args.with_permissions, now).to_row())

    df = pd.DataFrame(rows, columns=list(SpaceAudit.__dataclass_fields__))
    export_excel(df, args.output)
    log.info("Report written to %s (%d spaces)", args.output, len(df))
    return 0


if __name__ == "__main__":
    sys.exit(main())

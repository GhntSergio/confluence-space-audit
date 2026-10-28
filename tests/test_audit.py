from datetime import datetime, timezone

import pandas as pd

import confluence_space_audit as csa


class FakeClient:
    """Stands in for ConfluenceClient so tests never hit the network."""

    def __init__(self, spaces, pages):
        self._spaces = spaces
        self._pages = pages

    def iter_spaces(self, include_archived=True):
        yield from self._spaces

    def cql_count_and_latest(self, key):
        return self._pages[key]

    def permission_summary(self, space_id):
        return 4, 2


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_flags_empty_inactive_personal_archived():
    client = FakeClient(
        spaces=[],
        pages={
            "EMPTY": (0, None),
            "OLD": (12, "2024-01-01T10:00:00.000Z"),
            "LIVE": (40, "2026-09-20T10:00:00.000Z"),
            "~bob": (3, "2026-09-01T10:00:00.000Z"),
            "ARCH": (5, "2026-09-01T10:00:00.000Z"),
        },
    )
    raw = {
        "EMPTY": {"id": "1", "key": "EMPTY", "type": "global", "status": "current"},
        "OLD": {"id": "2", "key": "OLD", "type": "global", "status": "current"},
        "LIVE": {"id": "3", "key": "LIVE", "type": "global", "status": "current"},
        "~bob": {"id": "4", "key": "~bob", "type": "personal", "status": "current"},
        "ARCH": {"id": "5", "key": "ARCH", "type": "global", "status": "archived"},
    }
    result = {k: csa.audit_space(client, v, 365, False, NOW).flags for k, v in raw.items()}

    assert result["EMPTY"] == ["EMPTY"]
    assert result["OLD"] == ["INACTIVE"]
    assert result["LIVE"] == []
    assert result["~bob"] == ["PERSONAL"]
    assert result["ARCH"] == ["ARCHIVED"]


def test_permissions_are_optional():
    client = FakeClient([], {"K": (1, "2026-09-30T00:00:00Z")})
    space = csa.audit_space(client, {"id": "9", "key": "K"}, 365, True, NOW)
    assert (space.permission_entries, space.group_count) == (4, 2)


def test_export_creates_expected_sheets(tmp_path):
    client = FakeClient([], {"A": (0, None), "B": (3, "2020-01-01T00:00:00Z")})
    rows = [csa.audit_space(client, {"id": i, "key": k, "type": "global", "status": "current"},
                            365, False, NOW).to_row() for i, k in enumerate("AB")]
    out = tmp_path / "audit.xlsx"
    csa.export_excel(pd.DataFrame(rows), str(out))

    sheets = pd.read_excel(out, sheet_name=None)
    assert set(sheets) == {"Summary", "Spaces", "Empty", "Inactive", "Archived"}
    summary = dict(zip(sheets["Summary"]["Metric"], sheets["Summary"]["Value"]))
    assert summary["Total spaces"] == 2
    assert summary["Empty spaces"] == 1
    assert summary["Inactive spaces"] == 1


def test_missing_credentials_returns_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for var in ("CONFLUENCE_BASE_URL", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert csa.main([]) == 1


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._payload, self.status_code, self.headers = payload, status, headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_client_follows_cursor_pagination_and_retries_on_429(monkeypatch):
    client = csa.ConfluenceClient("https://example.atlassian.net", "me@example.com", "token")
    responses = iter([
        FakeResponse({}, status=429, headers={"Retry-After": "0"}),
        FakeResponse({"results": [{"key": "A"}], "_links": {"next": "/wiki/api/v2/spaces?cursor=x"}}),
        FakeResponse({"results": [{"key": "B"}], "_links": {}}),
    ])
    calls = []
    monkeypatch.setattr(client.session, "get", lambda url, **kw: calls.append(url) or next(responses))
    monkeypatch.setattr(csa.time, "sleep", lambda s: None)

    assert [s["key"] for s in client.iter_spaces()] == ["A", "B"]
    assert calls[-1] == "https://example.atlassian.net/wiki/api/v2/spaces?cursor=x"

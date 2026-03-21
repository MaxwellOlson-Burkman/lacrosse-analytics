"""Minimal Sidearm Sports `responsive-stats.ashx` client (team stats)."""

from __future__ import annotations

import json
from collections import deque
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import requests

__all__ = [
    "SidearmFetchError",
    "normalize_base_url",
    "build_team_stats_url",
    "fetch_sidearm_team_stats",
    "extract_team_stat_rows",
]


class SidearmFetchError(Exception):
    """Raised when the Sidearm stats endpoint returns an unusable response."""


def normalize_base_url(conference_url: str) -> str:
    raw = (conference_url or "").strip()
    if not raw:
        raise SidearmFetchError("conference_url is empty")
    if not urlparse(raw).scheme:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        raise SidearmFetchError(f"invalid conference_url: {conference_url!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def build_team_stats_url(base_url: str, division: int = 1, season: int | None = None) -> str:
    root = normalize_base_url(base_url)
    parsed = urlparse(root)
    query: dict[str, str] = {"type": "team", "division": str(division)}
    if season is not None:
        query["season"] = str(season)
    q = urlencode(query)
    return urlunparse((parsed.scheme, parsed.netloc, "/services/responsive-stats.ashx", "", q, ""))


def _is_team_row_list(obj: Any) -> bool:
    return isinstance(obj, list) and bool(obj) and all(isinstance(x, dict) for x in obj)


def extract_team_stat_rows(payload: Any) -> list[dict[str, Any]]:
    """Pull team stat rows from common Sidearm JSON wrappers."""
    if _is_team_row_list(payload):
        return payload

    if not isinstance(payload, dict):
        return []

    nested_keys = ("teams", "team", "items", "results")

    data = payload.get("data")
    if isinstance(data, list) and _is_team_row_list(data):
        return data
    if isinstance(data, dict):
        for key in nested_keys:
            inner = data.get(key)
            if _is_team_row_list(inner):
                return inner

    for key in nested_keys:
        inner = payload.get(key)
        if _is_team_row_list(inner):
            return inner

    # Deep search: first list of dicts that looks like team aggregate stats
    seen: set[int] = set()
    dq: deque[Any] = deque([payload])
    while dq:
        obj = dq.popleft()
        oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)

        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    dq.append(v)
        elif isinstance(obj, list):
            if _is_team_row_list(obj) and any(
                "tid" in row or "gf" in row or "w" in row for row in obj
            ):
                return obj
            for item in obj:
                if isinstance(item, (dict, list)):
                    dq.append(item)

    return []


def fetch_sidearm_team_stats(
    base_url: str,
    division: int = 1,
    season: int | None = None,
    timeout_seconds: int = 25,
) -> dict[str, Any]:
    request_url = build_team_stats_url(base_url, division=division, season=season)
    resp = requests.get(
        request_url,
        timeout=timeout_seconds,
        headers={"User-Agent": "Mozilla/5.0 (compatible; MSDIE/1.0; +https://example.local)"},
    )
    content_type = resp.headers.get("Content-Type") or ""

    if resp.status_code != 200:
        raise SidearmFetchError(
            f"HTTP {resp.status_code} for {request_url!r} (content-type: {content_type!r})"
        )

    try:
        payload = json.loads(resp.text)
    except json.JSONDecodeError as exc:
        raise SidearmFetchError(
            f"non-JSON response from {request_url!r} (content-type: {content_type!r}): {exc}"
        ) from exc

    rows = extract_team_stat_rows(payload)
    if not rows:
        keys_preview: object
        if isinstance(payload, dict):
            keys_preview = list(payload.keys())
        else:
            keys_preview = type(payload).__name__
        raise SidearmFetchError(f"no team stat rows in JSON from {request_url!r} (top-level: {keys_preview!r})")

    return {
        "request_url": request_url,
        "content_type": content_type,
        "rows": rows,
        "payload": payload,
    }

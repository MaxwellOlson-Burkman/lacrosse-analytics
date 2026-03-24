"""Fetch cumulative player-season stats from team-site HTML tables."""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urlparse, urlunparse

import requests


class PlayerStatsFetchError(Exception):
    """Raised when a player stats table cannot be fetched or parsed."""


def _normalize_base_url(team_url: str) -> str:
    raw = (team_url or "").strip()
    if not raw:
        raise PlayerStatsFetchError("team_url is empty")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if not parsed.netloc:
        raise PlayerStatsFetchError(f"invalid team_url: {team_url!r}")
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "", flags=re.IGNORECASE | re.DOTALL)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _strip_html(value).lower()).strip("_")


def _extract_tables(html: str) -> list[str]:
    return re.findall(r"<table[^>]*>.*?</table>", html, flags=re.IGNORECASE | re.DOTALL)


def _extract_headers(table_html: str) -> list[str]:
    header_row = ""
    rows = re.findall(r"<tr[^>]*>.*?</tr>", table_html, flags=re.IGNORECASE | re.DOTALL)
    if not rows:
        return []
    for row in rows[:3]:
        if re.search(r"<th\b", row, flags=re.IGNORECASE):
            header_row = row
            break
    if not header_row:
        header_row = rows[0]
    headers = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", header_row, flags=re.IGNORECASE | re.DOTALL)
    return [_normalize_header(h) for h in headers]


def _extract_rows(table_html: str) -> list[list[str]]:
    rows = re.findall(r"<tr[^>]*>.*?</tr>", table_html, flags=re.IGNORECASE | re.DOTALL)
    out: list[list[str]] = []
    for row_html in rows:
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row_html, flags=re.IGNORECASE | re.DOTALL)
        if not cells:
            continue
        out.append([_strip_html(c) for c in cells])
    return out


def _looks_like_player_table(headers: list[str]) -> bool:
    if not headers:
        return False
    has_name = any(h in {"name", "player"} for h in headers)
    stat_hits = sum(1 for h in headers if h in {"gp", "g", "a", "pts", "sh", "sog", "gb", "to", "ct"})
    return has_name and stat_hits >= 3


def _candidate_urls(team_url: str, season: int) -> list[str]:
    base = _normalize_base_url(team_url)
    return [
        f"{base}/sports/mens-lacrosse/stats/{season}",
        f"{base}/sports/mens-lacrosse/stats/season/{season}",
        f"{base}/sports/mens-lacrosse/stats",
    ]


def fetch_player_season_stats(team_url: str, season: int, timeout_seconds: int = 30) -> dict[str, object]:
    """Fetch player cumulative season table and return normalized row dicts."""
    last_error = ""
    for url in _candidate_urls(team_url, season):
        try:
            resp = requests.get(
                url,
                timeout=timeout_seconds,
                headers={"User-Agent": "Mozilla/5.0 (compatible; MSDIE/1.0; +https://example.local)"},
            )
        except requests.RequestException as exc:
            last_error = f"request error: {exc}"
            continue
        content_type = resp.headers.get("Content-Type") or ""
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code} for {url!r}"
            continue

        tables = _extract_tables(resp.text)
        for table in tables:
            headers = _extract_headers(table)
            if not _looks_like_player_table(headers):
                continue
            raw_rows = _extract_rows(table)
            parsed_rows: list[dict[str, str]] = []
            for raw in raw_rows:
                if len(raw) != len(headers):
                    continue
                row = dict(zip(headers, raw))
                name = (row.get("name") or row.get("player") or "").strip().lower()
                if not name or name in {"name", "player", "totals", "total", "team"}:
                    continue
                parsed_rows.append(row)
            if parsed_rows:
                return {
                    "request_url": url,
                    "content_type": content_type,
                    "rows": parsed_rows,
                }
        last_error = f"no player table found in {url!r}"
    raise PlayerStatsFetchError(last_error or "no candidate URLs succeeded")


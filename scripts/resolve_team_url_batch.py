"""Resolve stats URLs for a fixed team batch and persist verified hints."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from msdie.fetchers.pdf_team_fetcher import fetch_pdf_team_season_stats
from msdie.fetchers.presto_fetcher import fetch_presto_team_season_stats
from msdie.routing import REQUIRED_TEAM_FIELDS, classify_source_type, route_method_for_source

DEFAULT_INPUT = PROJECT_ROOT / "data" / "audit" / "URL_BY_URL_D2_2025_ACTIVE_BATCH_10.csv"
DEFAULT_HINTS = PROJECT_ROOT / "data" / "team_url_hints.yaml"
DEFAULT_OUT = PROJECT_ROOT / "data" / "audit" / "batch_url_resolution_results.csv"
DEFAULT_MANUAL_QUEUE = PROJECT_ROOT / "data" / "audit" / "manual_url_input_required.csv"
DEFAULT_WMT_IDS = PROJECT_ROOT / "data" / "wmt_team_ids.csv"
USER_AGENT = "Mozilla/5.0"


def _clean(v: object) -> str:
    return str(v or "").strip()


def _required_fields_ok(row: dict[str, Any]) -> bool:
    return all(_clean(row.get(k)) for k in REQUIRED_TEAM_FIELDS)


def _stats_slug(team_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", team_name.lower()).strip("-")
    slug = slug.replace("-ny-", "ny").replace("-de-", "de")
    return slug


def _extract_links(page_url: str) -> list[str]:
    try:
        resp = requests.get(page_url, timeout=12, headers={"User-Agent": USER_AGENT})
    except requests.RequestException:
        return []
    links: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', resp.text or "", flags=re.IGNORECASE):
        full = urljoin(resp.url, href)
        lo = full.lower()
        if ".pdf" in lo and not any(x in lo for x in ("stats", "cume", "boxscore", "mlax")):
            continue
        if any(x in lo for x in ("stats", "statistics", "boxscores", ".xml", ".pdf", "/teams/", "sb_output")):
            links.append(full)
    # Preserve order, unique.
    seen: set[str] = set()
    out: list[str] = []
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _expand_pdf_wrapper(url: str) -> str:
    if not url.lower().endswith("/pdf"):
        return url
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": USER_AGENT})
    except requests.RequestException:
        return url
    m = re.search(r"https?://[^\"']+/stats/mlax/\d{4}/pdf/cume\.pdf", resp.text or "", flags=re.IGNORECASE)
    return m.group(0) if m else url


def _season_tokens(season: int) -> tuple[str, str]:
    y = int(season)
    return str(y), f"{y - 1}-{y % 100:02d}"


def _load_wmt_games_urls(path: Path, season: int) -> tuple[dict[str, str], dict[str, str]]:
    by_org: dict[str, str] = {}
    by_name: dict[str, str] = {}
    if not path.is_file():
        return by_org, by_name
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            status = _clean(row.get("status")).lower()
            games = _clean(row.get("wmt_games_url"))
            if status != "ok" or not games:
                continue
            try:
                y = int(float(_clean(row.get("season"))))
            except Exception:
                continue
            if y != int(season):
                continue
            oid = _clean(row.get("org_id"))
            tname = _clean(row.get("team_name"))
            if oid:
                by_org[oid] = games
            if tname:
                by_name[tname] = games
    return by_org, by_name


def _candidate_urls(team_name: str, team_url: str, existing_stats_url: str, *, season: int) -> list[str]:
    cands: list[str] = []
    if existing_stats_url:
        cands.append(existing_stats_url)

    base = team_url.rstrip("/")
    origin = base.split("/sports/")[0]
    slug = _stats_slug(team_name)
    season_token, academic_year = _season_tokens(season)

    cands.extend(
        [
            f"{base}/stats",
            f"{base}/stats/",
            f"{base}/stats/{season_token}/pdf",
            f"{origin}/sports/mens-lacrosse/stats",
            f"{origin}/sports/mens-lacrosse/stats/{season_token}/pdf",
            f"{origin}/sports/mlax/stats",
            f"{origin}/sports/mlax/stats/{season_token}/pdf",
            f"{origin}/sports/mlax/{academic_year}/teams/{slug}",
        ]
    )
    cands.extend(_extract_links(team_url))

    seen: set[str] = set()
    out: list[str] = []
    for u in cands:
        u2 = _clean(u).replace("{season}", season_token).replace("{academic_year}", academic_year)
        if not u2 or u2 in seen:
            continue
        seen.add(u2)
        out.append(u2)
    return out


def _looks_like_stats_pdf(url: str) -> bool:
    lo = (url or "").lower()
    return lo.endswith("/pdf") or "cume.pdf" in lo or "/stats/" in lo


def _classify_blocker(error: str) -> str:
    e = (error or "").lower()
    if "missing_team_url" in e:
        return "missing_team_url"
    if "404" in e or "http 404" in e:
        return "hard_404"
    if "wmt offense total row not found" in e or "team totals rows not found" in e:
        return "wmt_total_row_missing"
    if "no_valid_candidate" in e or "no_verified_stats_url" in e:
        return "no_stats_endpoint_found"
    return "other"


def _manual_required(category: str) -> bool:
    return category in {"missing_team_url", "no_stats_endpoint_found", "hard_404"}


def _verify_candidate(team_name: str, team_url: str, candidate_url: str, *, season: int) -> tuple[bool, dict[str, str]]:
    url = _expand_pdf_wrapper(candidate_url)
    source_type = classify_source_type(url)
    route_method = route_method_for_source(source_type)
    try:
        if source_type == "pdf":
            if not _looks_like_stats_pdf(url):
                return False, {"error": "non_stats_pdf_candidate"}
            row = fetch_pdf_team_season_stats(url, team_name=team_name, season=int(season), timeout_seconds=20)
            if not _required_fields_ok(row):
                return False, {"error": "missing_required_fields"}
            return True, {
                "stats_page_url": url,
                "source_type": source_type,
                "route_method": route_method,
                "route_confidence": "high",
                "validation_note": "verified_required_fields",
            }

        parsed = fetch_presto_team_season_stats(
            team_url=team_url,
            season=int(season),
            direct_stats_url=url,
            timeout_seconds=12,
            http_max_retries=0,
            max_wmt_urls_per_team=2,
            team_name=team_name,
        )
        row = parsed["rows"][0]
        if not _required_fields_ok(row):
            return False, {"error": "missing_required_fields"}
        resolved_type = classify_source_type(_clean(parsed.get("request_url")) or url)
        return True, {
            "stats_page_url": _clean(parsed.get("request_url")) or url,
            "source_type": resolved_type,
            "route_method": route_method_for_source(resolved_type),
            "route_confidence": "high",
            "validation_note": "verified_required_fields",
        }
    except Exception as exc:
        return False, {"error": str(exc)[:260]}


def _load_hints(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"by_org_id": {}, "by_team_name": {}}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("by_org_id"), dict):
        raw["by_org_id"] = {}
    if not isinstance(raw.get("by_team_name"), dict):
        raw["by_team_name"] = {}
    return raw


def _save_hints(path: Path, hints: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(hints, fh, sort_keys=True, allow_unicode=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve team batch URLs and persist verified hints.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--hints", default=str(DEFAULT_HINTS))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--max-candidates", type=int, default=25)
    parser.add_argument("--manual-queue-out", default=str(DEFAULT_MANUAL_QUEUE))
    parser.add_argument("--wmt-ids", default=str(DEFAULT_WMT_IDS))
    args = parser.parse_args()

    input_path = Path(args.input)
    hints_path = Path(args.hints)
    out_path = Path(args.out)
    manual_queue_path = Path(args.manual_queue_out)
    wmt_ids_path = Path(args.wmt_ids)

    rows = list(csv.DictReader(input_path.open(encoding="utf-8-sig", newline="")))
    wmt_by_org, wmt_by_name = _load_wmt_games_urls(wmt_ids_path, season=int(args.season))
    hints = _load_hints(hints_path)
    by_name: dict[str, Any] = hints["by_team_name"]

    out_rows: list[dict[str, str]] = []
    manual_rows: list[dict[str, str]] = []
    resolved_count = 0
    blocked_count = 0

    for row in rows:
        team_name = _clean(row.get("team_name"))
        org_id = _clean(row.get("org_id"))
        team_url = _clean(row.get("team_url"))
        existing = _clean(row.get("stats_page_url"))
        if not team_url:
            blocked_count += 1
            out_rows.append(
                {
                    "team_name": team_name,
                    "team_url": "",
                    "status": "blocked",
                    "stats_page_url": "",
                    "source_type": "",
                    "route_method": "",
                    "route_confidence": "",
                    "validation_note": "",
                    "blocked_reason": "missing_team_url",
                    "blocked_category": "missing_team_url",
                    "manual_input_required": "true",
                }
            )
            manual_rows.append(
                {
                    "team_name": team_name,
                    "team_url": "",
                    "blocked_category": "missing_team_url",
                    "blocked_reason": "missing_team_url",
                    "suggested_field_to_fill": "team_url",
                }
            )
            continue
        wmt_url = wmt_by_org.get(org_id) or wmt_by_name.get(team_name) or ""
        candidates = _candidate_urls(team_name, team_url, existing, season=int(args.season))
        if wmt_url and wmt_url not in candidates:
            candidates.insert(0, wmt_url)
        resolved = None
        last_error = ""
        for cand in candidates[: max(1, int(args.max_candidates))]:
            ok, detail = _verify_candidate(team_name, team_url, cand, season=int(args.season))
            if ok:
                resolved = detail
                break
            last_error = _clean(detail.get("error"))

        if resolved:
            payload = by_name.get(team_name)
            if not isinstance(payload, dict):
                payload = {}
            if team_url and not _clean(payload.get("url")):
                payload["url"] = team_url
            payload["stats_page_url"] = resolved["stats_page_url"]
            payload["source_type"] = resolved["source_type"]
            payload["route_method"] = resolved["route_method"]
            payload["route_confidence"] = resolved["route_confidence"]
            payload["validation_note"] = resolved["validation_note"]
            by_name[team_name] = payload
            resolved_count += 1
            out_rows.append(
                {
                    "team_name": team_name,
                    "team_url": team_url,
                    "status": "fixed",
                    "stats_page_url": resolved["stats_page_url"],
                    "source_type": resolved["source_type"],
                    "route_method": resolved["route_method"],
                    "route_confidence": resolved["route_confidence"],
                    "validation_note": resolved["validation_note"],
                    "blocked_reason": "",
                    "blocked_category": "",
                    "manual_input_required": "false",
                }
            )
        else:
            blocked_count += 1
            category = _classify_blocker(last_error or "no_valid_candidate")
            manual_required = _manual_required(category)
            out_rows.append(
                {
                    "team_name": team_name,
                    "team_url": team_url,
                    "status": "blocked",
                    "stats_page_url": "",
                    "source_type": "",
                    "route_method": "",
                    "route_confidence": "",
                    "validation_note": "",
                    "blocked_reason": last_error or "no_valid_candidate",
                    "blocked_category": category,
                    "manual_input_required": "true" if manual_required else "false",
                }
            )
            if manual_required:
                manual_rows.append(
                    {
                        "team_name": team_name,
                        "team_url": team_url,
                        "blocked_category": category,
                        "blocked_reason": last_error or "no_valid_candidate",
                        "suggested_field_to_fill": "stats_page_url" if category != "missing_team_url" else "team_url",
                    }
                )

    _save_hints(hints_path, hints)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "team_name",
                "team_url",
                "status",
                "stats_page_url",
                "source_type",
                "route_method",
                "route_confidence",
                "validation_note",
                "blocked_reason",
                "blocked_category",
                "manual_input_required",
            ],
        )
        w.writeheader()
        w.writerows(out_rows)

    manual_queue_path.parent.mkdir(parents=True, exist_ok=True)
    with manual_queue_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["team_name", "team_url", "blocked_category", "blocked_reason", "suggested_field_to_fill"],
        )
        w.writeheader()
        w.writerows(manual_rows)

    print(f"batch_rows: {len(rows)}")
    print(f"fixed: {resolved_count}")
    print(f"blocked: {blocked_count}")
    print(f"manual_required: {len(manual_rows)}")
    print(f"results_out: {out_path}")
    print(f"manual_queue_out: {manual_queue_path}")
    print(f"hints_updated: {hints_path}")


if __name__ == "__main__":
    main()

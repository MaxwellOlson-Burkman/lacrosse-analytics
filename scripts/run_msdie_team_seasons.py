"""General MSDIE team-season runner for conference and team-site ingestion.

This runner routes each team via vendor hints from data/vendors.csv:
1) Conference hub (optional) if verified and available
2) Team-site fetch (Sidearm first unless vendor=presto)
3) Team-site Presto fallback

It writes canonical MSDIE team-season rows and a failed-team audit CSV.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from msdie.fetchers.presto_fetcher import PrestoFetchError, fetch_presto_team_season_stats
from msdie.fetchers.pdf_team_fetcher import PdfFetchError, fetch_pdf_team_season_stats
from msdie.fetchers.sidearm_fetcher import (
    SidearmFetchError,
    extract_team_stat_rows,
    fetch_sidearm_team_stats,
    normalize_base_url,
)
from msdie.fetchers.http_compat import fetch_json
from msdie.mapping import MSDIE_COLUMNS, map_sidearm_row_to_msdie
from msdie.routing import route_method_for_source
from msdie.validation import format_issues, validate_team_msdie_row

DEFAULT_VENDORS = PROJECT_ROOT / "data" / "vendors.csv"
DEFAULT_TEAM_STATS = PROJECT_ROOT / "data" / "processed" / "team" / "team_stats_with_sos_full_synced.csv"
DEFAULT_TEAM_HINTS = PROJECT_ROOT / "data" / "team_url_hints.yaml"
DEFAULT_WMT_IDS = PROJECT_ROOT / "data" / "wmt_team_ids.csv"

TEAM_URL_OVERRIDES = {
    "Johns Hopkins": "https://hopkinssports.com/sports/mens-lacrosse",
}

_AUDIT_FIELDNAMES = ["season", "division", "conference", "team_name", "org_id", "team_url", "error"]
_ROUTE_DIAG_FIELDNAMES = [
    "season",
    "division",
    "conference",
    "team_name",
    "org_id",
    "team_url",
    "status",
    "request_url",
    "source_method",
    "source_type",
    "route_method",
    "confidence",
    "validation_note",
    "fallback_used",
    "error",
]


def _append_audit_failure(path: Path, row: dict[str, str], header_written: list[bool]) -> None:
    """Stream one audit row; first row opens with truncate + header (drops stale audit from prior runs)."""
    mode = "a" if header_written[0] else "w"
    with path.open(mode, encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_AUDIT_FIELDNAMES)
        if not header_written[0]:
            w.writeheader()
            header_written[0] = True
        w.writerow(row)


def _append_route_diag(path: Path, row: dict[str, str], header_written: list[bool]) -> None:
    mode = "a" if header_written[0] else "w"
    with path.open(mode, encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_ROUTE_DIAG_FIELDNAMES)
        if not header_written[0]:
            w.writeheader()
            header_written[0] = True
        w.writerow(row)


def _load_team_url_hints(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return (by_org_id, by_team_name) from team_url_hints.yaml."""
    if not path.is_file():
        return {}, {}
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        return {}, {}
    by_org = raw.get("by_org_id") or {}
    by_name = raw.get("by_team_name") or {}
    if not isinstance(by_org, dict):
        by_org = {}
    if not isinstance(by_name, dict):
        by_name = {}
    out_org: dict[str, dict[str, Any]] = {}
    out_name: dict[str, dict[str, Any]] = {}
    for k, v in by_org.items():
        if isinstance(v, dict):
            out_org[str(k).strip()] = v
    for k, v in by_name.items():
        if isinstance(v, dict):
            out_name[str(k).strip()] = v
    return out_org, out_name


def _hint_for_team(
    by_org: dict[str, dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
    org_id: str,
    team_name: str,
) -> dict[str, Any]:
    """Merge hints: team_name first, then org_id overrides (more specific)."""
    out: dict[str, Any] = {}
    if team_name in by_name:
        out.update(by_name[team_name])
    oid = (org_id or "").strip()
    if oid and oid in by_org:
        out.update(by_org[oid])
    return out


def _load_wmt_ids(
    path: Path,
) -> tuple[dict[tuple[str, int], dict[str, str]], dict[tuple[str, int], dict[str, str]]]:
    """Return WMT rows keyed by (org_id, season) and (team_name, season) for status=ok."""
    if not path.is_file():
        return {}, {}
    by_org: dict[tuple[str, int], dict[str, str]] = {}
    by_name: dict[tuple[str, int], dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            status = (row.get("status") or "").strip().lower()
            games_url = (row.get("wmt_games_url") or "").strip()
            season_s = (row.get("season") or "").strip()
            team_name = (row.get("team_name") or "").strip()
            org_id = (row.get("org_id") or "").strip()
            if status != "ok" or not games_url:
                continue
            try:
                season = int(float(season_s))
            except Exception:
                continue
            if org_id:
                by_org[(org_id, season)] = row
            if team_name:
                by_name[(team_name, season)] = row
    return by_org, by_name


def _wmt_hint_for_team(
    by_org: dict[tuple[str, int], dict[str, str]],
    by_name: dict[tuple[str, int], dict[str, str]],
    *,
    org_id: str,
    team_name: str,
    season: int,
) -> dict[str, str]:
    """Build a stats-page hint from canonical WMT registry when available."""
    row = by_org.get(((org_id or "").strip(), int(season))) or by_name.get(((team_name or "").strip(), int(season)))
    if not row:
        return {}
    games_url = (row.get("wmt_games_url") or "").strip()
    players_url = (row.get("wmt_players_url") or "").strip()
    out: dict[str, str] = {}
    if games_url:
        out["stats_page_url"] = games_url
        out["source_type"] = "wmt_api"
        out["route_method"] = "html_parse"
        out["route_confidence"] = "high"
        out["validation_note"] = "wmt_registry_ok"
    if players_url:
        out["wmt_players_url"] = players_url
    out["wmt_team_id"] = (row.get("wmt_team_id") or "").strip()
    return out


def _apply_stats_url_template(url: str, *, season: int) -> str:
    """Expand ``{season}`` (calendar year) and ``{academic_year}`` (e.g. 2020-21 for season 2021)."""
    u = (url or "").strip()
    if not u:
        return ""
    y = int(season)
    # Spring lacrosse "season year" Y → Sidearm/PRESTO folder label (Y-1)-(last two digits of Y)
    academic_year = f"{y - 1}-{y % 100:02d}"
    return (
        u.replace("{season}", str(y))
        .replace("{academic_year}", academic_year)
    )


def _typed_route_from_hint(team_hint: dict[str, Any]) -> tuple[str, str, str, str]:
    """Return (source_type, route_method, confidence, validation_note)."""
    source_type = str(team_hint.get("source_type") or "").strip().lower()
    route_method = str(team_hint.get("route_method") or "").strip().lower()
    confidence = str(team_hint.get("route_confidence") or "").strip().lower()
    validation_note = str(team_hint.get("validation_note") or "").strip().lower()
    if not route_method:
        route_method = route_method_for_source(source_type)
    return source_type, route_method, confidence, validation_note


def _fetch_sidearm_direct_stats(
    stats_url: str,
    *,
    team_name: str,
    org_id: str,
    timeout_seconds: float,
    http_max_retries: int,
) -> tuple[dict[str, object], str]:
    """Fetch a direct Sidearm JSON URL and pick one team row."""
    payload, final_url, _ = fetch_json(
        stats_url,
        timeout_seconds=timeout_seconds,
        max_retries=http_max_retries,
    )
    rows = extract_team_stat_rows(payload)
    row = _choose_sidearm_row(rows, team_name=team_name, org_id=org_id)
    if org_id and not row.get("tid"):
        row["tid"] = org_id
    return row, final_url


def _division_number(label: str) -> int:
    cleaned = (label or "").strip().upper()
    if cleaned in {"D1", "1"}:
        return 1
    if cleaned in {"D2", "2"}:
        return 2
    raise ValueError(f"unsupported division label: {label!r}")


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def _choose_sidearm_row(rows: list[dict[str, object]], team_name: str, org_id: str) -> dict[str, object]:
    if not rows:
        raise SidearmFetchError(f"no rows returned for {team_name}")
    if org_id:
        by_tid = [r for r in rows if str(r.get("tid", "")).strip() == org_id]
        if len(by_tid) == 1:
            return by_tid[0]
    if len(rows) == 1:
        return rows[0]
    lowered = team_name.lower()
    by_name = [
        r
        for r in rows
        if lowered in str(r.get("team", "")).lower()
        or lowered in str(r.get("team_name", "")).lower()
        or lowered in str(r.get("school", "")).lower()
    ]
    if len(by_name) == 1:
        return by_name[0]
    raise SidearmFetchError(f"ambiguous team rows for {team_name}: {len(rows)} candidates")


def _to_int(value: str) -> int:
    try:
        return int(float((value or "").strip()))
    except Exception:
        return 0


def _to_float(value: str) -> float:
    try:
        return float((value or "").strip())
    except Exception:
        return 0.0


def _pct_for_msdie(raw: str) -> str:
    """Map CSV ratio (0–1) to MSDIE percent field (0–100)."""
    v = _to_float(raw)
    if v <= 0:
        return ""
    if v <= 1.0:
        s = f"{100.0 * v:.3f}"
        return s.rstrip("0").rstrip(".")
    return str(v)


def _penn_state_probe_fallback(team: dict[str, str], season: int, team_stats_path: Path) -> dict[str, object]:
    if not team_stats_path.is_file():
        raise RuntimeError(f"Penn State probe fallback missing file: {team_stats_path}")
    with team_stats_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    match = None
    for row in rows:
        if (
            (row.get("team_name") or "").strip() == "Penn St."
            and _to_int(row.get("academic_year", "")) == season
            and str(row.get("division", "")).strip() in {"1", "D1", "d1"}
        ):
            match = row
            break
    if match is None:
        raise RuntimeError(f"Penn State probe fallback row not found for season {season}")
    gp = _to_int(match.get("games_played", ""))
    gf_num = _to_float(match.get("scoring_offense", "")) * gp if gp else 0.0
    shot_pct_f = _to_float(match.get("shot_percentage", ""))
    sh_est = ""
    if gf_num > 0 and shot_pct_f > 0:
        sh_est = str(round(gf_num / shot_pct_f))
    return {
        "tid": (team.get("org_id") or "").strip(),
        "season": str(season),
        "w": match.get("wins", ""),
        "l": match.get("losses", ""),
        "gf": str(round(gf_num)) if gp else "",
        "ga": str(round(_to_float(match.get("scoring_defense", "")) * gp)) if gp else "",
        "sh": sh_est,
        "sh_pct": _pct_for_msdie(str(match.get("shot_percentage", ""))),
        "fow": "",
        "fol": "",
        "fo_pct": _pct_for_msdie(str(match.get("face_off_winning_percentage", ""))),
        "gb": str(round(_to_float(match.get("ground_balls_per_game", "")) * gp)) if gp else "",
        "to": str(round(_to_float(match.get("turnovers_per_game", "")) * gp)) if gp else "",
        "ct": str(round(_to_float(match.get("caused_turnovers_per_game", "")) * gp)) if gp else "",
        "cl_att": "",
        "cl_made": "",
        "cl_pct": _pct_for_msdie(str(match.get("clearing_percentage", ""))),
        "emo_g": "",
        "emo_att": "",
        "emo_pct": _pct_for_msdie(str(match.get("man_up_offense", ""))),
        "sv_pct": "",
    }


def _fetch_team_row(
    team: dict[str, str],
    *,
    season: int,
    division_number: int,
    team_stats_path: Path,
    enable_penn_state_probe: bool,
    team_hint: dict[str, Any],
    timeout_seconds: float,
    http_max_retries: int,
    max_wmt_urls_per_team: int,
    prefer_sidearm: bool,
) -> tuple[dict[str, object], str, str, dict[str, str]]:
    team_name = (team.get("team_name") or "").strip()
    org_id = (team.get("org_id") or "").strip()
    vendor = (team.get("vendor") or "").strip().lower()
    team_url = TEAM_URL_OVERRIDES.get(team_name, (team.get("team_url") or "").strip())
    root = (team_hint.get("team_root_url") or team_hint.get("url") or "").strip()
    if root:
        team_url = root
    stats_page_url = _apply_stats_url_template(
        str(team_hint.get("stats_page_url") or ""),
        season=season,
    )
    source_type, route_method, route_confidence, validation_note = _typed_route_from_hint(team_hint)
    route_diag = {
        "source_type": source_type,
        "route_method": route_method,
        "confidence": route_confidence,
        "validation_note": validation_note,
        "fallback_used": "false",
    }
    if not team_url:
        raise RuntimeError(f"{team_name}: missing team_url in vendors.csv")

    presto_kw = dict(
        team_name=team_name,
        timeout_seconds=timeout_seconds,
        max_wmt_urls_per_team=max_wmt_urls_per_team,
        http_max_retries=http_max_retries,
    )

    def _presto_source(base: str, payload: dict[str, object]) -> str:
        """Annotate source method with parser flavor for coverage reporting."""
        parser_method = str(payload.get("parser_method", "")).strip().lower()
        if parser_method == "xml":
            return f"{base}_xml"
        if parser_method == "wmt_api_games":
            return f"{base}_wmt_api"
        return base

    typed_route_error: Exception | None = None
    if route_method:
        try:
            if route_method == "sidearm_json":
                if stats_page_url:
                    row, final_url = _fetch_sidearm_direct_stats(
                        stats_page_url,
                        team_name=team_name,
                        org_id=org_id,
                        timeout_seconds=timeout_seconds,
                        http_max_retries=http_max_retries,
                    )
                    return row, final_url, "team_site_sidearm_typed", route_diag
                base_url = normalize_base_url(team_url)
                sidearm = fetch_sidearm_team_stats(
                    base_url=base_url,
                    division=division_number,
                    season=season,
                    timeout_seconds=timeout_seconds,
                    http_max_retries=http_max_retries,
                )
                row = _choose_sidearm_row(sidearm["rows"], team_name=team_name, org_id=org_id)
                if org_id and not row.get("tid"):
                    row["tid"] = org_id
                return row, str(sidearm.get("request_url", "")), "team_site_sidearm_typed", route_diag
            if route_method in {"xml_parse", "html_parse"}:
                if not stats_page_url:
                    raise PrestoFetchError("typed route requires stats_page_url")
                presto = fetch_presto_team_season_stats(
                    team_url=team_url,
                    season=season,
                    direct_stats_url=stats_page_url,
                    **presto_kw,
                )
                row = presto["rows"][0]
                if org_id:
                    row["tid"] = org_id
                return row, str(presto.get("request_url", "")), _presto_source("team_site_typed", presto), route_diag
            if route_method == "pdf_parse":
                if not stats_page_url:
                    raise PdfFetchError("typed pdf route requires stats_page_url")
                pdf_row = fetch_pdf_team_season_stats(
                    stats_page_url,
                    team_name=team_name,
                    season=season,
                    timeout_seconds=timeout_seconds,
                )
                if org_id:
                    pdf_row["tid"] = org_id
                return pdf_row, str(pdf_row.get("request_url", stats_page_url)), "team_site_pdf_typed", route_diag
        except Exception as exc:
            typed_route_error = exc
            route_diag["fallback_used"] = "true"

    if stats_page_url and not prefer_sidearm:
        try:
            presto = fetch_presto_team_season_stats(
                team_url=team_url,
                season=season,
                direct_stats_url=stats_page_url,
                **presto_kw,
            )
            row = presto["rows"][0]
            if org_id:
                row["tid"] = org_id
            return row, str(presto.get("request_url", "")), _presto_source("team_site_presto_direct", presto), route_diag
        except PrestoFetchError as _e:
            err_s = str(_e)
            if "redirected away from statistics content" in err_s:
                raise RuntimeError(f"{team_name}: {_e}") from _e
            if "no server-rendered table rows" in err_s:
                if enable_penn_state_probe and team_name == "Penn St.":
                    row = _penn_state_probe_fallback(
                        team=team, season=season, team_stats_path=team_stats_path
                    )
                    return row, f"local_probe:{team_stats_path}", "team_site_probe_fallback", route_diag
                raise RuntimeError(f"{team_name}: {_e}") from _e
            pass

    if vendor == "presto":
        presto = fetch_presto_team_season_stats(team_url=team_url, season=season, **presto_kw)
        row = presto["rows"][0]
        if org_id:
            row["tid"] = org_id
        return row, str(presto.get("request_url", "")), _presto_source("team_site_presto", presto), route_diag

    base_url = normalize_base_url(team_url)
    sidearm_err: SidearmFetchError | None = None
    try:
        sidearm = fetch_sidearm_team_stats(
            base_url=base_url,
            division=division_number,
            season=season,
            timeout_seconds=timeout_seconds,
            http_max_retries=http_max_retries,
        )
        row = _choose_sidearm_row(sidearm["rows"], team_name=team_name, org_id=org_id)
        if org_id and not row.get("tid"):
            row["tid"] = org_id
        return row, str(sidearm.get("request_url", "")), "team_site_sidearm", route_diag
    except SidearmFetchError as exc:
        sidearm_err = exc

    try:
        presto = fetch_presto_team_season_stats(team_url=team_url, season=season, **presto_kw)
        row = presto["rows"][0]
        if org_id:
            row["tid"] = org_id
        return row, str(presto.get("request_url", "")), _presto_source("team_site_presto", presto), route_diag
    except PrestoFetchError as exc:
        if stats_page_url and prefer_sidearm:
            try:
                presto = fetch_presto_team_season_stats(
                    team_url=team_url,
                    season=season,
                    direct_stats_url=stats_page_url,
                    **presto_kw,
                )
                row = presto["rows"][0]
                if org_id:
                    row["tid"] = org_id
                return row, str(presto.get("request_url", "")), _presto_source("team_site_presto_direct", presto), route_diag
            except PrestoFetchError as exc2:
                exc = exc2
        if enable_penn_state_probe and team_name == "Penn St.":
            row = _penn_state_probe_fallback(team=team, season=season, team_stats_path=team_stats_path)
            return row, f"local_probe:{team_stats_path}", "team_site_probe_fallback", route_diag
        typed_msg = f"; typed_route_failed ({typed_route_error})" if typed_route_error else ""
        raise RuntimeError(f"{team_name}: sidearm failed ({sidearm_err}); presto failed ({exc}){typed_msg}") from exc


def _default_out_path(year: int, division_label: str, conference: str) -> Path:
    div_num = _division_number(division_label)
    conf_suffix = f"_{_safe_slug(conference)}" if conference else ""
    return PROJECT_ROOT / "data" / "processed" / "msdie" / "team" / f"d{div_num}_men_{year}{conf_suffix}.csv"


def _default_audit_path(year: int, division_label: str, conference: str) -> Path:
    div_num = _division_number(division_label)
    conf_suffix = f"_{_safe_slug(conference)}" if conference else ""
    return PROJECT_ROOT / "data" / "audit" / f"msdie_team_failures_d{div_num}_{year}{conf_suffix}.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description="MSDIE team-season runner (D1/D2, conference optional)")
    parser.add_argument("--year", type=int, required=True, help="Season year, e.g. 2024")
    parser.add_argument("--division", type=str, required=True, choices=["D1", "D2"], help="Division scope")
    parser.add_argument("--conference", type=str, default="", help="Optional conference filter")
    parser.add_argument("--vendors", type=str, default=str(DEFAULT_VENDORS), help="Path to vendors.csv")
    parser.add_argument("--out", type=str, default="", help="Output CSV path")
    parser.add_argument("--audit-out", type=str, default="", help="Audit CSV path for failed teams")
    parser.add_argument("--team-stats", type=str, default=str(DEFAULT_TEAM_STATS), help="Penn State probe file")
    parser.add_argument("--sleep-seconds", type=float, default=0.35, help="Delay between team requests")
    parser.add_argument("--penn-state-probe", action="store_true", help="Enable Penn State local fallback")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue after team failures")
    parser.add_argument(
        "--team-hints",
        type=str,
        default=str(DEFAULT_TEAM_HINTS),
        help="YAML with stats_page_url / team_root_url (default: data/team_url_hints.yaml)",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=25.0,
        help="Per-request read budget (connect uses a shorter split); default 25",
    )
    parser.add_argument(
        "--http-retries",
        type=int,
        default=1,
        help="Extra attempts on timeout/connection errors (default: 1 retry)",
    )
    parser.add_argument(
        "--max-wmt-urls-per-team",
        type=int,
        default=0,
        help="Cap heuristic WMT team URL tries per school (0 = unlimited)",
    )
    parser.add_argument(
        "--prefer-sidearm",
        action="store_true",
        help="Try Sidearm JSON before stats_page_url direct fetch when both apply",
    )
    parser.add_argument(
        "--progress-out",
        type=str,
        default="",
        help="Append each successful row to this CSV as the run proceeds (creates file + header on first row)",
    )
    parser.add_argument(
        "--wmt-ids",
        type=str,
        default=str(DEFAULT_WMT_IDS),
        help="Canonical WMT team ID registry CSV (default: data/wmt_team_ids.csv)",
    )
    parser.add_argument(
        "--route-diagnostics-out",
        type=str,
        default="",
        help="Optional CSV to record parser-route diagnostics per team",
    )
    args = parser.parse_args()

    hints_path = Path(args.team_hints)
    hints_path = hints_path if hints_path.is_absolute() else PROJECT_ROOT / hints_path
    by_org_hints, by_name_hints = _load_team_url_hints(hints_path)
    wmt_ids_path = Path(args.wmt_ids)
    wmt_ids_path = wmt_ids_path if wmt_ids_path.is_absolute() else PROJECT_ROOT / wmt_ids_path
    by_org_wmt, by_name_wmt = _load_wmt_ids(wmt_ids_path)

    vendors_path = Path(args.vendors)
    if not vendors_path.is_file():
        print(f"error: vendors file not found: {vendors_path}", file=sys.stderr)
        sys.exit(1)
    with vendors_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    div_label = args.division.upper()
    conference_filter = (args.conference or "").strip()
    teams = [r for r in rows if (r.get("division") or "").strip().upper() == div_label]
    if conference_filter:
        teams = [r for r in teams if (r.get("conference") or "").strip() == conference_filter]
    teams = sorted(teams, key=lambda r: ((r.get("conference") or "").lower(), (r.get("team_name") or "").lower()))
    if not teams:
        print("error: no matching teams for selected filters", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out) if args.out else _default_out_path(args.year, div_label, conference_filter)
    out_path = out_path if out_path.is_absolute() else PROJECT_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    audit_path = Path(args.audit_out) if args.audit_out else _default_audit_path(args.year, div_label, conference_filter)
    audit_path = audit_path if audit_path.is_absolute() else PROJECT_ROOT / audit_path
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    division_number = _division_number(div_label)
    team_stats_path = Path(args.team_stats)
    team_stats_path = team_stats_path if team_stats_path.is_absolute() else PROJECT_ROOT / team_stats_path

    out_rows: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    audit_header_written = [False]
    route_diag_header_written = [False]
    progress_path: Path | None = None
    route_diag_path: Path | None = None
    if args.progress_out:
        progress_path = Path(args.progress_out)
        progress_path = progress_path if progress_path.is_absolute() else PROJECT_ROOT / progress_path
        progress_path.parent.mkdir(parents=True, exist_ok=True)
    if args.route_diagnostics_out:
        route_diag_path = Path(args.route_diagnostics_out)
        route_diag_path = route_diag_path if route_diag_path.is_absolute() else PROJECT_ROOT / route_diag_path
        route_diag_path.parent.mkdir(parents=True, exist_ok=True)
    for idx, team in enumerate(teams):
        team_name = (team.get("team_name") or "").strip()
        org_id = (team.get("org_id") or "").strip()
        hint = _hint_for_team(by_org_hints, by_name_hints, org_id, team_name)
        # Canonical WMT row should override ad-hoc hint URLs when present.
        hint.update(
            _wmt_hint_for_team(
                by_org_wmt,
                by_name_wmt,
                org_id=org_id,
                team_name=team_name,
                season=int(args.year),
            )
        )
        team_url = TEAM_URL_OVERRIDES.get(team_name, (team.get("team_url") or "").strip())
        root = (hint.get("team_root_url") or hint.get("url") or "").strip()
        if root:
            team_url = root
        if not team_url:
            frow = {
                "season": str(args.year),
                "division": div_label,
                "conference": (team.get("conference") or "").strip(),
                "team_name": team_name,
                "org_id": (team.get("org_id") or "").strip(),
                "team_url": "",
                "error": "skipped_empty_team_url (inactive program; see vendors notes)",
            }
            failures.append(frow)
            _append_audit_failure(audit_path, frow, audit_header_written)
            print(f"skip: {team_name} (no team_url — intentional skip)", file=sys.stderr)
            if not args.continue_on_error:
                break
            if idx < len(teams) - 1 and args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
            continue
        try:
            raw_row, request_url, source_method, route_diag = _fetch_team_row(
                team=team,
                season=args.year,
                division_number=division_number,
                team_stats_path=team_stats_path,
                enable_penn_state_probe=args.penn_state_probe,
                team_hint=hint,
                timeout_seconds=float(args.http_timeout),
                http_max_retries=max(0, int(args.http_retries)),
                max_wmt_urls_per_team=max(0, int(args.max_wmt_urls_per_team)),
                prefer_sidearm=bool(args.prefer_sidearm),
            )
            mapped = map_sidearm_row_to_msdie(
                raw_row,
                season_fallback=str(args.year),
                division_label=div_label,
                conference_fallback=(team.get("conference") or conference_filter or ""),
                source_method=source_method,
            )
            issues = validate_team_msdie_row(mapped)
            if issues:
                raise RuntimeError(f"validation failed: {format_issues(issues)}")
            out_rows.append(mapped)
            if route_diag_path is not None:
                _append_route_diag(
                    route_diag_path,
                    {
                        "season": str(args.year),
                        "division": div_label,
                        "conference": (team.get("conference") or "").strip(),
                        "team_name": team_name,
                        "org_id": (team.get("org_id") or "").strip(),
                        "team_url": team_url,
                        "status": "ok",
                        "request_url": request_url,
                        "source_method": source_method,
                        "source_type": route_diag.get("source_type", ""),
                        "route_method": route_diag.get("route_method", ""),
                        "confidence": route_diag.get("confidence", ""),
                        "validation_note": route_diag.get("validation_note", ""),
                        "fallback_used": route_diag.get("fallback_used", ""),
                        "error": "",
                    },
                    route_diag_header_written,
                )
            if progress_path is not None:
                is_new = not progress_path.is_file()
                with progress_path.open("a", encoding="utf-8", newline="") as pfh:
                    pw = csv.DictWriter(pfh, fieldnames=MSDIE_COLUMNS)
                    if is_new:
                        pw.writeheader()
                    pw.writerow(mapped)
            print(f"ok: {team_name} -> {request_url}")
        except Exception as exc:
            msg = str(exc)
            source_type, route_method, route_confidence, validation_note = _typed_route_from_hint(hint)
            frow = {
                "season": str(args.year),
                "division": div_label,
                "conference": (team.get("conference") or "").strip(),
                "team_name": team_name,
                "org_id": (team.get("org_id") or "").strip(),
                "team_url": team_url,
                "error": msg,
            }
            failures.append(frow)
            _append_audit_failure(audit_path, frow, audit_header_written)
            if route_diag_path is not None:
                _append_route_diag(
                    route_diag_path,
                    {
                        "season": str(args.year),
                        "division": div_label,
                        "conference": (team.get("conference") or "").strip(),
                        "team_name": team_name,
                        "org_id": (team.get("org_id") or "").strip(),
                        "team_url": team_url,
                        "status": "error",
                        "request_url": "",
                        "source_method": "",
                        "source_type": source_type,
                        "route_method": route_method,
                        "confidence": route_confidence,
                        "validation_note": validation_note,
                        "fallback_used": "",
                        "error": msg,
                    },
                    route_diag_header_written,
                )
            print(f"error: {team_name} ({team_url}) -> {msg}", file=sys.stderr)
            if not args.continue_on_error:
                break
        if idx < len(teams) - 1 and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MSDIE_COLUMNS)
        writer.writeheader()
        writer.writerows(out_rows)

    if not failures and audit_path.is_file():
        audit_path.unlink(missing_ok=True)

    print(f"rows_written: {len(out_rows)}")
    print(f"failed_teams: {len(failures)}")
    print(f"out: {out_path}")
    if failures:
        print(f"audit: {audit_path}")
    if failures and not args.continue_on_error:
        sys.exit(1)


if __name__ == "__main__":
    main()


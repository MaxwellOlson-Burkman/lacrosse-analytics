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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from msdie.fetchers.presto_fetcher import PrestoFetchError, fetch_presto_team_season_stats
from msdie.fetchers.sidearm_fetcher import SidearmFetchError, fetch_sidearm_team_stats, normalize_base_url
from msdie.mapping import MSDIE_COLUMNS, map_sidearm_row_to_msdie

DEFAULT_VENDORS = PROJECT_ROOT / "data" / "vendors.csv"
DEFAULT_TEAM_STATS = PROJECT_ROOT / "data" / "processed" / "team" / "team_stats_with_sos_full_synced.csv"

TEAM_URL_OVERRIDES = {
    "Johns Hopkins": "https://hopkinssports.com/sports/mens-lacrosse",
}


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
    return {
        "tid": (team.get("org_id") or "").strip(),
        "season": str(season),
        "w": match.get("wins", ""),
        "l": match.get("losses", ""),
        "gf": str(round(_to_float(match.get("scoring_offense", "")) * gp)) if gp else "",
        "ga": str(round(_to_float(match.get("scoring_defense", "")) * gp)) if gp else "",
        "sh": "",
        "sh_pct": match.get("shot_percentage", ""),
        "fow": "",
        "fol": "",
        "fo_pct": match.get("face_off_winning_percentage", ""),
        "gb": str(round(_to_float(match.get("ground_balls_per_game", "")) * gp)) if gp else "",
        "to": str(round(_to_float(match.get("turnovers_per_game", "")) * gp)) if gp else "",
        "ct": str(round(_to_float(match.get("caused_turnovers_per_game", "")) * gp)) if gp else "",
        "cl_att": "",
        "cl_made": "",
        "cl_pct": match.get("clearing_percentage", ""),
        "emo_g": "",
        "emo_att": "",
        "emo_pct": match.get("man_up_offense", ""),
        "sv_pct": "",
    }


def _fetch_team_row(
    team: dict[str, str],
    *,
    season: int,
    division_number: int,
    team_stats_path: Path,
    enable_penn_state_probe: bool,
) -> tuple[dict[str, object], str, str]:
    team_name = (team.get("team_name") or "").strip()
    org_id = (team.get("org_id") or "").strip()
    vendor = (team.get("vendor") or "").strip().lower()
    team_url = TEAM_URL_OVERRIDES.get(team_name, (team.get("team_url") or "").strip())
    if not team_url:
        raise RuntimeError(f"{team_name}: missing team_url in vendors.csv")

    if vendor == "presto":
        presto = fetch_presto_team_season_stats(team_url=team_url, season=season)
        row = presto["rows"][0]
        if org_id:
            row["tid"] = org_id
        return row, str(presto.get("request_url", "")), "team_site_presto"

    base_url = normalize_base_url(team_url)
    sidearm_err: SidearmFetchError | None = None
    try:
        sidearm = fetch_sidearm_team_stats(base_url=base_url, division=division_number, season=season)
        row = _choose_sidearm_row(sidearm["rows"], team_name=team_name, org_id=org_id)
        if org_id and not row.get("tid"):
            row["tid"] = org_id
        return row, str(sidearm.get("request_url", "")), "team_site_sidearm"
    except SidearmFetchError as exc:
        sidearm_err = exc

    try:
        presto = fetch_presto_team_season_stats(team_url=team_url, season=season)
        row = presto["rows"][0]
        if org_id:
            row["tid"] = org_id
        return row, str(presto.get("request_url", "")), "team_site_presto"
    except PrestoFetchError as exc:
        if enable_penn_state_probe and team_name == "Penn St.":
            row = _penn_state_probe_fallback(team=team, season=season, team_stats_path=team_stats_path)
            return row, f"local_probe:{team_stats_path}", "team_site_probe_fallback"
        raise RuntimeError(f"{team_name}: sidearm failed ({sidearm_err}); presto failed ({exc})") from exc


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
    args = parser.parse_args()

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
    for idx, team in enumerate(teams):
        team_name = (team.get("team_name") or "").strip()
        team_url = TEAM_URL_OVERRIDES.get(team_name, (team.get("team_url") or "").strip())
        if not team_url:
            failures.append(
                {
                    "season": str(args.year),
                    "division": div_label,
                    "conference": (team.get("conference") or "").strip(),
                    "team_name": team_name,
                    "org_id": (team.get("org_id") or "").strip(),
                    "team_url": "",
                    "error": "skipped_empty_team_url (inactive program; see vendors notes)",
                }
            )
            print(f"skip: {team_name} (no team_url — intentional skip)", file=sys.stderr)
            if not args.continue_on_error:
                break
            if idx < len(teams) - 1 and args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
            continue
        try:
            raw_row, request_url, source_method = _fetch_team_row(
                team=team,
                season=args.year,
                division_number=division_number,
                team_stats_path=team_stats_path,
                enable_penn_state_probe=args.penn_state_probe,
            )
            mapped = map_sidearm_row_to_msdie(
                raw_row,
                season_fallback=str(args.year),
                division_label=div_label,
                conference_fallback=(team.get("conference") or conference_filter or ""),
                source_method=source_method,
            )
            out_rows.append(mapped)
            print(f"ok: {team_name} -> {request_url}")
        except Exception as exc:
            msg = str(exc)
            failures.append(
                {
                    "season": str(args.year),
                    "division": div_label,
                    "conference": (team.get("conference") or "").strip(),
                    "team_name": team_name,
                    "org_id": (team.get("org_id") or "").strip(),
                    "team_url": team_url,
                    "error": msg,
                }
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

    if failures:
        fieldnames = ["season", "division", "conference", "team_name", "org_id", "team_url", "error"]
        with audit_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(failures)

    print(f"rows_written: {len(out_rows)}")
    print(f"failed_teams: {len(failures)}")
    print(f"out: {out_path}")
    if failures:
        print(f"audit: {audit_path}")
    if failures and not args.continue_on_error:
        sys.exit(1)


if __name__ == "__main__":
    main()


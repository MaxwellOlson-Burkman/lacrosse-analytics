"""MSDIE runner for player-season cumulative stats (2021-2025)."""

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

from msdie.fetchers.player_stats_fetcher import PlayerStatsFetchError, fetch_player_season_stats
from msdie.player_mapping import PLAYER_SEASON_COLUMNS, map_player_row_to_msdie

DEFAULT_VENDORS = PROJECT_ROOT / "data" / "vendors.csv"

TEAM_URL_OVERRIDES = {
    "Johns Hopkins": "https://hopkinssports.com/sports/mens-lacrosse",
}


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def _default_out_path(year: int, division: str, conference: str) -> Path:
    div_num = "1" if division.upper() == "D1" else "2"
    conf_suffix = f"_{_safe_slug(conference)}" if conference else ""
    return PROJECT_ROOT / "data" / "processed" / "msdie" / "players" / f"player_seasons_d{div_num}_{year}{conf_suffix}.csv"


def _default_audit_path(year: int, division: str, conference: str) -> Path:
    div_num = "1" if division.upper() == "D1" else "2"
    conf_suffix = f"_{_safe_slug(conference)}" if conference else ""
    return PROJECT_ROOT / "data" / "audit" / f"msdie_player_failures_d{div_num}_{year}{conf_suffix}.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MSDIE player-season cumulative ingestion")
    parser.add_argument("--year", type=int, required=True, help="Season year, e.g. 2024")
    parser.add_argument("--division", type=str, required=True, choices=["D1", "D2"], help="Division scope")
    parser.add_argument("--conference", type=str, default="", help="Optional conference filter")
    parser.add_argument("--vendors", type=str, default=str(DEFAULT_VENDORS), help="Path to vendors.csv")
    parser.add_argument("--out", type=str, default="", help="Output CSV path")
    parser.add_argument("--audit-out", type=str, default="", help="Audit CSV path")
    parser.add_argument("--sleep-seconds", type=float, default=0.35, help="Delay between team requests")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue processing after failures")
    args = parser.parse_args()

    vendors_path = Path(args.vendors)
    if not vendors_path.is_file():
        print(f"error: vendors file not found: {vendors_path}", file=sys.stderr)
        sys.exit(1)

    with vendors_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    division = args.division.upper()
    conference_filter = (args.conference or "").strip()
    teams = [r for r in rows if (r.get("division") or "").strip().upper() == division]
    if conference_filter:
        teams = [r for r in teams if (r.get("conference") or "").strip() == conference_filter]
    teams = sorted(teams, key=lambda r: ((r.get("conference") or "").lower(), (r.get("team_name") or "").lower()))
    if not teams:
        print("error: no matching teams for selected filters", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out) if args.out else _default_out_path(args.year, division, conference_filter)
    out_path = out_path if out_path.is_absolute() else PROJECT_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    audit_path = Path(args.audit_out) if args.audit_out else _default_audit_path(args.year, division, conference_filter)
    audit_path = audit_path if audit_path.is_absolute() else PROJECT_ROOT / audit_path
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    output_rows: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []

    for idx, team in enumerate(teams):
        team_name = (team.get("team_name") or "").strip()
        team_url = TEAM_URL_OVERRIDES.get(team_name, (team.get("team_url") or "").strip())
        if not team_url:
            failures.append(
                {
                    "season": str(args.year),
                    "division": division,
                    "conference": (team.get("conference") or "").strip(),
                    "team_name": team_name,
                    "org_id": (team.get("org_id") or "").strip(),
                    "team_url": "",
                    "error": "skipped_empty_team_url (inactive program; see vendors notes)",
                }
            )
            if not args.continue_on_error:
                break
            continue

        try:
            result = fetch_player_season_stats(team_url=team_url, season=args.year)
            for row in result["rows"]:
                mapped = map_player_row_to_msdie(
                    row,
                    team_id=(team.get("org_id") or "").strip(),
                    team_name=team_name,
                    season=args.year,
                    division=division,
                    conference=(team.get("conference") or "").strip(),
                    source_method="team_site_player_cumulative",
                    source_url=str(result.get("request_url", "")),
                )
                output_rows.append(mapped)
            print(f"ok: {team_name} -> {result.get('request_url', '')} ({len(result['rows'])} players)")
        except PlayerStatsFetchError as exc:
            failures.append(
                {
                    "season": str(args.year),
                    "division": division,
                    "conference": (team.get("conference") or "").strip(),
                    "team_name": team_name,
                    "org_id": (team.get("org_id") or "").strip(),
                    "team_url": team_url,
                    "error": str(exc),
                }
            )
            print(f"error: {team_name} ({team_url}) -> {exc}", file=sys.stderr)
            if not args.continue_on_error:
                break

        if idx < len(teams) - 1 and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PLAYER_SEASON_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)

    if failures:
        fieldnames = ["season", "division", "conference", "team_name", "org_id", "team_url", "error"]
        with audit_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(failures)

    print(f"rows_written: {len(output_rows)}")
    print(f"failed_teams: {len(failures)}")
    print(f"out: {out_path}")
    if failures:
        print(f"audit: {audit_path}")
    if failures and not args.continue_on_error:
        sys.exit(1)


if __name__ == "__main__":
    main()


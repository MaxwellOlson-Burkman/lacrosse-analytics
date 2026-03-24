"""MSDIE Tier-2 pilot: scrape each Big Ten team website."""

from __future__ import annotations

import argparse
import csv
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
PRESTO_TEAM_URL_OVERRIDES = {
    "Johns Hopkins": "https://hopkinssports.com/sports/mens-lacrosse",
}


def _choose_sidearm_row(rows: list[dict[str, object]], team_name: str, org_id: str) -> dict[str, object]:
    if not rows:
        raise SidearmFetchError(f"no rows returned for {team_name}")
    if org_id:
        by_tid = [r for r in rows if str(r.get("tid", "")).strip() == org_id]
        if len(by_tid) == 1:
            return by_tid[0]
    if len(rows) == 1:
        return rows[0]
    # best-effort by conference/team-like keys, then hard-fail if ambiguous
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


def _fetch_team_row(team: dict[str, str], season: int) -> tuple[dict[str, object], str, str]:
    team_name = (team.get("team_name") or "").strip()
    org_id = (team.get("org_id") or "").strip()
    vendor = (team.get("vendor") or "").strip().lower()
    team_url = (team.get("team_url") or "").strip()

    if team_name in PRESTO_TEAM_URL_OVERRIDES:
        team_url = PRESTO_TEAM_URL_OVERRIDES[team_name]

    if not team_url:
        raise RuntimeError(f"{team_name}: missing team_url")

    if vendor == "presto":
        presto = fetch_presto_team_season_stats(team_url=team_url, season=season)
        row = presto["rows"][0]
        if org_id:
            row["tid"] = org_id
        return row, str(presto.get("request_url", "")), "team_site_presto"

    base_url = normalize_base_url(team_url)
    sidearm_err: SidearmFetchError | None = None
    try:
        sidearm = fetch_sidearm_team_stats(base_url=base_url, division=1, season=season)
        row = _choose_sidearm_row(sidearm["rows"], team_name=team_name, org_id=org_id)
        if org_id and not row.get("tid"):
            row["tid"] = org_id
        return row, str(sidearm.get("request_url", "")), "team_site_sidearm"
    except SidearmFetchError as exc:
        sidearm_err = exc

    # Fallback for sites with cumulative stats pages but no Sidearm JSON endpoint.
    try:
        presto = fetch_presto_team_season_stats(team_url=team_url, season=season)
        row = presto["rows"][0]
        if org_id:
            row["tid"] = org_id
        return row, str(presto.get("request_url", "")), "team_site_presto"
    except PrestoFetchError as exc:
        raise RuntimeError(f"{team_name}: sidearm failed ({sidearm_err}); presto fallback failed ({exc})") from exc


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
    """Penn State-specific fallback while endpoint discovery is pending.

    Uses local processed team season aggregates and maps them to Sidearm-like
    keys so the same MSDIE mapper can be reused.
    """
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


def main() -> None:
    parser = argparse.ArgumentParser(description="MSDIE Big Ten Tier-2 per-team scrape pilot")
    parser.add_argument("--year", type=int, default=2024, help="Season year (default: 2024)")
    parser.add_argument("--vendors", type=str, default=str(DEFAULT_VENDORS), help="Path to vendors.csv")
    parser.add_argument(
        "--team-stats",
        type=str,
        default=str(DEFAULT_TEAM_STATS),
        help="Path to processed team stats for Penn State probe fallback",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Output CSV path (default: data/processed/msdie/d1_men_{year}_bigten_teams.csv)",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.35,
        help="Delay between team requests (default: 0.35)",
    )
    parser.add_argument(
        "--penn-state-probe",
        action="store_true",
        help="Enable Penn State-specific probe fallback when team-site parsing fails",
    )
    args = parser.parse_args()

    vendors_path = Path(args.vendors)
    if not vendors_path.is_file():
        print(f"error: vendors file not found: {vendors_path}", file=sys.stderr)
        sys.exit(1)

    with vendors_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    big_ten = sorted(
        [r for r in rows if (r.get("conference") or "").strip() == "Big Ten"],
        key=lambda r: (r.get("team_name") or "").lower(),
    )
    if not big_ten:
        print("error: no Big Ten rows in vendors.csv", file=sys.stderr)
        sys.exit(1)

    out_rows: list[dict[str, str]] = []
    probe_teams: list[str] = []
    team_stats_path = Path(args.team_stats)
    team_stats_path = team_stats_path if team_stats_path.is_absolute() else PROJECT_ROOT / team_stats_path
    for idx, team in enumerate(big_ten):
        team_name = (team.get("team_name") or "").strip()
        team_url = PRESTO_TEAM_URL_OVERRIDES.get(team_name, (team.get("team_url") or "").strip())
        try:
            raw_row, request_url, source_method = _fetch_team_row(team=team, season=args.year)
        except Exception as exc:
            if args.penn_state_probe and team_name == "Penn St.":
                raw_row = _penn_state_probe_fallback(team=team, season=args.year, team_stats_path=team_stats_path)
                request_url = f"local_probe:{team_stats_path}"
                source_method = "team_site_probe_fallback"
                probe_teams.append(team_name)
                print(f"probe: {team_name} -> local season fallback ({exc})")
            else:
                print(f"error: {team_name} ({team_url}) -> {exc}", file=sys.stderr)
                sys.exit(1)
        try:
            mapped = map_sidearm_row_to_msdie(
                raw_row,
                season_fallback=str(args.year),
                division_label=(team.get("division") or "D1").upper(),
                conference_fallback="Big Ten",
                source_method=source_method,
            )
            out_rows.append(mapped)
            print(f"ok: {team_name} -> {request_url}")
        except Exception as exc:
            print(f"error: {team_name} ({team_url}) mapping failed -> {exc}", file=sys.stderr)
            sys.exit(1)

        if idx < len(big_ten) - 1 and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    out_path = (
        Path(args.out)
        if args.out
        else PROJECT_ROOT / "data" / "processed" / "msdie" / f"d1_men_{args.year}_bigten_teams.csv"
    )
    out_path = out_path if out_path.is_absolute() else PROJECT_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MSDIE_COLUMNS)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"rows_written: {len(out_rows)}")
    if probe_teams:
        print(f"probe_fallback_teams: {', '.join(probe_teams)}")
    print(f"out: {out_path}")


if __name__ == "__main__":
    main()


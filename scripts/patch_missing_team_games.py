"""Patch missing team-game rows into an existing games_YYYY_dX.csv slice.

Why this exists:
- Sometimes a batch scrape is stopped or a team page fails, leaving a slice
  missing one or a few teams (e.g. 2017 D1 Yale).
- Re-scraping an entire year/division can take a long time.

What this script does:
1) Loads team_stats_model_ready.csv, filters to a specific (year, division) and
   one or more teams (by team_name or org_id).
2) Scrapes schedules ONLY for those teams using build_game_table_for_seasons().
3) Appends rows into the existing games_<year>_d<div>.csv and de-dupes.
4) Recomputes WP/OppWP/OppOppWP/RPI for the whole slice and writes them into
   team_stats_with_sos_<year>_d<div>.csv.

Run (project root):
  python scripts/patch_missing_team_games.py --year 2017 --division 1 --team-name "Yale"

Or by org_id:
  python scripts/patch_missing_team_games.py --year 2017 --division 1 --org-id 110571
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.schedule_scraper import build_game_table_for_seasons  # noqa: E402
from src.data.sos import compute_sos_metrics  # noqa: E402


PROCESSED = PROJECT_ROOT / "data" / "processed"


def _slice_paths(year: int, division: int) -> tuple[Path, Path]:
    games_path = PROCESSED / f"games_{year}_d{division}.csv"
    seasons_path = PROCESSED / f"team_stats_with_sos_{year}_d{division}.csv"
    return games_path, seasons_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Patch missing team schedules into an existing games slice.")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--division", type=int, required=True, choices=[1, 2])
    parser.add_argument("--team-name", action="append", default=[], help="Team name to patch (repeatable).")
    parser.add_argument("--org-id", action="append", default=[], help="Team org_id to patch (repeatable).")
    args = parser.parse_args()

    year = args.year
    division = args.division
    team_names = [t.strip() for t in args.team_name if (t or "").strip()]
    org_ids = [int(x) for x in args.org_id] if args.org_id else []

    if not team_names and not org_ids:
        raise SystemExit("Provide at least one of: --team-name or --org-id")

    seasons_model_ready_path = PROCESSED / "team_stats_model_ready.csv"
    if not seasons_model_ready_path.exists():
        raise SystemExit(f"Missing {seasons_model_ready_path}. Run your season table build first.")

    games_path, seasons_path = _slice_paths(year, division)
    if not games_path.exists():
        raise SystemExit(f"Missing {games_path}. Scrape the slice at least once before patching.")
    if not seasons_path.exists():
        raise SystemExit(f"Missing {seasons_path}. Scrape the slice at least once before patching.")

    seasons = pd.read_csv(seasons_model_ready_path)
    subset = seasons[(seasons["academic_year"] == year) & (seasons["division"] == division)].copy()
    if subset.empty:
        raise SystemExit(f"No seasons rows found for {year} D{division} in team_stats_model_ready.csv")

    # Select targets
    targets = subset
    if team_names:
        targets = targets[targets["team_name"].isin(team_names)]
    if org_ids:
        targets = targets[targets["org_id"].astype(int).isin(org_ids)]

    targets = targets.drop_duplicates(subset=["academic_year", "division", "org_id"])
    if targets.empty:
        raise SystemExit("No matching teams found for the provided --team-name/--org-id filters.")

    print(f"Patching {len(targets)} team(s) into {games_path.name}:")
    for r in targets[["team_name", "org_id"]].itertuples(index=False, name=None):
        print(f"  - {r[0]} ({int(r[1])})")

    # Scrape only these teams for this slice
    new_games = build_game_table_for_seasons(targets, years=[year], divisions=[division])
    if new_games.empty:
        raise SystemExit("Scrape produced no rows. Likely a fetch/challenge issue; try again.")

    games = pd.read_csv(games_path)
    combined = pd.concat([games, new_games], ignore_index=True)

    # De-dupe on stable identity columns (date+teams+scores+result should be enough)
    dedupe_cols = [
        "academic_year",
        "division",
        "team_org_id",
        "opp_org_id",
        "game_date",
        "team_score",
        "opp_score",
        "result",
    ]
    dedupe_cols = [c for c in dedupe_cols if c in combined.columns]
    combined = combined.drop_duplicates(subset=dedupe_cols)

    # Recompute SOS metrics for the slice
    sos = compute_sos_metrics(combined).rename(columns={"team_org_id": "org_id"})

    seasons_chunk = pd.read_csv(seasons_path)
    merge_keys = ["academic_year", "division", "org_id"]
    sos_cols = [c for c in sos.columns if c not in merge_keys]
    seasons_clean = seasons_chunk.drop(columns=sos_cols, errors="ignore")
    updated = seasons_clean.merge(sos, on=merge_keys, how="left")

    # Write back
    combined.to_csv(games_path, index=False)
    updated.to_csv(seasons_path, index=False)

    # Basic verification output
    patched_ids = set(int(x) for x in targets["org_id"].astype(int).tolist())
    present_ids = set(int(x) for x in pd.Series(combined["team_org_id"]).dropna().astype(int).unique().tolist())
    missing_after = patched_ids - present_ids
    if missing_after:
        raise SystemExit(f"Patch finished but these team_org_id are still missing in games: {sorted(missing_after)}")

    print("Patch complete.")
    print(f"- Updated {games_path} ({len(combined)} rows)")
    print(f"- Updated {seasons_path} ({len(updated)} rows)")


if __name__ == "__main__":
    main()


"""Build schedule-based SOS and RPI metrics from scraped game tables.

Prototype workflow:
  1. Load season-level model-ready data (team_stats_model_ready.csv).
  2. Scrape schedules for a subset of seasons/divisions into the game table.
  3. Compute WP, OppWP, OppOppWP, and RPI per team-season.
  4. Join SOS metrics back onto the season table and write augmented output.

This script is intended to be run manually at first, on a *small* slice of
data (e.g., D1 2022–2024) to validate parsing and math before scaling up.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

# Ensure the project root (containing the `src` package) is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.schedule_scraper import build_game_table_for_seasons
from src.data.sos import compute_sos_metrics


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Build schedule-based SOS metrics")
    parser.add_argument(
        "--years",
        type=str,
        default="2022-2024",
        help="Academic years to include, e.g. '2022-2024' or '2024'",
    )
    parser.add_argument(
        "--divisions",
        type=str,
        default="1",
        help="Divisions to include, comma-separated (e.g. '1' or '1,2')",
    )
    parser.add_argument(
        "--seasons-path",
        type=str,
        default="data/processed/team_stats_model_ready.csv",
        help="Path to season-level CSV (model-ready table)",
    )
    parser.add_argument(
        "--games-out",
        type=str,
        default="data/processed/games.csv",
        help="Where to write the scraped game table CSV",
    )
    parser.add_argument(
        "--seasons-out",
        type=str,
        default="data/processed/team_stats_with_sos.csv",
        help="Where to write the augmented season table CSV",
    )
    args = parser.parse_args()

    # Parse years and divisions
    if "-" in args.years:
        start, end = args.years.split("-", 1)
        years = list(range(int(start), int(end) + 1))
    else:
        years = [int(args.years)]

    divisions = [int(x) for x in args.divisions.split(",") if x.strip()]

    project_root = Path(__file__).resolve().parent.parent
    seasons_path = project_root / args.seasons_path
    games_out = project_root / args.games_out
    seasons_out = project_root / args.seasons_out

    seasons_df = pd.read_csv(seasons_path)

    # Step 1: build games table
    games_df = build_game_table_for_seasons(seasons_df, years=years, divisions=divisions)
    games_out.parent.mkdir(parents=True, exist_ok=True)
    games_df.to_csv(games_out, index=False)
    print(f"Saved games table to {games_out} with {len(games_df)} rows")

    # Step 2: compute SOS metrics
    sos_df = compute_sos_metrics(games_df)

    # Step 3: join back onto season table
    sos_df_renamed = sos_df.rename(columns={"team_org_id": "org_id"})
    merged = seasons_df.merge(
        sos_df_renamed,
        on=["academic_year", "division", "org_id"],
        how="left",
    )

    seasons_out.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(seasons_out, index=False)
    print(f"Saved seasons+SOS table to {seasons_out} with {len(merged)} rows")


if __name__ == "__main__":
    main()


"""Recompute WP/OppWP/OppOppWP/RPI for ALL seasons directly from games_*_d*.csv.

Usage (from project root):

    python scripts/recompute_sos_from_games_all.py \
      --seasons-in data/processed/team/team_stats_with_sos.csv \
      --seasons-out data/processed/team/team_stats_with_sos_full.csv

This does NOT scrape anything. It:
  - Reads games.csv
  - Uses src.data.sos to compute team-level WP, OppWP, OppOppWP, RPI
    for every (academic_year, division, team_org_id) that appears in games.
  - Renames team_org_id -> org_id
  - Merges these columns onto the season table, overwriting existing wp/opp_wp/opp_opp_wp/rpi.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.sos import compute_team_wp, compute_opp_wp, compute_opp_opp_wp  # noqa: E402

_TEAM_KEY = ["academic_year", "division", "team_org_id"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recompute schedule-based WP/OppWP/OppOppWP/RPI from per-slice games_*_d*.csv files."
    )
    parser.add_argument(
        "--seasons-in",
        type=str,
        default="data/processed/team/team_stats_with_sos.csv",
        help="Input season table CSV (default: data/processed/team/team_stats_with_sos.csv)",
    )
    parser.add_argument(
        "--seasons-out",
        type=str,
        default="data/processed/team/team_stats_with_sos_full.csv",
        help="Output season table CSV (default: data/processed/team/team_stats_with_sos_full.csv)",
    )
    args = parser.parse_args()

    processed_dir = PROJECT_ROOT / "data" / "processed"
    games_dir = processed_dir / "games"
    seasons_in_path = PROJECT_ROOT / args.seasons_in
    seasons_out_path = PROJECT_ROOT / args.seasons_out

    if not processed_dir.exists():
        raise SystemExit(f"Processed dir not found: {processed_dir}")
    if not seasons_in_path.exists():
        raise SystemExit(f"Seasons file not found: {seasons_in_path}")

    # Build games table from all per-slice files to ensure full coverage of all seasons/divisions.
    print(f"Loading games from per-slice files in {games_dir} ...")
    game_files = sorted(
        f for f in games_dir.glob("games_*_d*.csv") if f.name != "games.csv"
    )
    if not game_files:
        raise SystemExit(f"No games_*_d*.csv files found in {games_dir}")

    games_dfs = []
    for f in game_files:
        df = pd.read_csv(f)
        games_dfs.append(df)
        print(f"  - {f.name}: {len(df)} rows")

    games = pd.concat(games_dfs, ignore_index=True)
    print(f"Combined games_*_d*.csv: {len(games)} rows total.")
    required_cols = ["academic_year", "division", "team_org_id", "opp_org_id", "team_score", "opp_score"]
    missing = [c for c in required_cols if c not in games.columns]
    if missing:
        raise SystemExit(f"games.csv is missing required columns: {missing}")

    # 1) Compute WP, OppWP, OppOppWP
    print("Computing WP, OppWP, OppOppWP ...")
    wp_df = compute_team_wp(games)          # columns: _TEAM_KEY + [games_played, wins, losses, ties, wp]
    opp_df = compute_opp_wp(games, wp_df)   # columns: _TEAM_KEY + [opp_wp]
    opp_opp_df = compute_opp_opp_wp(games, opp_df)  # columns: _TEAM_KEY + [opp_opp_wp]

    # 2) Combine into single SOS DataFrame and compute RPI
    print("Combining SOS components ...")
    sos_df = (
        wp_df.merge(opp_df, on=_TEAM_KEY, how="left")
        .merge(opp_opp_df, on=_TEAM_KEY, how="left")
    )
    sos_df["rpi"] = (
        0.25 * sos_df["wp"].fillna(0.0)
        + 0.5 * sos_df["opp_wp"].fillna(0.0)
        + 0.25 * sos_df["opp_opp_wp"].fillna(0.0)
    )

    # 3) Prepare for merge onto seasons
    sos_df = sos_df.rename(columns={"team_org_id": "org_id"})
    sos_key = ["academic_year", "division", "org_id"]
    sos_cols = [c for c in sos_df.columns if c not in sos_key]

    print(f"Loaded SOS metrics for {len(sos_df)} team-seasons.")

    print(f"Loading season table from {seasons_in_path} ...")
    seasons = pd.read_csv(seasons_in_path)

    # Ensure key types align
    for c in sos_key:
        if c in seasons.columns:
            seasons[c] = seasons[c].astype(int, errors="ignore")
        sos_df[c] = sos_df[c].astype(int, errors="ignore")

    # Drop any existing versions of these columns so merge overwrites cleanly
    seasons_clean = seasons.drop(columns=sos_cols, errors="ignore")

    print("Merging SOS metrics onto season table ...")
    merged = seasons_clean.merge(sos_df, on=sos_key, how="left")

    print(f"Writing updated season table to {seasons_out_path} ({len(merged)} rows) ...")
    seasons_out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(seasons_out_path, index=False)
    print("Done.")


if __name__ == "__main__":
    main()
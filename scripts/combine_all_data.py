"""Combine all games and all team_stats_with_sos chunk files into single CSVs.

Run from project root. Writes:
  - data/processed/games/games.csv (all games from games_*_d1.csv and games_*_d2.csv)
  - data/processed/team/team_stats_with_sos.csv (all team_stats_with_sos_*_d*.csv chunks)

After running, training will use team_stats_with_sos.csv when present.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED = PROJECT_ROOT / "data" / "processed"
GAMES_DIR = PROCESSED / "games"
TEAM_DIR = PROCESSED / "team"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)


def combine_games() -> Path:
    """Concatenate all games_*_d1.csv and games_*_d2.csv into games.csv."""
    # Chunk files only: games_YYYY_d1.csv or games_YYYY_YYYY_d1.csv, same for d2
    # Exclude the output file games.csv
    pattern = "games_*_d*.csv"
    files = sorted(GAMES_DIR.glob(pattern))
    files = [f for f in files if f.name != "games.csv"]

    if not files:
        logging.warning("No games_*_d*.csv files found in %s", GAMES_DIR)
        return GAMES_DIR / "games.csv"

    dfs = []
    for f in files:
        df = pd.read_csv(f)
        dfs.append(df)
        logging.info("Read %s: %d rows", f.name, len(df))

    combined = pd.concat(dfs, ignore_index=True)
    # Optional: drop exact duplicates (e.g. if same file was included twice)
    before = len(combined)
    combined = combined.drop_duplicates()
    if len(combined) < before:
        logging.info("Dropped %d duplicate rows", before - len(combined))

    out = GAMES_DIR / "games.csv"
    combined.to_csv(out, index=False)
    logging.info("Wrote %s: %d rows", out, len(combined))
    return out


def combine_team_stats_with_sos() -> Path:
    """Concatenate all team_stats_with_sos_*_d1/d2 chunk files into one CSV."""
    # Chunk files: team_stats_with_sos_2014_2016_d1.csv, team_stats_with_sos_2022_d2.csv, etc.
    # Exclude the combined output file team_stats_with_sos.csv
    pattern = "team_stats_with_sos_*_d*.csv"
    files = sorted(TEAM_DIR.glob(pattern))
    files = [f for f in files if f.name != "team_stats_with_sos.csv"]

    if not files:
        logging.warning("No team_stats_with_sos_*_d*.csv chunk files found in %s", TEAM_DIR)
        return TEAM_DIR / "team_stats_with_sos.csv"

    dfs = []
    for f in files:
        df = pd.read_csv(f)
        dfs.append(df)
        logging.info("Read %s: %d rows", f.name, len(df))

    combined = pd.concat(dfs, ignore_index=True)
    before = len(combined)
    combined = combined.drop_duplicates(subset=["academic_year", "division", "org_id"])
    if len(combined) < before:
        logging.info("Dropped %d duplicate team-season rows", before - len(combined))

    out = TEAM_DIR / "team_stats_with_sos.csv"
    combined.to_csv(out, index=False)
    logging.info("Wrote %s: %d rows", out, len(combined))
    return out


def main() -> None:
    if not PROCESSED.exists():
        logging.error("Processed dir not found: %s", PROCESSED)
        sys.exit(1)

    logging.info("Combining games...")
    combine_games()
    logging.info("Combining team_stats_with_sos...")
    combine_team_stats_with_sos()
    logging.info("Done. Run training with: python run_training.py")


if __name__ == "__main__":
    main()

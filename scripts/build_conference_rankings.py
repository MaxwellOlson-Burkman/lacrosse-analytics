"""Build conference strength and rank per (academic_year, division).

Reads team_stats_with_sos_full_synced.csv; computes team_strength from
winning_percentage, rpi, schedule_difficulty_score; aggregates to conference
level and ranks within each year/division. Writes conference_rankings.csv.

Run from project root:
  python scripts/build_conference_rankings.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED = PROJECT_ROOT / "data" / "processed"
TEAM_DIR = PROCESSED / "team"
DEFAULT_SEASONS = TEAM_DIR / "team_stats_with_sos_full_synced.csv"
DEFAULT_OUT = TEAM_DIR / "conference_rankings.csv"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build conference strength and rank from season-level stats."
    )
    parser.add_argument(
        "--seasons",
        type=str,
        default=str(DEFAULT_SEASONS),
        help="Path to seasons CSV (default: data/processed/team/team_stats_with_sos_full_synced.csv)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=str(DEFAULT_OUT),
        help="Path to write conference_rankings.csv",
    )
    parser.add_argument(
        "--min-teams",
        type=int,
        default=3,
        help="Minimum teams per conference to include (default: 3)",
    )
    args = parser.parse_args()

    seasons_path = PROJECT_ROOT / args.seasons if not Path(args.seasons).is_absolute() else Path(args.seasons)
    out_path = PROJECT_ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)

    if not seasons_path.exists():
        print(f"ERROR: Seasons file not found: {seasons_path}", file=sys.stderr)
        sys.exit(1)

    required = ["academic_year", "division", "conference", "winning_percentage", "rpi", "schedule_difficulty_score", "org_id"]
    df = pd.read_csv(seasons_path)
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"ERROR: Seasons CSV missing columns: {missing}", file=sys.stderr)
        sys.exit(1)

    # Fill missing components with 0 so every row has a team_strength
    wp = df["winning_percentage"].fillna(0.0)
    rpi = df["rpi"].fillna(0.0)
    sds = df["schedule_difficulty_score"].fillna(0.0)
    df = df.copy()
    df["team_strength"] = 0.5 * wp + 0.3 * rpi + 0.2 * sds

    group_cols = ["academic_year", "division", "conference"]
    conf = (
        df.groupby(group_cols, as_index=False)
        .agg(
            conference_strength=("team_strength", "mean"),
            team_count=("org_id", "nunique"),
        )
    )

    if args.min_teams > 0:
        conf = conf[conf["team_count"] >= args.min_teams].copy()

    conf["conference_rank"] = (
        conf.groupby(["academic_year", "division"])["conference_strength"]
        .rank(ascending=False, method="dense")
        .astype(int)
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    conf.to_csv(out_path, index=False)
    print(f"Wrote {len(conf)} conference-season rows to {out_path}")


if __name__ == "__main__":
    main()

"""Build stats-based SOS (Option A) and model-based schedule strength (Option B).

Reads games.csv and team_stats_with_sos.csv; adds sos_avg_opp_win_pct,
sos_avg_opp_offense, sos_avg_opp_defense, sos_std_opp_win_pct, sos_stats_rank,
schedule_difficulty_score, schedule_difficulty_rank; writes updated season table
and saves the schedule strength model.

Run from project root:
  python scripts/build_sos_stats_and_model.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import joblib
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.sos_stats import (
    add_sos_ranks_to_seasons,
    build_schedule_feature_matrix,
    compute_stats_based_sos,
    predict_and_rank,
    train_schedule_strength_model,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build stats-based SOS and schedule strength model")
    parser.add_argument(
        "--games",
        type=str,
        default="data/processed/games/games.csv",
        help="Path to games CSV",
    )
    parser.add_argument(
        "--seasons",
        type=str,
        default="data/processed/team/team_stats_with_sos.csv",
        help="Path to season table with SOS CSV",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="data/processed/team/team_stats_with_sos.csv",
        help="Path to write updated season table",
    )
    parser.add_argument(
        "--model-out",
        type=str,
        default="models/schedule_strength_model.joblib",
        help="Path to save schedule strength model",
    )
    args = parser.parse_args()

    games_path = PROJECT_ROOT / args.games
    seasons_path = PROJECT_ROOT / args.seasons
    out_path = PROJECT_ROOT / args.out
    model_path = PROJECT_ROOT / args.model_out

    if not games_path.exists():
        logging.error("Games file not found: %s", games_path)
        sys.exit(1)
    if not seasons_path.exists():
        logging.error("Seasons file not found: %s", seasons_path)
        sys.exit(1)

    games_df = pd.read_csv(games_path)
    seasons_df = pd.read_csv(seasons_path)
    logging.info("Loaded games: %d rows, seasons: %d rows", len(games_df), len(seasons_df))

    # Option A: stats-based SOS
    stat_cols = [c for c in ["winning_percentage", "points_per_game", "scoring_defense"] if c in seasons_df.columns]
    sos_stats_df = compute_stats_based_sos(games_df, seasons_df, stat_cols=stat_cols)
    logging.info("Option A: computed stats-based SOS for %d team-seasons", len(sos_stats_df))

    merged = add_sos_ranks_to_seasons(seasons_df, sos_stats_df)
    logging.info("Merged Option A columns onto season table")

    # Option B: schedule strength model
    X, y = build_schedule_feature_matrix(merged)
    if X.empty or len(y) < 10:
        logging.warning("Insufficient data for Option B model; skipping. Add schedule_difficulty_score/rank as NaN.")
        merged["schedule_difficulty_score"] = float("nan")
        merged["schedule_difficulty_rank"] = pd.NA
    else:
        model, feature_names = train_schedule_strength_model(X, y)
        merged = predict_and_rank(model, feature_names, merged)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": model, "feature_names": feature_names}, model_path)
        meta_path = model_path.with_suffix(".json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"feature_names": feature_names}, f, indent=2)
        logging.info("Option B: trained and saved model to %s", model_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out_path, index=False)
    logging.info("Wrote updated season table to %s with %d rows", out_path, len(merged))


if __name__ == "__main__":
    main()

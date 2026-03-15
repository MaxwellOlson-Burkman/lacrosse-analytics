"""Stats-based SOS (Option A) and model-based schedule strength (Option B).

Option A: Average opponent quality from our season stats (winning_percentage,
points_per_game, scoring_defense) and rank within year/division.
Option B: Schedule features -> regression model -> schedule_difficulty_score and rank.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

_TEAM_KEY = ["academic_year", "division", "team_org_id"]

# Opponent stat columns to average for Option A (must exist in team_stats)
SOS_STAT_COLS = ["winning_percentage", "points_per_game", "scoring_defense"]
SOS_AVG_COLS = ["sos_avg_opp_win_pct", "sos_avg_opp_offense", "sos_avg_opp_defense"]

# Schedule features for Option B (subset of Option A outputs + games_played)
SCHEDULE_FEATURE_COLS = [
    "sos_avg_opp_win_pct",
    "sos_avg_opp_offense",
    "sos_avg_opp_defense",
    "games_played",
    "sos_std_opp_win_pct",
]


def compute_stats_based_sos(
    games: pd.DataFrame,
    team_stats: pd.DataFrame,
    stat_cols: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Compute stats-based SOS: average opponent quality per team-season (Option A).

    Uses games (team_org_id, opp_org_id) and team_stats (org_id, stat columns)
    to compute mean opponent winning_percentage and optionally points_per_game,
    scoring_defense. Returns one row per (academic_year, division, team_org_id)
    with sos_avg_opp_win_pct, optional sos_avg_opp_offense/defense, and sos_stats_rank.
    """
    if games.empty:
        out_cols = _TEAM_KEY + ["sos_avg_opp_win_pct", "sos_stats_rank"]
        if stat_cols and len(stat_cols) > 1:
            out_cols = _TEAM_KEY + SOS_AVG_COLS[: len(stat_cols)] + ["sos_stats_rank"]
        return pd.DataFrame(columns=out_cols)

    stat_cols = stat_cols or ["winning_percentage", "points_per_game", "scoring_defense"]
    stat_cols = [c for c in stat_cols if c in team_stats.columns]
    # Map output names
    name_map = {
        "winning_percentage": "sos_avg_opp_win_pct",
        "points_per_game": "sos_avg_opp_offense",
        "scoring_defense": "sos_avg_opp_defense",
    }
    output_names = [name_map.get(c, f"sos_avg_opp_{c}") for c in stat_cols]

    # Lookup: (academic_year, division, org_id) -> dict of stat -> value
    team_stats = team_stats.drop_duplicates(subset=["academic_year", "division", "org_id"])
    lookup_keys = list(zip(
        team_stats["academic_year"].astype(int),
        team_stats["division"].astype(int),
        team_stats["org_id"].astype(int),
    ))
    lookup = {}
    for i, key in enumerate(lookup_keys):
        lookup[key] = {c: team_stats.iloc[i][c] for c in stat_cols if c in team_stats.columns}

    # Also need std of opponent win% per team-season for Option B
    need_std = "winning_percentage" in stat_cols

    rows = []
    for key, grp in games.groupby(_TEAM_KEY):
        year, div, tid = int(key[0]), int(key[1]), int(key[2])
        opp_ids = grp["opp_org_id"].dropna().astype(int).unique().tolist()

        avgs = {}
        std_vals = [] if need_std else None
        for col, out_name in zip(stat_cols, output_names):
            if col not in team_stats.columns:
                continue
            vals = []
            for oid in opp_ids:
                opp_key = (year, div, int(oid))
                if opp_key in lookup and col in lookup[opp_key]:
                    v = lookup[opp_key][col]
                    if pd.notna(v):
                        vals.append(float(v))
            if vals:
                avgs[out_name] = float(np.mean(vals))
                if need_std and col == "winning_percentage":
                    std_vals = vals
            else:
                avgs[out_name] = np.nan

        row = {"academic_year": year, "division": div, "team_org_id": tid, **avgs}
        if need_std and std_vals is not None and len(std_vals) > 0:
            row["sos_std_opp_win_pct"] = float(np.std(std_vals))
        else:
            row["sos_std_opp_win_pct"] = np.nan
        rows.append(row)

    out = pd.DataFrame(rows)

    # Rank within (academic_year, division) by sos_avg_opp_win_pct descending
    if "sos_avg_opp_win_pct" in out.columns:
        out["sos_stats_rank"] = (
            out.groupby(["academic_year", "division"])["sos_avg_opp_win_pct"]
            .rank(ascending=False, method="min")
            .astype("Int64")
        )
    return out


def add_sos_ranks_to_seasons(
    seasons_df: pd.DataFrame,
    sos_stats_df: pd.DataFrame,
) -> pd.DataFrame:
    """Merge Option A output onto season table. Renames team_org_id -> org_id for merge."""
    if sos_stats_df.empty:
        return seasons_df
    ren = sos_stats_df.rename(columns={"team_org_id": "org_id"})
    return seasons_df.merge(
        ren,
        on=["academic_year", "division", "org_id"],
        how="left",
    )


def build_schedule_feature_matrix(seasons_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Build feature matrix and target for Option B model.

    Features: sos_avg_opp_win_pct, sos_avg_opp_offense, sos_avg_opp_defense,
    games_played, sos_std_opp_win_pct (all must exist). Target: sos_avg_opp_win_pct.
    Drops rows with NaN target. Returns (X, y) with same index.
    """
    feats = [c for c in SCHEDULE_FEATURE_COLS if c in seasons_df.columns]
    if "sos_avg_opp_win_pct" not in feats:
        feats.insert(0, "sos_avg_opp_win_pct")
    target = "sos_avg_opp_win_pct"
    y = seasons_df[target]
    valid = y.notna()
    X = seasons_df.loc[valid, feats].copy()
    y = y.loc[valid]
    # Impute remaining NaNs in X with column median
    for c in X.columns:
        if X[c].isna().any():
            X[c] = X[c].fillna(X[c].median())
    return X, y


def train_schedule_strength_model(
    X: pd.DataFrame,
    y: pd.Series,
) -> tuple[object, list[str]]:
    """Train a small regressor for schedule strength (Option B). Returns (model, feature_names)."""
    from sklearn.ensemble import GradientBoostingRegressor

    model = GradientBoostingRegressor(
        n_estimators=50,
        max_depth=3,
        learning_rate=0.1,
        random_state=42,
    )
    model.fit(X, y)
    return model, list(X.columns)


def predict_and_rank(
    model: object,
    feature_names: list[str],
    seasons_df: pd.DataFrame,
) -> pd.DataFrame:
    """Predict schedule_difficulty_score for each row and add schedule_difficulty_rank."""
    df = seasons_df.copy()
    available = [c for c in feature_names if c in df.columns]
    if not available:
        df["schedule_difficulty_score"] = np.nan
        df["schedule_difficulty_rank"] = pd.NA
        return df
    X = df[available].copy()
    for c in X.columns:
        if X[c].isna().any():
            X[c] = X[c].fillna(X[c].median())
    scores = model.predict(X)
    df["schedule_difficulty_score"] = scores
    df["schedule_difficulty_rank"] = (
        df.groupby(["academic_year", "division"])["schedule_difficulty_score"]
        .rank(ascending=False, method="min")
        .astype("Int64")
    )
    return df

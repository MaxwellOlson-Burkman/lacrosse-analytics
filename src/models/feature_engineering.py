"""Feature engineering: derived metrics, imputation, scaling."""

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

# Raw stat columns from model-ready data
RAW_STAT_COLS = [
    "assists_per_game",
    "caused_turnovers_per_game",
    "clearing_percentage",
    "face_off_winning_percentage",
    "ground_balls_per_game",
    "man_down_defense",
    "man_up_offense",
    "opponent_clear_percentage",
    "points_per_game",
    "saves_per_game",
    "scoring_defense",
    "scoring_margin",
    "scoring_offense",
    "shot_percentage",
    "turnovers_per_game",
]

# Columns to use for modeling (exclude opponent_clear_percentage due to 57% missing)
# We add it back as derived clearing_margin only when both exist
BASE_FEATURE_COLS = [
    c for c in RAW_STAT_COLS if c != "opponent_clear_percentage"
]

TARGET_COL = "winning_percentage"


def _possession_value_index(row: pd.Series) -> float:
    """Combine face-off win % and turnover efficiency into possession quality.
    Higher FO% = more possessions; fewer turnovers = better use of them."""
    fo = row.get("face_off_winning_percentage")
    to = row.get("turnovers_per_game")
    if pd.isna(fo) or pd.isna(to):
        return np.nan
    # Invert turnovers (lower is better); scale to 0-1 range approximately
    to_penalty = np.clip(to / 25.0, 0, 1)  # ~25 turnovers/game is bad
    return fo * (1 - to_penalty)


def _offensive_efficiency(row: pd.Series) -> float:
    """Scoring offense normalized by shot percentage (goals per shot)."""
    so = row.get("scoring_offense")
    sp = row.get("shot_percentage")
    if pd.isna(so) or pd.isna(sp) or sp <= 0:
        return np.nan
    return so / sp  # shots per game implied


def _defensive_efficiency(row: pd.Series) -> float:
    """Combined defensive quality: lower scoring defense + strong man-down + saves."""
    sd = row.get("scoring_defense")
    md = row.get("man_down_defense")
    sv = row.get("saves_per_game")
    if pd.isna(sd):
        return np.nan
    # Lower scoring defense is better; higher man-down pct and saves help
    # Simple composite: penalize high goals allowed, reward man-down
    base = 20 - sd  # invert so higher = better defense
    if not pd.isna(md):
        base += md * 5
    if not pd.isna(sv):
        base += sv * 0.3
    return base


def _extra_man_impact(row: pd.Series) -> float:
    """Net special teams edge: man-up offense minus man-down vulnerability."""
    mu = row.get("man_up_offense")
    md = row.get("man_down_defense")
    if pd.isna(mu) or pd.isna(md):
        return np.nan
    # man_down_defense is pct of EMO opportunities stopped; (1 - md) = vulnerability
    return mu - (1 - md)


def _clearing_margin(row: pd.Series) -> float:
    """Clearing % minus opponent clearing % (only when both exist)."""
    cl = row.get("clearing_percentage")
    opp = row.get("opponent_clear_percentage")
    if pd.isna(cl) or pd.isna(opp):
        return np.nan
    return cl - opp


def build_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add derived metrics to the DataFrame."""
    out = df.copy()

    out["possession_value_index"] = out.apply(_possession_value_index, axis=1)
    out["offensive_efficiency"] = out.apply(_offensive_efficiency, axis=1)
    out["defensive_efficiency"] = out.apply(_defensive_efficiency, axis=1)
    out["extra_man_impact"] = out.apply(_extra_man_impact, axis=1)
    out["clearing_margin"] = out.apply(_clearing_margin, axis=1)

    return out


def impute_missing(
    df: pd.DataFrame,
    feature_cols: Optional[list[str]] = None,
    strategy: str = "median",
) -> pd.DataFrame:
    """Impute missing values with per-year-division median (or mean)."""
    if feature_cols is None:
        feature_cols = [
            c for c in BASE_FEATURE_COLS
            + ["possession_value_index", "offensive_efficiency", "defensive_efficiency", "extra_man_impact"]
            if c in df.columns
        ]
    out = df.copy()
    agg = "median" if strategy == "median" else "mean"
    for col in feature_cols:
        if col not in out.columns:
            continue
        medians = out.groupby(["academic_year", "division"])[col].transform(agg)
        out[col] = out[col].fillna(medians)
        # Fallback: global median
        if out[col].isna().any():
            out[col] = out[col].fillna(out[col].median())
    return out


def get_feature_columns(include_derived: bool = True, include_clearing_margin: bool = False) -> list[str]:
    """Return list of feature column names for modeling."""
    cols = list(BASE_FEATURE_COLS)
    if include_derived:
        cols += ["possession_value_index", "offensive_efficiency", "defensive_efficiency", "extra_man_impact"]
    if include_clearing_margin:
        cols.append("clearing_margin")
    return cols


def prepare_features(
    df: pd.DataFrame,
    feature_cols: Optional[list[str]] = None,
    include_division: bool = True,
    scale: bool = False,
    scaler: Optional[StandardScaler] = None,
) -> tuple[pd.DataFrame, Optional[StandardScaler]]:
    """Build feature matrix with optional scaling. Returns (X, scaler or None)."""
    if feature_cols is None:
        feature_cols = get_feature_columns(include_derived=True, include_clearing_margin=False)

    available = [c for c in feature_cols if c in df.columns]
    X = df[available].copy()

    if include_division:
        X["division"] = df["division"].astype(int)

    if scale:
        if scaler is None:
            scaler = StandardScaler()
            X_scaled = pd.DataFrame(
                scaler.fit_transform(X),
                columns=X.columns,
                index=X.index,
            )
        else:
            X_scaled = pd.DataFrame(
                scaler.transform(X),
                columns=X.columns,
                index=X.index,
            )
        return X_scaled, scaler

    return X, None


def load_and_prepare(data_path: Optional[Path] = None) -> pd.DataFrame:
    """Load model-ready CSV, impute base stats, build derived features."""
    if data_path is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        processed = project_root / "data" / "processed"
        # Prefer combined season+SOS file when present (from scripts/combine_all_data.py)
        combined = processed / "team_stats_with_sos.csv"
        data_path = combined if combined.exists() else processed / "team_stats_model_ready.csv"

    df = pd.read_csv(data_path)
    df = impute_missing(df, feature_cols=[c for c in BASE_FEATURE_COLS if c in df.columns])
    df = build_derived_features(df)
    # Impute any remaining NaNs in derived features (edge cases)
    derived = ["possession_value_index", "offensive_efficiency", "defensive_efficiency", "extra_man_impact"]
    df = impute_missing(df, feature_cols=[c for c in derived if c in df.columns])
    return df

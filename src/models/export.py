"""Export model artifacts and team reports for the RAG pipeline."""

import json
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np
import pandas as pd

from .feature_engineering import prepare_features

# Display names for SOS and other features in team reports
FEATURE_DISPLAY_NAMES = {
    "wp": "Win % (from schedule)",
    "opp_wp": "Opponent win % (SOS)",
    "opp_opp_wp": "Opponents' opponent win %",
    "rpi": "RPI rating (schedule)",
}


def export_model(
    model,
    feature_names: list[str],
    metrics: dict[str, float],
    metadata: Optional[dict[str, Any]] = None,
    output_dir: Optional[Path] = None,
) -> dict[Path, Path]:
    """Save trained model, feature importance, and metadata."""
    if output_dir is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        output_dir = project_root / "models"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save model
    model_path = output_dir / "best_model.joblib"
    joblib.dump(model, model_path)

    # Feature importance (tree-based models)
    importance_path = output_dir / "feature_importance.json"
    if hasattr(model, "feature_importances_"):
        imp = dict(zip(feature_names, model.feature_importances_.tolist()))
        imp_sorted = dict(sorted(imp.items(), key=lambda x: x[1], reverse=True))
        with open(importance_path, "w", encoding="utf-8") as f:
            json.dump(imp_sorted, f, indent=2)
    else:
        with open(importance_path, "w", encoding="utf-8") as f:
            json.dump({}, f)

    # Model metadata
    meta_path = output_dir / "model_metadata.json"
    meta = metadata or {}
    meta["metrics"] = metrics
    meta["feature_names"] = feature_names
    meta["model_type"] = type(model).__name__
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return {"model": model_path, "importance": importance_path, "metadata": meta_path}


def generate_team_report(
    row: pd.Series,
    predictions: dict[str, float],
    league_means: pd.Series,
    top_features: list[str],
    df: Optional[pd.DataFrame] = None,
) -> str:
    """Generate a text summary of a team's stats vs league average for RAG retrieval."""
    team = row.get("team_name", "Unknown")
    year = row.get("academic_year", "")
    div = row.get("division", "")
    record = row.get("record", "")
    wp = row.get("winning_percentage")
    pred = predictions.get("winning_percentage")

    lines = [
        f"Team: {team}",
        f"Season: {year} D{div}",
        f"Record: {record}",
        f"Winning %: {wp:.3f}" if not pd.isna(wp) else "Winning %: N/A",
    ]
    if pred is not None:
        lines.append(f"Predicted Winning %: {pred:.3f}")

    lines.append("\nStat Comparison vs League Average:")
    for feat in top_features[:10]:
        if feat not in row.index or feat not in league_means:
            continue
        val = row.get(feat)
        avg = league_means.get(feat)
        if pd.isna(val) or pd.isna(avg):
            continue
        diff = val - avg
        direction = "above" if diff > 0 else "below"
        label = FEATURE_DISPLAY_NAMES.get(feat, feat)
        lines.append(f"  {label}: {val:.3f} ({abs(diff):.3f} {direction} average)")

    # Strength of schedule block (when columns present)
    if "sos_stats_rank" in row.index or "schedule_difficulty_rank" in row.index:
        year_div_count = None
        if df is not None and "academic_year" in df.columns and "division" in df.columns:
            mask = (df["academic_year"] == year) & (df["division"] == div)
            year_div_count = int(mask.sum())
        of_y = f" of {year_div_count}" if year_div_count is not None else ""

        lines.append("\nStrength of schedule:")
        if "sos_stats_rank" in row.index and pd.notna(row.get("sos_stats_rank")):
            r = int(row["sos_stats_rank"])
            lines.append(f"  Stats-based SOS rank: {r}{of_y}")
            if "sos_avg_opp_win_pct" in row.index and pd.notna(row.get("sos_avg_opp_win_pct")):
                lines.append(f"  Avg opponent win %: {row['sos_avg_opp_win_pct']:.3f}")
        if "schedule_difficulty_rank" in row.index and pd.notna(row.get("schedule_difficulty_rank")):
            r = int(row["schedule_difficulty_rank"])
            lines.append(f"  Model schedule difficulty rank: {r}{of_y}")
            if "schedule_difficulty_score" in row.index and pd.notna(row.get("schedule_difficulty_score")):
                lines.append(f"  Schedule difficulty score: {row['schedule_difficulty_score']:.3f}")

    return "\n".join(lines)


def export_team_reports(
    df: pd.DataFrame,
    model,
    feature_names: list[str],
    output_dir: Optional[Path] = None,
) -> Path:
    """Generate per-team performance reports and save as text files."""
    if output_dir is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        output_dir = project_root / "models" / "team_reports"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    X, _ = prepare_features(df, feature_cols=feature_names, include_division=True)
    preds = model.predict(X)
    league_means = df[feature_names].mean()

    # Feature importance for ranking
    if hasattr(model, "feature_importances_"):
        idx = np.argsort(model.feature_importances_)[::-1]
        top_features = [feature_names[i] for i in idx]
    else:
        top_features = feature_names[:10]

    for i, row in df.iterrows():
        pred_dict = {"winning_percentage": preds[i]}
        report = generate_team_report(row, pred_dict, league_means, top_features, df=df)
        safe_name = str(row.get("team_name", "unknown")).replace("/", "-").replace("\\", "-")
        year = row.get("academic_year", "")
        div = row.get("division", "")
        fname = f"{year}_D{div}_{safe_name}.txt"
        (output_dir / fname).write_text(report, encoding="utf-8")

    return output_dir

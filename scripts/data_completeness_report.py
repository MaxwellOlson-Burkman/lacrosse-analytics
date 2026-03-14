"""Generate completeness report for model-ready lacrosse features.

Usage:
    python scripts/data_completeness_report.py
    python scripts/data_completeness_report.py --input data/processed/team_stats_model_ready.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


FEATURE_COLS = [
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
    "winning_percentage",
]


def default_input_path(project_root: Path) -> Path:
    return project_root / "data" / "processed" / "team_stats_model_ready.csv"


def _load_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported file type: {path.suffix}")


def _normalize_to_model_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Rename common raw/processed columns into model-ready feature names."""
    rename_map = {
        "faceoff_winning_pct": "face_off_winning_percentage",
        "clearing_pct": "clearing_percentage",
        "opponent_clear_pct": "opponent_clear_percentage",
        "shot_pct": "shot_percentage",
        "winning_pct": "winning_percentage",
    }
    out = df.rename(columns=rename_map).copy()
    for col in FEATURE_COLS:
        if col not in out.columns:
            out[col] = pd.NA
    return out


def load_dataset(path: Path, project_root: Path) -> tuple[pd.DataFrame, Path]:
    # Primary: user path
    if path.exists():
        return _normalize_to_model_schema(_load_file(path)), path

    # Fallback candidates when model-ready file does not exist yet
    candidates = [
        project_root / "data" / "processed" / "team_stats_model_ready.parquet",
        project_root / "data" / "processed" / "team_stats_model_ready.csv",
        project_root / "data" / "processed" / "team_stats.parquet",
        project_root / "data" / "processed" / "team_stats.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return _normalize_to_model_schema(_load_file(candidate)), candidate

    raise FileNotFoundError(
        "No processed dataset found. Expected one of:\n"
        f"- {project_root / 'data' / 'processed' / 'team_stats_model_ready.csv'}\n"
        f"- {project_root / 'data' / 'processed' / 'team_stats_model_ready.parquet'}\n"
        f"- {project_root / 'data' / 'processed' / 'team_stats.csv'}\n"
        f"- {project_root / 'data' / 'processed' / 'team_stats.parquet'}\n\n"
        "Run the pipeline first:\n"
        "python run_pipeline.py --years 2014-2015"
    )


def build_feature_completeness(df: pd.DataFrame) -> pd.DataFrame:
    total_rows = len(df)
    rows = []

    for col in FEATURE_COLS:
        if col not in df.columns:
            non_null = 0
        else:
            non_null = int(df[col].notna().sum())
        missing = total_rows - non_null
        pct_missing = (missing / total_rows * 100.0) if total_rows else 0.0
        rows.append(
            {
                "feature": col,
                "non_null_rows": non_null,
                "missing_rows": missing,
                "missing_pct": round(pct_missing, 2),
            }
        )

    return pd.DataFrame(rows).sort_values("missing_pct", ascending=False)


def build_year_division_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    required = {"academic_year", "division", *FEATURE_COLS}
    missing_required = [c for c in required if c not in df.columns]
    if missing_required:
        raise ValueError(f"Dataset is missing required columns: {missing_required}")

    grouped = df.groupby(["academic_year", "division"], dropna=False)
    rows = []
    for (year, division), g in grouped:
        total_rows = len(g)
        complete_rows = int(g[FEATURE_COLS].notna().all(axis=1).sum())
        rows.append(
            {
                "academic_year": int(year),
                "division": int(division),
                "rows": total_rows,
                "fully_complete_rows": complete_rows,
                "fully_complete_pct": round((complete_rows / total_rows * 100.0), 2)
                if total_rows
                else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values(["academic_year", "division"])


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(description="Generate model-ready data completeness report")
    parser.add_argument(
        "--input",
        type=Path,
        default=default_input_path(project_root),
        help="Path to model-ready CSV/Parquet (default: data/processed/team_stats_model_ready.csv)",
    )
    parser.add_argument(
        "--save-dir",
        type=Path,
        default=None,
        help="Optional output folder to save report CSVs",
    )
    args = parser.parse_args()

    dataset_path = args.input if args.input.is_absolute() else (project_root / args.input)
    df, loaded_from = load_dataset(dataset_path, project_root)

    print(f"\nLoaded dataset: {loaded_from}")
    print(f"Total rows: {len(df)}")
    print(f"Columns: {len(df.columns)}")

    feature_report = build_feature_completeness(df)
    year_div_report = build_year_division_summary(df)

    print("\n=== Feature Completeness ===")
    print(feature_report.to_string(index=False))

    print("\n=== Year/Division Completeness ===")
    if year_div_report.empty:
        print("No rows available.")
    else:
        print(year_div_report.to_string(index=False))

    if args.save_dir is not None:
        save_dir = args.save_dir if args.save_dir.is_absolute() else (project_root / args.save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        feature_path = save_dir / "feature_completeness.csv"
        year_div_path = save_dir / "year_division_completeness.csv"
        feature_report.to_csv(feature_path, index=False)
        year_div_report.to_csv(year_div_path, index=False)
        print(f"\nSaved report files:\n- {feature_path}\n- {year_div_path}")


if __name__ == "__main__":
    main()

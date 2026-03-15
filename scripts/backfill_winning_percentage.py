"""Backfill winning_percentage from record in model-ready CSV/Parquet.

Run after the pipeline to fill winning_percentage where it's missing but record exists.
Usage:
    python scripts/backfill_winning_percentage.py
    python scripts/backfill_winning_percentage.py --input data/processed/team_stats_model_ready.csv
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd


def _winning_percentage_from_record(record_series: pd.Series) -> pd.Series:
    """Compute winning_percentage from record (e.g. '12-6' -> 12/(12+6))."""
    def one(val):
        if pd.isna(val) or not isinstance(val, str):
            return None
        m = re.match(r"^(\d+)-(\d+)$", str(val).strip())
        if not m:
            return None
        w, L = int(m.group(1)), int(m.group(2))
        total = w + L
        return (w / total) if total else None

    return record_series.map(one)


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Backfill winning_percentage from record")
    parser.add_argument(
        "--input",
        type=Path,
        default=project_root / "data" / "processed" / "team_stats_model_ready.csv",
        help="Path to model-ready CSV or Parquet",
    )
    parser.add_argument("--inplace", action="store_true", help="Overwrite the file in place")
    args = parser.parse_args()

    path = args.input if args.input.is_absolute() else (project_root / args.input)
    if not path.exists():
        print(f"File not found: {path}")
        return

    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)

    if "record" not in df.columns or "winning_percentage" not in df.columns:
        print("Missing 'record' or 'winning_percentage' column.")
        return

    before = df["winning_percentage"].notna().sum()
    computed = _winning_percentage_from_record(df["record"])
    df["winning_percentage"] = df["winning_percentage"].fillna(computed)
    after = df["winning_percentage"].notna().sum()
    filled = after - before

    print(f"Filled winning_percentage from record for {filled} rows (now {after}/{len(df)} non-null).")

    if args.inplace:
        if path.suffix.lower() == ".parquet":
            df.to_parquet(path, index=False)
        else:
            df.to_csv(path, index=False)
        print(f"Saved: {path}")
    else:
        out = path.parent / (path.stem + "_backfilled" + path.suffix)
        if path.suffix.lower() == ".parquet":
            df.to_parquet(out, index=False)
        else:
            df.to_csv(out, index=False)
        print(f"Wrote: {out} (use --inplace to overwrite original)")


if __name__ == "__main__":
    main()

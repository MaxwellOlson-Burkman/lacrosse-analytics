"""Sync season-level team stats from team_stats_model_ready.csv into a SOS table.

This does NOT scrape anything.

It:
  - Reads the "base" stats table: data/processed/team_stats_model_ready.csv
  - Reads a target seasons+SOS table (default: team_stats_with_sos_full.csv)
  - For every (academic_year, division, org_id) present in the target,
    overwrites all non-key stat columns with the values from the base table.

Result: any missing / stale stats like face_off_winning_percentage,
assists_per_game, etc. in the SOS table are refreshed from the canonical
model_ready stats.

Usage (from project root):

  python scripts/sync_team_stats_from_base.py \
    --target-in data/processed/team_stats_with_sos_full.csv \
    --target-out data/processed/team_stats_with_sos_full_synced.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED = PROJECT_ROOT / "data" / "processed"
BASE_PATH = PROCESSED / "team_stats_model_ready.csv"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Sync season-level team stats from team_stats_model_ready.csv into "
            "a SOS-augmented seasons table (no scraping)."
        )
    )
    parser.add_argument(
        "--target-in",
        type=str,
        default="data/processed/team_stats_with_sos_full.csv",
        help="Input SOS seasons CSV to update "
        "(default: data/processed/team_stats_with_sos_full.csv).",
    )
    parser.add_argument(
        "--target-out",
        type=str,
        default="data/processed/team_stats_with_sos_full_synced.csv",
        help="Output CSV with stats synced from base "
        "(default: data/processed/team_stats_with_sos_full_synced.csv).",
    )
    args = parser.parse_args()

    target_in_path = PROJECT_ROOT / args.target_in
    target_out_path = PROJECT_ROOT / args.target_out

    if not BASE_PATH.exists():
        raise SystemExit(f"Base stats file not found: {BASE_PATH}")
    if not target_in_path.exists():
        raise SystemExit(f"Target seasons file not found: {target_in_path}")

    print(f"Loading base stats from {BASE_PATH} ...")
    base = pd.read_csv(BASE_PATH)

    print(f"Loading target seasons from {target_in_path} ...")
    target = pd.read_csv(target_in_path)

    key_cols = ["academic_year", "division", "org_id"]

    # Ensure key types align
    for c in key_cols:
        if c in base.columns:
            base[c] = base[c].astype(int, errors="ignore")
        if c in target.columns:
            target[c] = target[c].astype(int, errors="ignore")

    # Columns we will copy from the base table.
    # These are all non-key columns; they include stats like
    # face_off_winning_percentage, assists_per_game, etc.
    base_cols_to_copy = [c for c in base.columns if c not in key_cols]

    # Deduplicate base on keys, keeping the first occurrence.
    before = len(base)
    base = base.drop_duplicates(subset=key_cols, keep="first")
    after = len(base)
    if after != before:
        print(f"Deduplicated base stats on keys: {before} -> {after} rows.")

    print(
        f"Will sync {len(base_cols_to_copy)} stat columns from base into target "
        f"for {len(target)} season rows."
    )

    # Drop those columns from target (if present) so the merge overwrites
    # cleanly, without touching SOS-related columns that only exist in target.
    target_clean = target.drop(columns=base_cols_to_copy, errors="ignore")

    merged = target_clean.merge(
        base[key_cols + base_cols_to_copy],
        on=key_cols,
        how="left",
        validate="m:1",
    )

    print(f"Writing synced seasons to {target_out_path} ({len(merged)} rows) ...")
    target_out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(target_out_path, index=False)
    print("Done.")


if __name__ == "__main__":
    main()


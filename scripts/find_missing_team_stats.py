"""Find season-level team stat gaps between team_stats_model_ready.csv
and team_stats_with_sos.csv.

Usage (from project root):

  # Simple report across all years/divisions
  python scripts/find_missing_team_stats.py

  # Limit the range / divisions
  python scripts/find_missing_team_stats.py --min-year 2014 --max-year 2020 --divisions 1,2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED = PROJECT_ROOT / "data" / "processed"
BASE_PATH = PROCESSED / "team" / "team_stats_model_ready.csv"
SOS_PATH = PROCESSED / "team" / "team_stats_with_sos.csv"


def iter_year_div_combos(
    seasons: pd.DataFrame,
    min_year: int | None,
    max_year: int | None,
    divisions: set[int] | None,
) -> Iterable[tuple[int, int]]:
    df = seasons[["academic_year", "division"]].dropna().copy()
    df["academic_year"] = df["academic_year"].astype(int)
    df["division"] = df["division"].astype(int)

    if min_year is not None:
        df = df[df["academic_year"] >= min_year]
    if max_year is not None:
        df = df[df["academic_year"] <= max_year]
    if divisions:
        df = df[df["division"].isin(divisions)]

    combos = (
        df.drop_duplicates()
        .sort_values(["academic_year", "division"])
        .itertuples(index=False, name=None)
    )
    return combos


def as_key_set(df: pd.DataFrame, key_cols: list[str]) -> set[tuple[int, ...]]:
    if df.empty:
        return set()
    out = df[key_cols].dropna().copy()
    for c in key_cols:
        out[c] = out[c].astype(int)
    return set(map(tuple, out.values.tolist()))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find missing season-level team stats and rows with NaN SOS fields."
    )
    parser.add_argument("--min-year", type=int, default=None)
    parser.add_argument("--max-year", type=int, default=None)
    parser.add_argument(
        "--divisions",
        type=str,
        default=None,
        help="Comma-separated list of divisions (e.g. '1' or '1,2'). Default: both.",
    )
    parser.add_argument(
        "--check-sos-cols",
        action="store_true",
        help="Also report rows with NaNs in SOS-related columns (wp, opp_wp, opp_opp_wp, rpi, sos_avg_opp_win_pct, sos_stats_rank).",
    )
    args = parser.parse_args()

    if not BASE_PATH.exists():
        print(f"ERROR: {BASE_PATH} does not exist.", file=sys.stderr)
        sys.exit(1)
    if not SOS_PATH.exists():
        print(f"ERROR: {SOS_PATH} does not exist.", file=sys.stderr)
        sys.exit(1)

    base = pd.read_csv(BASE_PATH)
    sos = pd.read_csv(SOS_PATH)

    divisions: set[int] | None = None
    if args.divisions:
        divisions = set(int(x) for x in args.divisions.split(",") if x.strip())

    combos = list(iter_year_div_combos(base, args.min_year, args.max_year, divisions))
    if not combos:
        print("No (academic_year, division) combos found matching filters.")
        return

    print(f"Scanning {len(combos)} (year, division) combos for season stat gaps...\n")

    key_cols = ["academic_year", "division", "org_id"]
    total_missing_rows = 0
    total_nan_rows = 0

    for year, div in combos:
        base_slice = base[(base["academic_year"] == year) & (base["division"] == div)].copy()
        sos_slice = sos[(sos["academic_year"] == year) & (sos["division"] == div)].copy()

        if base_slice.empty:
            continue

        base_keys = as_key_set(base_slice, key_cols)
        sos_keys = as_key_set(sos_slice, key_cols)

        missing = sorted(base_keys - sos_keys)
        extra = sorted(sos_keys - base_keys)  # sanity check; usually should be empty

        if not missing:
            print(f"{year} D{div}: OK (all base season rows present in team_stats_with_sos).")
        else:
            print(f"{year} D{div}: {len(missing)} season row(s) missing from team_stats_with_sos:")
            for y, d, org in missing:
                row = base_slice[base_slice["org_id"].astype(int) == org]
                name = row.iloc[0]["team_name"] if not row.empty else "<unknown>"
                print(f"  - {name} ({org})")
                total_missing_rows += 1
        if extra:
            print(f"{year} D{div}: WARNING: {len(extra)} row(s) exist in team_stats_with_sos but not in base table.")
        print()

        if args.check_sos_cols and not sos_slice.empty:
            # Which columns to treat as SOS-related?
            sos_cols = [
                c
                for c in [
                    "wp",
                    "opp_wp",
                    "opp_opp_wp",
                    "rpi",
                    "sos_avg_opp_win_pct",
                    "sos_avg_opp_offense",
                    "sos_avg_opp_defense",
                    "sos_std_opp_win_pct",
                    "sos_stats_rank",
                    "schedule_difficulty_score",
                    "schedule_difficulty_rank",
                ]
                if c in sos_slice.columns
            ]
            if sos_cols:
                mask_nan = sos_slice[sos_cols].isna().any(axis=1)
                bad = sos_slice[mask_nan]
                if not bad.empty:
                    print(f"{year} D{div}: {len(bad)} row(s) with NaN in SOS-related columns:")
                    for _, r in bad.iterrows():
                        org_id = int(r["org_id"]) if "org_id" in r else None
                        name = r.get("team_name", "<unknown>")
                        nan_cols = [c for c in sos_cols if pd.isna(r[c])]
                        print(f"  - {name} ({org_id}): NaN in {', '.join(nan_cols)}")
                        total_nan_rows += 1
                    print()

    print(f"\nSUMMARY:")
    print(f"- {total_missing_rows} missing season row(s) (present in model_ready, absent in team_stats_with_sos).")
    if args.check_sos_cols:
        print(f"- {total_nan_rows} row(s) with NaNs in SOS-related columns.")


if __name__ == "__main__":
    main()
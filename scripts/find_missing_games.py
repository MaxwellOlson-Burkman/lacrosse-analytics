"""Find teams whose schedules are missing from games_YYYY_dX.csv slices.

Usage (from project root):

  # Just report missing teams
  python scripts/find_missing_games.py

  # Report and automatically patch missing teams by scraping them
  python scripts/find_missing_games.py --auto-patch

You can also limit the scan:

  python scripts/find_missing_games.py --min-year 2014 --max-year 2020 --divisions 1,2
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED = PROJECT_ROOT / "data" / "processed"
SEASONS_PATH = PROCESSED / "team_stats_model_ready.csv"


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Find and optionally patch teams missing from games_YYYY_dX.csv slices.")
    parser.add_argument("--min-year", type=int, default=None, help="Only consider seasons >= this academic year.")
    parser.add_argument("--max-year", type=int, default=None, help="Only consider seasons <= this academic year.")
    parser.add_argument(
        "--divisions",
        type=str,
        default=None,
        help="Comma-separated list of divisions to include (e.g. '1' or '1,2'). Default: both.",
    )
    parser.add_argument(
        "--auto-patch",
        action="store_true",
        help="For each missing team, call patch_missing_team_games.py to scrape and fill games/SOS.",
    )
    parser.add_argument(
        "--dry-run-patch",
        action="store_true",
        help="With --auto-patch, only print patch commands instead of running them.",
    )
    args = parser.parse_args()

    if not SEASONS_PATH.exists():
        print(f"ERROR: {SEASONS_PATH} does not exist. Build team_stats_model_ready.csv first.", file=sys.stderr)
        sys.exit(1)

    seasons = pd.read_csv(SEASONS_PATH)
    divisions: set[int] | None = None
    if args.divisions:
        divisions = set(int(x) for x in args.divisions.split(",") if x.strip())

    combos = list(iter_year_div_combos(seasons, args.min_year, args.max_year, divisions))
    if not combos:
        print("No (academic_year, division) combos found matching filters.")
        return

    print(f"Scanning {len(combos)} (year, division) combos for missing team schedules...\n")

    total_missing = 0
    patch_commands: list[list[str]] = []

    for year, div in combos:
        games_path = PROCESSED / f"games_{year}_d{div}.csv"
        seasons_slice = seasons[(seasons["academic_year"] == year) & (seasons["division"] == div)].copy()

        if seasons_slice.empty:
            continue

        expected_ids = set(seasons_slice["org_id"].astype(int).tolist())

        if not games_path.exists():
            # Entire slice missing: all teams are missing
            print(f"{year} D{div}: games slice {games_path.name} is missing entirely; {len(expected_ids)} team(s) missing.")
            for team_name, org_id in (
                seasons_slice[["team_name", "org_id"]]
                .drop_duplicates()
                .itertuples(index=False, name=None)
            ):
                org_id = int(org_id)
                missing_report_line = f"{year} D{div}: {team_name} ({org_id}) [no_slice]"
                print(missing_report_line)
                total_missing += 1
                if args.auto_patch:
                    cmd = [
                        sys.executable,
                        str(PROJECT_ROOT / "scripts" / "patch_missing_team_games.py"),
                        "--year",
                        str(year),
                        "--division",
                        str(div),
                        "--org-id",
                        str(org_id),
                    ]
                    patch_commands.append(cmd)
            print()
            continue

        # We have a games slice: check which org_ids have zero games as team_org_id
        games = pd.read_csv(games_path)
        if "team_org_id" not in games.columns:
            print(f"{year} D{div}: {games_path.name} has no 'team_org_id' column; skipping.", file=sys.stderr)
            continue

        have_ids = set(games["team_org_id"].dropna().astype(int).unique().tolist())
        missing_ids = sorted(expected_ids - have_ids)
        if not missing_ids:
            print(f"{year} D{div}: OK (no missing team schedules).")
            continue

        print(f"{year} D{div}: {len(missing_ids)} team(s) missing from {games_path.name}:")
        for oid in missing_ids:
            row = seasons_slice[seasons_slice["org_id"].astype(int) == oid]
            if row.empty:
                name = "<unknown>"
            else:
                name = str(row.iloc[0]["team_name"])
            print(f"  - {name} ({oid})")
            total_missing += 1
            if args.auto_patch:
                cmd = [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "patch_missing_team_games.py"),
                    "--year",
                    str(year),
                    "--division",
                    str(div),
                    "--org-id",
                    str(oid),
                ]
                patch_commands.append(cmd)
        print()

    print(f"\nSUMMARY: {total_missing} missing team-season schedule(s) detected.")

    if not args.auto_patch or not patch_commands:
        if not args.auto_patch:
            print("\nRe-run with --auto-patch to automatically scrape and fill missing teams.")
        return

    print(f"\nPrepared {len(patch_commands)} patch command(s).")

    for cmd in patch_commands:
        print("PATCH:", " ".join(cmd))
        if args.dry_run_patch:
            continue
        # Run each patch sequentially so you can watch progress and failures
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"  -> Patch command failed with exit code {result.returncode}; continuing with next.", file=sys.stderr)


if __name__ == "__main__":
    main()
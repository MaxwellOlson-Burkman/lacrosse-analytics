"""Find team-seasons missing from player stats in the Django DB.

Compares expected team-seasons from team_stats_with_sos.csv (same source as
scrape_player_stats.py) to Player records in the DB. Reports which team-seasons
have no players (or suspiciously few) so you can clear those keys from
.scrape_player_progress.json and re-run the scraper.

Progress keys are: {year}_D{division}_{org_id}

Usage (from project root):

  # Report missing team-seasons
  python scripts/find_missing_player_stats.py

  # Limit scope
  python scripts/find_missing_player_stats.py --min-year 2021 --max-year 2024 --divisions 1,2

  # Also report team-seasons with very few players (possible bad parse)
  python scripts/find_missing_player_stats.py --min-players 5

  # Write missing rows to CSV (includes progress_key for clearing progress)
  python scripts/find_missing_player_stats.py --write-csv missing_player_stats.csv
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PROCESSED = PROJECT_ROOT / "data" / "processed"
SEASONS_CSV = PROCESSED / "team" / "team_stats_with_sos.csv"

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lacrosse_site.settings")
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

import django  # noqa: E402

django.setup()

from dashboard.models import Player  # noqa: E402


def _progress_key(year: int, division: int, org_id: int) -> str:
    """Match scrape_player_stats.py key format."""
    return f"{year}_D{division}_{org_id}"


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
    parser = argparse.ArgumentParser(
        description="Find team-seasons missing from player stats in the Django DB."
    )
    parser.add_argument(
        "--min-year",
        type=int,
        default=None,
        help="Only consider seasons >= this academic year.",
    )
    parser.add_argument(
        "--max-year",
        type=int,
        default=None,
        help="Only consider seasons <= this academic year.",
    )
    parser.add_argument(
        "--divisions",
        type=str,
        default=None,
        help="Comma-separated list of divisions (e.g. '1' or '1,2'). Default: both.",
    )
    parser.add_argument(
        "--seasons-csv",
        type=Path,
        default=SEASONS_CSV,
        help=f"Path to seasons CSV. Default: {SEASONS_CSV}",
    )
    parser.add_argument(
        "--min-players",
        type=int,
        default=0,
        metavar="N",
        help="Also report team-seasons with player count < N (default 0 = disabled).",
    )
    parser.add_argument(
        "--write-csv",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write missing rows (and optional low-count) to CSV with progress_key column.",
    )
    args = parser.parse_args()

    if not args.seasons_csv.exists():
        print(f"ERROR: {args.seasons_csv} does not exist.", file=sys.stderr)
        sys.exit(1)

    seasons = pd.read_csv(args.seasons_csv)
    required = ["academic_year", "division", "org_id", "team_name"]
    missing_cols = [c for c in required if c not in seasons.columns]
    if missing_cols:
        print(f"ERROR: CSV missing columns: {missing_cols}", file=sys.stderr)
        sys.exit(1)

    divisions: set[int] | None = None
    if args.divisions:
        divisions = set(int(x) for x in args.divisions.split(",") if x.strip())

    combos = list(iter_year_div_combos(seasons, args.min_year, args.max_year, divisions))
    if not combos:
        print("No (academic_year, division) combos found matching filters.")
        return

    print(f"Scanning {len(combos)} (year, division) combos for missing player stats...\n")

    total_missing = 0
    total_low_count = 0
    rows_missing: list[dict] = []
    rows_low_count: list[dict] = []

    for year, div in combos:
        slice_df = seasons[
            (seasons["academic_year"] == year) & (seasons["division"] == div)
        ].copy()
        if slice_df.empty:
            continue

        slice_df["org_id"] = slice_df["org_id"].astype(int)
        expected_ids = set(slice_df["org_id"].tolist())
        org_to_name = dict(zip(slice_df["org_id"], slice_df["team_name"]))

        have_ids = set(
            Player.objects.filter(academic_year=year, division=div)
            .values_list("team_org_id", flat=True)
            .distinct()
        )

        missing_ids = sorted(expected_ids - have_ids)
        if missing_ids:
            print(f"{year} D{div}: {len(missing_ids)} team(s) missing:")
            for oid in missing_ids:
                name = org_to_name.get(oid, "<unknown>")
                print(f"  - {name} ({oid})")
                total_missing += 1
                rows_missing.append(
                    {
                        "academic_year": year,
                        "division": div,
                        "org_id": oid,
                        "team_name": name,
                        "progress_key": _progress_key(year, div, oid),
                    }
                )
            print()
        else:
            print(f"{year} D{div}: OK (no missing).")

        if args.min_players > 0:
            for oid in sorted(have_ids):
                count = Player.objects.filter(
                    academic_year=year, division=div, team_org_id=oid
                ).count()
                if count < args.min_players:
                    name = org_to_name.get(oid, "<unknown>")
                    print(f"{year} D{div}: low count ({count} players): {name} ({oid})")
                    total_low_count += 1
                    rows_low_count.append(
                        {
                            "academic_year": year,
                            "division": div,
                            "org_id": oid,
                            "team_name": name,
                            "progress_key": _progress_key(year, div, oid),
                            "player_count": count,
                        }
                    )
            if total_low_count and missing_ids:
                print()

    summary_parts = [f"SUMMARY: {total_missing} missing team-season(s)."]
    if args.min_players > 0:
        summary_parts.append(f" {total_low_count} with player count < {args.min_players}.")
    print(f"\n{''.join(summary_parts)}")

    if total_missing > 0:
        print(
            "Re-run the player scraper after clearing these keys from .scrape_player_progress.json to retry."
        )

    if args.write_csv and (rows_missing or rows_low_count):
        out_rows = []
        for r in rows_missing:
            out_rows.append({**r, "reason": "missing"})
        for r in rows_low_count:
            out_rows.append({**r, "reason": "low_count"})
        out_df = pd.DataFrame(out_rows)
        out_path = Path(args.write_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_df.to_csv(out_path, index=False)
        print(f"\nWrote {len(out_rows)} row(s) to {out_path}")


if __name__ == "__main__":
    main()

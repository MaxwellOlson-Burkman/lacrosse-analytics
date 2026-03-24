"""Batch runner for build_sos.py over all year/division combos.

Usage (from project root):

    python scripts/run_build_sos_all.py

Resume-friendly usage:

    # Stop anytime with Ctrl+C; rerun and it will skip completed slices.
    python scripts/run_build_sos_all.py --resume

    # Or start from a specific slice (inclusive)
    python scripts/run_build_sos_all.py --start-year 2017 --start-division 2

This script:
  - Reads data/processed/team/team_stats_model_ready.csv to discover which
    (academic_year, division) combos exist.
  - For each combo, calls scripts/build_sos.py via subprocess with:
        --years <year>
        --divisions <division>
        --seasons-path data/processed/team/team_stats_model_ready.csv
        --games-out data/processed/games/games_<year>_d<div>.csv
        --seasons-out data/processed/team/team_stats_with_sos_<year>_d<div>.csv

After it finishes, you can run:

    python scripts/combine_all_data.py

to merge all per-year/division chunk files into unified games.csv and
team_stats_with_sos.csv for training and the dashboard.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED = PROJECT_ROOT / "data" / "processed"
TEAM_DIR = PROCESSED / "team"
GAMES_DIR = PROCESSED / "games"
SEASONS_REL = Path("data") / "processed" / "team" / "team_stats_model_ready.csv"
SEASONS_PATH = PROJECT_ROOT / SEASONS_REL
DEFAULT_PROGRESS = PROJECT_ROOT / ".build_sos_progress.json"


def discover_year_div_combos() -> list[tuple[int, int]]:
    """Read model-ready seasons table and return sorted (year, div) combos."""
    if not SEASONS_PATH.exists():
        raise FileNotFoundError(f"Seasons file not found: {SEASONS_PATH}")

    df = pd.read_csv(SEASONS_PATH, usecols=["academic_year", "division"])
    df = df.dropna(subset=["academic_year", "division"])
    df["academic_year"] = df["academic_year"].astype(int)
    df["division"] = df["division"].astype(int)

    combos = (
        df[["academic_year", "division"]]
        .drop_duplicates()
        .sort_values(["academic_year", "division"])
        .itertuples(index=False, name=None)
    )
    return list(combos)


def _outputs_for_combo(year: int, division: int) -> tuple[Path, Path, Path, Path]:
    """Return (games_rel, seasons_rel, games_abs, seasons_abs)."""
    games_rel = Path("data") / "processed" / "games" / f"games_{year}_d{division}.csv"
    seasons_rel = Path("data") / "processed" / "team" / f"team_stats_with_sos_{year}_d{division}.csv"
    return games_rel, seasons_rel, PROJECT_ROOT / games_rel, PROJECT_ROOT / seasons_rel


def _output_files_look_complete(games_path: Path, seasons_path: Path) -> bool:
    """Heuristic: both files exist and have a non-trivial size."""
    try:
        return (
            games_path.is_file()
            and seasons_path.is_file()
            and games_path.stat().st_size > 200
            and seasons_path.stat().st_size > 200
        )
    except OSError:
        return False


def _load_progress(progress_path: Path) -> dict:
    if not progress_path.exists():
        return {}
    try:
        return json.loads(progress_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_progress(progress_path: Path, payload: dict) -> None:
    progress_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_build_sos_for_combo(year: int, division: int, *, dry_run: bool = False) -> None:
    """Invoke scripts/build_sos.py for a single (year, division) slice."""
    PROCESSED.mkdir(parents=True, exist_ok=True)

    games_rel, seasons_out_rel, _, _ = _outputs_for_combo(year, division)

    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_sos.py"),
        "--years",
        str(year),
        "--divisions",
        str(division),
        "--seasons-path",
        str(SEASONS_REL),
        "--games-out",
        str(games_rel),
        "--seasons-out",
        str(seasons_out_rel),
    ]

    print(f"\n=== Building SOS for {year} D{division} ===")
    print("Command:", " ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-run build_sos.py across all year/division combos")
    parser.add_argument(
        "--start-year",
        type=int,
        default=None,
        help="Start from this academic year (inclusive). Requires --start-division if the year exists.",
    )
    parser.add_argument(
        "--start-division",
        type=int,
        default=None,
        choices=[1, 2],
        help="Start from this division (inclusive) within --start-year.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from the last completed slice recorded in the progress file (skips completed outputs too).",
    )
    parser.add_argument(
        "--progress-file",
        type=str,
        default=str(DEFAULT_PROGRESS),
        help="Path to progress JSON file (default: .build_sos_progress.json in project root).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip slices whose output files already exist (recommended).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would run, but do not execute build_sos.py.",
    )
    args = parser.parse_args()

    combos = discover_year_div_combos()
    if not combos:
        print("No (academic_year, division) combos found in team_stats_model_ready.csv.")
        return

    progress_path = Path(args.progress_file)
    progress = _load_progress(progress_path) if args.resume else {}
    last_done = progress.get("last_completed") if isinstance(progress, dict) else None

    start_year = args.start_year
    start_div = args.start_division
    if args.resume and last_done and isinstance(last_done, dict):
        try:
            start_year = int(last_done.get("academic_year"))
            start_div = int(last_done.get("division"))
        except Exception:
            start_year = args.start_year
            start_div = args.start_division

    print(f"Found {len(combos)} (year, division) combos to process:")
    for year, div in combos:
        print(f"  - {year} D{div}")

    def should_run_combo(year: int, div: int) -> bool:
        if start_year is None:
            return True
        if year > start_year:
            return True
        if year < start_year:
            return False
        # same year
        if start_div is None:
            return True
        return div >= start_div

    completed = 0
    skipped = 0
    for year, div in combos:
        if not should_run_combo(year, div):
            skipped += 1
            continue

        games_rel, seasons_rel, games_abs, seasons_abs = _outputs_for_combo(year, div)
        if args.skip_existing and _output_files_look_complete(games_abs, seasons_abs):
            print(f"\n=== Skipping {year} D{div} (outputs exist) ===")
            print(f"- {games_rel}")
            print(f"- {seasons_rel}")
            skipped += 1
            # still advance progress so resume keeps moving forward
            if not args.dry_run:
                _save_progress(
                    progress_path,
                    {"last_completed": {"academic_year": year, "division": div, "skipped": True}},
                )
            continue

        run_build_sos_for_combo(year, div, dry_run=args.dry_run)
        completed += 1
        if not args.dry_run:
            _save_progress(progress_path, {"last_completed": {"academic_year": year, "division": div, "skipped": False}})

    print(f"\nDone. Completed: {completed}, skipped: {skipped}")
    print("\nAll build_sos runs finished.")
    print("Next, combine per-slice outputs with:")
    print("  python scripts/combine_all_data.py")


if __name__ == "__main__":
    main()


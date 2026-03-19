"""Export player stats from DB to data/processed/players/player_stats.csv.

Workflow:
  1. python scripts/export_player_stats_to_csv.py
  2. python scripts/orgainze_players_csf.py   (regenerates sorted derived CSVs)

Or use --then-organize to do both in one command:
  python scripts/export_player_stats_to_csv.py --then-organize
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from itertools import islice
from pathlib import Path

# ---------------------------------------------------------------------------
# Django setup (must happen before importing models)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lacrosse_site.settings")

import django  # noqa: E402
django.setup()

from dashboard.models import Player, SeasonTotals  # noqa: E402

# ---------------------------------------------------------------------------
# Column order matching existing player_stats.csv
# ---------------------------------------------------------------------------
FIELDNAMES = [
    "id", "name", "team_name", "team_org_id", "academic_year", "division",
    "position", "class_year",
    "games_played", "goals", "assists", "points", "shots", "shots_on_goal",
    "ground_balls", "turnovers", "caused_turnovers", "faceoffs_won",
    "faceoffs_lost", "saves", "goals_allowed", "minutes_played",
]


def _season_totals_row(st: SeasonTotals | None) -> dict:
    if st is None:
        return {
            "games_played": 0, "goals": 0, "assists": 0, "points": 0,
            "shots": 0, "shots_on_goal": 0, "ground_balls": 0,
            "turnovers": 0, "caused_turnovers": 0, "faceoffs_won": 0,
            "faceoffs_lost": 0, "saves": "", "goals_allowed": "",
            "minutes_played": "",
        }
    return {
        "games_played": st.games_played,
        "goals": st.goals,
        "assists": st.assists,
        "points": st.points,
        "shots": st.shots,
        "shots_on_goal": st.shots_on_goal,
        "ground_balls": st.ground_balls,
        "turnovers": st.turnovers,
        "caused_turnovers": st.caused_turnovers,
        "faceoffs_won": st.faceoffs_won,
        "faceoffs_lost": st.faceoffs_lost,
        "saves": "" if st.saves is None else st.saves,
        "goals_allowed": "" if st.goals_allowed is None else st.goals_allowed,
        "minutes_played": "" if st.minutes_played is None else st.minutes_played,
    }


def _batched(iterable, batch_size: int):
    it = iter(iterable)
    while True:
        batch = list(islice(it, batch_size))
        if not batch:
            return
        yield batch


def export(out_path: Path, start_year: int | None = None, end_year: int | None = None) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    players_qs = Player.objects.all()
    if start_year is not None:
        players_qs = players_qs.filter(academic_year__gte=start_year)
    if end_year is not None:
        players_qs = players_qs.filter(academic_year__lte=end_year)
    players_qs = players_qs.order_by("academic_year", "team_name", "name")

    rows_written = 0
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        # Process players in chunks to avoid SQLite "too many SQL variables"
        for player_batch in _batched(players_qs.iterator(chunk_size=500), 500):
            player_ids = [p.id for p in player_batch]
            season_totals_by_player_id: dict[int, SeasonTotals] = {}
            for st in SeasonTotals.objects.filter(player_id__in=player_ids).order_by("player_id", "id"):
                season_totals_by_player_id.setdefault(st.player_id, st)

            for player in player_batch:
                st = season_totals_by_player_id.get(player.id)
                row = {
                    "id": player.id,
                    "name": player.name,
                    "team_name": player.team_name,
                    "team_org_id": player.team_org_id,
                    "academic_year": player.academic_year,
                    "division": player.division,
                    "position": player.position,
                    "class_year": player.class_year,
                }
                row.update(_season_totals_row(st))
                writer.writerow(row)
                rows_written += 1

    return rows_written


def main() -> None:
    parser = argparse.ArgumentParser(description="Export player stats DB → CSV")
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "data" / "processed" / "players" / "player_stats.csv"),
        help="Output CSV path (default: data/processed/players/player_stats.csv)",
    )
    parser.add_argument(
        "--then-organize",
        action="store_true",
        help="After export, run scripts/orgainze_players_csf.py to regenerate sorted CSVs",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=None,
        help="Optional lower bound for academic_year (inclusive)",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help="Optional upper bound for academic_year (inclusive)",
    )
    args = parser.parse_args()
    if args.start_year is not None and args.end_year is not None and args.start_year > args.end_year:
        parser.error("--start-year cannot be greater than --end-year")

    out_path = Path(args.out)
    print(f"Exporting to {out_path} …")
    count = export(out_path, start_year=args.start_year, end_year=args.end_year)
    print(f"Done. {count:,} rows written.")

    if args.then_organize:
        organize_script = PROJECT_ROOT / "scripts" / "orgainze_players_csf.py"
        print(f"\nRunning {organize_script} …")
        result = subprocess.run(
            [sys.executable, str(organize_script)],
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0:
            print("Warning: organize script exited with a non-zero code.", file=sys.stderr)


if __name__ == "__main__":
    main()

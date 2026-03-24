"""Build season-to-date player rollups from player-game rows (2026+)."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


def _to_int(value: str) -> int:
    try:
        return int(float((value or "").strip()))
    except Exception:
        return 0


def _to_float(value: str) -> float:
    try:
        return float((value or "").strip())
    except Exception:
        return 0.0


def _pct(n: int, d: int) -> str:
    if d <= 0:
        return ""
    return f"{n / d:.3f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate player-game rows into season-to-date totals")
    parser.add_argument("--in", dest="in_path", required=True, help="Input player-game CSV path")
    parser.add_argument("--out", dest="out_path", required=True, help="Output season-to-date CSV path")
    parser.add_argument("--qa-out", default="", help="Optional QA summary CSV path")
    args = parser.parse_args()

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)
    qa_out = Path(args.qa_out) if args.qa_out else None
    if not in_path.exists():
        raise FileNotFoundError(f"input file not found: {in_path}")

    with in_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise RuntimeError("input CSV has no rows")

    required = [
        "season",
        "division",
        "team_id",
        "team_name",
        "player_name",
        "goals",
        "assists",
        "shots",
        "shots_on_goal",
        "ground_balls",
        "turnovers",
        "caused_turnovers",
        "faceoffs_won",
        "faceoffs_lost",
    ]
    missing = [c for c in required if c not in rows[0]]
    if missing:
        raise RuntimeError(f"input missing required columns: {missing}")

    grouped: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    for row in rows:
        key = (
            (row.get("season") or "").strip(),
            (row.get("division") or "").strip(),
            (row.get("team_id") or "").strip(),
            (row.get("team_name") or "").strip(),
            (row.get("player_name") or "").strip(),
        )
        if key not in grouped:
            grouped[key] = {
                "season": key[0],
                "division": key[1],
                "team_id": key[2],
                "team_name": key[3],
                "player_name": key[4],
                "games_played": "0",
                "goals": "0",
                "assists": "0",
                "points": "0",
                "shots": "0",
                "shots_on_goal": "0",
                "ground_balls": "0",
                "turnovers": "0",
                "caused_turnovers": "0",
                "faceoffs_won": "0",
                "faceoffs_lost": "0",
                "faceoff_pct": "",
                "shot_pct": "",
            }
        out = grouped[key]
        out["games_played"] = str(_to_int(out["games_played"]) + 1)
        out["goals"] = str(_to_int(out["goals"]) + _to_int(row.get("goals", "")))
        out["assists"] = str(_to_int(out["assists"]) + _to_int(row.get("assists", "")))
        out["shots"] = str(_to_int(out["shots"]) + _to_int(row.get("shots", "")))
        out["shots_on_goal"] = str(_to_int(out["shots_on_goal"]) + _to_int(row.get("shots_on_goal", "")))
        out["ground_balls"] = str(_to_int(out["ground_balls"]) + _to_int(row.get("ground_balls", "")))
        out["turnovers"] = str(_to_int(out["turnovers"]) + _to_int(row.get("turnovers", "")))
        out["caused_turnovers"] = str(
            _to_int(out["caused_turnovers"]) + _to_int(row.get("caused_turnovers", ""))
        )
        out["faceoffs_won"] = str(_to_int(out["faceoffs_won"]) + _to_int(row.get("faceoffs_won", "")))
        out["faceoffs_lost"] = str(_to_int(out["faceoffs_lost"]) + _to_int(row.get("faceoffs_lost", "")))

    output_rows: list[dict[str, str]] = []
    team_goal_check: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for row in grouped.values():
        goals = _to_int(row["goals"])
        assists = _to_int(row["assists"])
        shots = _to_int(row["shots"])
        fow = _to_int(row["faceoffs_won"])
        fol = _to_int(row["faceoffs_lost"])
        row["points"] = str(goals + assists)
        row["shot_pct"] = _pct(goals, shots)
        row["faceoff_pct"] = _pct(fow, fow + fol)
        output_rows.append(row)
        team_key = (row["season"], row["division"], row["team_id"], row["team_name"])
        team_goal_check[team_key] += goals

    fieldnames = [
        "season",
        "division",
        "team_id",
        "team_name",
        "player_name",
        "games_played",
        "goals",
        "assists",
        "points",
        "shots",
        "shots_on_goal",
        "shot_pct",
        "ground_balls",
        "turnovers",
        "caused_turnovers",
        "faceoffs_won",
        "faceoffs_lost",
        "faceoff_pct",
    ]
    output_rows.sort(key=lambda r: (r["team_name"].lower(), r["player_name"].lower()))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    if qa_out:
        qa_rows = [
            {
                "season": k[0],
                "division": k[1],
                "team_id": k[2],
                "team_name": k[3],
                "sum_player_goals": str(v),
            }
            for k, v in sorted(team_goal_check.items(), key=lambda x: x[0][3].lower())
        ]
        qa_out.parent.mkdir(parents=True, exist_ok=True)
        with qa_out.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["season", "division", "team_id", "team_name", "sum_player_goals"],
            )
            writer.writeheader()
            writer.writerows(qa_rows)

    print(f"rows_in: {len(rows)}")
    print(f"rows_out: {len(output_rows)}")
    print(f"out: {out_path}")
    if qa_out:
        print(f"qa_out: {qa_out}")


if __name__ == "__main__":
    main()


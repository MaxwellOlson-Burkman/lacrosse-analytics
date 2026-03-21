"""MSDIE Step 2 pilot: conference-hub Sidearm team stats → processed CSV."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from msdie.fetchers.sidearm_fetcher import (
    SidearmFetchError,
    build_team_stats_url,
    fetch_sidearm_team_stats,
    normalize_base_url,
)

DEFAULT_VENDORS = PROJECT_ROOT / "data" / "vendors.csv"

MSDIE_COLUMNS = [
    "team_id",
    "season",
    "division",
    "conference",
    "wins",
    "losses",
    "goals_for",
    "goals_against",
    "shots",
    "shot_pct",
    "faceoff_wins",
    "faceoff_losses",
    "faceoff_pct",
    "ground_balls",
    "turnovers",
    "caused_turnovers",
    "clears_attempted",
    "clears_made",
    "clear_pct",
    "emo_goals",
    "emo_attempts",
    "emo_pct",
    "save_pct",
    "source_method",
]

# MSDIE schema ← Sidearm keys (see data/MSDIE_README.md §4)
SIDEARM_KEY_BY_COLUMN: dict[str, str] = {
    "team_id": "tid",
    "season": "season",
    "conference": "conf",
    "wins": "w",
    "losses": "l",
    "goals_for": "gf",
    "goals_against": "ga",
    "shots": "sh",
    "shot_pct": "sh_pct",
    "faceoff_wins": "fow",
    "faceoff_losses": "fol",
    "faceoff_pct": "fo_pct",
    "ground_balls": "gb",
    "turnovers": "to",
    "caused_turnovers": "ct",
    "clears_attempted": "cl_att",
    "clears_made": "cl_made",
    "clear_pct": "cl_pct",
    "emo_goals": "emo_g",
    "emo_attempts": "emo_att",
    "emo_pct": "emo_pct",
    "save_pct": "sv_pct",
}


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def map_sidearm_row_to_msdie(
    row: dict[str, object],
    *,
    season_fallback: str,
    division_label: str,
    conference_fallback: str,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for col in MSDIE_COLUMNS:
        if col == "source_method":
            out[col] = "conference_hub"
            continue
        if col == "division":
            out[col] = division_label
            continue
        if col == "conference":
            raw = row.get(SIDEARM_KEY_BY_COLUMN[col])
            out[col] = _cell(raw) if raw not in (None, "") else conference_fallback
            continue
        if col == "season":
            raw = row.get(SIDEARM_KEY_BY_COLUMN[col])
            out[col] = _cell(raw) if raw not in (None, "") else season_fallback
            continue
        sk = SIDEARM_KEY_BY_COLUMN[col]
        out[col] = _cell(row.get(sk, ""))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="MSDIE Step 2 Sidearm conference-hub pilot")
    parser.add_argument("--year", type=int, default=2024, help="Season year (default: 2024)")
    parser.add_argument(
        "--vendors",
        type=str,
        default=str(DEFAULT_VENDORS),
        help="Path to vendors.csv",
    )
    parser.add_argument(
        "--conference",
        type=str,
        default="Big Ten",
        help="Conference name filter (default: Big Ten)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Output CSV path (default: data/processed/msdie/d1_men_{year}.csv)",
    )
    args = parser.parse_args()

    vendors_path = Path(args.vendors)
    if not vendors_path.is_file():
        print(f"error: vendors file not found: {vendors_path}", file=sys.stderr)
        sys.exit(1)

    with vendors_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    conf_filter = (args.conference or "").strip()
    filtered = [r for r in rows if (r.get("conference") or "").strip() == conf_filter]
    if not filtered:
        print(f"error: no rows for conference {conf_filter!r} in {vendors_path}", file=sys.stderr)
        sys.exit(1)

    division_label = "D1"
    for r in filtered:
        div = (r.get("division") or "").strip().upper()
        if div:
            division_label = div
            break

    conference_url = ""
    for r in filtered:
        conference_url = (r.get("conference_url") or "").strip()
        if conference_url:
            break

    if not conference_url:
        print(
            f"error: no conference_url on any row for conference {conf_filter!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    base_url = normalize_base_url(conference_url)
    season_fallback = str(args.year)

    attempted_urls: list[str] = []
    result: dict | None = None
    last_err: SidearmFetchError | None = None

    url_with_season = build_team_stats_url(base_url, division=1, season=args.year)
    attempted_urls.append(url_with_season)
    try:
        result = fetch_sidearm_team_stats(base_url, division=1, season=args.year)
    except SidearmFetchError as exc:
        last_err = exc

    if result is None:
        print(
            f"warning: season-specific fetch failed ({last_err}); retrying without season parameter",
            file=sys.stderr,
        )
        url_no_season = build_team_stats_url(base_url, division=1, season=None)
        attempted_urls.append(url_no_season)
        try:
            result = fetch_sidearm_team_stats(base_url, division=1, season=None)
        except SidearmFetchError as exc:
            print(f"error: {exc}", file=sys.stderr)
            for u in attempted_urls:
                print(f"  attempted: {u}", file=sys.stderr)
            sys.exit(1)

    out_path = Path(args.out) if args.out else PROJECT_ROOT / "data" / "processed" / "msdie" / f"d1_men_{args.year}.csv"
    out_path = out_path if out_path.is_absolute() else PROJECT_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    msdie_rows = [
        map_sidearm_row_to_msdie(
            raw,
            season_fallback=season_fallback,
            division_label=division_label,
            conference_fallback=conf_filter,
        )
        for raw in result["rows"]
    ]

    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MSDIE_COLUMNS)
        writer.writeheader()
        writer.writerows(msdie_rows)

    print(f"request_url: {result['request_url']}")
    print(f"content_type: {result['content_type']}")
    print(f"rows_written: {len(msdie_rows)}")
    print(f"out: {out_path}")


if __name__ == "__main__":
    main()

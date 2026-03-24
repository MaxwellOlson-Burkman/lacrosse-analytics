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
from msdie.mapping import MSDIE_COLUMNS, map_sidearm_row_to_msdie

DEFAULT_VENDORS = PROJECT_ROOT / "data" / "vendors.csv"


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

    # Big Ten frequently links from bigten.org pages while stats APIs may be
    # hosted elsewhere (commonly big10sports.com). Keep the first pass strict
    # to conference_url-derived host and fail loudly for discovery workflows.
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
            source_method="conference_hub",
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

#!/usr/bin/env python3
"""
NCAA Lacrosse Data Pipeline Runner

Usage:
    python run_pipeline.py                    # Use config defaults
    python run_pipeline.py --years 2024       # Single year
    python run_pipeline.py --years 2023-2025  # Year range
    python run_pipeline.py --config path/to/config.yaml
"""

import argparse
from pathlib import Path

from src.data.pipeline import run_pipeline


def parse_years(s: str) -> tuple[int, int]:
    """Parse '2024' or '2023-2025' into (start, end)."""
    if "-" in s:
        parts = s.split("-")
        return int(parts[0]), int(parts[1])
    y = int(s)
    return y, y


def main() -> None:
    parser = argparse.ArgumentParser(description="Run NCAA Lacrosse data pipeline")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to data_config.yaml (default: config/data_config.yaml)",
    )
    parser.add_argument(
        "--years",
        type=str,
        default=None,
        help="Year or range, e.g. 2024 or 2014-2024 (overrides config)",
    )
    parser.add_argument(
        "--no-incremental",
        action="store_true",
        help="Re-scrape all seasons even if raw data exists",
    )
    parser.add_argument(
        "--process-only",
        action="store_true",
        help="Skip scraping; only process existing raw files into CSV/Parquet",
    )
    args = parser.parse_args()

    start_year = end_year = None
    if args.years:
        start_year, end_year = parse_years(args.years)

    scraped, df = run_pipeline(
        config_path=args.config,
        start_year=start_year,
        end_year=end_year,
        incremental=not args.no_incremental,
        process_only=args.process_only,
    )

    print(f"\nScraped {len(scraped)} new file(s)")
    print(f"Processed {len(df)} team-season records")
    if not df.empty:
        print(f"\nSample columns: {list(df.columns[:10])}...")


if __name__ == "__main__":
    main()

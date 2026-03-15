"""Orchestrate scraping and processing pipeline."""

import logging
import re
import time
from pathlib import Path

import pandas as pd

from .config_loader import load_config
from .archive_parsers import load_and_parse_archive
from .parsers import load_and_parse_raw_files
from .scraper import scrape_season

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _winning_percentage_from_record(record_series: pd.Series) -> pd.Series:
    """Compute winning_percentage from record (e.g. '12-6' -> 12/(12+6))."""
    def one(val):
        if pd.isna(val) or not isinstance(val, str):
            return None
        m = re.match(r"^(\d+)-(\d+)$", str(val).strip())
        if not m:
            return None
        w, L = int(m.group(1)), int(m.group(2))
        total = w + L
        return (w / total) if total else None

    return record_series.map(one)


def get_existing_raw_paths(raw_dir: Path) -> set[Path]:
    """Collect paths of existing raw HTML files."""
    paths = set()
    if not raw_dir.exists():
        return paths
    for f in raw_dir.rglob("*.html"):
        paths.add(f)
    return paths


def run_pipeline(
    config_path: str | Path | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    incremental: bool = True,
    process_only: bool = False,
) -> tuple[list[Path], pd.DataFrame]:
    """Run the full data pipeline: scrape then process.

    Args:
        config_path: Path to YAML config. Defaults to config/data_config.yaml.
        start_year: Override config start_year (optional).
        end_year: Override config end_year (optional).
        incremental: If True, skip seasons that already have raw data.
        process_only: If True, skip scraping and only process existing raw files.

    Returns:
        Tuple of (list of newly scraped file paths, combined processed DataFrame).
    """
    config = load_config(config_path)
    project_root = Path(__file__).resolve().parent.parent.parent

    raw_dir = project_root / config["output"]["raw_dir"]
    processed_dir = project_root / config["output"]["processed_dir"]

    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    scraping = config["scraping"]
    sy = start_year if start_year is not None else scraping["start_year"]
    ey = end_year if end_year is not None else scraping["end_year"]
    divisions = scraping["divisions"]

    scraped: list[Path] = []

    if not process_only:
        existing = get_existing_raw_paths(raw_dir) if incremental else set()
        season_cooldown = scraping.get("season_cooldown_seconds", 5)

        for year in range(sy, ey + 1):
            for division in divisions:
                logger.info("Scraping %s Division %s", year, division)
                new_files = scrape_season(
                    config=config,
                    year=year,
                    division=division,
                    existing_raw_paths=existing,
                    raw_dir=raw_dir,
                )
                scraped.extend(new_files)
                for p in new_files:
                    existing.add(p)
                if new_files:
                    logger.info("Cooling down %ds between seasons...", season_cooldown)
                    time.sleep(season_cooldown)
    else:
        logger.info("Process-only mode: skipping scraping.")

    # Process all raw data into structured output
    all_dfs: list[pd.DataFrame] = []
    for year in range(sy, ey + 1):
        for division in divisions:
            df = load_and_parse_raw_files(
                raw_dir=raw_dir,
                year=year,
                division=division,
                stat_configs=config["team_stats"],
            )
            if df.empty:
                # Try web1 ranksummary orgSummary fallback format
                df = load_and_parse_archive(
                    raw_dir=raw_dir,
                    year=year,
                    division=division,
                    stat_configs=config["team_stats"],
                )
            if not df.empty:
                all_dfs.append(df)

    if not all_dfs:
        logger.warning("No data to process. Run scraper first or check raw_dir.")
        combined = pd.DataFrame()
    else:
        combined = pd.concat(all_dfs, ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["academic_year", "division", "team_name", "org_id"],
            keep="first",
        )

        parquet_path = processed_dir / config["output"]["team_stats_file"]
        csv_path = processed_dir / config["output"]["team_stats_csv"]
        combined.to_parquet(parquet_path, index=False)
        combined.to_csv(csv_path, index=False)
        logger.info("Saved processed data: %s, %s", parquet_path, csv_path)

        # Build model-ready feature table with consistent names
        rename_map = {
            "faceoff_winning_pct": "face_off_winning_percentage",
            "clearing_pct": "clearing_percentage",
            "opponent_clear_pct": "opponent_clear_percentage",
            "shot_pct": "shot_percentage",
            "winning_pct": "winning_percentage",
        }
        feature_cols = [
            "assists_per_game",
            "caused_turnovers_per_game",
            "clearing_percentage",
            "face_off_winning_percentage",
            "ground_balls_per_game",
            "man_down_defense",
            "man_up_offense",
            "opponent_clear_percentage",
            "points_per_game",
            "saves_per_game",
            "scoring_defense",
            "scoring_margin",
            "scoring_offense",
            "shot_percentage",
            "turnovers_per_game",
            "winning_percentage",
        ]

        model_df = combined.rename(columns=rename_map).copy()
        for col in feature_cols:
            if col not in model_df.columns:
                model_df[col] = pd.NA

        model_df = model_df[
            ["academic_year", "division", "team_name", "org_id", "conference", "record", *feature_cols]
        ]

        # Fill winning_percentage from record where missing
        if "record" in model_df.columns and "winning_percentage" in model_df.columns:
            computed = _winning_percentage_from_record(model_df["record"])
            before = model_df["winning_percentage"].notna().sum()
            model_df["winning_percentage"] = model_df["winning_percentage"].fillna(computed)
            filled = model_df["winning_percentage"].notna().sum() - before
            if filled > 0:
                logger.info("Filled winning_percentage from record for %d rows", filled)

        model_parquet = processed_dir / config["output"]["model_ready_file"]
        model_csv = processed_dir / config["output"]["model_ready_csv"]
        model_df.to_parquet(model_parquet, index=False)
        model_df.to_csv(model_csv, index=False)
        logger.info("Saved model-ready data: %s, %s", model_parquet, model_csv)

    return scraped, combined

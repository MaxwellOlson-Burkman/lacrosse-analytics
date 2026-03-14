# Lacrosse Analytics Data Directory

This directory holds all scraped and processed NCAA lacrosse data.

## Structure

```
data/
├── raw/                    # Raw scraped HTML/JSON - never modify by hand
│   └── {year}/
│       └── division_{1|2}/
│           └── {stat_name}.html
├── processed/              # Clean, structured data ready for modeling
│   ├── team_stats.parquet
│   ├── team_stats.csv
│   ├── team_stats_model_ready.parquet
│   └── team_stats_model_ready.csv
└── README.md               # This file
```

## Data Sources

- **Primary:** https://stats.ncaa.org/rankings/national_ranking
- **Archive fallback:** https://web1.ncaa.org/stats/StatsSrv/ranksummary (rankSeq/listSchools/orgSummary flow)
- **Sport:** Men's Lacrosse (MLA)
- **Divisions:** I and II (configurable)
- **Years:** Configurable via `config/data_config.yaml`

When stats.ncaa.org returns 403, the pipeline automatically switches to web1 ranksummary and collects per-team `orgSummary` pages for the same season/division.

The `team_stats_model_ready.*` outputs are standardized for ML training and include:
- assists_per_game
- caused_turnovers_per_game
- clearing_percentage
- face_off_winning_percentage
- ground_balls_per_game
- man_down_defense
- man_up_offense
- opponent_clear_percentage
- points_per_game
- saves_per_game
- scoring_defense
- scoring_margin
- scoring_offense
- shot_percentage
- turnovers_per_game
- winning_percentage

## Re-running the Pipeline

1. Update `config/data_config.yaml` (e.g., `end_year: 2025` to add a new season)
2. Run: `python run_pipeline.py`
3. The scraper will skip seasons already present in `raw/` (incremental mode)

## Dataset snapshot (in repo)

- **Processed files** (`processed/*.csv`, `processed/*.parquet`) are committed so the repo is usable without re-scraping.
- **Coverage:** D1 and D2, academic years 2014–2024 (~1,418 team-season rows).
- **Known gaps:** `winning_percentage` and `opponent_clear_percentage` are partially missing (especially in archive-sourced years). Run `python scripts/data_completeness_report.py` for details.

## Last Updated

Run date is logged in pipeline output. Raw files live in `data/raw/` (gitignored); re-run the pipeline to refresh or extend data.

"""Re-fetch scoring_margin.html for D2 years where team count is low.

Probes multiple ranking_periods to find the one with the most teams.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.scraper import (
    RANKING_PERIOD_CANDIDATES,
    _count_teams,
    _create_session,
    _has_ranking_table,
    fetch_page,
    USER_AGENTS,
)

BASE_URL = "https://stats.ncaa.org"
RANKINGS_PATH = "/rankings/national_ranking"
SPORT_CODE = "MLA"
STAT_SEQ = 238  # scoring_margin
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def refetch_scoring_margin(year: int, division: int, min_teams: int = 30) -> None:
    out_dir = RAW_DIR / str(year) / f"division_{division}"
    out_path = out_dir / "scoring_margin.html"

    if out_path.exists():
        html = out_path.read_text(encoding="utf-8")
        count = _count_teams(html)
        if count >= min_teams:
            print(f"{year} D{division}: already has {count} teams, skipping.")
            return
        print(f"{year} D{division}: only {count} teams, probing higher periods...")
    else:
        print(f"{year} D{division}: file missing, fetching...")

    session = _create_session(USER_AGENTS[0])
    try:
        session.get(BASE_URL, timeout=10)
        time.sleep(1)
    except Exception:
        pass

    best_html = None
    best_count = 0
    best_period = None

    for period in sorted(RANKING_PERIOD_CANDIDATES, reverse=True):
        params = {
            "academic_year": year,
            "division": division,
            "ranking_period": period,
            "sport_code": SPORT_CODE,
            "stat_seq": STAT_SEQ,
        }
        html = fetch_page(base_url=BASE_URL, path=RANKINGS_PATH, params=params, session=session)
        if html and _has_ranking_table(html):
            count = _count_teams(html)
            print(f"  period={period}: {count} teams")
            if count > best_count:
                best_count = count
                best_html = html
                best_period = period
            if count >= min_teams:
                break
        time.sleep(3)

    if best_html and best_count > 0:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(best_html, encoding="utf-8")
        print(f"  Saved: {out_path} ({best_count} teams, period={best_period})")
    else:
        print(f"  FAILED: no valid page found for {year} D{division}")


if __name__ == "__main__":
    targets = [
        (2014, 2), (2016, 2), (2017, 2), (2018, 2),
        (2019, 2), (2020, 2), (2021, 2),
    ]
    for year, div in targets:
        refetch_scoring_margin(year, div)
        time.sleep(5)

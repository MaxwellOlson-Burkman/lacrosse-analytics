from __future__ import annotations

"""Debug helper: inspect 2014 team schedule table structure."""

from pathlib import Path

import sys
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.schedule_scraper import BrowserSession


def main() -> None:
    team_org_id = 103551  # 2014 Air Force
    year = 2014
    url = f"https://stats.ncaa.org/teams/{team_org_id}?year_id={team_org_id}"

    browser = BrowserSession()
    try:
        html = browser.fetch(url)
    finally:
        browser.close()

    if not html:
        print("Failed to fetch HTML")
        return

    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    print(f"Total tables: {len(tables)}")

    for idx, tbl in enumerate(tables):
        print(f"\n=== TABLE {idx} ===")
        ths = [th.get_text(strip=True) for th in tbl.find_all("th")]
        print(f"TH headers ({len(ths)}): {ths}")
        first_tr = tbl.find("tr")
        if first_tr:
            tds = [td.get_text(strip=True) for td in first_tr.find_all("td")]
            print(f"First row TDs ({len(tds)}): {tds}")
        rows = tbl.find_all("tr")
        print(f"Total rows: {len(rows)}")


if __name__ == "__main__":
    main()


from __future__ import annotations

"""Inspect 2014 schedule rows for one team to debug parsing."""

from pathlib import Path
import sys

from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.schedule_scraper import BrowserSession, _discover_year_id_for_team


def main() -> None:
    academic_year = 2014
    team_org_id = 103551  # Air Force

    browser = BrowserSession()
    try:
        year_id = _discover_year_id_for_team(browser, team_org_id, academic_year)
        print(f"year_id for {team_org_id} {academic_year}: {year_id}")
        url = f"https://stats.ncaa.org/teams/{team_org_id}?year_id={year_id}"
        html = browser.fetch(url)
    finally:
        browser.close()

    if not html:
        print("Failed to fetch HTML")
        return

    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    print(f"Total tables: {len(tables)}")

    # Choose table with Date/Opponent headers
    target = None
    for tbl in tables:
        headers = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        print("Table headers:", headers)
        if "date" in headers and "opponent" in headers:
            target = tbl
            break

    if target is None:
        print("No matching table found.")
        return

    rows = target.find_all("tr")
    print(f"Rows in target table: {len(rows)}")
    for idx, tr in enumerate(rows[:10]):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        ths = [th.get_text(strip=True) for th in tr.find_all("th")]
        print(f"Row {idx}: TH={ths} TD={tds}")


if __name__ == "__main__":
    main()


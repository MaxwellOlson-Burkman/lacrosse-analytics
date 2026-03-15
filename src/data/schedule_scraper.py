"""Scrape team schedules from stats.ncaa.org into the game table schema.

Uses Playwright in headed mode with anti-detection settings to bypass
Akamai bot protection. A single browser session is kept alive across all
team pages so the Akamai challenge only needs to be solved once.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Iterable, List, Optional
import re

import pandas as pd
from bs4 import BeautifulSoup

from .schedules import GameRecord, game_records_to_dataframe

logger = logging.getLogger(__name__)

BASE_URL = "https://stats.ncaa.org"

# Delay between team requests (seconds)
REQUEST_DELAY = 3

# How long to wait for the initial Akamai challenge vs. normal page loads (seconds)
AKAMAI_INITIAL_WAIT = 12
AKAMAI_PAGE_WAIT = 3

# Playwright navigation timeout (milliseconds)
PLAYWRIGHT_TIMEOUT_MS = 30000

# How many times to retry a page before giving up
MAX_FETCH_RETRIES = 2

_year_id_cache: dict[int, dict[int, int]] = {}


class BrowserSession:
    """Manages a persistent Playwright browser for scraping NCAA team pages."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._page = None
        self._challenge_solved = False

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self._page = context.new_page()

    def fetch(self, url: str) -> Optional[str]:
        self._ensure_browser()
        for attempt in range(1, MAX_FETCH_RETRIES + 1):
            try:
                self._page.goto(
                    url, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS
                )
            except Exception as exc:
                logger.warning(
                    "Playwright navigation failed for %s on attempt %d/%d: %s",
                    url,
                    attempt,
                    MAX_FETCH_RETRIES,
                    exc,
                )
                if attempt == MAX_FETCH_RETRIES:
                    # Give up on this URL; caller will skip this team/season.
                    return None
                # Short backoff before retrying
                time.sleep(5)
                continue

            # Navigation succeeded, wait for potential Akamai JS to finish
            wait = AKAMAI_INITIAL_WAIT if not self._challenge_solved else AKAMAI_PAGE_WAIT
            time.sleep(wait)

            content = self._page.content()
            if not content or len(content) < 3000:
                # Probably a challenge or blank page; treat as failure and maybe retry
                logger.warning("Empty/short content for %s (len=%d)", url, len(content or ""))
                if attempt == MAX_FETCH_RETRIES:
                    return None
                time.sleep(5)
                continue

            if "access denied" in content.lower():
                logger.warning("Access Denied for %s", url)
                return None

            if "<table" in content.lower():
                self._challenge_solved = True
                return content

            # Unexpected content; log and maybe retry
            logger.warning("No table found in content for %s", url)
            if attempt == MAX_FETCH_RETRIES:
                return None
            time.sleep(5)

        return None

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._page = None
        self._browser = None
        self._pw = None


def _season_label(academic_year: int) -> str:
    """Return label used in year dropdowns, e.g. 2024 -> '2023-24'."""
    start = academic_year - 1
    end_2 = str(academic_year)[-2:]
    return f"{start}-{end_2}"


def _discover_year_id_for_team(
    browser: BrowserSession,
    team_org_id: int,
    academic_year: int,
) -> Optional[int]:
    """Discover the correct year_id for a team/season from the year dropdown."""
    if team_org_id in _year_id_cache and academic_year in _year_id_cache[team_org_id]:
        return _year_id_cache[team_org_id][academic_year]

    url = f"{BASE_URL}/teams/{team_org_id}"
    html = browser.fetch(url)
    if not html:
        return None

    soup = BeautifulSoup(html, "lxml")
    select = soup.find("select", attrs={"name": "year_id"}) or soup.find(
        "select", id="year_id"
    )
    if not select:
        logger.warning("No year_id <select> found for team %s", team_org_id)
        return None

    target_label = _season_label(academic_year)
    _year_id_cache.setdefault(team_org_id, {})

    for opt in select.find_all("option"):
        text = opt.get_text(strip=True)
        value = opt.get("value")
        if not value:
            continue
        try:
            vid = int(float(value))
        except ValueError:
            continue

        if target_label in text:
            _year_id_cache[team_org_id][academic_year] = vid
            logger.info(
                "Discovered year_id=%s for team %s academic_year=%s",
                vid, team_org_id, academic_year,
            )
            return vid

    logger.warning(
        "Could not match academic_year=%s (%s) to any dropdown option for team %s",
        academic_year, target_label, team_org_id,
    )
    return None


def _fetch_team_schedule_html(
    browser: BrowserSession,
    team_org_id: int,
    academic_year: int,
    year_id: Optional[int] = None,
) -> Optional[str]:
    """Fetch the HTML page that contains the team's game-by-game schedule."""
    if year_id is None:
        year_id = _discover_year_id_for_team(browser, team_org_id, academic_year)
    if year_id is None:
        return None

    url = f"{BASE_URL}/teams/{team_org_id}?year_id={year_id}"
    return browser.fetch(url)


def _parse_score(result_text: str) -> Optional[tuple[str, int, int]]:
    """Parse a result cell like 'W 12-10' or 'L 10-11 (OT)'.

    Returns (result, team_score, opp_score) or None if parsing fails.
    """
    text = result_text.strip()
    if not text:
        return None
    parts = text.split()
    if not parts:
        return None
    res = parts[0].upper()
    if res not in ("W", "L", "T"):
        return None
    # Find pattern like "8-14" or "8 - 14" anywhere in the string
    m = re.search(r"(\d+)\s*-\s*(\d+)", text)
    if not m:
        return None
    try:
        team_score = int(m.group(1))
        opp_score = int(m.group(2))
    except ValueError:
        return None
    return res, team_score, opp_score


def _infer_location(opponent_cell_text: str) -> tuple[str, str]:
    """Infer location from opponent cell text and normalize opponent name."""
    raw = opponent_cell_text.strip()
    if raw.startswith("@"):
        name = raw.lstrip("@").strip()
        return "A", name
    return "H", raw


def parse_team_schedule_html(
    academic_year: int,
    division: int,
    team_org_id: int,
    team_name: str,
    html: str,
) -> List[GameRecord]:
    """Parse a team schedule HTML page into GameRecord objects."""
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        logger.warning("No tables found for team %s %s", team_org_id, academic_year)
        return []

    target_table = None
    for tbl in tables:
        headers = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if "date" in headers and "opponent" in headers:
            target_table = tbl
            break

    if target_table is None:
        logger.warning(
            "Could not find schedule table for team %s %s (no Date/Opponent headers)",
            team_org_id, academic_year,
        )
        return []

    # Find the actual header row: some older seasons put a section row first
    # (e.g. a single 'Schedule/Results' cell) and the TH header in the second row.
    all_rows = target_table.find_all("tr")
    header_row = None
    for tr in all_rows:
        ths = tr.find_all("th")
        if ths:
            # Skip section label rows like a single 'Schedule/Results' TD row
            header_row = tr
            break

    header_map: dict[int, str] = {}
    start_row_idx = 0

    if header_row is not None:
        header_cells = header_row.find_all("th")
        header_map = {i: th.get_text(strip=True).lower() for i, th in enumerate(header_cells)}
        # Data rows start after the header row
        start_row_idx = all_rows.index(header_row) + 1
    else:
        # Fallback: treat the first row's TDs as headers (very old layouts)
        first_row = all_rows[0] if all_rows else None
        if not first_row:
            logger.warning(
                "No usable header row for team %s %s (no TRs at all)",
                team_org_id,
                academic_year,
            )
            return []
        tds = first_row.find_all("td")
        header_map = {i: td.get_text(strip=True).lower() for i, td in enumerate(tds)}
        start_row_idx = 1

    date_idx = next((i for i, h in header_map.items() if "date" in h), None)
    opp_idx = next((i for i, h in header_map.items() if "opponent" in h), None)
    res_idx = next((i for i, h in header_map.items() if "result" in h or "score" in h), None)
    if date_idx is None or opp_idx is None or res_idx is None:
        logger.warning(
            "Missing required columns for team %s %s. Headers: %s",
            team_org_id, academic_year, list(header_map.values()),
        )
        return []

    records: List[GameRecord] = []
    for tr in all_rows[start_row_idx:]:
        tds = tr.find_all("td")
        if not tds or len(tds) <= max(date_idx, opp_idx, res_idx):
            continue

        date_text = tds[date_idx].get_text(strip=True)
        game_dt = None
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"):
            try:
                game_dt = datetime.strptime(date_text, fmt).date()
                break
            except ValueError:
                continue

        opp_cell = tds[opp_idx]
        opp_text = opp_cell.get_text(" ", strip=True)
        location, opp_name = _infer_location(opp_text)

        opp_link = opp_cell.find("a", href=True)
        opp_org_id: Optional[int] = None
        if opp_link and "/teams/" in opp_link["href"]:
            try:
                opp_org_id = int(
                    opp_link["href"].split("/teams/")[1].split("?")[0].split("/")[0]
                )
            except ValueError:
                opp_org_id = None

        res_text = tds[res_idx].get_text(" ", strip=True)
        parsed = _parse_score(res_text)
        if not parsed:
            continue
        result, team_score, opp_score = parsed

        records.append(
            GameRecord(
                academic_year=academic_year,
                division=division,
                team_org_id=team_org_id,
                opp_org_id=opp_org_id,
                team_name=team_name,
                opp_name=opp_name,
                game_date=game_dt,
                location=location,
                team_score=team_score,
                opp_score=opp_score,
                result=result,
                goal_margin=team_score - opp_score,
            )
        )

    logger.info(
        "Parsed %d games for %s D%s %s",
        len(records), academic_year, division, team_name,
    )
    return records


def scrape_team_schedule(
    browser: BrowserSession,
    academic_year: int,
    division: int,
    team_org_id: int,
    team_name: str,
) -> List[GameRecord]:
    """Scrape and parse one team's season schedule into GameRecord objects."""
    html = _fetch_team_schedule_html(browser, team_org_id, academic_year)
    if not html:
        return []
    return parse_team_schedule_html(
        academic_year=academic_year,
        division=division,
        team_org_id=team_org_id,
        team_name=team_name,
        html=html,
    )


def build_game_table_for_seasons(
    seasons_df: pd.DataFrame,
    years: Iterable[int],
    divisions: Iterable[int],
) -> pd.DataFrame:
    """Build a full games DataFrame for selected years/divisions.

    Opens a single Playwright browser, solves the Akamai challenge once,
    then iterates through all teams reusing the same authenticated session.
    """
    mask = seasons_df["academic_year"].isin(list(years)) & seasons_df["division"].isin(
        list(divisions)
    )
    subset = seasons_df.loc[mask, ["academic_year", "division", "org_id", "team_name"]]

    all_records: list[GameRecord] = []
    total = len(subset)
    browser = BrowserSession()

    try:
        for idx, (_, row) in enumerate(subset.iterrows(), 1):
            year = int(row["academic_year"])
            div = int(row["division"])
            org_id = int(row["org_id"])
            name = str(row["team_name"])
            logger.info(
                "[%d/%d] Scraping schedule for %s D%s %s (%s)",
                idx, total, year, div, name, org_id,
            )
            recs = scrape_team_schedule(
                browser=browser,
                academic_year=year,
                division=div,
                team_org_id=org_id,
                team_name=name,
            )
            all_records.extend(recs)

            if idx < total:
                time.sleep(REQUEST_DELAY)
    finally:
        browser.close()

    return game_records_to_dataframe(all_records)

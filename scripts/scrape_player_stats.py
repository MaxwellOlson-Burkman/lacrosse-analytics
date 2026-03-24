"""Scrape player-level season stats from stats.ncaa.org.

Phase 1B of the Lacrosse Analytics "War Room" plan.

Reuses the Playwright BrowserSession from src.data.schedule_scraper to bypass
Akamai, then for each team-season:
  1. Visits the team landing page (/teams/{org_id}).
  2. Discovers the "Roster" navigation link from the page HTML.
  3. Follows that link to get the player stats table.
  4. Parses per-player season totals into Player + SeasonTotals Django models.

Progress is checkpointed to .scrape_player_progress.json so the run can
safely resume after interruption.

Usage:
    python scripts/scrape_player_stats.py --years 2024
    python scripts/scrape_player_stats.py --years 2021-2026
    python scripts/scrape_player_stats.py --test 594020   # test one team by org_id
    python scripts/scrape_player_stats.py --reset          # clear checkpoint & exit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lacrosse_site.settings")
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
import django  # noqa: E402

django.setup()

from dashboard.models import Player, SeasonTotals  # noqa: E402
from src.data.schedule_scraper import (  # noqa: E402
    BASE_URL,
    BrowserSession,
    REQUEST_DELAY,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

PROGRESS_PATH = PROJECT_ROOT / ".scrape_player_progress.json"
SEASONS_CSV = PROJECT_ROOT / "data" / "processed" / "team" / "team_stats_with_sos.csv"


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def _load_progress() -> dict[str, bool]:
    if not PROGRESS_PATH.exists():
        return {}
    try:
        with open(PROGRESS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): bool(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_progress(done: dict[str, bool]) -> None:
    PROGRESS_PATH.write_text(json.dumps(done, indent=2), encoding="utf-8")


def _key(year: int, division: int, org_id: int) -> str:
    return f"{year}_D{division}_{org_id}"


# ---------------------------------------------------------------------------
# Team-season iterator
# ---------------------------------------------------------------------------

def _iter_team_seasons(seasons_csv: Path, years: list[int] | None):
    df = pd.read_csv(seasons_csv)
    if years:
        df = df[df["academic_year"].isin(years)]
    cols = ["academic_year", "division", "org_id", "team_name"]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise SystemExit(f"Seasons CSV missing columns: {missing}")
    for _, row in df[cols].drop_duplicates().iterrows():
        yield int(row["academic_year"]), int(row["division"]), int(row["org_id"]), str(row["team_name"])


# ---------------------------------------------------------------------------
# Link discovery – find the roster/stats URL from the team landing page
# ---------------------------------------------------------------------------

def _discover_stats_url(team_html: str, org_id: int) -> str | None:
    """Parse team landing page HTML to find the player stats link.

    stats.ncaa.org team pages have sub-navigation with links like:
       <a href="/teams/594020/season_to_date_stats">Team Statistics</a>
       <a href="/teams/594020/roster">Roster</a>

    The "Team Statistics" (season_to_date_stats) page has actual player stats
    (goals, assists, etc.).  The "Roster" page only has GP/GS and bio info.
    We prioritise "Team Statistics" over "Roster".
    """
    soup = BeautifulSoup(team_html, "lxml")

    def _abs(href: str) -> str:
        return href if href.startswith("http") else f"{BASE_URL}{href}"

    # Pass 1: look for "Team Statistics" / "season_to_date_stats"
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        text = a_tag.get_text(strip=True).lower()
        if not href or href == "#" or "javascript" in href.lower():
            continue
        if "season_to_date" in href or "team statistic" in text:
            url = _abs(href)
            logger.info("  Discovered stats link: %s (text=%r)", url, a_tag.get_text(strip=True))
            return url

    # Pass 2: look for "Player Stats" / "Individual Stats"
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        text = a_tag.get_text(strip=True).lower()
        if not href or href == "#" or "javascript" in href.lower():
            continue
        if "player stat" in text or "individual stat" in text:
            url = _abs(href)
            logger.info("  Discovered player-stats link: %s (text=%r)", url, a_tag.get_text(strip=True))
            return url

    # Pass 3: fall back to "Roster" (has GP/GS but not full stats)
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        text = a_tag.get_text(strip=True).lower()
        if not href or href == "#" or "javascript" in href.lower():
            continue
        if "roster" in text or re.search(r"/roster\b", href, re.IGNORECASE):
            url = _abs(href)
            logger.info("  Discovered roster link (fallback): %s (text=%r)", url, a_tag.get_text(strip=True))
            return url

    return None


def _dump_page_links(html: str, label: str) -> None:
    """Log all anchor hrefs on a page for debugging."""
    soup = BeautifulSoup(html, "lxml")
    links = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        text = a_tag.get_text(strip=True)
        if href and href != "#" and "javascript" not in href.lower():
            links.append(f"  {text!r:40s} -> {href}")
    logger.info("Links found on %s (%d total):\n%s", label, len(links), "\n".join(links[:50]))


# ---------------------------------------------------------------------------
# HTML fetching – two-step: team page -> roster page
# ---------------------------------------------------------------------------

def _fetch_team_stats_html(
    browser: BrowserSession,
    org_id: int,
    academic_year: int,
    *,
    debug: bool = False,
) -> str | None:
    """Fetch the page containing player stats for a team-season.

    The org_id in our CSV is already year-specific (it changes each season),
    so we simply load /teams/{org_id}, find the "Roster" nav link, and follow it.
    """
    team_url = f"{BASE_URL}/teams/{org_id}"
    team_html = browser.fetch(team_url)
    if not team_html:
        logger.warning("  Could not load team page %s", team_url)
        return None

    if debug:
        _dump_page_links(team_html, team_url)

    # Check if the team page itself already has a player-level stats table
    soup = BeautifulSoup(team_html, "lxml")
    for tbl in soup.find_all("table"):
        first_row = tbl.find("tr")
        if first_row:
            hdr = " ".join(th.get_text(strip=True).lower() for th in first_row.find_all("th"))
            if "name" in hdr and any(kw in hdr for kw in ("goal", "pts", "gp", "assist")):
                logger.info("  Player stats found directly on team page")
                return team_html

    # Discover stats/roster link from page navigation
    stats_url = _discover_stats_url(team_html, org_id)
    if stats_url:
        stats_html = browser.fetch(stats_url)
        if stats_html:
            return stats_html
        logger.warning("  Stats URL returned no content: %s", stats_url)

    # Fallback: try common URL patterns (vary by NCAA site era)
    for pattern in [
        f"{BASE_URL}/teams/{org_id}/season_to_date_stats",
        f"{BASE_URL}/teams/{org_id}/roster",
    ]:
        logger.info("  Trying fallback URL: %s", pattern)
        html = browser.fetch(pattern)
        if html:
            return html

    logger.warning("  Could not find roster page for org_id=%s", org_id)
    return None


# ---------------------------------------------------------------------------
# Roster / stats table parser
# ---------------------------------------------------------------------------

def _parse_roster_stats(
    academic_year: int,
    division: int,
    org_id: int,
    team_name: str,
    html: str,
) -> list[tuple[Player, dict]]:
    """Parse a team stats page into (Player, season_totals_dict) pairs.

    The page may contain multiple tables (e.g., field players + goalies,
    or grouped by class year). We parse every table that has a recognisable
    'Player'/'Name' column header and aggregate all players.
    """
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        return []

    all_results: list[tuple[Player, dict]] = []
    seen_names: set[str] = set()

    for tbl_idx, tbl in enumerate(tables):
        header_row = tbl.find("tr")
        if header_row is None:
            continue

        headers = [th.get_text(strip=True).lower() for th in header_row.find_all("th")]
        if not headers:
            headers = [td.get_text(strip=True).lower() for td in header_row.find_all("td")]
        if not headers:
            continue

        headers_joined = " ".join(headers)
        if "player" not in headers_joined and "name" not in headers_joined:
            continue

        def _col_exact(*candidates: str) -> int | None:
            for want in candidates:
                for i, h in enumerate(headers):
                    if h == want:
                        return i
            for want in candidates:
                for i, h in enumerate(headers):
                    if want in h:
                        return i
            return None

        name_i = _col_exact("player", "name")
        if name_i is None:
            continue

        jersey_i = _col_exact("#", "no", "jersey")
        pos_i = _col_exact("pos", "position")
        cls_i = _col_exact("yr", "cl", "class")
        gp_i = _col_exact("gp", "games played")
        gs_i = _col_exact("gs", "games started")
        g_i = _col_exact("goals")
        a_i = _col_exact("assists", "ast")
        pts_i = _col_exact("points", "pts")
        sh_i = _col_exact("shots")
        sog_i = _col_exact("sog", "shots on goal")
        gb_i = _col_exact("gb", "ground balls")
        to_i = _col_exact("to", "turnovers")
        ct_i = _col_exact("ct", "caused turnovers")
        fow_i = _col_exact("fo won", "fow", "fo w")
        fol_i = _col_exact("fo lost", "fol", "fo l")
        fot_i = _col_exact("fos taken", "fo taken", "fot")
        sv_i = _col_exact("saves", "sv")
        ga_i = _col_exact("goals allowed", "ga")
        min_i = _col_exact("minutes", "min")

        logger.info("  Table %d headers: %s", tbl_idx, headers)

        def _get(tds, idx):
            if idx is None or idx >= len(tds):
                return ""
            return tds[idx].get_text(strip=True)

        def _int(tds, idx):
            txt = _get(tds, idx)
            try:
                return int(float(txt)) if txt else 0
            except ValueError:
                return 0

        def _float(tds, idx):
            txt = _get(tds, idx)
            try:
                return float(txt) if txt else 0.0
            except ValueError:
                return 0.0

        for tr in tbl.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if not tds or name_i >= len(tds):
                continue
            name = _get(tds, name_i)
            name_lower = name.lower()
            if not name or name_lower in ("totals", "total", "team", "opponent", "opponents") or "totals" in name_lower:
                continue

            dedup_key = f"{name}|{_get(tds, pos_i)}"
            if dedup_key in seen_names:
                continue
            seen_names.add(dedup_key)

            player, _ = Player.objects.get_or_create(
                name=name,
                team_org_id=org_id,
                academic_year=academic_year,
                division=division,
                defaults={
                    "team_name": team_name,
                    "jersey_number": _get(tds, jersey_i),
                    "position": _get(tds, pos_i),
                    "class_year": _get(tds, cls_i),
                },
            )
            if _get(tds, pos_i):
                player.position = _get(tds, pos_i)
            if _get(tds, cls_i):
                player.class_year = _get(tds, cls_i)
            player.team_name = team_name
            player.save()

            fo_won = _int(tds, fow_i)
            if fol_i is not None:
                fo_lost = _int(tds, fol_i)
            elif fot_i is not None:
                fo_lost = max(0, _int(tds, fot_i) - fo_won)
            else:
                fo_lost = 0

            totals = {
                "games_played": _int(tds, gp_i),
                "games_started": _int(tds, gs_i),
                "goals": _int(tds, g_i),
                "assists": _int(tds, a_i),
                "points": _int(tds, pts_i),
                "shots": _int(tds, sh_i),
                "shots_on_goal": _int(tds, sog_i),
                "ground_balls": _int(tds, gb_i),
                "turnovers": _int(tds, to_i),
                "caused_turnovers": _int(tds, ct_i),
                "faceoffs_won": fo_won,
                "faceoffs_lost": fo_lost,
                "saves": _int(tds, sv_i) if sv_i is not None else None,
                "goals_allowed": _int(tds, ga_i) if ga_i is not None else None,
                "minutes_played": _float(tds, min_i) if min_i is not None else None,
            }
            all_results.append((player, totals))

    return all_results


def _upsert_season_totals(player: Player, totals: dict) -> None:
    st, _ = SeasonTotals.objects.get_or_create(player=player)
    for field, value in totals.items():
        if value is not None and hasattr(st, field):
            setattr(st, field, value)
    st.save()


# ---------------------------------------------------------------------------
# Test mode – try one team and dump what we find
# ---------------------------------------------------------------------------

def test_single_team(org_id: int) -> None:
    """Fetch one team page, dump all discovered links and tables for debugging."""
    browser = BrowserSession()
    try:
        team_url = f"{BASE_URL}/teams/{org_id}"
        logger.info("Fetching team page: %s", team_url)
        team_html = browser.fetch(team_url)
        if not team_html:
            logger.error("Could not load team page (Akamai block or invalid org_id?)")
            return

        logger.info("Team page loaded (%d chars)", len(team_html))
        _dump_page_links(team_html, team_url)

        # Show all tables on the page
        soup = BeautifulSoup(team_html, "lxml")
        tables = soup.find_all("table")
        logger.info("Tables on team page: %d", len(tables))
        for i, tbl in enumerate(tables):
            first_row = tbl.find("tr")
            if first_row:
                hdrs = [th.get_text(strip=True) for th in first_row.find_all("th")]
                logger.info("  Table %d headers: %s", i, hdrs)

        # Try to discover and follow stats link
        stats_url = _discover_stats_url(team_html, org_id)
        if stats_url:
            logger.info("Following stats link: %s", stats_url)
            time.sleep(REQUEST_DELAY)
            stats_html = browser.fetch(stats_url)
            if stats_html:
                logger.info("Stats page loaded (%d chars)", len(stats_html))
                rsoup = BeautifulSoup(stats_html, "lxml")
                rtables = rsoup.find_all("table")
                logger.info("Tables on stats page: %d", len(rtables))
                for i, tbl in enumerate(rtables):
                    first_row = tbl.find("tr")
                    if first_row:
                        hdrs = [th.get_text(strip=True) for th in first_row.find_all("th")]
                        logger.info("  Stats table %d headers: %s", i, hdrs)
                    rows = tbl.find_all("tr")
                    if len(rows) > 1:
                        sample_tds = rows[1].find_all("td")
                        sample = [td.get_text(strip=True) for td in sample_tds[:12]]
                        logger.info("  Stats table %d sample row: %s", i, sample)
            else:
                logger.warning("Stats page returned no content")
        else:
            logger.warning("No stats link found on team page. Trying fallbacks...")
            for pattern in [
                f"{BASE_URL}/teams/{org_id}/season_to_date_stats",
                f"{BASE_URL}/teams/{org_id}/roster",
            ]:
                logger.info("  Trying: %s", pattern)
                time.sleep(REQUEST_DELAY)
                html = browser.fetch(pattern)
                if html:
                    logger.info("  Got content (%d chars)", len(html))
                    fsoup = BeautifulSoup(html, "lxml")
                    ftables = fsoup.find_all("table")
                    logger.info("  Tables: %d", len(ftables))
                    for i, tbl in enumerate(ftables):
                        first_row = tbl.find("tr")
                        if first_row:
                            hdrs = [th.get_text(strip=True) for th in first_row.find_all("th")]
                            logger.info("    Table %d headers: %s", i, hdrs)
                    break
                else:
                    logger.info("  No content returned")
    finally:
        browser.close()


# ---------------------------------------------------------------------------
# Main scrape loop
# ---------------------------------------------------------------------------

def scrape_player_stats(seasons_csv: Path, years: list[int] | None) -> None:
    done = _load_progress()
    browser = BrowserSession()
    try:
        teams = list(_iter_team_seasons(seasons_csv, years))
        total = len(teams)
        success = 0
        skipped = 0
        failed = 0

        for idx, (year, div, org_id, team_name) in enumerate(teams, 1):
            k = _key(year, div, org_id)
            if done.get(k):
                skipped += 1
                continue

            logger.info("[%d/%d] Scraping roster for %s D%s %s (org %s)", idx, total, year, div, team_name, org_id)
            html = _fetch_team_stats_html(browser, org_id, year)
            if not html:
                logger.warning("  No HTML returned; skipping.")
                done[k] = False
                _save_progress(done)
                failed += 1
                continue

            pairs = _parse_roster_stats(year, div, org_id, team_name, html)
            for player, totals in pairs:
                _upsert_season_totals(player, totals)
            logger.info("  Saved %d players for %s", len(pairs), team_name)

            done[k] = True
            _save_progress(done)
            success += 1

            if idx < total:
                time.sleep(REQUEST_DELAY)

        logger.info("Summary: %d succeeded, %d failed, %d skipped (already done)", success, failed, skipped)
    finally:
        browser.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_years(raw: str) -> list[int]:
    if "-" in raw:
        lo, hi = raw.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(y) for y in raw.replace(",", " ").split()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape player stats into Django DB.")
    parser.add_argument("--seasons-csv", type=str, default=str(SEASONS_CSV))
    parser.add_argument("--years", type=str, default="2021-2026",
                        help="Years to scrape, e.g. '2024' or '2021-2026'.")
    parser.add_argument("--test", type=int, default=None, metavar="ORG_ID",
                        help="Test mode: fetch one team by org_id and dump links/tables found.")
    parser.add_argument("--reset", action="store_true",
                        help="Clear the checkpoint progress file and exit.")
    args = parser.parse_args()

    if args.reset:
        if PROGRESS_PATH.exists():
            PROGRESS_PATH.unlink()
            logger.info("Cleared progress file: %s", PROGRESS_PATH)
        else:
            logger.info("No progress file to clear.")
        return

    if args.test is not None:
        test_single_team(args.test)
        return

    years = _parse_years(args.years)
    logger.info("Target years: %s", years)
    scrape_player_stats(Path(args.seasons_csv), years)
    logger.info("Done.")


if __name__ == "__main__":
    main()

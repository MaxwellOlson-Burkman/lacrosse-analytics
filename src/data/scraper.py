"""NCAA stats.ncaa.org scraper with ranking_period discovery and 403 bypass."""

import logging
import re
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Referer": "https://stats.ncaa.org/",
}

# Candidate ranking_periods to probe, highest first.
# NCAA season length varies year to year — final periods can range from ~19 to ~35.
# We probe high-to-low so we find the final (most complete) period.
RANKING_PERIOD_CANDIDATES = [35, 34, 33, 32, 31, 30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19, 18]

# Longer pause between discovery probes to avoid triggering rate limits
DISCOVERY_PROBE_DELAY = 3

# Cache: (year, division) -> discovered final ranking_period
_period_cache: dict[tuple[int, int], int] = {}


def _create_session(
    user_agent: str,
    max_retries: int = 3,
) -> requests.Session:
    session = requests.Session()
    session.headers.update(BASE_HEADERS)
    session.headers["User-Agent"] = user_agent
    retries = Retry(
        total=max_retries,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _fetch_with_curl_cffi(url: str, timeout: int = 30) -> tuple[str | None, int | None]:
    try:
        from curl_cffi import requests as curl_requests

        resp = curl_requests.get(url, timeout=timeout, impersonate="chrome120")
        if resp.status_code == 200:
            return (resp.text, 200)
        return (None, resp.status_code)
    except ImportError:
        return (None, None)
    except Exception as e:
        logger.debug("curl_cffi failed: %s", e)
        return (None, None)


def _fetch_with_cloudscraper(url: str, timeout: int = 30) -> str | None:
    try:
        import cloudscraper

        scraper = cloudscraper.create_scraper()
        resp = scraper.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
    except ImportError:
        pass
    except Exception as e:
        logger.debug("cloudscraper failed: %s", e)
    return None


def _fetch_with_playwright(url: str, timeout: int = 30000) -> str | None:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            content = page.content() if resp and resp.ok else None
            browser.close()
            if content is not None:
                return content
    except ImportError:
        pass
    except Exception as e:
        logger.debug("Playwright failed: %s", e)
    return None


def _url_with_float_params(params: dict) -> str:
    float_params = {k: float(v) if isinstance(v, (int, float)) else v for k, v in params.items()}
    return urlencode(float_params)


def _has_ranking_table(html: str) -> bool:
    """Quick check that the page contains actual team ranking data, not an error page."""
    return "/teams/" in html and "<table" in html.lower()


def _count_teams(html: str) -> int:
    """Count team links in a rankings page to gauge completeness."""
    return len(re.findall(r"/teams/\d+", html))


def _parse_ranking_period_dropdown(html: str) -> tuple[int | None, int | None]:
    """Parse the ranking period <select> dropdown from an NCAA stats page.

    Returns (final_period, latest_period) where:
    - final_period: the period labeled "Final Statistics", or None if season is in progress
    - latest_period: the first (most recent) period in the dropdown, or None
    """
    select_match = re.search(
        r'<select[^>]*name="rp"[^>]*>(.*?)</select>',
        html, re.DOTALL | re.IGNORECASE,
    )
    if not select_match:
        return None, None

    select_html = select_match.group(1)

    final_match = re.search(
        r'<option\s+value="([\d.]+)"[^>]*>[^<]*Final Statistics',
        select_html, re.IGNORECASE,
    )
    final_period = int(float(final_match.group(1))) if final_match else None

    first_match = re.search(r'<option\s+value="([\d.]+)"', select_html)
    latest_period = int(float(first_match.group(1))) if first_match else None

    return final_period, latest_period


def fetch_page(
    base_url: str,
    path: str,
    params: dict,
    session: requests.Session | None = None,
) -> str | None:
    """Fetch a page using layered 403/404 bypass.

    Returns HTML string on success, None if all methods fail.
    """
    url = urljoin(base_url, path)
    urls_to_try = list(dict.fromkeys([
        f"{url}?{_url_with_float_params(params)}",
        f"{url}?{urlencode(params)}",
    ]))

    for full_url in urls_to_try:
        html, status = _fetch_with_curl_cffi(full_url)
        if html and _has_ranking_table(html):
            return html

        if status == 404:
            logger.debug("curl_cffi got 404 for %s; page does not exist", full_url)
            continue

        html = _fetch_with_cloudscraper(full_url)
        if html and _has_ranking_table(html):
            return html

        html = _fetch_with_playwright(full_url)
        if html and _has_ranking_table(html):
            return html

    # Last resort: requests with rotating User-Agents
    for i, ua in enumerate(USER_AGENTS):
        try:
            sess = _create_session(ua)
            resp = sess.get(urls_to_try[0], timeout=30)
            if resp.status_code == 200 and _has_ranking_table(resp.text):
                return resp.text
            if resp.status_code == 403:
                logger.debug("403 with UA #%s", i + 1)
            if i < len(USER_AGENTS) - 1:
                time.sleep(2)
        except Exception:
            if i < len(USER_AGENTS) - 1:
                time.sleep(2)

    return None


def discover_final_ranking_period(
    base_url: str,
    rankings_path: str,
    academic_year: int,
    division: int,
    sport_code: str,
    stat_seq: int,
    default_period: int,
    session: requests.Session | None = None,
) -> int:
    """Find the correct final ranking_period for a year/division.

    Primary strategy: fetch one page and parse the ranking-period <select>
    dropdown for the option labeled "Final Statistics".  Every stats.ncaa.org
    page includes this dropdown with every available period for the sport/year,
    so a single request is enough.

    Fallback: if no "Final Statistics" label exists (e.g. an in-progress
    season), use the most recent (first) period in the dropdown.

    Results are cached per (year, division).
    """
    cache_key = (academic_year, division)
    if cache_key in _period_cache:
        return _period_cache[cache_key]

    # We need ANY valid page to read the ranking-period dropdown.
    # Older years (pre-2015) use low period numbers (6-21), newer years use
    # higher numbers (26-130+).  Try the default first, then a spread of
    # fallbacks so we hit at least one valid period.
    probe_periods = [default_period] + [p for p in [21, 15, 10, 50, 100] if p != default_period]

    html = None
    for probe in probe_periods:
        params = {
            "academic_year": academic_year,
            "division": division,
            "ranking_period": probe,
            "sport_code": sport_code,
            "stat_seq": stat_seq,
        }
        html = fetch_page(base_url=base_url, path=rankings_path, params=params, session=session)
        if html:
            break
        time.sleep(DISCOVERY_PROBE_DELAY)

    if html:
        final_period, latest_period = _parse_ranking_period_dropdown(html)

        if final_period is not None:
            logger.info(
                "Found 'Final Statistics' at ranking_period=%d for %s D%s",
                final_period, academic_year, division,
            )
            _period_cache[cache_key] = final_period
            return final_period

        if latest_period is not None:
            logger.info(
                "No 'Final Statistics' for %s D%s (season in progress?); "
                "using latest period=%d",
                academic_year, division, latest_period,
            )
            _period_cache[cache_key] = latest_period
            return latest_period

    logger.warning(
        "Could not discover ranking_period for %s D%s; using default %d",
        academic_year, division, default_period,
    )
    _period_cache[cache_key] = default_period
    return default_period


def scrape_season(
    config: dict,
    year: int,
    division: int,
    existing_raw_paths: set[Path],
    raw_dir: Path,
) -> list[Path]:
    """Scrape all team stat pages for a season/division from stats.ncaa.org.

    Key improvements over earlier version:
    - Auto-discovers the correct final ranking_period per year/division.
    - Tries stats.ncaa.org FIRST for every stat, even if archive data exists.
    - Does NOT bail to archive on a single stat failure; skips that stat and continues.
    - Only falls back to archive when stats.ncaa.org is completely inaccessible.
    """
    scraping = config["scraping"]
    team_stats = config["team_stats"]
    output = config["output"]

    base_url = scraping["base_url"]
    rankings_path = scraping["rankings_path"]
    default_period = scraping["ranking_period"]
    sport_code = scraping["sport_code"]
    delay = scraping["request_delay_seconds"]

    session = _create_session(USER_AGENTS[0], scraping.get("max_retries", 3))

    # Visit landing page to establish cookies/session
    try:
        session.get(base_url, timeout=10)
        time.sleep(1)
    except requests.RequestException:
        pass

    season_dir = raw_dir / output["raw_format"].format(year=year, division=division)
    season_dir.mkdir(parents=True, exist_ok=True)

    # Check if all stat files already exist (skip discovery + scraping entirely)
    all_exist = all(
        (season_dir / f"{s['name']}.html") in existing_raw_paths
        for s in team_stats
    )
    if all_exist:
        logger.info("All stats already on disk for %s D%s; skipping.", year, division)
        return [season_dir / f"{s['name']}.html" for s in team_stats]

    # Check how many files we're actually missing
    missing_stats = [s for s in team_stats if (season_dir / f"{s['name']}.html") not in existing_raw_paths]
    have_stats = [s for s in team_stats if (season_dir / f"{s['name']}.html") in existing_raw_paths]
    if have_stats:
        logger.info(
            "%s D%s: %d stats on disk, %d to fetch",
            year, division, len(have_stats), len(missing_stats),
        )

    # Discover the correct final ranking_period for this year/division
    probe_stat = team_stats[0]["stat_seq"]
    final_period = discover_final_ranking_period(
        base_url=base_url,
        rankings_path=rankings_path,
        academic_year=year,
        division=division,
        sport_code=sport_code,
        stat_seq=probe_stat,
        default_period=default_period,
        session=session,
    )
    logger.info("Using ranking_period=%d for %s D%s", final_period, year, division)

    saved: list[Path] = []
    failed: list[str] = []
    expected_teams = 0

    for stat in team_stats:
        stat_seq = stat["stat_seq"]
        stat_name = stat["name"]
        out_path = season_dir / f"{stat_name}.html"

        if out_path in existing_raw_paths:
            logger.info("Skipping existing: %s", out_path)
            saved.append(out_path)
            continue

        params = {
            "academic_year": year,
            "division": division,
            "ranking_period": final_period,
            "sport_code": sport_code,
            "stat_seq": stat_seq,
        }
        html = fetch_page(base_url=base_url, path=rankings_path, params=params, session=session)

        if html and _has_ranking_table(html):
            count = _count_teams(html)
            expected_teams = max(expected_teams, count)

            # If this page has <50% of expected teams, the ranking_period may
            # be wrong for this specific stat. Try higher periods.
            if expected_teams > 0 and count < expected_teams * 0.5:
                logger.info(
                    "%s has %d teams (expected ~%d); probing higher periods...",
                    stat_name, count, expected_teams,
                )
                best_html = html
                best_count = count
                for alt_period in RANKING_PERIOD_CANDIDATES:
                    if alt_period <= final_period:
                        continue
                    alt_params = {**params, "ranking_period": alt_period}
                    alt_html = fetch_page(
                        base_url=base_url, path=rankings_path, params=alt_params, session=session,
                    )
                    if alt_html and _has_ranking_table(alt_html):
                        alt_count = _count_teams(alt_html)
                        if alt_count > best_count:
                            best_count = alt_count
                            best_html = alt_html
                            logger.info(
                                "ranking_period=%d gives %d teams for %s",
                                alt_period, alt_count, stat_name,
                            )
                        if alt_count >= expected_teams * 0.8:
                            break
                    time.sleep(DISCOVERY_PROBE_DELAY)
                html = best_html

            out_path.write_text(html, encoding="utf-8")
            saved.append(out_path)
            logger.info("Saved: %s (%d teams)", out_path, _count_teams(html))
        else:
            failed.append(stat_name)
            logger.warning(
                "Could not fetch %s D%s %s (all methods failed); skipping stat.",
                year, division, stat_name,
            )

        time.sleep(delay)

    if failed:
        logger.warning(
            "Missing %d stat(s) for %s D%s: %s",
            len(failed), year, division, failed,
        )

    # If stats.ncaa.org returned NOTHING at all, try archive as last resort
    if not saved and scraping.get("use_archive_fallback", False):
        logger.info(
            "stats.ncaa.org returned no data for %s D%s; trying archive fallback...",
            year, division,
        )
        from .archive_scraper import scrape_archive_season

        archive_saved = scrape_archive_season(
            config=config,
            year=year,
            division=division,
            existing_raw_paths=existing_raw_paths,
            raw_dir=raw_dir,
            delay=delay,
        )
        if archive_saved:
            logger.info("Using web1 archive for %s D%s (stats.ncaa.org unavailable)", year, division)
            return archive_saved

    return saved

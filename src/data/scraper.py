"""NCAA stats.org scraper with rate limiting, 403 bypass, and graceful error handling."""

import logging
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Rotate through these User-Agents on 403 (different browsers/versions)
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


def _create_session(
    user_agent: str,
    max_retries: int = 3,
) -> requests.Session:
    """Create a requests session with retry logic (excludes 403 from auto-retry)."""
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
    """Try curl_cffi (mimics browser TLS fingerprint). Returns (html, status_code) or (None, status)."""
    try:
        from curl_cffi import requests as curl_requests

        resp = curl_requests.get(url, timeout=timeout, impersonate="chrome120")
        if resp.status_code == 200:
            return (resp.text, 200)
        return (None, resp.status_code)
    except ImportError:
        logger.debug("curl_cffi not installed, skipping")
        return (None, None)
    except Exception as e:
        logger.warning("curl_cffi failed: %s", e)
        return (None, None)


def _fetch_with_cloudscraper(url: str, timeout: int = 30) -> str | None:
    """Try cloudscraper (bypasses Cloudflare). Returns HTML or None on failure."""
    try:
        import cloudscraper

        scraper = cloudscraper.create_scraper()
        resp = scraper.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
        logger.warning("cloudscraper returned %s for %s", resp.status_code, url[:80])
    except ImportError:
        logger.debug("cloudscraper not installed, skipping")
    except Exception as e:
        logger.warning("cloudscraper failed: %s", e)
    return None


def _fetch_with_playwright(url: str, timeout: int = 30000) -> str | None:
    """Use real headless browser (Playwright). Bypasses virtually all anti-bot protection."""
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
            logger.warning("Playwright returned %s for %s", resp.status if resp else "None", url[:80])
    except ImportError:
        logger.debug("Playwright not installed; run: pip install playwright && playwright install chromium")
    except Exception as e:
        logger.warning("Playwright failed: %s", e)
    return None


def _fetch_with_requests(
    url: str,
    session: requests.Session,
    timeout: int = 30,
) -> str:
    """Fetch with requests; raises on non-2xx."""
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _url_with_float_params(params: dict) -> str:
    """NCAA sometimes expects float params (e.g. academic_year=2014.0)."""
    float_params = {k: float(v) if isinstance(v, (int, float)) else v for k, v in params.items()}
    return urlencode(float_params)


def fetch_page(
    base_url: str,
    path: str,
    params: dict,
    session: requests.Session | None = None,
) -> str:
    """Fetch a page with 403 bypass: curl_cffi -> cloudscraper -> Playwright -> requests with UA rotation.

    Raises:
        requests.HTTPError: If all methods fail with 403 or other error.
    """
    url = urljoin(base_url, path)
    urls_to_try = [
        f"{url}?{urlencode(params)}",
        f"{url}?{_url_with_float_params(params)}",  # NCAA uses academic_year=2014.0
    ]
    urls_to_try = list(dict.fromkeys(urls_to_try))  # dedupe

    def _try_all_methods(u: str) -> str | None:
        html, status = _fetch_with_curl_cffi(u)
        if html is not None:
            return html
        if status != 404:
            html = _fetch_with_cloudscraper(u)
            if html is not None:
                return html
        return _fetch_with_playwright(u)

    for full_url in urls_to_try:
        html = _try_all_methods(full_url)
        if html is not None:
            return html

    # 4. Fallback: requests with rotating User-Agents (create fresh session per UA)
    last_error: Exception | None = None
    for i, ua in enumerate(USER_AGENTS):
        try:
            sess = _create_session(ua)
            html = _fetch_with_requests(full_url, sess)
            return html
        except requests.HTTPError as e:
            last_error = e
            if e.response is not None and e.response.status_code == 403:
                logger.warning("403 with UA #%s, trying next in 2s...", i + 1)
            if i < len(USER_AGENTS) - 1:
                time.sleep(2)
                continue
            raise
        except Exception as e:
            last_error = e
            logger.warning("Request failed with UA #%s: %s", i + 1, e)
            if i < len(USER_AGENTS) - 1:
                time.sleep(2)
                continue
            raise

    if last_error is not None:
        raise last_error
    raise requests.HTTPError(f"Failed to fetch {full_url}")


def scrape_team_stats_page(
    base_url: str,
    rankings_path: str,
    academic_year: int,
    division: int,
    stat_seq: int,
    ranking_period: int,
    sport_code: str,
    session: requests.Session | None,
) -> str:
    """Fetch a single team stats ranking page."""
    params = {
        "academic_year": academic_year,
        "division": division,
        "ranking_period": ranking_period,
        "sport_code": sport_code,
        "stat_seq": stat_seq,
    }
    return fetch_page(base_url=base_url, path=rankings_path, params=params, session=session)


def scrape_season(
    config: dict,
    year: int,
    division: int,
    existing_raw_paths: set[Path],
    raw_dir: Path,
) -> list[Path]:
    """Scrape all team stat pages for a season/division.

    Skips pages that already exist. On 403, retries with cloudscraper and UA rotation.
    If skip_failed is True in config, continues on failure; otherwise raises.
    """
    scraping = config["scraping"]
    team_stats = config["team_stats"]
    output = config["output"]

    base_url = scraping["base_url"]
    rankings_path = scraping["rankings_path"]
    ranking_period = scraping["ranking_period"]
    sport_code = scraping["sport_code"]
    delay = scraping["request_delay_seconds"]
    skip_failed = scraping.get("skip_failed", False)

    session = _create_session(USER_AGENTS[0], scraping.get("max_retries", 3))

    # Visit landing page to establish session
    try:
        session.get(base_url, timeout=10)
        time.sleep(1)
    except requests.RequestException:
        pass

    season_dir = raw_dir / output["raw_format"].format(year=year, division=division)
    season_dir.mkdir(parents=True, exist_ok=True)

    # Fast path: if archive fallback data already exists, skip expensive stats.ncaa.org attempts.
    archive_listschools = season_dir / "archive_listschools.html"
    archive_org_files = list(season_dir.glob("orgsummary_*.html"))
    if archive_listschools.exists() and archive_org_files:
        logger.info(
            "Skipping stats scrape for %s D%s (archive fallback already present: %s teams)",
            year,
            division,
            len(archive_org_files),
        )
        return [archive_listschools, *archive_org_files]

    saved: list[Path] = []
    failed: list[str] = []

    def _has_any_season_html() -> bool:
        # If at least one stat page exists for this season/division, we can continue
        # with partial data instead of aborting an entire long-running job.
        return any(season_dir.glob("*.html"))

    for stat in team_stats:
        stat_seq = stat["stat_seq"]
        stat_name = stat["name"]
        out_path = season_dir / f"{stat_name}.html"

        if out_path in existing_raw_paths:
            logger.info("Skipping existing: %s", out_path)
            continue

        try:
            html = scrape_team_stats_page(
                base_url=base_url,
                rankings_path=rankings_path,
                academic_year=year,
                division=division,
                stat_seq=stat_seq,
                ranking_period=ranking_period,
                sport_code=sport_code,
                session=session,
            )
            out_path.write_text(html, encoding="utf-8")
            saved.append(out_path)
            logger.info("Saved: %s", out_path)
        except requests.RequestException as e:
            failed.append((stat_name, e))
            if skip_failed:
                logger.warning("Failed %s/%s (skipping): %s", year, stat_name, e)
            else:
                logger.error("Failed to fetch %s/%s: %s", year, stat_name, e)
                # Try archive immediately on first failure (avoid 15 slow 403s)
                if scraping.get("use_archive_fallback", False):
                    from .archive_scraper import scrape_archive_season

                    logger.info(
                        "Switching to archive fallback for %s D%s after failure on stat '%s'",
                        year,
                        division,
                        stat_name,
                    )
                    archive_saved = scrape_archive_season(
                        config=config,
                        year=year,
                        division=division,
                        existing_raw_paths=existing_raw_paths,
                        raw_dir=raw_dir,
                        delay=delay,
                    )
                    if archive_saved:
                        logger.info(
                            "Using web1 archive for %s D%s (stats.ncaa.org returned 403)",
                            year,
                            division,
                        )
                        return saved + archive_saved
                    # Archive failed too. If we already have some season files, continue
                    # and keep only this stat missing instead of crashing the full run.
                    if _has_any_season_html():
                        logger.warning(
                            "Archive fallback unavailable for %s D%s after '%s' failure; "
                            "continuing with partial season data.",
                            year,
                            division,
                            stat_name,
                        )
                        continue
                    # No usable season data exists, re-raise.
                    raise e

        time.sleep(delay)

    if failed and not skip_failed:
        if _has_any_season_html():
            stat_names = [s[0] for s in failed]
            logger.warning(
                "Proceeding with partial scrape for %s D%s; missing stat(s): %s",
                year,
                division,
                stat_names,
            )
            return saved

        # Try archive fallback before giving up
        use_archive = scraping.get("use_archive_fallback", False)
        if use_archive:
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
                saved.extend(archive_saved)
                failed.clear()
            else:
                _, first_err = failed[0]
                raise first_err
        else:
            _, first_err = failed[0]
            raise first_err

    if failed and skip_failed:
        stat_names = [s[0] for s in failed]
        logger.warning("Skipped %s stat(s) for %s D%s: %s", len(failed), year, division, stat_names)

    return saved

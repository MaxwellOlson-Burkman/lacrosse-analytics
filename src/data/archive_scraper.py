"""Web1 ranksummary scraper fallback.

When stats.ncaa.org/rankings blocks automated traffic (403), this module uses
https://web1.ncaa.org/stats/StatsSrv/ranksummary with a cookie-backed session:
1) GET ranksummary index (contains rankSeq IDs by year/division)
2) POST doWhat=listSchools for selected year/division
3) POST doWhat=orgSummary for each orgId to get per-team stat values
"""

import logging
import re
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

RANKSUMMARY_URL = "https://web1.ncaa.org/stats/StatsSrv/ranksummary"
RANKSUMMARY_INDEX_URL = "https://web1.ncaa.org/stats/StatsSrv/ranksummary?sportCode=MLA"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://web1.ncaa.org/",
}


def _request_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    max_attempts: int,
    timeout: int,
    **kwargs,
) -> requests.Response:
    """HTTP request with retry/backoff for transient failures."""
    last_err: Exception | None = None
    backoff = 2.0
    for attempt in range(1, max_attempts + 1):
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = requests.HTTPError(f"HTTP {resp.status_code} for {url}")
            else:
                return resp
        except requests.RequestException as e:
            last_err = e

        if attempt < max_attempts:
            logger.warning(
                "Archive request retry %s/%s for %s %s (sleep %.1fs): %s",
                attempt,
                max_attempts,
                method,
                url[:90],
                backoff,
                last_err,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, 20.0)

    if isinstance(last_err, requests.RequestException):
        raise last_err
    raise requests.RequestException(f"Request failed after retries: {method} {url}")


def _season_label(academic_year: int) -> str:
    """Convert 2014 -> 2013-14 season label used in ranksummary UI."""
    start_year = academic_year - 1
    return f"{start_year}-{str(academic_year)[-2:]}"


def _extract_rankseq(index_html: str, academic_year: int, division: int) -> str | None:
    """Extract the LAST (final-season) rankSeq for season/division.

    The web1 ranksummary page lists multiple ranking snapshots per season;
    the last one corresponds to end-of-season / final statistics.
    """
    def _find_all_in_window(window: str) -> list[str]:
        return re.findall(rf"loadDivision\((\d+),\s*{division}\)", window)

    # Most reliable anchor is the division/year HTML marker (e.g., D2_2014)
    marker_pattern = rf"D{division}_{academic_year}"
    marker_match = re.search(marker_pattern, index_html)
    if marker_match:
        window = index_html[marker_match.start() : marker_match.start() + 30000]
        matches = _find_all_in_window(window)
        if matches:
            return matches[-1]

    season = _season_label(academic_year)
    season_idx = index_html.find(season)
    if season_idx != -1:
        window = index_html[season_idx : season_idx + 30000]
        matches = _find_all_in_window(window)
        if matches:
            return matches[-1]

    # Last resort: global search
    all_matches = re.findall(
        rf"(?:{academic_year}|{re.escape(season)}).*?loadDivision\((\d+),\s*{division}\)",
        index_html,
        re.DOTALL,
    )
    if all_matches:
        return all_matches[-1]
    return None


def _extract_org_ids(list_schools_html: str) -> list[int]:
    """Extract org IDs from showTeam(orgId) links."""
    ids = {int(x) for x in re.findall(r"showTeam\((\d+)\)", list_schools_html)}
    return sorted(ids)


def scrape_archive_season(
    config: dict,
    year: int,
    division: int,
    existing_raw_paths: set[Path],
    raw_dir: Path,
    delay: float = 1.5,
) -> list[Path]:
    """Scrape web1 ranksummary orgSummary pages for a season/division.

    Saves:
      - archive_index.html
      - archive_listschools.html
      - orgsummary_<org_id>.html
    """
    output = config["output"]
    raw_format = output["raw_format"]
    season_dir = raw_dir / raw_format.format(year=year, division=division)
    season_dir.mkdir(parents=True, exist_ok=True)

    listschools_path = season_dir / "archive_listschools.html"
    max_attempts = max(2, int(config["scraping"].get("max_retries", 3)) + 1)
    progress_every = int(config["scraping"].get("archive_progress_every", 10))
    max_runtime_minutes = float(config["scraping"].get("archive_max_runtime_minutes", 20))
    deadline = time.monotonic() + (max_runtime_minutes * 60.0)

    session = requests.Session()
    session.headers.update(HEADERS)

    # 1) Load index (must happen first to establish cookies/session context)
    idx_resp = _request_with_retries(
        session=session,
        method="GET",
        url=RANKSUMMARY_INDEX_URL,
        max_attempts=max_attempts,
        timeout=30,
    )
    idx_resp.raise_for_status()
    index_html = idx_resp.text
    (season_dir / "archive_index.html").write_text(index_html, encoding="utf-8")

    rank_seq = _extract_rankseq(index_html, academic_year=year, division=division)
    if rank_seq is None:
        logger.warning("No rankSeq found for %s D%s on web1 ranksummary", year, division)
        return []

    # 2) List schools for this rankSeq/division
    list_payload = {
        "division": str(division),
        "sportCode": "MLA",
        "academicYear": str(year),
        "doWhat": "listSchools",
        "rankSeq": rank_seq,
    }
    list_resp = _request_with_retries(
        session=session,
        method="POST",
        url=RANKSUMMARY_URL,
        data=list_payload,
        max_attempts=max_attempts,
        timeout=30,
    )
    list_resp.raise_for_status()
    list_html = list_resp.text
    listschools_path.write_text(list_html, encoding="utf-8")
    time.sleep(delay)

    org_ids = _extract_org_ids(list_html)
    if not org_ids:
        logger.warning("No org IDs found in listSchools for %s D%s", year, division)
        return [listschools_path]

    saved: list[Path] = [listschools_path]
    total = len(org_ids)
    logger.info(
        "Archive fallback started for %s D%s: %s teams, max runtime %.1f min",
        year,
        division,
        total,
        max_runtime_minutes,
    )

    # 3) Fetch per-team orgSummary pages (contains rank/value for each stat)
    for idx, org_id in enumerate(org_ids, start=1):
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Archive fallback timeout for {year} D{division} after {max_runtime_minutes:.1f} min "
                f"(processed {idx-1}/{total} teams)"
            )

        out_path = season_dir / f"orgsummary_{org_id}.html"
        if out_path.exists():
            saved.append(out_path)
            if idx % progress_every == 0 or idx == total:
                logger.info("Archive progress %s D%s: %s/%s teams", year, division, idx, total)
            continue

        payload = {
            "division": str(division),
            "sportCode": "MLA",
            "academicYear": str(year),
            "doWhat": "orgSummary",
            "rankSeq": rank_seq,
            "orgId": str(org_id),
        }
        resp = _request_with_retries(
            session=session,
            method="POST",
            url=RANKSUMMARY_URL,
            data=payload,
            max_attempts=max_attempts,
            timeout=30,
        )
        resp.raise_for_status()
        out_path.write_text(resp.text, encoding="utf-8")
        saved.append(out_path)
        if idx % progress_every == 0 or idx == total:
            logger.info("Archive progress %s D%s: %s/%s teams", year, division, idx, total)
        time.sleep(delay)

    logger.info(
        "Saved web1 archive ranksummary for %s D%s (%s teams)",
        year,
        division,
        len(org_ids),
    )
    return saved

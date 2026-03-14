"""Parse web1 ranksummary orgSummary pages into structured records."""

import re
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup


def _parse_team_header(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Extract team name and record from orgSummary header."""
    header_cell = soup.select_one("tr.schoolheading td")
    text = header_cell.get_text(" ", strip=True) if header_cell else soup.get_text(" ", strip=True)
    match = re.search(
        r"([A-Za-z0-9\.\- '&]+)\s+\((\d+-\d+)\)\s+Men's Lacrosse National Ranking Summary",
        text,
    )
    if not match:
        return None, None
    return match.group(1).strip(), match.group(2).strip()


def _parse_org_id_from_path(path: Path) -> int | None:
    match = re.search(r"orgsummary_(\d+)\.html$", path.name)
    return int(match.group(1)) if match else None


def parse_orgsummary_file(path: Path, academic_year: int, division: int, stat_seq_to_name: dict[int, str]) -> dict:
    """Parse one orgSummary HTML into a wide feature row for that team."""
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")
    team_name, record = _parse_team_header(soup)
    org_id = _parse_org_id_from_path(path)

    if not team_name:
        return {}

    row: dict = {
        "academic_year": academic_year,
        "division": division,
        "team_name": team_name,
        "org_id": org_id,
        "record": record,
        "conference": None,
    }

    # Team stats table rows include links like javascript:showRankings(228, 21)
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        first_link = tds[0].find("a", href=True)
        if not first_link:
            continue

        href = first_link.get("href", "")
        seq_match = re.search(r"showRankings\((\d+),\s*\d+\)", href)
        if not seq_match:
            continue

        stat_seq = int(seq_match.group(1))
        stat_name = stat_seq_to_name.get(stat_seq)
        if not stat_name:
            # Skip stats not configured in data_config.yaml
            continue

        rank_text = tds[1].get_text(strip=True)
        value_text = tds[2].get_text(strip=True).replace(",", "")

        try:
            row[f"{stat_name}_rank"] = int(rank_text)
        except ValueError:
            row[f"{stat_name}_rank"] = None

        try:
            row[stat_name] = float(value_text)
        except ValueError:
            row[stat_name] = None

    return row


def load_and_parse_archive(
    raw_dir: Path,
    year: int,
    division: int,
    stat_configs: list[dict] | None = None,
) -> pd.DataFrame:
    """Load orgSummary fallback files for a season/division and build DataFrame."""
    raw_format = "{year}/division_{division}"
    season_dir = raw_dir / raw_format.format(year=year, division=division)
    if not season_dir.exists():
        return pd.DataFrame()

    org_files = sorted(season_dir.glob("orgsummary_*.html"))
    if not org_files:
        return pd.DataFrame()

    stat_seq_to_name: dict[int, str] = {}
    if stat_configs:
        stat_seq_to_name = {int(s["stat_seq"]): s["name"] for s in stat_configs}

    rows: list[dict] = []
    for f in org_files:
        row = parse_orgsummary_file(
            path=f,
            academic_year=year,
            division=division,
            stat_seq_to_name=stat_seq_to_name,
        )
        if row:
            rows.append(row)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)

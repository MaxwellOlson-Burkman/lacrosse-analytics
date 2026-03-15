"""Parse raw NCAA HTML into structured records."""

import re
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

# Regex to extract org_id from team link: /teams/571420
TEAM_LINK_PATTERN = re.compile(r"/teams/(\d+)")


def parse_team_ranking_table(html: str, stat_name: str) -> list[dict]:
    """Parse a team ranking HTML page into rows.

    NCAA tables vary by stat - common columns: Rank, Team, Games, W-L, value columns.
    We extract: rank, team_name, conference, org_id, and the primary stat value.
    """
    soup = BeautifulSoup(html, "lxml")
    rows = []

    # Find the main data table (NCAA uses standard table layout)
    table = soup.find("table")
    if not table:
        return rows

    headers: list[str] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            ths = tr.find_all("th")
            if ths:
                headers = [th.get_text(strip=True).lower().replace(" ", "_") for th in ths]
            continue

        row = {"stat": stat_name}

        for i, td in enumerate(cells):
            header = headers[i] if i < len(headers) else f"col_{i}"
            text = td.get_text(strip=True)

            # First column is usually rank
            if i == 0:
                try:
                    row["rank"] = int(text) if text and text != "-" else None
                except ValueError:
                    row["rank"] = None
                continue

            # Team column has link with org_id
            team_link = td.find("a", href=TEAM_LINK_PATTERN)
            if team_link:
                match = TEAM_LINK_PATTERN.search(team_link.get("href", ""))
                row["org_id"] = int(match.group(1)) if match else None
                full_team = team_link.get_text(strip=True)
                # Parse "Team Name (Conference)"
                if "(" in full_team and ")" in full_team:
                    paren = full_team.rfind("(")
                    row["team_name"] = full_team[:paren].strip()
                    row["conference"] = full_team[paren + 1 : -1].strip()
                else:
                    row["team_name"] = full_team
                    row["conference"] = None
                continue

            # Remaining columns - capture by header
            if header == "games":
                try:
                    row["games"] = int(text) if text else None
                except ValueError:
                    row["games"] = None
            elif header == "w-l":
                row["record"] = text
            elif header in ("per_game", "value", "pct", "pct."):
                try:
                    row["value"] = float(text) if text else None
                except ValueError:
                    row["value"] = text
            else:
                # NCAA always puts the ranking stat in the LAST numeric column.
                # Keep overwriting so we end up with the rightmost value.
                try:
                    row["value"] = float(text.replace(",", ""))
                except (ValueError, AttributeError):
                    pass

        if "team_name" in row and "org_id" in row:
            rows.append(row)

    return rows


def load_and_parse_raw_files(
    raw_dir: Path,
    year: int,
    division: int,
    stat_configs: list[dict],
    ) -> pd.DataFrame:
    """Load raw HTML files and parse into a combined DataFrame."""
    all_rows = []

    raw_format = "{year}/division_{division}"
    season_dir = raw_dir / raw_format.format(year=year, division=division)

    if not season_dir.exists():
        return pd.DataFrame()

    for stat in stat_configs:
        stat_name = stat["name"]
        html_path = season_dir / f"{stat_name}.html"
        if not html_path.exists():
            continue

        html = html_path.read_text(encoding="utf-8")
        rows = parse_team_ranking_table(html, stat_name)
        for r in rows:
            r["academic_year"] = year
            r["division"] = division
            all_rows.append(r)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)

    # Pivot on the minimal key to avoid mismatches when different stat pages
    # show different metadata (games, record) for the same team.
    pivot_index = ["academic_year", "division", "team_name", "org_id"]
    pivot_df = df.pivot_table(
        index=pivot_index,
        columns="stat",
        values="value",
        aggfunc="first",
    ).reset_index()

    # Attach metadata (conference, games, record) from whichever stat page
    # reported it, preferring non-null values.
    for meta_col in ("conference", "games", "record"):
        if meta_col not in df.columns:
            continue
        meta = (
            df.dropna(subset=[meta_col])
            .drop_duplicates(subset=pivot_index, keep="first")[pivot_index + [meta_col]]
        )
        pivot_df = pivot_df.merge(meta, on=pivot_index, how="left")

    return pivot_df

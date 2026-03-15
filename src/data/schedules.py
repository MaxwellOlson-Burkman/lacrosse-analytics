"""Game-level schedule schema and helpers for SOS computation.

This module defines the canonical *game table* structure that we will use
to build schedule-based Strength of Schedule (SOS) and RPI-style ratings.

The goal is to have one row per *team-game*, keyed by
    academic_year, division, team_org_id, opp_org_id, date
with enough information to:
  - Reconstruct team win percentage (WP) directly from this table.
  - Compute opponent win percentage (OppWP) and opponent's opponent win
    percentage (OppOppWP).
  - Join SOS metrics back onto the season-level table
    data/processed/team_stats_model_ready.csv using:
        (academic_year, division, org_id == team_org_id)

This file is intentionally focused on the data *shape* and basic helpers.
Actual scraping/parsing of HTML pages into this schema will live in a
separate script/module so it can evolve independently of the core model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class GameRecord:
    """One team-centric game record.

    Each physical game will typically appear twice in the full table:
    once from the perspective of each team. This duplication is fine,
    and in fact convenient, for computing WP / OppWP / OppOppWP.

    Attributes
    ----------
    academic_year:
        Season end year (e.g. 2024 for the 2023–24 season). Must match
        the values used in `team_stats_model_ready.csv`.
    division:
        NCAA division as an integer (1 or 2 in this project).
    team_org_id:
        NCAA org_id for the team *in whose perspective* this row is written.
        This corresponds to the `org_id` column in the season table.
    opp_org_id:
        NCAA org_id for the opponent, if known. If we cannot parse a team
        link from the schedule page, this may be None while `opp_name`
        still contains the textual opponent name.
    team_name:
        Team name string for `team_org_id`. Redundant with the season
        table but handy for inspection and reporting.
    opp_name:
        Opponent name as displayed on the schedule page.
    game_date:
        Python `date` object representing the calendar date of the game.
        When parsing, we should convert the site's date string into a
        `datetime.date`. If the date is truly unavailable, this may be None
        but a valid row should generally have a date.
    location:
        Encodes game location from the team's perspective.
        Suggested values:
            "H" = home
            "A" = away (e.g. opponent prefixed with '@')
            "N" = neutral site
        When parsing, we can either derive this from an '@' prefix or use
        an explicit location column if present.
    team_score:
        Goals scored by `team_org_id` in this game.
    opp_score:
        Goals scored by the opponent in this game.
    result:
        Result from the team's perspective. Suggested values:
            "W" = win
            "L" = loss
            "T" = tie (if any exist in historical data)
    goal_margin:
        Convenience field: team_score - opp_score. Positive for wins,
        negative for losses, zero for ties.
    """

    academic_year: int
    division: int
    team_org_id: int
    opp_org_id: int | None
    team_name: str
    opp_name: str
    game_date: date | None
    location: str | None
    team_score: int
    opp_score: int
    result: str
    goal_margin: int


GAME_COLUMNS: list[str] = [
    "academic_year",
    "division",
    "team_org_id",
    "opp_org_id",
    "team_name",
    "opp_name",
    "game_date",
    "location",
    "team_score",
    "opp_score",
    "result",
    "goal_margin",
]


def game_records_to_dataframe(records: Iterable[GameRecord]) -> pd.DataFrame:
    """Convert an iterable of GameRecord objects into a canonical DataFrame.

    The returned frame:
      - Has columns ordered as in GAME_COLUMNS.
      - Uses `datetime64[ns]` for game_date when non-null.
      - Is suitable as input for SOS/RPI computation.
    """
    rows = []
    for r in records:
        rows.append(
            {
                "academic_year": r.academic_year,
                "division": r.division,
                "team_org_id": r.team_org_id,
                "opp_org_id": r.opp_org_id,
                "team_name": r.team_name,
                "opp_name": r.opp_name,
                "game_date": r.game_date,
                "location": r.location,
                "team_score": r.team_score,
                "opp_score": r.opp_score,
                "result": r.result,
                "goal_margin": r.goal_margin,
            }
        )

    df = pd.DataFrame(rows, columns=GAME_COLUMNS)
    if "game_date" in df.columns and not df["game_date"].isna().all():
        df["game_date"] = pd.to_datetime(df["game_date"])
    return df


def link_games_to_seasons(
    games: pd.DataFrame,
    seasons: pd.DataFrame,
) -> pd.DataFrame:
    """Join per-team SOS-ready games onto the season table.

    This does *not* compute SOS by itself; it simply ensures the keys line up
    and can be used in analysis or validation notebooks. The expected linkage
    is:

        seasons.org_id <-> games.team_org_id
        seasons.academic_year == games.academic_year
        seasons.division == games.division

    Parameters
    ----------
    games:
        DataFrame following GAME_COLUMNS, typically produced by
        `game_records_to_dataframe` or a future scraping function.
    seasons:
        The season-level table, e.g. the DataFrame loaded from
        `data/processed/team_stats_model_ready.csv`.

    Returns
    -------
    merged:
        A DataFrame where each season row has been matched to its games via
        `(academic_year, division, org_id == team_org_id)`. Season rows with
        no matching games (or vice versa) can be detected and investigated.
    """
    key_games = games.copy()
    key_games = key_games.rename(columns={"team_org_id": "org_id"})

    merged = seasons.merge(
        key_games,
        on=["academic_year", "division", "org_id"],
        how="left",
        suffixes=("", "_game"),
    )
    return merged


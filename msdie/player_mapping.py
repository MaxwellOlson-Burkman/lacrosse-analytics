"""Player-season schema and mapping helpers for MSDIE."""

from __future__ import annotations

import re
from datetime import datetime, timezone

PLAYER_SEASON_COLUMNS = [
    "team_id",
    "team_name",
    "season",
    "division",
    "conference",
    "player_name",
    "jersey_number",
    "position",
    "class_year",
    "games_played",
    "games_started",
    "goals",
    "assists",
    "points",
    "shots",
    "shots_on_goal",
    "shot_pct",
    "ground_balls",
    "turnovers",
    "caused_turnovers",
    "faceoffs_won",
    "faceoffs_lost",
    "faceoff_pct",
    "man_up_goals",
    "man_down_goals",
    "saves",
    "goals_allowed",
    "save_pct",
    "minutes_played",
    "source_method",
    "source_url",
    "scraped_at_utc",
]

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "player_name": ("name", "player", "athlete"),
    "jersey_number": ("no", "no.", "#", "jersey", "number"),
    "position": ("pos", "position"),
    "class_year": ("cl", "class", "yr"),
    "games_played": ("gp", "games_played", "g"),
    "games_started": ("gs", "games_started"),
    "goals": ("g", "goals"),
    "assists": ("a", "assists"),
    "points": ("pts", "points", "p"),
    "shots": ("sh", "shots"),
    "shots_on_goal": ("sog", "shots_on_goal"),
    "shot_pct": ("sh_pct", "shot_pct", "shot_percentage"),
    "ground_balls": ("gb", "ground_balls"),
    "turnovers": ("to", "turnovers"),
    "caused_turnovers": ("ct", "caused_turnovers"),
    "faceoffs_won": ("fow", "fo_w", "faceoffs_won"),
    "faceoffs_lost": ("fol", "fo_l", "faceoffs_lost"),
    "faceoff_pct": ("fo_pct", "faceoff_pct"),
    "man_up_goals": ("up", "man_up_goals", "ppg"),
    "man_down_goals": ("dwn", "man_down_goals"),
    "saves": ("sv", "saves"),
    "goals_allowed": ("ga", "goals_allowed"),
    "save_pct": ("sv_pct", "save_pct"),
    "minutes_played": ("min", "minutes", "minutes_played"),
}


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _pick(row: dict[str, object], canonical_key: str) -> str:
    for key in _SYNONYMS.get(canonical_key, ()):
        if key in row and _cell(row[key]):
            return _cell(row[key])
    return ""


def map_player_row_to_msdie(
    row: dict[str, object],
    *,
    team_id: str,
    team_name: str,
    season: int,
    division: str,
    conference: str,
    source_method: str,
    source_url: str,
    scraped_at_utc: str | None = None,
) -> dict[str, str]:
    """Map parsed player row dictionaries into canonical MSDIE player-season schema."""
    normalized = {_normalize_key(k): v for k, v in row.items()}
    timestamp = scraped_at_utc or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    out: dict[str, str] = {}
    for col in PLAYER_SEASON_COLUMNS:
        if col == "team_id":
            out[col] = team_id
        elif col == "team_name":
            out[col] = team_name
        elif col == "season":
            out[col] = str(season)
        elif col == "division":
            out[col] = division
        elif col == "conference":
            out[col] = conference
        elif col == "source_method":
            out[col] = source_method
        elif col == "source_url":
            out[col] = source_url
        elif col == "scraped_at_utc":
            out[col] = timestamp
        else:
            out[col] = _pick(normalized, col)
    return out


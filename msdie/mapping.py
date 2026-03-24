"""Shared MSDIE row mapping helpers for ingestion pilots."""

from __future__ import annotations

MSDIE_COLUMNS = [
    "team_id",
    "season",
    "division",
    "conference",
    "wins",
    "losses",
    "goals_for",
    "goals_against",
    "shots",
    "shot_pct",
    "faceoff_wins",
    "faceoff_losses",
    "faceoff_pct",
    "ground_balls",
    "turnovers",
    "caused_turnovers",
    "clears_attempted",
    "clears_made",
    "clear_pct",
    "emo_goals",
    "emo_attempts",
    "emo_pct",
    "save_pct",
    "source_method",
]

# MSDIE schema <- Sidearm-style keys (see data/MSDIE_README.md section 4)
SIDEARM_KEY_BY_COLUMN: dict[str, str] = {
    "team_id": "tid",
    "season": "season",
    "conference": "conf",
    "wins": "w",
    "losses": "l",
    "goals_for": "gf",
    "goals_against": "ga",
    "shots": "sh",
    "shot_pct": "sh_pct",
    "faceoff_wins": "fow",
    "faceoff_losses": "fol",
    "faceoff_pct": "fo_pct",
    "ground_balls": "gb",
    "turnovers": "to",
    "caused_turnovers": "ct",
    "clears_attempted": "cl_att",
    "clears_made": "cl_made",
    "clear_pct": "cl_pct",
    "emo_goals": "emo_g",
    "emo_attempts": "emo_att",
    "emo_pct": "emo_pct",
    "save_pct": "sv_pct",
}


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def map_sidearm_row_to_msdie(
    row: dict[str, object],
    *,
    season_fallback: str,
    division_label: str,
    conference_fallback: str,
    source_method: str,
) -> dict[str, str]:
    """Map a Sidearm-like row dict into the canonical MSDIE CSV schema."""
    out: dict[str, str] = {}
    for col in MSDIE_COLUMNS:
        if col == "source_method":
            out[col] = source_method
            continue
        if col == "division":
            out[col] = division_label
            continue
        if col == "conference":
            raw = row.get(SIDEARM_KEY_BY_COLUMN[col])
            out[col] = _cell(raw) if raw not in (None, "") else conference_fallback
            continue
        if col == "season":
            raw = row.get(SIDEARM_KEY_BY_COLUMN[col])
            out[col] = _cell(raw) if raw not in (None, "") else season_fallback
            continue
        sk = SIDEARM_KEY_BY_COLUMN[col]
        out[col] = _cell(row.get(sk, ""))
    return out


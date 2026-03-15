"""Schedule-based Strength of Schedule (SOS) and RPI-style ratings.

This module operates on the *game table* defined in `schedules.py`:
one row per team-game, with keys:

    academic_year, division, team_org_id, opp_org_id, team_score, opp_score

Given that table, we can compute:

- Team win percentage (WP)
- Opponent win percentage (OppWP)
- Opponent's Opponent win percentage (OppOppWP)
- A simple RPI-style rating:
      RPI = 0.25 * WP + 0.5 * OppWP + 0.25 * OppOppWP

The output is a per-team-season DataFrame that can be joined back onto
`team_stats_model_ready.csv` using:

    academic_year, division, org_id == team_org_id
"""

from __future__ import annotations

import pandas as pd
import numpy as np

_TEAM_KEY = ["academic_year", "division", "team_org_id"]


def compute_team_wp(games: pd.DataFrame) -> pd.DataFrame:
    """Compute win percentage (WP) per team-season from the games table."""
    if games.empty:
        return pd.DataFrame(columns=_TEAM_KEY + ["games_played", "wins", "losses", "ties", "wp"])

    df = games.copy()
    df["_win"] = (df["team_score"] > df["opp_score"]).astype(int)
    df["_loss"] = (df["team_score"] < df["opp_score"]).astype(int)
    df["_tie"] = (df["team_score"] == df["opp_score"]).astype(int)

    grouped = (
        df.groupby(_TEAM_KEY, as_index=False)
        .agg(wins=("_win", "sum"), losses=("_loss", "sum"), ties=("_tie", "sum"))
    )
    grouped["games_played"] = grouped["wins"] + grouped["losses"] + grouped["ties"]
    grouped["wp"] = np.where(
        grouped["games_played"] > 0,
        grouped["wins"] / grouped["games_played"],
        0.0,
    )
    return grouped


def compute_opp_wp(games: pd.DataFrame, wp_df: pd.DataFrame) -> pd.DataFrame:
    """Compute opponent win percentage (OppWP) for each team-season.

    For each team, average the WP of all opponents they played that season.
    """
    if games.empty or wp_df.empty:
        return pd.DataFrame(columns=_TEAM_KEY + ["opp_wp"])

    wp_lookup = wp_df.set_index(_TEAM_KEY)["wp"].to_dict()

    rows = []
    for key, grp in games.groupby(_TEAM_KEY):
        year, div, tid = key
        opp_wps = []
        for oid in grp["opp_org_id"].dropna().astype(int):
            opp_key = (year, div, int(oid))
            if opp_key in wp_lookup:
                opp_wps.append(wp_lookup[opp_key])
        rows.append({
            "academic_year": year,
            "division": div,
            "team_org_id": tid,
            "opp_wp": float(np.mean(opp_wps)) if opp_wps else float("nan"),
        })
    return pd.DataFrame(rows)


def compute_opp_opp_wp(
    games: pd.DataFrame,
    opp_wp_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute OppOppWP (opponents' opponents' win %) per team-season."""
    if games.empty or opp_wp_df.empty:
        return pd.DataFrame(columns=_TEAM_KEY + ["opp_opp_wp"])

    opp_wp_lookup = opp_wp_df.set_index(_TEAM_KEY)["opp_wp"].to_dict()

    rows = []
    for key, grp in games.groupby(_TEAM_KEY):
        year, div, tid = key
        vals = []
        for oid in grp["opp_org_id"].dropna().astype(int):
            opp_key = (year, div, int(oid))
            v = opp_wp_lookup.get(opp_key)
            if v is not None and not np.isnan(v):
                vals.append(v)
        rows.append({
            "academic_year": year,
            "division": div,
            "team_org_id": tid,
            "opp_opp_wp": float(np.mean(vals)) if vals else float("nan"),
        })
    return pd.DataFrame(rows)


def compute_sos_metrics(games: pd.DataFrame) -> pd.DataFrame:
    """Compute WP, OppWP, OppOppWP, and RPI-style rating for each team-season.

    Returns empty DataFrame with correct columns when input is empty.
    """
    expected_cols = _TEAM_KEY + [
        "games_played", "wins", "losses", "ties", "wp", "opp_wp", "opp_opp_wp", "rpi",
    ]
    if games.empty:
        return pd.DataFrame(columns=expected_cols)

    wp_df = compute_team_wp(games)
    opp_df = compute_opp_wp(games, wp_df)
    opp_opp_df = compute_opp_opp_wp(games, opp_df)

    sos = (
        wp_df.merge(opp_df, on=_TEAM_KEY, how="left")
        .merge(opp_opp_df, on=_TEAM_KEY, how="left")
    )

    sos["rpi"] = (
        0.25 * sos["wp"].fillna(0.0)
        + 0.5 * sos["opp_wp"].fillna(0.0)
        + 0.25 * sos["opp_opp_wp"].fillna(0.0)
    )

    return sos

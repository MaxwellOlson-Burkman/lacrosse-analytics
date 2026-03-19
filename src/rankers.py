"""Position-based leaderboard ranker with minimum-threshold cutoff logic.

Phase 3 of the Lacrosse Analytics "War Room" plan.

Provides specialized rankings for Offense, Defense, Goalies, and Faceoff
specialists, with configurable minimum sample-size filters to eliminate noise.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lacrosse_site.settings")


@dataclass
class CutoffConfig:
    """Minimum thresholds to qualify for leaderboards."""
    min_games_pct: float = 0.50        # 50% of team games played
    min_faceoff_pct: float = 0.10      # 10% of team's total faceoffs
    min_faceoff_total: int = 10        # absolute minimum FO attempts
    min_goalie_minutes: float = 15.0   # avg minutes/game for goalies


DEFAULT_CUTOFFS = CutoffConfig()


class PositionRanker:
    """Generates specialized leaderboards from SeasonTotals queryset."""

    OFFENSE_COLS = ["points", "goals", "assists", "shots_on_goal", "games_played"]
    DEFENSE_COLS = ["caused_turnovers", "ground_balls", "games_played"]
    GOALIE_COLS = ["saves", "goals_allowed", "minutes_played", "games_played"]
    FACEOFF_COLS = ["faceoffs_won", "faceoffs_lost", "games_played"]

    def __init__(self, cutoffs: Optional[CutoffConfig] = None):
        self.cutoffs = cutoffs or DEFAULT_CUTOFFS

    def _base_queryset(self, year: int, division: int) -> pd.DataFrame:
        import django.apps
        if not django.apps.apps.ready:
            import django
            django.setup()
        from dashboard.models import Player, SeasonTotals

        qs = SeasonTotals.objects.filter(
            player__academic_year=year,
            player__division=division,
        ).select_related("player")

        rows = []
        for st in qs:
            p = st.player
            rows.append({
                "player_id": p.id,
                "player_name": p.name,
                "team_name": p.team_name,
                "team_org_id": p.team_org_id,
                "position": p.position.strip() if p.position and p.position.strip() else "\u2014",
                "games_played": st.games_played or 0,
                "goals": st.goals,
                "assists": st.assists,
                "points": st.points,
                "shots": st.shots,
                "shots_on_goal": st.shots_on_goal,
                "ground_balls": st.ground_balls,
                "turnovers": st.turnovers,
                "caused_turnovers": st.caused_turnovers,
                "faceoffs_won": st.faceoffs_won,
                "faceoffs_lost": st.faceoffs_lost,
                "saves": st.saves or 0,
                "goals_allowed": st.goals_allowed or 0,
                "minutes_played": st.minutes_played or 0.0,
            })
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def _apply_games_cutoff(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove players who haven't played enough games."""
        if df.empty:
            return df
        # Approximate: team games = max games_played among teammates
        team_max = df.groupby("team_org_id")["games_played"].transform("max")
        threshold = team_max * self.cutoffs.min_games_pct
        return df[df["games_played"] >= threshold].copy()

    def rank_offense(self, year: int, division: int, top_n: int = 50) -> pd.DataFrame:
        df = self._base_queryset(year, division)
        if df.empty:
            return df
        df = self._apply_games_cutoff(df)
        if df.empty:
            return df

        gp = df["games_played"].replace(0, 1)
        df["ppg"] = (df["points"] / gp).round(2)
        df["gpg"] = (df["goals"] / gp).round(2)
        df["apg"] = (df["assists"] / gp).round(2)

        df = df.sort_values("ppg", ascending=False).head(top_n).reset_index(drop=True)
        df["rank"] = df.index + 1
        return df[["rank", "player_name", "team_name", "position",
                    "games_played", "points", "goals", "assists", "ppg", "gpg", "apg"]]

    def rank_defense(self, year: int, division: int, top_n: int = 50) -> pd.DataFrame:
        df = self._base_queryset(year, division)
        if df.empty:
            return df
        df = self._apply_games_cutoff(df)
        if df.empty:
            return df

        gp = df["games_played"].replace(0, 1)
        df["ct_pg"] = (df["caused_turnovers"] / gp).round(2)
        df["gb_pg"] = (df["ground_balls"] / gp).round(2)
        df["def_score"] = (df["ct_pg"] * 2 + df["gb_pg"]).round(2)

        df = df.sort_values("def_score", ascending=False).head(top_n).reset_index(drop=True)
        df["rank"] = df.index + 1
        return df[["rank", "player_name", "team_name", "position",
                    "games_played", "caused_turnovers", "ground_balls", "ct_pg", "gb_pg", "def_score"]]

    def rank_goalies(self, year: int, division: int, top_n: int = 50) -> pd.DataFrame:
        df = self._base_queryset(year, division)
        if df.empty:
            return df

        # Filter to goalies: position starts with G, or has significant saves
        mask_pos = df["position"].str.upper().str.startswith("G")
        mask_saves = df["saves"] > 10
        df = df[mask_pos | mask_saves].copy()
        if df.empty:
            return df

        gp = df["games_played"].replace(0, 1)
        avg_min = df["minutes_played"] / gp
        df = df[avg_min >= self.cutoffs.min_goalie_minutes].copy()
        if df.empty:
            return df

        shots_faced = (df["saves"] + df["goals_allowed"]).replace(0, 1)
        df["save_pct"] = (df["saves"] / shots_faced).round(3)
        df["gaa"] = (df["goals_allowed"] / gp).round(2)

        df = df.sort_values("save_pct", ascending=False).head(top_n).reset_index(drop=True)
        df["rank"] = df.index + 1
        return df[["rank", "player_name", "team_name",
                    "games_played", "saves", "goals_allowed", "save_pct", "gaa"]]

    def rank_faceoff(self, year: int, division: int, top_n: int = 50) -> pd.DataFrame:
        df = self._base_queryset(year, division)
        if df.empty:
            return df

        fo_total = df["faceoffs_won"] + df["faceoffs_lost"]
        df = df[fo_total > 0].copy()
        if df.empty:
            return df

        # Keep players who meet EITHER the team-share threshold OR the absolute minimum
        team_fo = df.groupby("team_org_id")[["faceoffs_won", "faceoffs_lost"]].transform("sum")
        team_fo_total = team_fo["faceoffs_won"] + team_fo["faceoffs_lost"]
        player_fo = df["faceoffs_won"] + df["faceoffs_lost"]
        pct_of_team = player_fo / team_fo_total.replace(0, 1)
        meets_pct = pct_of_team >= self.cutoffs.min_faceoff_pct
        meets_abs = player_fo >= self.cutoffs.min_faceoff_total
        df = df[meets_pct | meets_abs].copy()
        if df.empty:
            return df

        fo_total = (df["faceoffs_won"] + df["faceoffs_lost"]).replace(0, 1)
        df["fo_pct"] = (df["faceoffs_won"] / fo_total).round(3)
        df["fo_total"] = df["faceoffs_won"] + df["faceoffs_lost"]

        df = df.sort_values("fo_pct", ascending=False).head(top_n).reset_index(drop=True)
        df["rank"] = df.index + 1
        return df[["rank", "player_name", "team_name", "position",
                    "games_played", "faceoffs_won", "faceoffs_lost", "fo_total", "fo_pct"]]

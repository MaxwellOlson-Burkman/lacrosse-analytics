from __future__ import annotations

from django.db import models


class Player(models.Model):
    """Individual NCAA player tied to a team-season."""

    ncaa_player_id = models.IntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=200)
    team_org_id = models.IntegerField(db_index=True)
    team_name = models.CharField(max_length=200)
    academic_year = models.IntegerField(db_index=True)
    division = models.IntegerField()
    jersey_number = models.CharField(max_length=10, blank=True)
    position = models.CharField(max_length=20, blank=True)  # A, M, D, G, FO, etc.
    class_year = models.CharField(max_length=10, blank=True)  # Fr, So, Jr, Sr

    class Meta:
        indexes = [
            models.Index(fields=["academic_year", "division", "team_org_id"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - representation only
        return f"{self.name} ({self.academic_year} D{self.division} {self.team_name})"


class GameLog(models.Model):
    """Per-game box score line for a player."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="game_logs")
    game_date = models.DateField(null=True)
    opponent_name = models.CharField(max_length=200)
    opponent_org_id = models.IntegerField(null=True, blank=True)

    # Box score fields
    goals = models.IntegerField(default=0)
    assists = models.IntegerField(default=0)
    points = models.IntegerField(default=0)
    shots = models.IntegerField(default=0)
    shots_on_goal = models.IntegerField(default=0)
    ground_balls = models.IntegerField(default=0)
    turnovers = models.IntegerField(default=0)
    caused_turnovers = models.IntegerField(default=0)
    faceoffs_won = models.IntegerField(default=0)
    faceoffs_lost = models.IntegerField(default=0)

    # Goalie-specific fields (null for field players)
    saves = models.IntegerField(null=True, blank=True)
    goals_allowed = models.IntegerField(null=True, blank=True)
    minutes_played = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["game_date"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - representation only
        return f"{self.player.name} vs {self.opponent_name} on {self.game_date}"


class SeasonTotals(models.Model):
    """Season-level aggregate stats for a player."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="season_totals")

    games_played = models.IntegerField(default=0)
    games_started = models.IntegerField(default=0)

    # Aggregated from GameLog (or scraped directly from roster page)
    goals = models.IntegerField(default=0)
    assists = models.IntegerField(default=0)
    points = models.IntegerField(default=0)
    shots = models.IntegerField(default=0)
    shots_on_goal = models.IntegerField(default=0)
    ground_balls = models.IntegerField(default=0)
    turnovers = models.IntegerField(default=0)
    caused_turnovers = models.IntegerField(default=0)
    faceoffs_won = models.IntegerField(default=0)
    faceoffs_lost = models.IntegerField(default=0)

    saves = models.IntegerField(null=True, blank=True)
    goals_allowed = models.IntegerField(null=True, blank=True)
    minutes_played = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["player"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - representation only
        return f"Season totals for {self.player}"


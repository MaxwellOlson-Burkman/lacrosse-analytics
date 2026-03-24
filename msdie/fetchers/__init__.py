"""Vendor-specific stat fetchers."""

from msdie.fetchers.sidearm_fetcher import (
    SidearmFetchError,
    build_team_stats_url,
    fetch_sidearm_team_stats,
    normalize_base_url,
)
from msdie.fetchers.presto_fetcher import PrestoFetchError, fetch_presto_team_season_stats
from msdie.fetchers.player_stats_fetcher import PlayerStatsFetchError, fetch_player_season_stats

__all__ = [
    "SidearmFetchError",
    "build_team_stats_url",
    "fetch_sidearm_team_stats",
    "normalize_base_url",
    "PrestoFetchError",
    "fetch_presto_team_season_stats",
    "PlayerStatsFetchError",
    "fetch_player_season_stats",
]

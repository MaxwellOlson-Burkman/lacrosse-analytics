"""Dashboard URL configuration."""
from django.urls import path
from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.index_view, name="index"),
    path("chat/", views.chat_page_view, name="chat"),
    path("chat/post/", views.chat_partial, name="chat_partial"),
    path("chat/clear/", views.clear_chat_partial, name="clear_chat_partial"),
    path("rankings/", views.rankings_view, name="rankings"),
    path("conference-rankings/", views.conference_rankings_view, name="conference_rankings"),
    path("compare/", views.compare_view, name="compare"),
    path("tewaaraton/", views.tewaaraton_view, name="tewaaraton"),
    path("leaderboards/", views.leaderboards_view, name="leaderboards"),
    path("compare-players/", views.compare_players_view, name="compare_players"),
    path("player/", views.player_lookup_view, name="player_lookup"),
    path("player/<int:player_id>/", views.player_detail_view, name="player_detail"),
    path("favicon.ico", views.favicon_view),
]

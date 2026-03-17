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
    path("favicon.ico", views.favicon_view),
]

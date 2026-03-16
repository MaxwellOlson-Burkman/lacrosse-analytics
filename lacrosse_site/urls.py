"""URL configuration for lacrosse_site."""
from django.urls import path, include

urlpatterns = [
    path("", include("dashboard.urls")),
]

from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("", RedirectView.as_view(url="/monitor/backend/")),
    path("enter/", views.enter, name="monitor-enter"),
    path("feed/backend/", views.backend_feed),
    path("feed/database/", views.database_feed),
    path("feed/login/", views.login_feed),
    path("<str:name>/", views.page, name="monitor-page"),
]

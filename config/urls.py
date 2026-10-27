from django.contrib import admin
from django.urls import include, path

from config.views import healthz
from supervision.views import dashboard_page

urlpatterns = [
    path("healthz", healthz),
    # Before admin.site.urls, which would otherwise answer 404 for it.
    path("admin/tableau/", admin.site.admin_view(dashboard_page), name="admin_tableau"),
    path("admin/", admin.site.urls),
    path("", include("ui.urls")),
    path("", include("accounts.urls")),
    path("", include("onboarding.urls")),
    path("", include("matching.urls")),
]

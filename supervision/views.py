from django.contrib import admin
from django.template.response import TemplateResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from supervision import dashboard


@require_GET
def dashboard_page(request):
    """Wrapped by `admin.site.admin_view` in config/urls.py: staff signed in, never cached, CSRF of the admin."""
    context = {**admin.site.each_context(request), "title": "Tableau de bord", **dashboard.build(timezone.now())}
    return TemplateResponse(request, "supervision/dashboard.html", context)

from django.db import connection
from django.http import HttpResponse


def healthz(request):
    """Liveness probe for the Docker healthcheck: also proves the database answers."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")

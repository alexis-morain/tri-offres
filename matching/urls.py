from django.urls import path, re_path

from matching import views

app_name = "matching"
urlpatterns = [
    path("tri/", views.sort, name="sort"),
    path("tri/avis/", views.verdict, name="verdict"),
    path("tri/annuler/", views.undo, name="undo"),
    path("tri/appris/", views.learned, name="learned"),
    path("tri/appris/effacer/", views.learned_clear, name="learned_clear"),
    path("mes-offres/", views.history, name="history"),
    # At most 18 digits, as views.OFFER_ID: a longer key would overflow SQLite's integer.
    re_path(r"^mes-offres/(?P<offer_id>[0-9]{1,18})/$", views.follow, name="follow"),
]

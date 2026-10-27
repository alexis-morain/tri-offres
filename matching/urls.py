from django.urls import path

from matching import views

app_name = "matching"
urlpatterns = [
    path("tri/", views.sort, name="sort"),
    path("tri/avis/", views.verdict, name="verdict"),
    path("tri/annuler/", views.undo, name="undo"),
    path("tri/appris/", views.learned, name="learned"),
    path("tri/appris/effacer/", views.learned_clear, name="learned_clear"),
]

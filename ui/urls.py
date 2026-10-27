from django.urls import path

from ui.views import style_page

app_name = "ui"
urlpatterns = [
    path("interne/style/", style_page, name="style"),
]

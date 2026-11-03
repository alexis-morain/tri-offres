from django.urls import path

from ui.views import favicon, style_page

app_name = "ui"
urlpatterns = [
    path("interne/style/", style_page, name="style"),
    path("favicon.ico", favicon),
]

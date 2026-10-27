from django.urls import path, re_path

from accounts import views

app_name = "accounts"
urlpatterns = [
    path("", views.home, name="home"),
    path("connexion/code/", views.code, name="code"),
    path("connexion/renvoyer/", views.resend, name="resend"),
    path("connexion/lien/<str:token>/", views.link, name="link"),
    path("deconnexion/", views.sign_out, name="logout"),
    path("compte-supprime/", views.account_deleted, name="deleted"),
    # The token's pattern is bounded before any view reads it (E2).
    re_path(r"^desinscription/(?P<token>[A-Za-z0-9_:.-]{1,200})/$", views.unsubscribe_page, name="unsubscribe"),
    re_path(r"^desinscription/(?P<token>[A-Za-z0-9_:.-]{1,200})/un-clic/$", views.unsubscribe_one_click,
            name="unsubscribe_one_click"),
    path("openrouter/", views.openrouter_connect, name="openrouter_connect"),
    path("openrouter/tuto/", views.openrouter_tutorial, name="openrouter_tutorial"),
    path("openrouter/depart/", views.openrouter_start, name="openrouter_start"),
    path("openrouter/retour/", views.openrouter_return, name="openrouter_return"),
    path("openrouter/connecte/", views.openrouter_done, name="openrouter_done"),
    path("openrouter/annulee/", views.openrouter_cancelled, name="openrouter_cancelled"),
    path("openrouter/deconnecter/", views.openrouter_disconnect, name="openrouter_disconnect"),
    path("confidentialite/", views.privacy, name="privacy"),
    path("mentions-legales/", views.legal_notice, name="legal"),
]

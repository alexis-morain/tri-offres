from django.urls import path

from onboarding import views

app_name = "onboarding"
urlpatterns = [
    path("demarrer/", views.start, name="start"),
    # Fixed paths before the screen slug, so they are never read as one.
    path("questionnaire/compteur/", views.counter, name="counter"),
    path("questionnaire/cv/", views.cv_screen, name="cv"),
    path("questionnaire/cv/liste/", views.cv_list, name="cv_list"),
    path("questionnaire/cv/supprimer/", views.cv_delete, name="cv_delete"),
    path("questionnaire/postes/", views.job_suggestions, name="job_suggestions"),
    path("questionnaire/<slug:slug>/", views.step, name="step"),
    path("synthese/", views.summary_screen, name="summary"),
    path("synthese/rediger/", views.summary_write, name="summary_write"),
    path("synthese/corriger/", views.summary_correct, name="summary_correct"),
    path("synthese/texte/", views.summary_text, name="summary_text"),
    path("synthese/valider/", views.summary_validate, name="summary_validate"),
    path("fin/", views.end, name="end"),
    path("ma-recherche/", views.my_search, name="my_search"),
    path("ma-recherche/<slug:block>/", views.my_search_block, name="my_search_block"),
    path("mes-donnees/", views.my_data, name="my_data"),
    path("mes-donnees/reabonner/", views.resubscribe, name="resubscribe"),
    path("mes-donnees/supprimer/", views.delete_account, name="delete_account"),
    path("mes-donnees/supprimer-cv/", views.my_data_cv_delete, name="my_data_cv_delete"),
]

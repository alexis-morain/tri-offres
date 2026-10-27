"""The forced stop at the OpenRouter connection (plan 2026-10-27, decisions 3.6 and 3.8).

Under the api engine, a signed-in person without a key goes to the connection screen instead of the questionnaire, « À
trier », « Mes offres » and « Ma recherche ». Only those two apps are gated: the accounts app (sign-in, sign-out,
unsubscribing, legal pages, the tutorial, the OpenRouter views), the admin and the health check never are, nor the few
« Mes données » pages below: a person who does not want to connect must still see what is kept, leave or delete the
account.
"""
from django.shortcuts import redirect

from accounts import openrouter

GATED_APPS = {"onboarding", "matching"}
OPEN = {"onboarding:my_data", "onboarding:my_data_cv_delete", "onboarding:resubscribe", "onboarding:delete_account"}


class RequireOpenRouter:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if (match is None or match.namespace not in GATED_APPS or match.view_name in OPEN
                or not request.user.is_authenticated or not openrouter.must_connect(request.user)):
            return None
        return redirect("accounts:openrouter_connect")

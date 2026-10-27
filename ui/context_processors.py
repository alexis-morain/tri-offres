from django.conf import settings


def beta(request):
    """`beta` for every template: « bêta gratuite » is written during the beta only."""
    return {"beta": settings.BETA}

from django.conf import settings


def beta(request):
    """`beta` for every template: « bêta gratuite » is written during the beta only. `source_code_url` for the
    footer's link to the code."""
    return {"beta": settings.BETA, "source_code_url": settings.SOURCE_CODE_URL}

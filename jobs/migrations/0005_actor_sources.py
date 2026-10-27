"""The sources of the Apify actor collected for France (step 4). A copy of jobs/actor_sources.py as of 13/10:
a migration must not import code that will move."""
from django.db import migrations

ACTOR_SOURCES = (
    ("wttj", "Welcome to the Jungle", "https://www.welcometothejungle.com", []),
    ("hellowork", "HelloWork", "https://www.hellowork.com", []),
    ("meteojob", "Meteojob", "https://www.meteojob.com", []),
    ("free_work_jobs", "Free-Work, emplois", "https://www.free-work.com", ["M18"]),
    ("free_work", "Free-Work, missions", "https://www.free-work.com", ["M18"]),
    ("collective", "Collective", "https://www.collective.work", []),
    ("codeur", "Codeur.com", "https://www.codeur.com", []),
    ("taleez", "Taleez", "https://www.taleez.com", []),
    ("teamtailor", "Teamtailor", "https://www.teamtailor.com", []),
    ("profilculture", "ProfilCulture", "https://www.profilculture.com", ["E", "K", "L"]),
    ("livreshebdo", "Livres Hebdo", "https://www.livreshebdo.fr", ["E", "K", "L"]),
    ("agences_livre", "Agences régionales du livre", "", ["E", "K", "L"]),
    ("eures", "EURES (France Travail)", "https://eures.europa.eu", []),
    ("choisir_service_public", "Choisir le service public", "https://choisirleservicepublic.gouv.fr", []),
    ("emploi_territorial", "Emploi territorial", "https://www.emploi-territorial.fr", []),
    ("service_civique", "Service Civique", "https://www.service-civique.gouv.fr", []),
)


def forwards(apps, schema_editor):
    """Creates the sources missing; a source already holding the key is left as it is."""
    Source = apps.get_model("jobs", "Source")
    existing = set(Source.objects.values_list("actor_source", flat=True))
    Source.objects.bulk_create(
        Source(actor_source=key, name=name, url=url, domains=domains, kind="apify", state="active")
        for key, name, url, domains in ACTOR_SOURCES if key not in existing
    )


def backwards(apps, schema_editor):
    """Drops the sources of this list without offers; one holding offers stays, so nothing collected is lost."""
    Source = apps.get_model("jobs", "Source")
    keys = [key for key, _, _, _ in ACTOR_SOURCES]
    Source.objects.filter(kind="apify", actor_source__in=keys, offers__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0004_collect_fields'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

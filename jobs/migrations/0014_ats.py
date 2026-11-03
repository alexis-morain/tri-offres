"""The ATS platforms, boards listed in jobs/data/ats-france.txt (plan of 29/10, section 2). A copy of the end of
jobs/actor_sources.py as of 30/10: a migration must not import code that will move."""
from django.db import migrations

ACTOR_SOURCES = (
    ("ats", "ATS (Greenhouse, Lever, Workday…)", "", []),
)


def forwards(apps, schema_editor):
    """Creates the sources missing; a source already holding the key is left as it is."""
    Source = apps.get_model("jobs", "Source")
    existing = set(Source.objects.values_list("actor_source", flat=True))
    Source.objects.bulk_create(
        Source(actor_source=key, name=name, url=url, domains=domains, kind="actor", state="active")
        for key, name, url, domains in ACTOR_SOURCES if key not in existing
    )


def backwards(apps, schema_editor):
    """Drops the sources of this list without offers; one holding offers stays, so nothing collected is lost."""
    Source = apps.get_model("jobs", "Source")
    keys = [key for key, _, _, _ in ACTOR_SOURCES]
    Source.objects.filter(kind="actor", actor_source__in=keys, offers__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0013_beetween_editis"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

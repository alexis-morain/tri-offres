"""The segment of each source becomes the ROME domains it serves (sub-step 3.11). Empty: every person."""
from django.db import migrations, models

SEGMENT_DOMAINS = {"culture": ["E", "K", "L"], "growth": ["D", "M17"], "": []}
GROWTH_DOMAINS = {"D", "M17"}


def forwards(apps, schema_editor):
    Source = apps.get_model("jobs", "Source")
    for source in Source.objects.all():
        source.domains = SEGMENT_DOMAINS.get(source.segment, [])
        source.save(update_fields=["domains"])


def backwards(apps, schema_editor):
    Source = apps.get_model("jobs", "Source")
    for source in Source.objects.all():
        if not source.domains:
            continue
        source.segment = "growth" if GROWTH_DOMAINS & set(source.domains) else "culture"
        source.save(update_fields=["segment"])


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0002_segment_growth_label'),
    ]

    operations = [
        migrations.AddField(
            model_name='source',
            name='domains',
            field=models.JSONField(blank=True, default=list, verbose_name='domaines servis'),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(
            model_name='source',
            name='segment',
        ),
    ]

"""The actor's sources change kind: "apify" becomes "actor", since the actor runs locally (the design plan-
locale.md)."""
from django.db import migrations, models


def forwards(apps, schema_editor):
    apps.get_model("jobs", "Source").objects.filter(kind="apify").update(kind="actor")


def backwards(apps, schema_editor):
    apps.get_model("jobs", "Source").objects.filter(kind="actor").update(kind="apify")


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0006_offer_prepass_tracking"),
    ]

    operations = [
        migrations.AlterField(
            model_name="source",
            name="kind",
            field=models.CharField(choices=[("module", "Module maison"), ("ats_feed", "Flux ATS"),
                                            ("jsonld", "JSON-LD"), ("actor", "Actor")],
                                   max_length=20, verbose_name="type"),
        ),
        migrations.RunPython(forwards, backwards),
    ]

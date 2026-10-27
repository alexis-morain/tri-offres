from django.db import migrations, models

from jobs.text import fold_title


def fold_titles(apps, schema_editor):
    JobOffer = apps.get_model("jobs", "JobOffer")
    offers = []
    for offer in JobOffer.objects.only("pk", "title").iterator():
        offer.title_folded = fold_title(offer.title)
        offers.append(offer)
        if len(offers) == 1000:
            JobOffer.objects.bulk_update(offers, ["title_folded"])
            offers = []
    JobOffer.objects.bulk_update(offers, ["title_folded"])


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0010_zone_postal_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='joboffer',
            name='title_folded',
            field=models.CharField(blank=True, max_length=300, verbose_name='titre sans accents'),
        ),
        migrations.RunPython(fold_titles, migrations.RunPython.noop),
    ]

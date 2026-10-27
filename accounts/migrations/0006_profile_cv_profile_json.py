from django.db import migrations, models


class Migration(migrations.Migration):
    """cv_profile becomes JSON. No real data exists yet: the old text column is dropped, not converted."""

    dependencies = [
        ("accounts", "0005_profile_seek_refuse_text"),
    ]

    operations = [
        migrations.RemoveField(model_name="profile", name="cv_profile"),
        migrations.AddField(
            model_name="profile",
            name="cv_profile",
            field=models.JSONField(blank=True, default=dict, verbose_name="profil extrait du CV"),
        ),
        migrations.AddField(
            model_name="profile",
            name="cv_processed_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="CV lu le"),
        ),
    ]

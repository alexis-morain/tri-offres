from django.db import migrations, models


def copy_free_text(apps, schema_editor):
    Profile = apps.get_model("accounts", "Profile")
    Profile.objects.exclude(free_text="").update(seek_text=models.F("free_text"))


class Migration(migrations.Migration):
    """free_text becomes seek_text and refuse_text. No real user yet: free_text goes to seek_text as is."""

    dependencies = [
        ("accounts", "0004_onboarding_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="seek_text",
            field=models.TextField(blank=True, verbose_name="ce que la personne cherche"),
        ),
        migrations.AddField(
            model_name="profile",
            name="refuse_text",
            field=models.TextField(blank=True, verbose_name="ce que la personne refuse"),
        ),
        migrations.RunPython(copy_free_text, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="profile",
            name="free_text",
        ),
    ]

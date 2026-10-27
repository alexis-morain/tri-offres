"""The segment of each account becomes ROME domains on its profile (sub-step 3.11).

Lists are copied here, never imported: a migration keeps the codes of its day.
"""
from django.db import migrations, models

SEGMENT_DOMAINS = {"culture": ["E", "K", "L"], "growth": ["D", "M17"]}
GROWTH_DOMAINS = {"D", "M17"}
STRUCTURES = ["company", "startup", "association_ess", "culture", "public_sector", "health_social", "education"]
OLD_STRUCTURES = {
    "publisher": "culture", "bookshop": "culture", "library_venue": "culture", "museum": "culture",
    "performing_arts": "culture", "association": "association_ess", "ess_company": "association_ess",
    "local_authority": "public_sector", "school": "education", "early_startup": "startup", "scaleup": "startup",
    "saas": "company", "agency": "company",
}


def _renamed_sources(items):
    """Summary sentences or questions with "segment" renamed in their sources. Anything of another shape is left as
    it is: the application reads a value that does not fit its schema as empty (onboarding/views._profile)."""
    if not isinstance(items, list):
        return items
    return [{**item, "sources": ["domains" if source == "segment" else source for source in item["sources"]]}
            if isinstance(item, dict) and isinstance(item.get("sources"), list) else item
            for item in items]


def forwards(apps, schema_editor):
    Profile = apps.get_model("accounts", "Profile")
    for profile in Profile.objects.exclude(onboarding_step="").select_related("user"):
        segment = profile.user.segment
        profile.domains = SEGMENT_DOMAINS.get(segment, SEGMENT_DOMAINS["culture"])  # culture was the default
        # Values of another shape are left as they are, read as empty by the application (see _renamed_sources).
        if segment != "growth" and isinstance(profile.hard_criteria, dict):  # the offer kind is now asked to all
            profile.hard_criteria.setdefault("offer_kind", "job")
        preferences = profile.weighted_preferences
        if isinstance(preferences, dict) and isinstance(preferences.get("structures"), list):
            chosen = {OLD_STRUCTURES.get(code, code) for code in preferences["structures"] if isinstance(code, str)}
            preferences["structures"] = [code for code in STRUCTURES if code in chosen]
        profile.summary_sentences = _renamed_sources(profile.summary_sentences)
        profile.summary_questions = _renamed_sources(profile.summary_questions)
        profile.save(update_fields=["domains", "hard_criteria", "weighted_preferences", "summary_sentences",
                                    "summary_questions"])


def backwards(apps, schema_editor):
    Profile = apps.get_model("accounts", "Profile")
    for profile in Profile.objects.select_related("user"):
        domains = profile.domains if isinstance(profile.domains, list) else []
        profile.user.segment = "growth" if GROWTH_DOMAINS & set(domains) else "culture"
        profile.user.save(update_fields=["segment"])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_profile_summary'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='domains',
            field=models.JSONField(blank=True, default=list, verbose_name='domaines'),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(
            model_name='user',
            name='segment',
        ),
    ]

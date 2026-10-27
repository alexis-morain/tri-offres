"""A real trial of the summary prompt on three made-up profiles, kept in memory: nothing is stored.

The profiles belong to an unsaved user whose address is the first of DEV_EMAILS, so the oauth engine (Max token)
may serve them. Never run by the tests with a real engine.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

import llm
from accounts.models import Profile, User
from onboarding import summary

LYON = {"name": "Lyon", "insee": "69123", "department": "69", "region": "84"}
PARIS = {"name": "Paris", "insee": "75056", "department": "75", "region": "11"}

PROFILES = [
    ("Culture, en reconversion depuis l’enseignement", {
        "domains": ["E", "K"],
        "hard_criteria": {
            "version": 1,
            "offer_kind": "job",
            "location": {"cities": [LYON], "distance": "30", "remote": "hybrid", "strict": True},
            "contracts": {"values": ["cdi", "cdd", "fonction_publique"], "strict": True},
            "pay": {"floor": 26000, "unit": "year", "strict": True},
            "hours": {"value": "full", "strict": True},
            "experience": {"value": "5_9", "strict": False},
        },
        "weighted_preferences": {"version": 1, "values": ["meaning", "team"],
                                 "jobs": ["mediation", "nonprofit_projects", "teaching"],
                                 "structures": ["association_ess", "culture"]},
        "career_change": True,
        "previous_job": "Professeure des écoles",
        "seek_text": "Je quitte l’enseignement après dix ans. Je cherche un poste où je garde le contact avec les "
                     "publics, des enfants peut-être, mais hors de la classe : médiation, ateliers, action culturelle.",
        "refuse_text": "Plus de classe entière toute la journée, plus de réunions de parents le soir.",
        "cv_profile": {"positions": [{"title": "Professeure des écoles", "organisation": "Éducation nationale",
                                      "years": "2014-2024"},
                                     {"title": "Animatrice d’ateliers lecture", "organisation": "Médiathèque",
                                      "years": "2012-2014"}],
                       "skills": ["Animation de groupes", "Conception de séquences", "Accueil des familles"],
                       "education": ["Master MEEF, 2014", "Licence de lettres modernes, 2012"]},
    }),
    ("Culture, jeune diplômée, édition", {
        "domains": ["E"],
        "hard_criteria": {
            "version": 1,
            "offer_kind": "job",
            "location": {"cities": [PARIS], "distance": "region", "remote": "onsite", "strict": True},
            "contracts": {"values": ["cdi", "cdd"], "strict": True},
            "pay": {"floor": None, "unit": "year", "strict": True},
            "hours": {"value": "full", "strict": True},
            "experience": {"value": "lt1", "strict": True},
        },
        "weighted_preferences": {"version": 1, "values": ["learning", "meaning"],
                                 "jobs": ["E11", "editorial"], "structures": ["culture"]},
        "seek_text": "Un premier poste dans l’édition jeunesse, assistante éditoriale ou en fabrication, dans une "
                     "maison où je peux apprendre le métier.",
        "refuse_text": "Les stages déguisés en CDD, la vente en librairie.",
    }),
    ("Growth, SDR qui vise des missions", {
        "domains": ["D", "M17"],
        "hard_criteria": {
            "version": 1,
            "offer_kind": "mission",
            "location": {"cities": [], "distance": "city", "remote": "full_remote", "strict": True},
            "contracts": {"values": ["freelance"], "strict": True},
            "pay": {"floor": 400, "unit": "day", "strict": False},
            "hours": {"value": "part_ok", "strict": True},
            "experience": {"value": "3_4", "strict": True},
        },
        "weighted_preferences": {"version": 1, "values": ["autonomy", "flexible_hours"],
                                 "jobs": ["sdr_bdr", "outbound"], "structures": ["company", "startup"]},
        "seek_text": "Des missions de prospection outbound pour des startups B2B, deux à trois jours par semaine. "
                     "J’aime construire les séquences moi-même.",
        "refuse_text": "La prospection téléphonique pure, les commissions sans fixe.",
    }),
]


class Command(BaseCommand):
    help = "Write the summary of three made-up profiles with the configured Claude engine, storing nothing."

    def handle(self, *args, **options):
        if not settings.DEV_EMAILS:
            raise CommandError("DEV_EMAILS is empty: the made-up profiles need a development address.")
        for name, fields in PROFILES:
            user = User(email=settings.DEV_EMAILS[0])  # never saved
            profile = Profile(user=user, **fields)
            self.stdout.write(f"\n== {name}")
            try:
                result = summary.compose(profile)
            except llm.LLMError as exc:
                self.stdout.write(f"  failed: {type(exc).__name__}")
                continue
            for sentence in result["sentences"]:
                self.stdout.write(f"- {sentence['text']} [{', '.join(sentence['sources'])}]")
            for question in result["questions"]:
                self.stdout.write(f"? {question['text']} [{', '.join(question['sources'])}]")

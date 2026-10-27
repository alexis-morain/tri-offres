"""A real trial of the night (the design plan, decision 17 of the step 5 plan): on the
throwaway database filled by `manage.py collecte`, three made-up accounts, the night of
matching.night for them alone, then a report to reread.

A maintainer's tool on a throwaway database, run in a terminal with the oauth engine: the report shows the
offers' summaries and Claude's reasons, which no log ever does. The summary of each profile is written here by hand,
so the trial calls the pre-pass and the scoring only. Never run by the tests with a real engine.
"""
import random
import tempfile
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import NamedTuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

import llm
from accounts.models import Profile, SummaryStatus, User
from jobs.models import JobOffer
from matching import night
from matching.models import Score, ScoringRun
from onboarding import flow, rome
from onboarding.criteria import format_number

LYON = {"name": "Lyon", "insee": "69123", "department": "69", "region": "84"}
PARIS = {"name": "Paris", "insee": "75056", "department": "75", "region": "11"}
NANTES = {"name": "Nantes", "insee": "44109", "department": "44", "region": "52"}


class Account(NamedTuple):
    email: str
    label: str
    profile: dict  # fields of the Profile


ACCOUNTS = [
    Account("essai+culture@example.org", "Culture, Lyon", {
        "domains": ["E", "L"],
        "hard_criteria": {
            "version": 1,
            "offer_kind": "job",
            "location": {"cities": [LYON], "distance": "30", "remote": "any", "strict": True},
            "contracts": {"values": ["cdi", "cdd", "cdd_projet"], "strict": True},
            "pay": {"floor": None, "unit": "year", "strict": True},
            "hours": {"value": "full", "strict": False},
            "experience": {"value": "3_4", "strict": False},
        },
        "weighted_preferences": {"version": 1, "values": ["meaning", "team"],
                                 "jobs": ["mediation", "production_admin", "L13"],
                                 "structures": ["culture", "association_ess"]},
        "seek_text": "Un poste de médiation ou de production dans un lieu culturel de la région lyonnaise, au contact "
                     "des publics et des équipes artistiques.",
        "refuse_text": "Les postes seulement commerciaux, la billetterie seule.",
        "summary_text": "Vous cherchez un poste en médiation culturelle ou en production de spectacles, à Lyon ou à "
                        "moins de 30 km, en CDI, en CDD ou en CDD de projet. Vous tenez au sens de votre travail et "
                        "à l’équipe, dans une structure culturelle ou associative.",
    }),
    Account("essai+info@example.org", "Informatique, Paris", {
        "domains": ["M18"],
        "hard_criteria": {
            "version": 1,
            "offer_kind": "job",
            "location": {"cities": [PARIS], "distance": "60", "remote": "any", "strict": True},
            "contracts": {"values": ["cdi"], "strict": True},
            "pay": {"floor": 45000, "unit": "year", "strict": True},
            "hours": {"value": "full", "strict": True},
            "experience": {"value": "5_9", "strict": False},
        },
        "weighted_preferences": {"version": 1, "values": ["autonomy", "learning", "pay"],
                                 "jobs": ["M18", {"custom": "développeur web"}],
                                 "structures": ["company", "startup"]},
        "seek_text": "Un poste de développeur web, plutôt côté serveur, en Python ou en PHP, dans une équipe produit "
                     "qui livre souvent.",
        "refuse_text": "Les sociétés de services qui placent leurs développeurs chez des clients, la maintenance "
                       "seule.",
        "summary_text": "Vous cherchez un poste de développeur web en CDI, à Paris ou à moins de 60 km, payé 45 000 € "
                        "brut par an au moins. Vous préférez une équipe produit, en entreprise ou en startup, où "
                        "vous gagnez en autonomie.",
    }),
    Account("essai+sante@example.org", "Santé, Nantes", {
        "domains": ["J"],
        "hard_criteria": {
            "version": 1,
            "offer_kind": "job",
            "location": {"cities": [NANTES], "distance": "30", "remote": "any", "strict": True},
            "contracts": {"values": ["cdi", "cdd"], "strict": True},
            "pay": {"floor": None, "unit": "month", "strict": True},
            "hours": {"value": "full", "strict": False},
            "experience": {"value": "10_plus", "strict": False},
        },
        "weighted_preferences": {"version": 1, "values": ["stability", "team", "proximity"],
                                 "jobs": ["J15", {"custom": "infirmière"}],
                                 "structures": ["health_social", "public_sector"]},
        "seek_text": "Un poste d’infirmière en service de soins ou en EHPAD, près de Nantes, avec des horaires qui "
                     "laissent une vie de famille.",
        "refuse_text": "Les nuits fixes et l’intérim.",
        "summary_text": "Vous cherchez un poste d’infirmière en CDI ou en CDD, à Nantes ou à moins de 30 km, à "
                        "l’hôpital, en clinique ou en EHPAD. Vous tenez à la stabilité, à l’équipe et à des "
                        "horaires compatibles avec une vie de famille.",
    }),
]
EMAILS = [account.email for account in ACCOUNTS]


class Price(NamedTuple):
    """Dollars per million tokens."""

    input: Decimal
    output: Decimal
    cache_read: Decimal


# Prices of 13/10/2026; a cache write costs the input price times CACHE_WRITE_FACTOR.
PRICES = {
    llm.SONNET: Price(Decimal("2"), Decimal("10"), Decimal("0.20")),
    llm.HAIKU: Price(Decimal("0.10"), Decimal("0.50"), Decimal("0.01")),
}
CACHE_WRITE_FACTOR = Decimal("1.25")
MODEL_NAMES = {llm.HAIKU: "Haiku 5.5", llm.SONNET: "Sonnet 5.5"}
# The model of each kind of run: jobs.summaries calls Haiku, matching.scoring Sonnet.
PREPASS_MODEL, SCORING_MODEL = llm.HAIKU, llm.SONNET
SAMPLE_SIZE = 5
SAMPLE_SEED = 13  # the same draw from one run to the next, on the same offers


def is_scratch_database(name):
    """Whether the database `name` lies inside /tmp or the system's temporary folder: never the project's base nor
    a production one."""
    path = Path(str(name)).resolve()
    roots = {Path(root).resolve() for root in ("/tmp", tempfile.gettempdir())}
    return any(path != root and path.is_relative_to(root) for root in roots)


def cost(model, tokens):
    """Dollars for `tokens` ({ScoringRun usage field: count}) of `model`, exact."""
    price = PRICES[model]
    per_million = (tokens["input_tokens"] * price.input + tokens["output_tokens"] * price.output
                   + tokens["cache_read_tokens"] * price.cache_read
                   + tokens["cache_creation_tokens"] * price.input * CACHE_WRITE_FACTOR)
    return per_million / 1_000_000


def _dollars(amount, places=2):
    """« 11,40 $ »."""
    rounded = amount.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return f"{rounded:,.{places}f}".replace(",", " ").replace(".", ",") + " $"


def _percent(part, whole):
    return f"{round(100 * part / whole)} %" if whole else "sans objet"


def _plural(number, singular, plural):
    return f"{format_number(number)} {singular if number < 2 else plural}"


class ResetCounts(NamedTuple):
    scores: int
    runs: int
    summaries: int
    codes: int


def reset(emails):
    """Deletes the scores and runs of the accounts of `emails`; clears what the pre-pass wrote on every offer of the
    throwaway database (summaries, and the ROME codes it found, never one found by the title)."""
    with transaction.atomic():
        scores = Score.objects.filter(user__email__in=emails).delete()[1].get(Score._meta.label, 0)
        runs = ScoringRun.objects.filter(user__email__in=emails).delete()[1].get(ScoringRun._meta.label, 0)
        codes = JobOffer.objects.filter(rome_by_claude=True).update(rome_code="", rome_by_claude=False)
        summaries = (JobOffer.objects.exclude(summary="", summary_written_at__isnull=True)
                     .update(summary="", summary_written_at=None))
    return ResetCounts(scores, runs, summaries, codes)


def _write_accounts(now):
    """The three accounts, active, their questionnaire finished and their summary validated; created once, their
    profile written again at each run."""
    profiles = []
    for account in ACCOUNTS:
        user, _ = User.objects.get_or_create(email=account.email)
        user.status, user.is_active = User.Status.ACTIVE, True
        user.save(update_fields=["status", "is_active"])
        profile, _ = Profile.objects.update_or_create(user=user, defaults={
            **account.profile, "summary_status": SummaryStatus.READY, "summary_generated_at": now,
            "summary_validated_at": now, "onboarding_step": flow.SUMMARY, "onboarding_completed_at": now})
        profiles.append(profile)
    return profiles


def _appellation(offer):
    """The ROME job of the offer as the report shows it: its code, its professional domain, and who found it."""
    if not offer.rome_code:
        return "sans code ROME"
    label = rome.PROFESSIONAL_DOMAIN_LABELS.get(offer.rome_code[:3], "domaine inconnu")
    return f"code ROME {offer.rome_code}, {label}, {'par Claude' if offer.rome_by_claude else 'par le titre'}"


def _prepass_lines():
    """The pre-pass over the whole throwaway database, then SAMPLE_SIZE summaries drawn."""
    handled = JobOffer.objects.filter(summary_written_at__isnull=False)
    count = handled.count()
    summarized = handled.exclude(summary="")
    written = summarized.count()
    without = handled.filter(rome_code="").count()
    lines = ["", "== Pré-passe (toutes les offres de la base jetable)",
             f"Offres passées par la pré-passe : {format_number(count)}. Offres résumées : {format_number(written)}. "
             f"Résumés refusés : {format_number(count - written)}.",
             f"Codes ROME trouvés par Claude : {format_number(JobOffer.objects.filter(rome_by_claude=True).count())}. "
             f"Offres sans code ROME : {format_number(without)} sur {format_number(count)} "
             f"({_percent(without, count)})."]
    offers = list(summarized.order_by("pk"))
    drawn = random.Random(SAMPLE_SEED).sample(offers, min(SAMPLE_SIZE, len(offers)))
    if drawn:
        lines.append(f"{_plural(len(drawn), 'résumé tiré', 'résumés tirés')} au hasard :")
    for offer in drawn:
        lines += [f"- {offer.title} ({_appellation(offer)})", f"  {offer.summary}"]
    return lines


def _profile_lines(account, user, report):
    """One account: its candidates tonight, all its scores from the best, and the cost of its scoring."""
    scores = list(Score.objects.filter(user=user).select_related("offer__source").order_by("-score", "offer_id"))
    spread = ", ".join(f"{label} : {format_number(sum(score.category == value for score in scores))}"
                       for value, label in Score.Category.choices)
    lines = ["", f"== {account.label} ({account.email})",
             f"Candidats : {format_number(report.requested if report else 0)}. "
             f"Notes : {format_number(len(scores))} ({spread})."]
    if report is not None:
        lines.append(f"Coût de la notation : {_dollars(cost(SCORING_MODEL, report.tokens))}.")
    for score in scores:
        offer = score.offer
        exploration = ", piste à côté" if score.is_exploration else ""
        place = offer.city or offer.location or "lieu inconnu"
        lines += [f"- {score.score}/10, {score.get_category_display()}{exploration} : {offer.title}, "
                  f"{offer.employer or 'employeur inconnu'}, {place}, {offer.source.name}",
                  f"  {score.reason}"]
    return lines


def _tokens(tokens):
    return (f"{format_number(tokens['input_tokens'])} en entrée, {format_number(tokens['output_tokens'])} en "
            f"sortie, {_plural(tokens['cache_read_tokens'], 'lu', 'lus')} en cache, "
            f"{_plural(tokens['cache_creation_tokens'], 'écrit', 'écrits')} en cache")


def _cost_lines(night_report):
    """Tokens and dollars of this run, per model, then per person and per offer scored."""
    runs = [(PREPASS_MODEL, night_report.summaries)] + [(SCORING_MODEL, report) for report in night_report.people]
    by_model = {model: dict.fromkeys(ScoringRun.USAGE_FIELDS, 0) for model in (PREPASS_MODEL, SCORING_MODEL)}
    for model, report in runs:
        for name, count in (report.tokens if report else {}).items():
            by_model[model][name] += count
    lines = ["", "== Coût de cet essai (tarifs du 13/10/2026)"]
    total = Decimal(0)
    for model, tokens in by_model.items():
        amount = cost(model, tokens)
        total += amount
        lines.append(f"{MODEL_NAMES[model]} : {_tokens(tokens)}, {_dollars(amount)}")
    scored = sum(report.written for report in night_report.people)
    lines += [f"Total : {_dollars(total)}", f"Par personne : {_dollars(total / len(ACCOUNTS))}",
              f"Par offre notée : {_dollars(total / scored, 4)}" if scored else "Par offre notée : aucune offre notée."]
    return lines


def _failure_lines(night_report):
    lines = ["", "== Runs en échec"]
    for report in ([night_report.summaries] if night_report.summaries else []) + night_report.people:
        if report.failed_batches:
            lines.append(f"- {report.label} : {report.state}, {_plural(report.failed_batches, 'lot', 'lots')} en "
                         f"échec, code {', '.join(sorted(report.errors))}")
    lines += [f"- compte {skipped.account} : {night.SKIP_REASONS[skipped.reason]}, personne sautée"
              for skipped in night_report.skipped]
    if len(lines) == 2:
        lines.append("Aucun run en échec.")
    return lines


class Command(BaseCommand):
    help = ("Essai réel de la nuit sur une base jetable : crée trois comptes fictifs, résume les "
            "offres et les note pour eux seuls, puis écrit un rapport à relire (résumés, notes, raisons, coût).")

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Supprime d’abord les notes et les runs des trois comptes et efface les résumés et "
                                 "les codes ROME écrits par la pré-passe, pour tout rejouer après un réglage de "
                                 "prompt.")

    def handle(self, *args, **options):
        # Every guard before the first write.
        if not is_scratch_database(settings.DATABASES["default"]["NAME"]):
            raise CommandError("Cette commande n’écrit que dans une base jetable, sous /tmp ou dans le dossier "
                               "temporaire du système. Relancez-la avec DATABASE_PATH=/tmp/tri-offres-dev.sqlite3.")
        missing = [email for email in EMAILS if email not in settings.DEV_EMAILS]
        if missing:
            raise CommandError(f"Ajoutez {', '.join(missing)} à DEV_EMAILS pour cette commande : le jeton Max ne "
                               "sert que les adresses de DEV_EMAILS.")
        if not JobOffer.objects.exists():
            raise CommandError("La base ne contient aucune offre : lancez d’abord manage.py collecte.")
        if options["reset"]:
            counts = reset(EMAILS)
            self.stdout.write(f"Remise à zéro : {_plural(counts.scores, 'note', 'notes')}, "
                              f"{_plural(counts.runs, 'run', 'runs')}, "
                              f"{_plural(counts.summaries, 'résumé', 'résumés')} et "
                              f"{_plural(counts.codes, 'code ROME', 'codes ROME')} effacés.\n")
        now = timezone.now()
        profiles = _write_accounts(now)
        night_report = night.run_night(now, profiles=profiles)
        people = {report.account: report for report in night_report.people}
        lines = night.format_report(night_report) + _prepass_lines()
        for account, profile in zip(ACCOUNTS, profiles):
            lines += _profile_lines(account, profile.user, people.get(profile.user_id))
        lines += _cost_lines(night_report) + _failure_lines(night_report)
        self.stdout.write("\n".join(lines))

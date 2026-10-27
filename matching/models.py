from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

import llm
from jobs.models import JobOffer

SCORE_MIN = 0
SCORE_MAX = 10


class Score(models.Model):
    # Categories of the mail sections.
    class Category(models.TextChoices):
        TO_REVIEW = "to_review", "À regarder"
        MAYBE = "maybe", "Peut-être"
        DISCARDED = "discarded", "Écartée"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="scores",
                             verbose_name="utilisateur")
    offer = models.ForeignKey(JobOffer, on_delete=models.CASCADE, related_name="scores", verbose_name="offre")
    # Whole points, SCORE_MIN irrelevant, SCORE_MAX perfect match.
    score = models.PositiveSmallIntegerField("note")
    reason = models.CharField("raison", max_length=500, blank=True)
    category = models.CharField("catégorie", max_length=20, choices=Category)
    is_exploration = models.BooleanField("exploration", default=False)
    sent_on = models.DateField("envoyée le", null=True, blank=True)

    class Meta:
        verbose_name = "note"
        verbose_name_plural = "notes"
        constraints = [
            models.UniqueConstraint(fields=["user", "offer"], name="score_unique_per_user_offer"),
            models.CheckConstraint(condition=models.Q(score__gte=SCORE_MIN, score__lte=SCORE_MAX),
                                   name="score_within_bounds"),
        ]

    def __str__(self):
        return f"{self.score}/{SCORE_MAX} {self.offer} pour {self.user}"


class FeedbackQuerySet(models.QuerySet):
    def current_verdict(self, user, offer):
        """The user's verdict on the offer: latest non-cancelled row, or None."""
        return (self.filter(user=user, offer=offer, cancelled=False)
                .order_by("-created_at", "-pk").values_list("verdict", flat=True).first())


class Verdict(models.TextChoices):
    YES = "yes", "Oui"
    MAYBE = "maybe", "Peut-être"
    NO = "no", "Non"


class Feedback(models.Model):
    """One swipe. Several rows per user and offer keep the undo/redo history."""

    # Module level so Meta can reach it; Feedback.Verdict stays the public name.
    Verdict = Verdict

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="feedback",
                             verbose_name="utilisateur")
    offer = models.ForeignKey(JobOffer, on_delete=models.CASCADE, related_name="feedback", verbose_name="offre")
    verdict = models.CharField("avis", max_length=10, choices=Verdict)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    cancelled = models.BooleanField("annulé", default=False)

    objects = FeedbackQuerySet.as_manager()

    class Meta:
        verbose_name = "avis"
        verbose_name_plural = "avis"
        constraints = [
            models.CheckConstraint(condition=models.Q(verdict__in=Verdict.values), name="feedback_valid_verdict"),
        ]

    def __str__(self):
        return f"{self.get_verdict_display()} : {self.offer}"


class Application(models.Model):
    """The person's follow-up of an offer they said yes to, in « Mes offres ». Never read by Claude: the note is
    theirs. No row reads as TO_APPLY; the row stays when the verdict changes."""

    class Status(models.TextChoices):
        TO_APPLY = "to_apply", "À postuler"
        APPLIED = "applied", "Candidature envoyée"
        INTERVIEW = "interview", "Entretien"
        OFFER = "offer", "Offre reçue"
        ACCEPTED = "accepted", "Acceptée"
        REJECTED = "rejected", "Refusée"

    NOTE_MAX = 1000

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="applications",
                             verbose_name="utilisateur")
    offer = models.ForeignKey(JobOffer, on_delete=models.CASCADE, related_name="applications", verbose_name="offre")
    status = models.CharField("statut", max_length=20, choices=Status, default=Status.TO_APPLY)
    note = models.CharField("note", max_length=NOTE_MAX, blank=True)
    updated_at = models.DateTimeField("mis à jour le", auto_now=True)

    class Meta:
        verbose_name = "candidature"
        verbose_name_plural = "candidatures"
        constraints = [
            models.UniqueConstraint(fields=["user", "offer"], name="application_unique_per_user_offer"),
            models.CheckConstraint(condition=models.Q(status__in=["to_apply", "applied", "interview", "offer",
                                                                  "accepted", "rejected"]),
                                   name="application_valid_status"),
        ]


class Delivery(models.Model):
    """One daily email sent to a user."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="deliveries",
                             verbose_name="utilisateur")
    date = models.DateField("date")
    offer_count = models.PositiveSmallIntegerField("nombre d'offres")
    resend_id = models.CharField("identifiant Resend", max_length=100, blank=True)

    class Meta:
        verbose_name = "envoi"
        verbose_name_plural = "envois"
        constraints = [
            models.UniqueConstraint(fields=["user", "date"], name="delivery_unique_per_user_day"),
        ]

    def __str__(self):
        return f"{self.date} pour {self.user}"


class ScoringRunQuerySet(models.QuerySet):
    def fail_stale(self, now):
        """The runs of this queryset still running after ScoringRun.STALE_AFTER, failed as stale (their thread or
        command died). Returns how many."""
        return self.filter(state=ScoringRun.State.RUNNING, started_at__lt=now - ScoringRun.STALE_AFTER).update(
            state=ScoringRun.State.FAILED, error=ScoringRun.Error.STALE, finished_at=now)


class ScoringRun(models.Model):
    """One scoring job: a night or an on-demand run for one person, or the night's pre-pass for no one (decision
    Q22). Counts and short codes only, never an offer's content nor Claude's output."""

    class Kind(models.TextChoices):
        NIGHT = "night", "Nuit"
        ON_DEMAND = "on_demand", "À la demande"
        SUMMARIES = "summaries", "Résumés"
        LEARNED = "learned", "Synthèse apprise"

    class State(models.TextChoices):
        RUNNING = "running", "En cours"
        DONE = "done", "Terminé"
        PARTIAL = "partial", "Partiel"
        FAILED = "failed", "En échec"
        REFUSED = "refused", "Refusé (plafond)"

    class Error:
        """The short codes of `error`, never a message nor a content."""

        ENGINE = "engine"  # the engine failed (LLMError)
        REFUSED = "refused"  # the engine refused the call (EngineRefused)
        CRASH = "crash"  # an unexpected error
        STALE = "stale"  # still running after STALE_AFTER
        CAP = "cap"  # refused by the site's on-demand cap
        TRIAL_OVER = "trial_over"  # the person's trial ended between the request and the execution of the run
        PRIORITY = (CRASH, REFUSED, ENGINE)  # which code a run with several failed batches keeps

        @classmethod
        def of(cls, error):
            """The code of a batch that raised `error`."""
            if isinstance(error, llm.EngineRefused):
                return cls.REFUSED
            return cls.ENGINE if isinstance(error, llm.LLMError) else cls.CRASH

    # A run still running after this died with its thread or its command (decision 8).
    STALE_AFTER = timedelta(minutes=10)

    # Token counters of the run: {field: attribute of llm.Usage}.
    USAGE_FIELDS = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cache_read_tokens": "cache_read_input_tokens",
        "cache_creation_tokens": "cache_creation_input_tokens",
    }

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="scoring_runs", verbose_name="utilisateur")
    kind = models.CharField("type", max_length=20, choices=Kind)
    state = models.CharField("état", max_length=20, choices=State, default=State.RUNNING)
    offers_requested = models.PositiveIntegerField("offres demandées", default=0)
    offers_scored = models.PositiveIntegerField("offres notées", default=0)
    input_tokens = models.PositiveIntegerField("jetons en entrée", default=0)
    output_tokens = models.PositiveIntegerField("jetons en sortie", default=0)
    cache_read_tokens = models.PositiveIntegerField("jetons lus en cache", default=0)
    cache_creation_tokens = models.PositiveIntegerField("jetons écrits en cache", default=0)
    started_at = models.DateTimeField("début", default=timezone.now, db_index=True)
    finished_at = models.DateTimeField("fin", null=True, blank=True)
    # A short code of Error, never a message nor a content.
    error = models.CharField("erreur", max_length=40, blank=True)

    objects = ScoringRunQuerySet.as_manager()

    class Meta:
        verbose_name = "run de notation"
        verbose_name_plural = "runs de notation"
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(kind="summaries", user__isnull=True)
                           | (~models.Q(kind="summaries") & models.Q(user__isnull=False))),
                name="scoringrun_user_unless_summaries"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} du {timezone.localtime(self.started_at):%d/%m/%Y %H:%M}"

    def add_usage(self, usage):
        """Adds the four counts of an llm.Usage, without saving."""
        for field, attribute in self.USAGE_FIELDS.items():
            setattr(self, field, getattr(self, field) + getattr(usage, attribute))

    def save_if_exists(self, update_fields=None):
        """save(), but as an UPDATE only: a run whose account was deleted meanwhile went with it (cascade), and
        must neither raise nor be inserted again."""
        fields = ([self._meta.get_field(name) for name in update_fields] if update_fields
                  else [field for field in self._meta.concrete_fields if not field.primary_key])
        values = {field.attname: getattr(self, field.attname) for field in fields}
        type(self).objects.filter(pk=self.pk).update(**values)

    def record_usage(self, usage):
        """Adds the four counts of an llm.Usage and saves them."""
        self.add_usage(usage)
        self.save_if_exists(list(self.USAGE_FIELDS))

    def finish(self, state, error=""):
        self.state = state
        self.error = error
        self.finished_at = timezone.now()
        self.save_if_exists()

    def finish_from(self, failed, total, errors):
        """Final state from its batches: done when none of `total` failed, failed when all did, partial otherwise;
        the code is the first of Error.PRIORITY among `errors` (the codes of the failed batches)."""
        if failed == 0:
            self.finish(self.State.DONE)
            return
        error = next((code for code in self.Error.PRIORITY if code in errors), self.Error.ENGINE)
        self.finish(self.State.FAILED if failed >= total else self.State.PARTIAL, error)

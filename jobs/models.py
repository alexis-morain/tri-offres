from datetime import timedelta
from typing import NamedTuple

from django.conf import settings
from django.db import models, transaction

# Retention (legal guardrails): the full text goes 7 days after collection, scored or not; the offer goes after
# 45 days or once expired, unless it carries a swipe feedback (cancelled or not), which the learning needs.
FULL_TEXT_DAYS = 7
OFFER_DAYS = 45


class Source(models.Model):
    class Kind(models.TextChoices):
        MODULE = "module", "Module maison"
        ATS_FEED = "ats_feed", "Flux ATS"
        JSONLD = "jsonld", "JSON-LD"
        ACTOR = "actor", "Actor"

    class State(models.TextChoices):
        ACTIVE = "active", "Active"
        FAILING = "failing", "En échec"
        DISABLED = "disabled", "Désactivée"

    name = models.CharField("nom", max_length=200)
    kind = models.CharField("type", max_length=20, choices=Kind)
    # Codes of onboarding/rome.DOMAINS. Empty means the source serves every person.
    domains = models.JSONField("domaines servis", default=list, blank=True)
    # Set when a user added their own careers page. Offers are shared, so the source outlives its owner.
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="sources", verbose_name="propriétaire")
    url = models.URLField("adresse", blank=True)
    state = models.CharField("état", max_length=20, choices=State, default=State.ACTIVE)
    # Key of the source in the actor (wttj, hellowork...), empty for the other kinds.
    actor_source = models.CharField("source de l’actor", max_length=50, blank=True, default="")
    # Kept by the import: two nights in a row without an offer turn the source to FAILING.
    empty_runs = models.PositiveSmallIntegerField("nuits sans offre", default=0)
    last_run_at = models.DateTimeField("dernière collecte", null=True, blank=True)
    created_at = models.DateTimeField("créée le", auto_now_add=True)

    class Meta:
        verbose_name = "source"
        verbose_name_plural = "sources"
        constraints = [
            models.UniqueConstraint(fields=["actor_source"], condition=~models.Q(actor_source=""),
                                    name="source_unique_actor_source"),
        ]

    def __str__(self):
        return self.name


class PurgeCounts(NamedTuple):
    texts: int  # full texts blanked this run
    deleted: int  # offers deleted this run
    kept: int  # every stale offer kept for its feedback, the earlier nights' included


class JobOfferQuerySet(models.QuerySet):
    def purge_full_text(self):
        """Blank the full text once scoring is done (legal guardrail). Returns the number of offers purged."""
        return self.exclude(full_text="").update(full_text="")

    def stale(self, now):
        """Offers past retention: collected more than OFFER_DAYS ago, or expired."""
        return self.filter(models.Q(collected_at__lt=now - timedelta(days=OFFER_DAYS)) | models.Q(expires_at__lt=now))

    def full_text_expired(self, now):
        """Offers whose full text must go: collected more than FULL_TEXT_DAYS ago, or stale and kept for feedback."""
        limit = now - timedelta(days=FULL_TEXT_DAYS)
        return self.filter(models.Q(collected_at__lt=limit) | models.Q(pk__in=self.stale(now).values("pk")))

    def purge(self, now, dry_run=False):
        """The nightly purge: deletes stale offers without feedback (their scores cascade), then blanks the due
        full texts of the remaining ones. `dry_run` counts the same numbers without writing. `kept` is the total of
        stale offers kept for their feedback, not only the ones that went stale tonight."""
        stale = self.stale(now)
        doomed = stale.filter(feedback__isnull=True)
        kept = stale.filter(feedback__isnull=False).distinct().count()
        if dry_run:
            texts = self.full_text_expired(now).exclude(full_text="").exclude(pk__in=doomed.values("pk")).count()
            return PurgeCounts(texts, doomed.count(), kept)
        with transaction.atomic():
            deleted = doomed.delete()[1].get(self.model._meta.label, 0)
            texts = self.full_text_expired(now).purge_full_text()
        return PurgeCounts(texts, deleted, kept)


class JobOffer(models.Model):
    """An offer collected once and shared by every user. Fields follow the output schema of the actor."""

    class Contract(models.TextChoices):
        CDI = "CDI", "CDI"
        CDD = "CDD", "CDD"
        STAGE = "STAGE", "Stage"
        ALTERNANCE = "ALTERNANCE", "Alternance"
        INTERIM = "INTERIM", "Intérim"
        FREELANCE = "FREELANCE", "Freelance"
        VIE = "VIE", "VIE"
        VIA = "VIA", "VIA"
        SERVICE_CIVIQUE = "SERVICE_CIVIQUE", "Service civique"
        VOLONTARIAT = "VOLONTARIAT", "Volontariat"
        FONCTION_PUBLIQUE = "FONCTION_PUBLIQUE", "Fonction publique"
        AUTRE = "AUTRE", "Autre"

    class Remote(models.TextChoices):
        FULL = "full", "Télétravail complet"
        PARTIAL = "partial", "Hybride"
        NONE = "none", "Sur place"

    class SalaryPeriod(models.TextChoices):
        YEAR = "year", "par an"
        MONTH = "month", "par mois"
        WEEK = "week", "par semaine"
        DAY = "day", "par jour"
        HOUR = "hour", "de l’heure"

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="offers", verbose_name="source")
    external_id = models.CharField("identifiant externe", max_length=255)
    title = models.CharField("titre", max_length=300)
    # jobs.text.fold(title): the keywords filter searches whole words in it (jobs/filters.py).
    title_folded = models.CharField("titre sans accents", max_length=300, blank=True)
    employer = models.CharField("employeur", max_length=200, blank=True)
    location = models.CharField("lieu", max_length=200, blank=True)
    # French label of contract_type, for display; jobs/filters.py filters on contract_type, the actor's value.
    contract = models.CharField("contrat", max_length=100, blank=True)
    contract_type = models.CharField("type de contrat", max_length=20, choices=Contract, blank=True)
    published_at = models.DateTimeField("publiée le", null=True, blank=True)
    expires_at = models.DateTimeField("expire le", null=True, blank=True)
    url = models.URLField("adresse", max_length=1000)
    apply_url = models.URLField("adresse de candidature", max_length=1000, blank=True)
    # Written by the pre-pass of step 5 (jobs/summaries.py) and kept until the offer goes.
    summary = models.TextField("résumé", blank=True)
    # When the pre-pass handled the offer in a successful answer, its summary accepted or refused: never sent again.
    summary_written_at = models.DateTimeField("pré-passe faite le", null=True, blank=True)
    # Kept only for the scoring night, then purged.
    full_text = models.TextField("texte complet", blank=True)
    city = models.CharField("ville", max_length=200, blank=True)
    postal_code = models.CharField("code postal", max_length=10, blank=True)
    insee = models.CharField("code INSEE", max_length=5, blank=True)
    # Without a commune, a postal code of jobs/data/communes.json.gz the actor or the text of the place gives: the
    # distance filter then reads its communes (the design plan, decision 2).
    zone_postal_code = models.CharField("code postal de la zone", max_length=5, blank=True)
    # A code of jobs/data/communes.json.gz, or "": the commune's, else the actor's, else read in the text of the place
    # (jobs.geo.place_for).
    department_code = models.CharField("code du département", max_length=3, blank=True)
    department = models.CharField("département", max_length=100, blank=True)
    region = models.CharField("région", max_length=100, blank=True)
    # The department's region, else the one the actor or the text of the place names; "" when unknown.
    region_code = models.CharField("code de la région", max_length=2, blank=True)
    country = models.CharField("pays", max_length=2, blank=True)  # ISO 3166
    remote_type = models.CharField("télétravail", max_length=10, choices=Remote, blank=True)
    part_time = models.BooleanField("temps partiel", null=True, blank=True)
    salary_min = models.DecimalField("salaire minimum", max_digits=12, decimal_places=2, null=True, blank=True)
    salary_max = models.DecimalField("salaire maximum", max_digits=12, decimal_places=2, null=True, blank=True)
    salary_currency = models.CharField("devise", max_length=3, blank=True)
    salary_period = models.CharField("période du salaire", max_length=10, choices=SalaryPeriod, blank=True)
    daily_rate_min = models.DecimalField("TJM minimum", max_digits=12, decimal_places=2, null=True, blank=True)
    daily_rate_max = models.DecimalField("TJM maximum", max_digits=12, decimal_places=2, null=True, blank=True)
    # Attribution some sources require next to their offers.
    source_license = models.CharField("licence de la source", max_length=300, blank=True)
    language = models.CharField("langue", max_length=2, blank=True)  # ISO 639-1
    # ROME job sheet whose appellations matched the title, else the pre-pass's; empty when none did.
    rome_code = models.CharField("code ROME", max_length=5, blank=True)
    # True when the code came from the pre-pass (the appellation Claude gave), never over a code found by the title.
    rome_by_claude = models.BooleanField("code ROME par Claude", default=False)
    # jobs.keys.match_key(): the same offer on two sources shares it.
    match_key = models.CharField("clé de rapprochement", max_length=255, blank=True, db_index=True)
    collected_at = models.DateTimeField("collectée le", auto_now_add=True, db_index=True)

    objects = JobOfferQuerySet.as_manager()

    class Meta:
        verbose_name = "offre"
        verbose_name_plural = "offres"
        constraints = [
            models.UniqueConstraint(fields=["source", "external_id"], name="joboffer_unique_per_source"),
        ]

    def __str__(self):
        return f"{self.title} ({self.employer})"

    @property
    def origin_url(self):
        """The offer's page at its source, linked from every offer card: never apply_url, the source keeps its
        traffic."""
        return self.url

    @property
    def attribution(self):
        """Shown next to the offer when its source requires it."""
        return self.source_license

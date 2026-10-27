from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from accounts import trial


def normalize_email(email):
    return (email or "").strip().lower()


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **fields):
        email = normalize_email(email)
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=email, **fields)
        if password:
            user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **fields):
        fields.update(is_staff=True, is_superuser=True)
        return self.create_user(email, password, **fields)

    def get_by_natural_key(self, email):
        return self.get(email=normalize_email(email))

    def mailable(self, now=None):
        """Users who get the daily email: an active status and, outside the beta, a trial not over at `now` (default:
        the real time). The one gate of the sending and of every scoring that costs a call (accounts/trial.py): a
        missing date is served, the exact end is not."""
        users = self.filter(is_active=True, status=User.Status.ACTIVE)
        if trial.enabled():
            users = users.exclude(trial_ends_at__lte=now or timezone.now())
        return users


class User(AbstractBaseUser, PermissionsMixin):
    class Status(models.TextChoices):
        ACTIVE = "active", "Actif"
        PAUSED = "paused", "En pause"
        UNSUBSCRIBED = "unsubscribed", "Désinscrit"

    email = models.EmailField("adresse mail", unique=True)
    date_joined = models.DateTimeField("inscrit le", default=timezone.now)
    trial_ends_at = models.DateTimeField("fin d'essai", null=True, blank=True)
    # status is the person's mailing choice; UserManager.mailable() decides whether the daily email goes out (the
    # status and, outside the beta, the end of the trial).
    # is_active keeps Django's meaning: may log in (an inactive user is never mailed either).
    status = models.CharField("statut d'envoi", max_length=20, choices=Status, default=Status.ACTIVE)
    is_active = models.BooleanField("actif", default=True)
    is_staff = models.BooleanField("équipe", default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "utilisateur"
        verbose_name_plural = "utilisateurs"
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
        ]

    def __str__(self):
        return self.email

    def clean(self):
        super().clean()
        self.email = normalize_email(self.email)

    def save(self, *args, **kwargs):
        # Covers every path that goes through save(): manager, plain create(), admin.
        # Paths that skip save() (bulk_create, update) are caught by the Lower(email) constraint.
        self.email = normalize_email(self.email)
        # Passwordless by design: people sign in by magic link. Only admins get a password.
        if not self.password:
            self.set_unusable_password()
        super().save(*args, **kwargs)


class SummaryStatus(models.TextChoices):
    NONE = "", "Pas encore rédigée"
    READY = "ready", "Prête"
    PENDING = "pending", "À rédiger cette nuit"
    STALE = "stale", "Réponses changées depuis"


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile", verbose_name="utilisateur")
    # Codes of the ROME domains searched, validated by onboarding/rome.normalize_domains.
    domains = models.JSONField("domaines", default=list, blank=True)
    # Schema documented and validated in onboarding/criteria.py.
    hard_criteria = models.JSONField("critères éliminatoires", default=dict, blank=True)
    # Schema documented and validated in onboarding/preferences.py.
    weighted_preferences = models.JSONField("préférences pondérées", default=dict, blank=True)
    seek_text = models.TextField("ce que la personne cherche", blank=True)
    refuse_text = models.TextField("ce que la personne refuse", blank=True)
    # Profile extracted from the CV, schema in onboarding/cv.py. Neither the file nor its text is ever stored.
    cv_profile = models.JSONField("profil extrait du CV", default=dict, blank=True)
    # When the CV was read, for "Mes données". Unchanged by a hand edit of the list, cleared with it.
    cv_processed_at = models.DateTimeField("CV lu le", null=True, blank=True)
    # "Voici ce que j’ai compris" (onboarding/summary.py): sentences and questions written by Claude, each with the
    # ids of the answers it comes from, and the person's own version of the text, shown first when not blank.
    summary_sentences = models.JSONField("synthèse : phrases", default=list, blank=True)
    summary_questions = models.JSONField("synthèse : questions", default=list, blank=True)
    summary_text = models.TextField("synthèse : texte modifié à la main", blank=True)
    summary_status = models.CharField("synthèse : état", max_length=10, choices=SummaryStatus,
                                      default=SummaryStatus.NONE, blank=True)
    summary_generated_at = models.DateTimeField("synthèse rédigée le", null=True, blank=True)
    summary_validated_at = models.DateTimeField("synthèse validée le", null=True, blank=True)
    # The weekly summary of what the sorts taught (step 5), distinct from the summary above.
    learned_summary = models.TextField("synthèse apprise", blank=True)
    # Last write of the text above, by Claude (matching/learned.py) or by the person (then _own: no AI label).
    learned_summary_at = models.DateTimeField("synthèse apprise écrite le", null=True, blank=True)
    learned_summary_own = models.BooleanField("synthèse apprise écrite par la personne", default=False)
    career_change = models.BooleanField("en reconversion", default=False)
    previous_job = models.CharField("métier actuel ou dernier", max_length=120, blank=True)
    # Slug of the furthest questionnaire screen validated (onboarding/flow.py). Blank: nothing validated yet.
    onboarding_step = models.CharField("dernier écran validé", max_length=40, blank=True)
    onboarding_completed_at = models.DateTimeField("questionnaire terminé le", null=True, blank=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        verbose_name = "profil"
        verbose_name_plural = "profils"

    def __str__(self):
        return f"Profil de {self.user}"


class LoginCode(models.Model):
    """One accepted request for a sign-in code and link. Neither is stored in clear, only their HMAC-SHA256.

    Active for the code: not used, not replaced, not expired and fewer than MAX_ATTEMPTS wrong codes.
    Active for the link: not used, not replaced, not expired; wrong codes do not block the link.
    """

    email = models.EmailField("adresse mail", db_index=True)
    code_hash = models.CharField("empreinte du code", max_length=64)
    token_hash = models.CharField("empreinte du lien", max_length=64, unique=True)
    created_at = models.DateTimeField("demandé le", default=timezone.now, db_index=True)
    expires_at = models.DateTimeField("expire le")
    attempts = models.PositiveSmallIntegerField("essais ratés", default=0)
    used_at = models.DateTimeField("utilisé le", null=True, blank=True)
    # Set when a newer code replaces this one. Too many wrong codes block the code through `attempts` only.
    invalidated_at = models.DateTimeField("remplacé le", null=True, blank=True)
    # Set once the mail backend accepted the code or waiting-list mail; the site's daily cap counts these.
    sent_at = models.DateTimeField("mail envoyé le", null=True, blank=True, db_index=True)
    # IPv4 as is, IPv6 cut to its /64 network: the per-IP limit counts by this value.
    requested_ip = models.GenericIPAddressField("IP de la demande", null=True, blank=True)

    class Meta:
        verbose_name = "code de connexion"
        verbose_name_plural = "codes de connexion"
        indexes = [models.Index(fields=["requested_ip", "created_at"], name="accounts_lc_ip_created")]

    def __str__(self):
        return f"Code pour {self.email}"


class AdminLoginFailure(models.Model):
    """One failed password attempt (the admin's form is the only place a password is asked), for the limit of
    accounts/throttle.py. Neither the password nor the address is kept: the address only as a keyed hash. Purged at
    7 days with the login codes."""

    created_at = models.DateTimeField("le", default=timezone.now, db_index=True)
    # IPv4 as is, IPv6 cut to its /64 network (auth.ip_bucket); empty when the request had no usable IP.
    ip_bucket = models.GenericIPAddressField("IP de la tentative", null=True, blank=True)
    # HMAC-SHA256 of the normalized address typed (account or not); empty when none was typed.
    address_hash = models.CharField("empreinte de l’adresse saisie", max_length=64, blank=True)

    class Meta:
        verbose_name = "échec de mot de passe"
        verbose_name_plural = "échecs de mot de passe"
        indexes = [models.Index(fields=["ip_bucket", "created_at"], name="accounts_alf_ip_created"),
                   models.Index(fields=["address_hash", "created_at"], name="accounts_alf_addr_created")]

    def __str__(self):
        return f"Échec du {self.created_at:%d/%m/%Y %H:%M}"


class WaitlistEntry(models.Model):
    email = models.EmailField("adresse mail", unique=True)
    created_at = models.DateTimeField("inscrit le", default=timezone.now)

    class Meta:
        verbose_name = "inscription en liste d’attente"
        verbose_name_plural = "liste d’attente"

    def __str__(self):
        return self.email


class OnboardingEvent(models.Model):
    """One step of the sign-up funnel, read in the admin. `code_requested` carries no user and no details."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name="onboarding_events",
                             verbose_name="utilisateur")
    name = models.CharField("étape", max_length=50)
    created_at = models.DateTimeField("le", default=timezone.now, db_index=True)
    details = models.JSONField("détails", default=dict, blank=True)

    class Meta:
        verbose_name = "événement d’inscription"
        verbose_name_plural = "événements d’inscription"

    def __str__(self):
        return self.name

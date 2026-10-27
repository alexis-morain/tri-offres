"""Django settings, read from environment variables only."""

from pathlib import Path

from config.env import (email_backend, env, env_bool, env_hostname, env_https_url, env_int, env_list, env_proxy_url,
                        env_strict_bool, publisher_identity, secret_key, security_settings)
from llm.sdk_loggers import SDK_LOGGERS

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = env_bool("DJANGO_DEBUG")
SECRET_KEY = secret_key(DEBUG)
# Old keys kept after a rotation so sessions survive it. Login codes and links are hashed with SECRET_KEY
# only, so a rotation invalidates those in flight (accepted in the sign-in contract, section 9.2).
SECRET_KEY_FALLBACKS = env_list("DJANGO_SECRET_KEY_FALLBACKS")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
CSRF_FAILURE_VIEW = "accounts.views.csrf_failure"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.forms",
    "ui",
    "accounts",
    "jobs",
    "matching",
    "mail",
    "onboarding",
    "llm",
    "supervision",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # Before the session: a refused /admin request creates none (config/admin_access.py).
    "config.admin_access.CloudflareAccessGuard",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
FORM_RENDERER = "ui.renderers.TriFormRenderer"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "ui.context_processors.beta",
            ],
        },
    },
]

DATABASE_PATH = Path(env("DATABASE_PATH", str(BASE_DIR / "data" / "tri-offres.sqlite3")))
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
# One lock file per task slot (supervision.locks), next to the database.
TASK_LOCK_DIR = Path(env("TASK_LOCK_DIR", str(DATABASE_PATH.parent / "locks")))
# The account journal (accounts/journal.py): outside the database, so a restored backup can be brought up to date.
# Next to it, Litestream replicates the database only.
ACCOUNT_JOURNAL_PATH = Path(env("ACCOUNT_JOURNAL_PATH", str(DATABASE_PATH.parent / "journal-comptes.txt")))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATABASE_PATH,
        "OPTIONS": {
            # WAL is required by Litestream; IMMEDIATE avoids "database is locked" on write upgrades.
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;",
            "transaction_mode": "IMMEDIATE",
        },
    }
}

AUTH_USER_MODEL = "accounts.User"
# Django's backend behind the limit on password attempts (accounts/throttle.py). The only one: accounts.auth.BACKEND,
# which the sign-in by code records in the session, is this path.
AUTHENTICATION_BACKENDS = ["accounts.backends.ThrottledModelBackend"]
# Sign-in pages compute their own destination; the `next` parameter Django adds here is never followed.
LOGIN_URL = "accounts:home"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Sign-in by code and link (the design plan, section 9).
# Absolute links in mails are built from SITE_URL, never from the request's Host header.
SITE_URL = env("SITE_URL", "http://localhost:8000").strip().rstrip("/")
# During the beta the trial does not exist: no end date is set and none is read (accounts/trial.py). Read strictly:
# a typo stops the start instead of cutting people off.
BETA = env_strict_bool("BETA", True)
BETA_CAPACITY = env_int("BETA_CAPACITY", 80)
# Login mails (codes and waiting list) per rolling 24 hours, for the whole site. Resend's free plan stops at 100.
LOGIN_MAIL_DAILY_CAP = env_int("LOGIN_MAIL_DAILY_CAP", 60)
# Read CF-Connecting-IP only behind the Cloudflare tunnel; anywhere else the header is client-controlled.
TRUST_CF_CONNECTING_IP = env_bool("TRUST_CF_CONNECTING_IP")

# HTTPS settings, set when SITE_URL is https:// (config/env.py, security_settings): Secure and __Host- cookies, the
# site's origin trusted for CSRF, HSTS, the proxy header believed behind the tunnel only.
globals().update(security_settings(SITE_URL, TRUST_CF_CONNECTING_IP, CSRF_TRUSTED_ORIGINS))
# Cloudflare Access in front of /admin (config/admin_access.py), active when SITE_URL is https://: the team's host
# (<team>.cloudflareaccess.com) and the application's audience tag. Either one empty closes /admin.
ADMIN_ACCESS_TEAM_DOMAIN = env_hostname("ADMIN_ACCESS_TEAM_DOMAIN")
ADMIN_ACCESS_AUD = env("ADMIN_ACCESS_AUD", "").strip()
# "oauth" runs Claude on a developer's Max token: then no mail may go to anyone outside DEV_EMAILS.
CLAUDE_ENGINE = env("CLAUDE_ENGINE", "").strip().lower()
DEV_EMAILS = [email.lower() for email in env_list("DEV_EMAILS")]
# `claude` binary for the oauth engine (llm/oauth.py).
CLAUDE_BIN = env("CLAUDE_BIN", "claude").strip() or "claude"

# CV uploads (the design plan, 8.1.1): files stay in memory, 5 MB at most, one per request, so no
# upload is ever written to a temporary file. CV analyses per rolling 24 hours for the whole site.
FILE_UPLOAD_HANDLERS = ["onboarding.uploads.CappedMemoryUploadHandler"]
DATA_UPLOAD_MAX_NUMBER_FILES = 1
CV_DAILY_CAP = env_int("CV_DAILY_CAP", 40)
# Summary calls to Claude (onboarding/summary.py) per rolling 24 hours for the whole site.
SUMMARY_DAILY_CAP = env_int("SUMMARY_DAILY_CAP", 150)
# The night's scoring (matching/night.py, `manage.py notation`): offers scored for the whole site per night (from
# 8 pm), offers summarized by the pre-pass per night.
NIGHT_SCORE_CAP = env_int("NIGHT_SCORE_CAP", 2000)
PREPASS_NIGHT_CAP = env_int("PREPASS_NIGHT_CAP", 2000)
# On-demand scoring runs (matching/on_demand.py) started per rolling 24 hours for the whole site.
ON_DEMAND_DAILY_CAP = env_int("ON_DEMAND_DAILY_CAP", 40)

# Collection by the actor run locally (jobs/scraper.py): its code at the
# commit of ACTOR_REF (scripts/fetch_actor.sh), its own venv, its storage kept between nights (the registry of
# offers seen lives there).
SCRAPER_DIR = Path(env("SCRAPER_DIR", "") or BASE_DIR / "vendor" / "actor")
SCRAPER_PYTHON = env("SCRAPER_PYTHON", "") or str(BASE_DIR / "vendor" / "actor-venv" / "bin" / "python")
SCRAPER_STORAGE = Path(env("SCRAPER_STORAGE", "") or BASE_DIR / "data" / "scraper")
# Past it the actor is killed and the run is TIMED-OUT.
SCRAPER_TIMEOUT_S = env_int("SCRAPER_TIMEOUT_S", 5400)
# HTTP proxy the actor goes out by (the VPN's proxy on the host), given to it as HTTPS_PROXY and HTTP_PROXY.
SCRAPER_PROXY_URL = env_proxy_url("SCRAPER_PROXY_URL")
# With it set, `collecte` refuses to run without the proxy, so the actor never goes out by the host's own IP.
SCRAPER_PROXY_REQUIRED = env_bool("SCRAPER_PROXY_REQUIRED")
# 500 is reached in 7 days by ten sources of sixteen (measurement run of 13/10, spec 4.7).
SCRAPER_MAX_RESULTS_PER_SOURCE = env_int("SCRAPER_MAX_RESULTS_PER_SOURCE", 1000)
# 15 cut ProfilCulture, HelloWork and Taleez on 18/10; the sources run side by side, under SCRAPER_TIMEOUT_S.
SCRAPER_MAX_MINUTES_PER_SOURCE = env_int("SCRAPER_MAX_MINUTES_PER_SOURCE", 30)

RESEND_API_KEY = env("RESEND_API_KEY", "")
EMAIL_BACKEND = email_backend(RESEND_API_KEY)
# Sender, reply-to (also the legal contact) and publisher name: the environment only, no personal default.
DEFAULT_FROM_EMAIL, MAIL_REPLY_TO, LEGAL_NAME = publisher_identity(DEBUG, RESEND_API_KEY)
# Technical alerts of the task slots (supervision/alerts.py) go to one person.
ALERT_EMAIL = env("ALERT_EMAIL", "").strip() or MAIL_REPLY_TO
# https://hc-ping.com/<ping key>: a secret (whoever has it can ping), never shown nor logged. Empty: no ping.
HEALTHCHECKS_PING_URL = env_https_url("HEALTHCHECKS_PING_URL")

# Publisher of the legal notice (LEGAL_NAME is read above). Address and SIRET stay out of the repository, which is
# public: a blank value hides its line.
LEGAL_ADDRESS = env("LEGAL_ADDRESS", "").strip()
LEGAL_SIRET = env("LEGAL_SIRET", "").strip()
LEGAL_EMAIL = MAIL_REPLY_TO

# Sessions: in the database, 30 days fixed from sign-in (the user_logged_in signal sets an absolute expiry,
# 12 hours for staff). Never signed_cookies.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 30 * 24 * 3600
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# The link token is a credential: keep it out of the request and CSRF warnings (HEAD on a link, a refused POST).
# Without DEBUG Django writes no 500 anywhere by itself: a console handler on django.request and django.security
# (the container's log) carries them, with the token filter on the handler, so that records of child loggers
# (django.security.csrf) and the traceback are masked too. gunicorn writes no access log (docker/gunicorn.conf.py).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact_login_tokens": {"()": "accounts.log_filters.RedactLoginTokens"}},
    "formatters": {"console": {"format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s"}},
    "handlers": {
        "console_redacted": {"class": "logging.StreamHandler", "level": "WARNING", "formatter": "console",
                             "filters": ["redact_login_tokens"]},
    },
    "loggers": {
        "django.request": {"handlers": ["console_redacted"], "level": "WARNING", "propagate": False,
                           "filters": ["redact_login_tokens"]},
        "django.security": {"handlers": ["console_redacted"], "level": "WARNING", "propagate": False},
        "django.security.csrf": {"filters": ["redact_login_tokens"]},
        # At DEBUG the Anthropic SDK and its HTTP client log whole requests, CV content included.
        **{name: {"level": "WARNING"} for name in SDK_LOGGERS},
    },
}

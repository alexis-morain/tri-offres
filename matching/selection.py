"""Which people and which offers the scoring works on (step 5): shared by the pre-pass, the night and the on-demand
scoring."""
import logging
from datetime import timedelta

from django.db.models import Q

from accounts.models import Profile, User
from jobs.filters import passing_offers
from jobs.models import FULL_TEXT_DAYS, JobOffer
from jobs.text import job_words
from matching import scoring
from matching.models import Score
from onboarding import criteria, rome

# The night scores the offers collected in the last NIGHT_WINDOW, NIGHT_PER_PERSON at most per person (decision 11).
NIGHT_WINDOW = timedelta(hours=48)
NIGHT_PER_PERSON = 40
# What selecting the offers of a profile with damaged criteria may raise: normalize() and the filters.
BROKEN_CRITERIA = (ValueError, KeyError, TypeError)
# Closeness of an offer without a ROME code to the person (the design plan, decision 1).
TITLE_WEIGHT = 3
SUMMARY_WEIGHT = 1

logger = logging.getLogger(__name__)


def active_profiles():
    """Profiles of the people who get the daily email and finished the questionnaire: the mailable ones, so under
    the api engine only those with a working OpenRouter key."""
    return (Profile.objects.filter(user__in=User.objects.mailable(), onboarding_completed_at__isnull=False)
            .select_related("user"))


def profile_criteria(profile):
    """The strict criteria of `profile`, normalized as the counter reads them; every selection goes through here.
    Raises ValueError for criteria that do not read."""
    return criteria.normalize(profile.hard_criteria)


def offers_to_summarize(now, profiles=None):
    """Offers the pre-pass still has to handle, newest first: not expired, pre-pass not done, full text still there
    (collected less than FULL_TEXT_DAYS ago), and passing the filters of at least one of `profiles` (by default the
    active ones). SQLite cannot search inside JSON: the union is made here, one query per profile. A profile whose
    criteria do not read is left out, logged by its account's key."""
    if profiles is None:
        profiles = active_profiles()
    since = now - timedelta(days=FULL_TEXT_DAYS)
    alive = Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    pending = Q(summary_written_at__isnull=True) & ~Q(full_text="") & alive
    pks = set()
    for profile in profiles:
        try:
            offers = passing_offers(profile_criteria(profile), now, profile.domains, since=since).filter(pending)
            pks.update(offers.values_list("pk", flat=True))
        except BROKEN_CRITERIA as error:
            logger.error("selection: account %s left out of the pre-pass, its criteria do not read (%s)",
                         profile.user_id, type(error).__name__)
    return JobOffer.objects.filter(pk__in=pks).order_by("-collected_at", "-pk")


def person_words(profile, normalized_criteria):
    """The words of the jobs `profile` chose (ROME labels and typed jobs), of its previous_job and of the keywords it
    requires, but the names of its cities: the place is filtered already. Not seek_text: its common words
    (« service », « public ») let unrelated offers in (step 6 plan, section 7, essai 5). Preferences that do not
    read give no job, as in the scoring's prompt."""
    weighted = profile.weighted_preferences
    jobs = scoring.labels(weighted.get("jobs")) if isinstance(weighted, dict) else []
    location = normalized_criteria.get("location") or {"cities": []}
    cities = job_words(" ".join(city["name"] for city in location["cities"]))
    keywords = (normalized_criteria.get("keywords") or {"include": []})["include"]
    return job_words(" ".join([*jobs, profile.previous_job, *keywords])) - cities


def closeness(words, title, summary):
    """How close an offer is to the person's `words`: TITLE_WEIGHT per word in its title, SUMMARY_WEIGHT per word in
    its summary."""
    return TITLE_WEIGHT * len(words & job_words(title)) + SUMMARY_WEIGHT * len(words & job_words(summary))


def candidates(profile, now, window, limit):
    """The offers to score for `profile`, `limit` at most: collected in the last `window` (the night's or the
    on-demand one), passing the person's filters, not expired, not scored for them yet. Offers with a ROME code come
    first, newest first: they are already in the person's domains (decision 12). Then the others, the closest to the
    person's words first, the newest at equal closeness; one sharing no word is dropped, unless the person gave no
    word at all (step 6 plan, decision 2) or its source serves the person's domains (plan of 28/10, chantier 2).
    One offer per non-empty match_key (the first in this order), none whose match_key is already scored for them on
    another offer (decision 6). Raises one of BROKEN_CRITERIA for criteria that do not read."""
    scored = Score.objects.filter(user_id=profile.user_id)
    alive = Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    normalized = profile_criteria(profile)
    offers = (passing_offers(normalized, now, profile.domains, since=now - window)
              .filter(alive)
              .exclude(pk__in=scored.values("offer_id"))
              .exclude(match_key__in=scored.exclude(offer__match_key="").values("offer__match_key"))
              .select_related("source").order_by("-collected_at", "-pk"))
    words = person_words(profile, normalized)
    coded = Q(rome_code__regex=rome.ROME_CODE_PATTERN)
    picked, keys = [], set()

    def take(match_key):
        if match_key:
            if match_key in keys:
                return False
            keys.add(match_key)
        return True

    for offer in offers.filter(coded).iterator():
        if len(picked) == limit:
            return picked
        if take(offer.match_key):
            picked.append(offer)
    # The rest is ranked in Python on its title and summary only; sorted() keeps the newest first at equal closeness.
    # An offer of a source serving the person's domains (passing_offers let it in for that) needs no shared word.
    rows = sorted(((pk, match_key, closeness(words, title, summary), source_domains)
                   for pk, match_key, title, summary, source_domains
                   in offers.exclude(coded).values_list("pk", "match_key", "title", "summary", "source__domains")),
                  key=lambda row: -row[2])
    chosen = []
    for pk, match_key, close, source_domains in rows:
        if words and not close and not source_domains:
            continue
        if len(picked) + len(chosen) == limit:
            break
        if take(match_key):
            chosen.append(pk)
    found = offers.in_bulk(chosen)
    return picked + [found[pk] for pk in chosen]

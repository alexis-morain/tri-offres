"""Import of the actor's items into JobOffer (the design plan).

Items come from third-party sites through the actor: every field is checked, cut to its column and cleaned before it
reaches the database, and a value of an unexpected type counts as absent. `manage.py collecte` reads the dataset
and calls these functions; this module never runs the actor itself. An offer already known by (source, external id)
is neither changed nor created again.
"""
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import islice
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Count, Q

from jobs import geo, keys, rome_titles
from jobs.actor_stats import RUN_STATS_COUNTS
from jobs.domains import offer_domains
from jobs.models import JobOffer, Source
from jobs.text import fold_title
from onboarding.criteria import format_number
from onboarding.rome import DOMAINS
from supervision.redaction import redact_text

BATCH_SIZE = 500
WINDOW = timedelta(days=7)  # duplicates and the counts by source and by domain
MAX_FULL_TEXT = 50_000
MAX_AMOUNT = Decimal(10_000_000)
CENT = Decimal("0.01")
YEARS = range(2000, 2101)  # dates outside are typing errors of a site
EXCLUDED_CONTRACTS = {"VIE", "VIA"}  # plan of step 4, decision 13

# Rejection reasons, with their label in the report: the checks of parse_item in their order, then the dataset's
# items that are not objects and the offers the database refused.
REASONS = {
    "unknown_source": "source inconnue",
    "disabled_source": "source désactivée",
    "country": "hors de France",
    "contract": "VIE ou VIA",
    "url": "adresse absente ou invalide",
    "title": "titre vide",
    "title_letter": "titre sans lettre",
    "id": "identifiant absent",
    "not_object": "pas un objet",
    "unreadable": "illisible",
}

# Control characters, then the invisible and bidirectional formatting ones (Unicode Cf): zero widths, marks,
# embeddings, overrides, isolates and the byte order mark, which would reorder or hide text in the admin and mails.
_FORMATTING = "\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff"
_CONTROL = re.compile(f"[\x00-\x1f\x7f-\x9f{_FORMATTING}]")
_CONTROL_BUT_NEWLINE = re.compile(f"[\x00-\x09\x0b-\x1f\x7f-\x9f{_FORMATTING}]")
_SPACING = re.compile(r"[\t\n\v\f\r\x85]")  # control characters that separate words: a space instead
_CURRENCY = re.compile(r"[A-Z]{3}")
_LETTER = re.compile(r"[^\W\d_]")
_VALIDATE_URL = URLValidator(schemes=["http", "https"])


def _max_length(name):
    return JobOffer._meta.get_field(name).max_length


def _text(value, max_length=None, multiline=False):
    """A string of the item, control characters removed, edges stripped, cut to `max_length`; "" for any other
    type. In a multiline text the newline stays."""
    if not isinstance(value, str):
        return ""
    if multiline:
        value = _CONTROL_BUT_NEWLINE.sub("", value.replace("\r\n", "\n").replace("\t", " "))
    else:
        value = _CONTROL.sub("", _SPACING.sub(" ", value))
    return value.strip()[:max_length].strip()


def _identifier(item):
    """`job_id`, else `id`: a string, or an integer written out."""
    for name in ("job_id", "id"):
        value = item.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            try:
                value = str(value)
            except ValueError:  # more digits than Python writes out
                continue
        value = _text(value, _max_length("external_id"))
        if value:
            return value
    return ""


def _url(value):
    """The address when Django's validator takes it as http or https and it fits its column, else ""."""
    value = _text(value)
    if not value or len(value) > _max_length("url"):
        return ""
    try:
        _VALIDATE_URL(value)
    except ValidationError:
        return ""
    return value


def _date(value):
    """ISO 8601 date and time in UTC (read in UTC without a time zone); None when invalid or out of YEARS."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
        parsed = parsed.replace(tzinfo=dt_timezone.utc) if parsed.tzinfo is None else parsed.astimezone(
            dt_timezone.utc)
    except (ValueError, OverflowError):
        return None
    return parsed if parsed.year in YEARS else None


def _amount(value):
    """The number rounded to the cent when it is then positive and under MAX_AMOUNT; None otherwise."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 < amount < MAX_AMOUNT:
            return None
        amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except (ValueError, InvalidOperation):  # str() of a huge integer
        return None
    return amount if 0 < amount < MAX_AMOUNT else None


def _choice(value, choices):
    return value if isinstance(value, str) and value in choices.values else ""


def _currency(value):
    value = _text(value)
    return value if _CURRENCY.fullmatch(value) else ""


class Parsed(NamedTuple):
    source_key: str  # the item's source, "" when it has none
    offer: JobOffer | None  # unsaved; None when rejected
    reason: str = ""  # a key of REASONS when rejected


def actor_sources():
    """Sources of the actor by their key, disabled ones included (their items are rejected)."""
    return {source.actor_source: source
            for source in Source.objects.filter(kind=Source.Kind.ACTOR).exclude(actor_source="")}


def requested_sources(sources):
    """The sources asked to the actor: all but the disabled ones, by key."""
    return sorted((source for source in sources.values() if source.state != Source.State.DISABLED),
                  key=lambda source: source.actor_source)


def parse_item(item, sources):
    """The unsaved offer of an item of the actor's dataset, or the reason it is rejected."""
    source_key = _text(item.get("source"), Source._meta.get_field("actor_source").max_length)
    source = sources.get(source_key)
    if source is None:
        return Parsed(source_key, None, "unknown_source")
    if source.state == Source.State.DISABLED:
        return Parsed(source_key, None, "disabled_source")
    country = _text(item.get("location_country")).upper()
    if country and country != "FR":
        return Parsed(source_key, None, "country")
    if _text(item.get("contract_type")) in EXCLUDED_CONTRACTS:
        return Parsed(source_key, None, "contract")
    url = _url(item.get("url"))
    if not url:
        return Parsed(source_key, None, "url")
    title = _text(item.get("title"), _max_length("title"))
    if not title:
        return Parsed(source_key, None, "title")
    if not _LETTER.search(title):  # « $1 », a placeholder the site left
        return Parsed(source_key, None, "title_letter")
    external_id = _identifier(item)
    if not external_id:
        return Parsed(source_key, None, "id")

    employer = _text(item.get("company"), _max_length("employer"))
    city = _text(item.get("location_city"), _max_length("city"))
    postal_code = _text(item.get("location_postal_code"), _max_length("postal_code"))
    department_code = _text(item.get("location_department_code"), _max_length("department_code"))
    location = _text(item.get("location"), _max_length("location"))
    region = _text(item.get("location_region"), _max_length("region"))
    place = geo.place_for(location, city, postal_code, department_code, region)
    contract_type = _choice(item.get("contract_type"), JobOffer.Contract)
    part_time = item.get("part_time")
    offer = JobOffer(
        source=source, external_id=external_id, title=title, title_folded=fold_title(title), employer=employer,
        location=location,
        contract=JobOffer.Contract(contract_type).label if contract_type else "", contract_type=contract_type,
        published_at=_date(item.get("posted_at")), expires_at=_date(item.get("expires_at")),
        url=url, apply_url=_url(item.get("apply_url")),
        full_text=_text(item.get("description"), MAX_FULL_TEXT, multiline=True),
        city=city, postal_code=postal_code, insee=place.insee, zone_postal_code=place.zone_postal_code,
        department_code=place.department,
        department=_text(item.get("location_department"), _max_length("department")),
        region=region, region_code=place.region,
        country=country,
        remote_type=_choice(item.get("remote_type"), JobOffer.Remote),
        part_time=part_time if isinstance(part_time, bool) else None,
        salary_min=_amount(item.get("salary_min")), salary_max=_amount(item.get("salary_max")),
        daily_rate_min=_amount(item.get("daily_rate_min")), daily_rate_max=_amount(item.get("daily_rate_max")),
        salary_currency=_currency(item.get("salary_currency")),
        salary_period=_choice(item.get("salary_period"), JobOffer.SalaryPeriod),
        source_license=_text(item.get("source_license"), _max_length("source_license")),
        language=_text(item.get("language"), _max_length("language")),
        rome_code=rome_titles.rome_code_for(title) or "",
        match_key=keys.match_key(title, employer, place.insee or city),
    )
    return Parsed(source_key, offer)


@dataclass
class SourceStats:
    new: int = 0
    known: int = 0
    rome: int = 0  # offers read whose title gave a ROME code
    rejected: Counter = field(default_factory=Counter)

    @property
    def read(self):
        return self.new + self.known


class NewOffer(NamedTuple):
    """What the report keeps of a new offer (never its text)."""
    source_id: int
    rome_code: str
    match_key: str


@dataclass
class ImportResult:
    """Offers « read » are the ones kept, new or known; rejected items are counted apart."""
    dry_run: bool = False
    items: int = 0  # items of the dataset, objects or not
    not_objects: int = 0
    unreadable: int = 0  # files of the dataset that could not be read
    read_error: str = ""  # why reading the dataset stopped midway
    by_source: dict = field(default_factory=lambda: defaultdict(SourceStats))  # by the item's source key
    new_offers: list = field(default_factory=list)
    run_keys: dict = field(default_factory=lambda: defaultdict(set))  # match key -> ids of sources in the run

    def total(self, name):
        return sum(getattr(stats, name) for stats in self.by_source.values())

    @property
    def read(self):
        return self.total("read")

    @property
    def new(self):
        return self.total("new")

    @property
    def known(self):
        return self.total("known")

    @property
    def rome(self):
        return self.total("rome")

    @property
    def rejected(self):
        """Rejected items by reason, the dataset's non-objects included."""
        rejected = sum((stats.rejected for stats in self.by_source.values()), Counter())
        if self.not_objects:
            rejected["not_object"] += self.not_objects
        if self.unreadable:
            rejected["unreadable"] += self.unreadable
        return rejected


def import_items(items, sources, dry_run=False, batch_size=BATCH_SIZE, stop_on=()):
    """Import the dataset's items by batches, one transaction each; with dry_run nothing is written. Items are read
    lazily: an exception of `stop_on` while reading ends the import, and the batches before it stay. `items` may
    carry `skipped` and `unreadable`, its counts of entries that were not objects or could not be read."""
    result = ImportResult(dry_run=dry_run)
    seen = set()  # (source id, external id) of the run
    iterator = iter(items)
    while not result.read_error:
        batch = []
        try:
            batch.extend(islice(iterator, batch_size))  # keeps the items read before an error
        except stop_on as error:
            result.read_error = redact_text(error)  # the error comes from the actor's side (S18)
        if not batch:
            break
        result.items += len(batch)
        offers = []
        for parsed in (parse_item(item, sources) for item in batch):
            stats = result.by_source[parsed.source_key]
            if parsed.offer is None:
                stats.rejected[parsed.reason] += 1
                continue
            offer = parsed.offer
            stats.rome += bool(offer.rome_code)
            if offer.match_key:
                result.run_keys[offer.match_key].add(offer.source_id)
            if (offer.source_id, offer.external_id) in seen:
                stats.known += 1
                continue
            seen.add((offer.source_id, offer.external_id))
            offers.append((stats, offer))
        _write(result, offers, batch_size)
    result.not_objects = getattr(items, "skipped", 0)
    result.unreadable = getattr(items, "unreadable", 0)
    result.items += result.not_objects + result.unreadable
    return result


def _known(offers):
    """(source id, external id) of the offers already in the database."""
    ids_by_source = defaultdict(list)
    for _, offer in offers:
        ids_by_source[offer.source_id].append(offer.external_id)
    query = Q(pk__in=[])
    for source_id, external_ids in ids_by_source.items():
        query |= Q(source_id=source_id, external_id__in=external_ids)
    return set(JobOffer.objects.filter(query).values_list("source_id", "external_id"))


def _write(result, offers, batch_size):
    """Write the new offers of a batch in one transaction with the lookup of the known ones. Should the database
    refuse the batch, each offer is tried alone and the refused ones are rejected as unreadable."""
    if not offers:
        return
    new = None
    refused = set()  # id() of the offers refused
    try:
        with transaction.atomic():
            known = _known(offers)
            new = [offer for _, offer in offers if (offer.source_id, offer.external_id) not in known]
            if new and not result.dry_run:
                JobOffer.objects.bulk_create(new, batch_size=batch_size, ignore_conflicts=True)
    except Exception:  # a safety net: one third-party offer must never stop the night
        if new is None:  # the lookup itself failed: nothing to do with the offers
            raise
        for offer in new:
            try:
                with transaction.atomic():
                    JobOffer.objects.bulk_create([offer], ignore_conflicts=True)
            except Exception:
                refused.add(id(offer))
    new = {id(offer) for offer in new}
    for stats, offer in offers:
        if id(offer) in refused:
            stats.rejected["unreadable"] += 1
        elif id(offer) in new:
            stats.new += 1
            result.new_offers.append(NewOffer(offer.source_id, offer.rome_code, offer.match_key))
        else:
            stats.known += 1


def duplicates(result, now):
    """New offers whose match key is already on another source: in the run, or collected in the last 7 days."""
    wanted = {offer.match_key for offer in result.new_offers if offer.match_key}
    sources_by_key = defaultdict(set)
    for key in wanted:
        sources_by_key[key] |= result.run_keys.get(key, set())
    wanted = sorted(wanted)
    for start in range(0, len(wanted), BATCH_SIZE):
        rows = JobOffer.objects.filter(match_key__in=wanted[start:start + BATCH_SIZE],
                                       collected_at__gte=now - WINDOW)
        for key, source_id in rows.values_list("match_key", "source_id").distinct():
            sources_by_key[key].add(source_id)
    return sum(1 for offer in result.new_offers
               if offer.match_key and sources_by_key[offer.match_key] - {offer.source_id})


def offers_by_source_and_domain(result, now):
    """(offers by source name, offers by domain code with None for every person) over the last 7 days. A dry run
    adds its new offers, which are not in the database."""
    sources = {source.pk: source for source in Source.objects.all()}
    groups = Counter()
    rows = (JobOffer.objects.filter(collected_at__gte=now - WINDOW).values("source_id", "rome_code")
            .annotate(offers=Count("id")))
    for row in rows:
        groups[row["source_id"], row["rome_code"]] += row["offers"]
    if result.dry_run:
        for offer in result.new_offers:
            groups[offer.source_id, offer.rome_code] += 1
    by_source, by_domain = Counter(), Counter()
    for (source_id, rome_code), offers in groups.items():
        source = sources[source_id]
        by_source[source.name] += offers
        domains = offer_domains(source.domains, rome_code)
        for domain in domains if domains is not None else [None]:
            by_domain[domain] += offers
    return by_source, by_domain


class ActorLine(NamedTuple):
    """A source's line of the actor, from the RUN_STATS record of its run (spec 4.7)."""
    failed: str | None = None  # why the source failed, None when it did not ("" from the actor is a failure too)
    pushed: int = 0
    filtered: int = 0
    dupes: int = 0
    cross_dupes: int = 0
    already_seen: int = 0
    invalid: int = 0
    timed_out: bool = False  # stopped by its time budget, not an empty night for that

    @property
    def empty(self):
        """A night that tells of a broken source: failed, or nothing seen at all. A quiet source sees offers it
        already knows (new-only mode) and is not empty."""
        return self.failed is not None or not any(getattr(self, name) for name in RUN_STATS_COUNTS)


MAX_REASON = 200


def actor_lines(source_keys, stats):
    """{source key: ActorLine} for the keys of `source_keys`, from the run's RUN_STATS record (`stats`, checked by
    scraper.run_stats; None gives no line). Failure reasons, written by the actor, are cleaned and cut here."""
    if stats is None:
        return {}
    lines = {key: ActorLine(failed=values["failed"],
                            **{name: values[name] for name in (*RUN_STATS_COUNTS, "timed_out")})
             for key, values in stats["sources"].items()}
    # Masked before the cut: a cut in the middle of a credential would leave half of it readable (S18).
    return {key: line if line.failed is None
            else line._replace(failed=_text(redact_text(_text(line.failed)), MAX_REASON) or "?")
            for key, line in lines.items() if key in source_keys}


def next_state(source, empty):
    """(empty_runs, state) of a source after a night: unchanged when the night tells nothing (`empty` None), back
    to active after a sound night, failing at the second empty night in a row."""
    if empty is None:
        return source.empty_runs, source.state
    if not empty:
        return 0, Source.State.ACTIVE
    empty_runs = source.empty_runs + 1
    return empty_runs, Source.State.FAILING if empty_runs >= 2 else source.state


def track_sources(requested, run, result, now, stats=None):
    """Record the night of each source asked to the actor, from its line of the actor (actor_lines); the sources
    that turn failing are returned (in a dry run, the ones that would, nothing written). Nothing is recorded
    unless the run SUCCEEDED and its dataset was read whole. A source disabled in the admin during the run is left
    alone."""
    if not requested or run is None or run.status != "SUCCEEDED" or result.read_error:
        return []
    lines = actor_lines({source.actor_source for source in requested}, stats)
    failing = []
    for source in requested:
        if not result.dry_run:
            source.refresh_from_db(fields=["state", "empty_runs"])
        if source.state == Source.State.DISABLED:
            continue
        line = lines.get(source.actor_source)
        empty_runs, state = next_state(source, line.empty if line else None)
        if state == Source.State.FAILING and source.state != Source.State.FAILING:
            failing.append(source)
        if not result.dry_run:
            source.empty_runs, source.state, source.last_run_at = empty_runs, state, now
            source.save(update_fields=["empty_runs", "state", "last_run_at"])
    return failing


def failures(result, run):
    """Why the command must end with a non-zero code: a run that did not succeed, or a dataset read in part. A night
    without any offer is not one (new-only mode: a calm night happens), the report warns about it."""
    errors = []
    if result.read_error:
        errors.append(f"lecture du dataset interrompue ({result.read_error})")
    if run is not None and run.status != "SUCCEEDED":
        errors.append(f"l’actor a fini en {run.status}")
    return errors


@dataclass
class Report:
    """The figures of the report, queries done; format_report() only writes them out."""
    result: ImportResult
    run: object  # the actor's run (scraper.Run: status, exit_code, duration_s)
    names: dict  # source key -> name
    requested: list  # keys of the sources asked to the actor
    actor_lines: dict  # source key -> ActorLine
    failing: list  # keys of the sources that turn failing
    duplicates: int
    by_source: Counter  # last 7 days
    by_domain: Counter
    from_stats: bool = False  # actor lines from the RUN_STATS record, else none
    stats_error: str = ""  # why RUN_STATS could not be read, when it failed otherwise than by being absent


def build_report(result, now, sources, run, requested=(), failing=(), stats=None, stats_error=""):
    keys = [source.actor_source for source in requested]
    by_source, by_domain = offers_by_source_and_domain(result, now)
    return Report(result=result, run=run,
                  names={key: source.name for key, source in sources.items()}, requested=keys,
                  actor_lines=actor_lines(set(keys), stats),
                  failing=[source.actor_source for source in failing], duplicates=duplicates(result, now),
                  by_source=by_source, by_domain=by_domain, from_stats=stats is not None,
                  stats_error=stats_error)


def _count(number, singular, plural=None):
    """« 0 lue », « 1 lue », « 2 lues »: French agreement, number spaced by format_number."""
    return f"{format_number(number)} {singular if number < 2 else plural or singular + 's'}"


def _share(part, whole):
    return f"{format_number(part)} sur {format_number(whole)}" + (f" ({round(100 * part / whole)} %)" if whole
                                                                     else "")


def _duration(seconds):
    minutes, seconds = divmod(round(seconds), 60)
    return f"{minutes} min {seconds} s" if minutes else f"{seconds} s"


def _reasons(rejected):
    order = list(REASONS)
    return ", ".join(f"{REASONS[reason]} : {format_number(rejected[reason])}"
                     for reason in sorted(rejected, key=order.index))


TIMED_OUT = "arrêtée par son budget de temps"


def _actor(line):
    if line.failed is not None:
        return f"en panne : {line.failed}" + (f", {TIMED_OUT}" if line.timed_out else "")
    parts = [_count(line.pushed, "poussée"), _count(line.filtered, "filtrée"), _count(line.dupes, "doublon"),
             _count(line.cross_dupes, "doublon") + " entre sources", _count(line.already_seen, "déjà vue"),
             _count(line.invalid, "invalide")]
    if line.timed_out:
        parts.append(TIMED_OUT)
    return ", ".join(parts)


def _origin(report):
    """Where the lines of the actor come from, for section 2 of a report on a run."""
    if report.from_stats:
        return "  Lignes de l’actor : enregistrement RUN_STATS du run."
    why = (f"RUN_STATS illisible : {report.stats_error.rstrip('.')}" if report.stats_error
           else "RUN_STATS absent ou inutilisable")
    return f"  Lignes de l’actor : aucune ({why}), compteurs des sources inchangés."


def _offers(stats):
    rejected = sum(stats.rejected.values())
    text = (f"{_count(stats.read, 'lue')}, {_count(stats.new, 'nouvelle')}, {_count(stats.known, 'déjà connue')}, "
            f"{_count(rejected, 'écartée')}")
    return text + (f" ({_reasons(stats.rejected)})" if rejected else "")


def format_report(report):
    """Lines of the collection report, in French, for a terminal and the night logs."""
    result, run = report.result, report.run
    lines = ["Collecte : essai à blanc, rien n’est écrit." if result.dry_run else "Collecte"]

    lines += ["", "1. Run"]
    exit_code = "tué à son délai" if run.exit_code is None else f"code de sortie {run.exit_code}"
    lines.append(f"  Actor lancé en local : {run.status}, {_duration(run.duration_s)}, {exit_code}.")
    if result.read_error:
        lines.append(f"  Lecture du dataset interrompue : {result.read_error}")
    rejected = result.rejected
    lines.append(f"  {_count(result.items, 'élément')} dans le dataset : "
                 f"{_count(result.read, 'offre lue', 'offres lues')}"
                 f" ({_count(result.new, 'nouvelle')}, {_count(result.known, 'déjà connue')}), "
                 f"{_count(sum(rejected.values()), 'écartée')}" + (f" ({_reasons(rejected)})." if rejected else "."))
    if not result.new and not result.read_error:
        lines.append("  Aucune offre nouvelle cette nuit.")

    lines += ["", "2. Par source (offres, puis la ligne de l’actor)"]
    lines.append(_origin(report))
    for key in sorted(set(result.by_source) | set(report.requested)):
        label = f"{key} ({report.names[key]})" if key in report.names else f"{key or '(sans source)'} (inconnue)"
        line = f"  {label} : {_offers(result.by_source.get(key, SourceStats()))}"
        if key in report.actor_lines:
            line += f" ; actor : {_actor(report.actor_lines[key])}"
        lines.append(line)
    empty = [key for key in report.requested if not result.by_source.get(key, SourceStats()).read]
    if empty:
        lines.append(f"  Demandées sans offre lue : {', '.join(empty)}.")
    timed_out = [key for key in report.requested if key in report.actor_lines and report.actor_lines[key].timed_out]
    if timed_out:
        lines.append(f"  Arrêtées par leur budget de temps : {', '.join(timed_out)}.")
    if report.failing:
        verb = "Passeraient" if result.dry_run else "Passent"
        lines.append(f"  {verb} « En échec » (deux nuits en panne ou sans rien voir) : {', '.join(report.failing)}.")

    lines += ["", "3. Codes ROME (titres reconnus)", f"  Ensemble : {_share(result.rome, result.read)}."]
    lines += [f"  {key} : {_share(stats.rome, stats.read)}." for key, stats in sorted(result.by_source.items())
              if stats.read]

    lines += ["", "4. Doublons entre sources (clé de rapprochement, aucune fusion)",
              f"  Offres nouvelles déjà présentes sur une autre source, dans le run ou sur 7 jours : "
              f"{_share(report.duplicates, result.new)}."]

    lines += ["", "5. Sur 7 jours" + (" (base, plus les offres nouvelles du run)" if result.dry_run else ""),
              "  Par source :"]
    lines += [f"    {name} : {format_number(count)}" for name, count in sorted(report.by_source.items())] \
        or ["    aucune"]
    lines.append("  Par domaine (une offre compte dans chacun de ses domaines) :")
    lines += [f"    {label} : {format_number(report.by_domain[code])}" for code, label in DOMAINS
              if report.by_domain[code]]
    lines.append(f"    Toutes les personnes : {format_number(report.by_domain[None])}")

    lines += ["", f"collecte : {_count(result.read, 'lue')}, {_count(result.new, 'nouvelle')}, "
                  f"{_count(result.known, 'déjà connue')}, {_count(sum(rejected.values()), 'écartée')}, "
                  f"ROME {_share(result.rome, result.read)}, {_count(report.duplicates, 'doublon')}, run {run.status}"
                  + (", essai à blanc" if result.dry_run else "")]
    return lines

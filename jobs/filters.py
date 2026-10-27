"""The live offer counter of the questionnaire: how many offers pass a person's filters. It counts yesterday's
offers, or the last 7 days' when fewer than LOW_WEEKLY pass over the week, and then says coverage is thin.

Plain SQL on the structured fields of JobOffer, no Claude. An offer counts for a person when it reaches one of their
domains (the rule of jobs.domains.offer_domains, decision 12 of the step 4 plan) and passes every strict criterion:
the place, remote work, contracts, the title's keywords, pay and working
time. A criterion turned into a preference excludes nothing, and
experience does not filter before step 5. A field the offer leaves blank never excludes it: an offer whose place,
remote work, contract, amount or working time is unknown passes (decision 6), and the questionnaire says so. An
offer without a commune is placed by the postal code of its zone, else by its department or its region.
"""
import re
from datetime import timedelta
from decimal import Decimal
from typing import NamedTuple

from django.db.models import Case, Count, DecimalField, Q, Value, When
from django.db.models.functions import Coalesce

from jobs import geo
from jobs.models import JobOffer, Source
from jobs.text import fold_title
from onboarding import rome
from onboarding.criteria import format_number

WINDOW = timedelta(hours=24)
WEEK = timedelta(days=7)
# Above this many offers, the counter says the next block will help sort them.
WIDE = 150
# Under this many offers over a week, the counter says coverage is thin (to review in 4.7).
LOW_WEEKLY = 5

Contract, Period, Remote = JobOffer.Contract, JobOffer.SalaryPeriod, JobOffer.Remote
# Answers of the contracts screen -> the actor's contract types (decision 13). AUTRE and blank always pass; VIE and
# VIA match no answer.
CONTRACT_TYPES = {
    "cdi": [Contract.CDI],
    "cdd": [Contract.CDD],
    "cdd_projet": [Contract.CDD],
    "fonction_publique": [Contract.FONCTION_PUBLIQUE],
    "service_civique": [Contract.SERVICE_CIVIQUE, Contract.VOLONTARIAT],
    "freelance": [Contract.FREELANCE],
    "alternance": [Contract.ALTERNANCE],
    "stage": [Contract.STAGE],
    "interim": [Contract.INTERIM],
}
UNKNOWN_CONTRACTS = [Contract.AUTRE, ""]

# Gross yearly pay (decision 14): a month is 12 of a year, a week 52, an hour 151.67 × 12. Also turns a floor per
# year or per month (the questionnaire's units share these keys) into a yearly one.
YEARLY = {Period.YEAR: 1, Period.MONTH: 12, Period.WEEK: 52, Period.HOUR: Decimal("151.67") * 12}
_AMOUNT = DecimalField(max_digits=16, decimal_places=4)


class CounterFilter(NamedTuple):
    name: str  # shown when nothing passes and this filter is the strictest
    query: Q


def _top(low, high):
    """The top of a range, its bottom when the top is missing."""
    return Coalesce(high, low, output_field=_AMOUNT)


def _yearly_pay():
    """Top of the salary range as gross pay per year, None for a daily salary or an unknown period."""
    return Case(*(When(salary_period=period, then=_top("salary_min", "salary_max") * Value(factor))
                  for period, factor in YEARLY.items()), default=None, output_field=_AMOUNT)


def _daily_pay():
    """The day rate (TJM), else a salary paid by the day."""
    by_day = Case(When(salary_period=Period.DAY, then=_top("salary_min", "salary_max")), default=None,
                  output_field=_AMOUNT)
    return Coalesce("daily_rate_max", "daily_rate_min", by_day, output_field=_AMOUNT)


def _city_codes(cities):
    """INSEE codes of the person's cities: the autocomplete's, else the commune found by name. A city without a
    commune is left out, the others still play."""
    codes = []
    for city in cities:
        code = city["insee"] or geo.commune_for(city["name"], department_code=city["department"])
        if code:
            codes.append(code)
    return codes


def _without_commune(departments, regions, postal_codes=None):
    """Offers without a commune in reach of the zone (the design plan, decision 2): their
    department is one of `departments`, or, without a department, their region one of `regions`. With neither, an
    offer passes. When `postal_codes` is given, an offer with a postal code of its zone passes only if it is one of
    them, whatever its department (the design plan, decision 2)."""
    zone = (Q(department_code="", region_code="") | Q(department_code__in=sorted(departments))
            | Q(department_code="", region_code__in=sorted(regions)))
    if postal_codes is not None:
        zone = Q(zone_postal_code__in=sorted(postal_codes)) | (Q(zone_postal_code="") & zone)
    return Q(insee="") & zone


def _regions(departments):
    return {geo.region_for_department(department) for department in departments} - {None}


def _place(location):
    """The place filter's query, None when it filters nothing. An offer without a commune passes when its postal
    code, else its department or its region, holds a commune in reach (_without_commune)."""
    codes = _city_codes(location["cities"])
    distance = location["distance"]
    if distance == "region":
        regions = {geo.region_for(code) for code in codes} - {None}
        departments = geo.departments_of_regions(regions)
        if not departments:
            return None
        query = _without_commune(departments, regions)
        for department in sorted(departments):  # an INSEE code starts with its department's code
            query |= Q(insee__startswith=department)
        return query
    if distance == "city":
        communes = set(codes)
        departments = geo.departments_of(codes)
    else:
        communes = set().union(*(geo.communes_within(code, int(distance)) for code in codes))
        departments = frozenset().union(*(geo.departments_within(code, int(distance)) for code in codes))
    if not communes:
        return None
    return (_without_commune(departments, _regions(departments), geo.postal_codes_of(communes))
            | Q(insee__in=sorted(communes)))


def _title_has(words):
    """Offers whose folded title holds one of `words` whole: bounded by the ends or a space, since fold turns every
    punctuation into one. SQLite's REGEXP is Python's re.search, as for rome.ROME_CODE_PATTERN."""
    # shortcut: « c++ » folds to « c » and matches any lone C; keep symbols in title_folded if someone needs them.
    return Q(title_folded__regex=r"(?:^| )(?:" + "|".join(re.escape(fold_title(word)) for word in words) + r")(?: |$)")


def _filters(criteria):
    """Every strict criterion, as named queries."""
    filters = []
    location = criteria.get("location")
    if location and location["strict"]:
        # Fully remote offers are worth anywhere: the place no longer filters.
        if location["remote"] == "full_remote":
            filters.append(CounterFilter("le télétravail", Q(remote_type__in=[Remote.FULL, ""])))
        else:
            place = _place(location)
            if place is not None:
                filters.append(CounterFilter("le lieu", place))
            if location["remote"] == "hybrid":
                filters.append(CounterFilter("le télétravail", ~Q(remote_type=Remote.NONE)))
    contracts = criteria.get("contracts")
    if contracts:
        types = [kind for value in contracts["values"] for kind in CONTRACT_TYPES[value]]
        filters.append(CounterFilter("les contrats", Q(contract_type__in=types + UNKNOWN_CONTRACTS)))
    keywords = criteria.get("keywords")
    if keywords and (keywords["include"] or keywords["exclude"]):
        query = Q()
        if keywords["include"]:
            query &= _title_has(keywords["include"])
        if keywords["exclude"]:
            query &= ~_title_has(keywords["exclude"])
        filters.append(CounterFilter("vos mots-clés", query))
    pay = criteria.get("pay")
    if pay and pay["strict"] and pay["floor"] is not None:
        if pay["unit"] == "day":
            compared = Q(daily_pay__isnull=True) | Q(daily_pay__gte=pay["floor"])
        else:
            compared = Q(yearly_pay__isnull=True) | Q(yearly_pay__gte=pay["floor"] * YEARLY[pay["unit"]])
        # Amounts in another currency, or in none, are not compared.
        filters.append(CounterFilter("le salaire", ~Q(salary_currency="EUR") | compared))
    hours = criteria.get("hours")
    if hours and hours["strict"] and hours["value"] in ("full", "part_only"):
        excluded = hours["value"] == "full"  # full time drops part-time offers, part time only drops full-time ones
        filters.append(CounterFilter("le temps de travail", Q(part_time__isnull=True) | ~Q(part_time=excluded)))
    return filters


def _domains(domains):
    """Offers reaching one of `domains`: their source serves one of them, or their ROME code falls in one, or they
    come from a source serving everyone without a ROME code. A code that is not a ROME code (rome.is_rome_code)
    counts as none. SQLite cannot search inside JSON: the sources, a few dozen, are read once and sorted here."""
    domains = set(domains)
    serving, general = [], []
    for pk, served in Source.objects.values_list("pk", "domains"):
        if not served:
            general.append(pk)
        elif domains & set(served):
            serving.append(pk)
    rome_code = Q(rome_code__regex=rome.ROME_CODE_PATTERN)
    in_domains = Q()
    for domain in sorted(domains):
        for professional, _ in rome.professional_domains(domain):
            in_domains |= Q(rome_code__startswith=professional)
    query = Q(source_id__in=serving) | (Q(source_id__in=general) & ~rome_code)
    return query | (rome_code & in_domains) if in_domains else query


def _offers(since, domains):
    return (JobOffer.objects.filter(_domains(domains), collected_at__gte=since)
            .alias(yearly_pay=_yearly_pay(), daily_pay=_daily_pay()))


def _passing(filters):
    """The query of offers passing all `filters`, Q() when there is none."""
    query = Q()
    for counter_filter in filters:
        query &= counter_filter.query
    return query


def _count(condition):
    """Count of the offers meeting `condition`. An aggregate takes None, not an empty Q(), for no condition."""
    return Count("pk", filter=condition or None)


def passing_offers(criteria, now, domains, since=None):
    """Offers for these domains collected since `since` (by default the 24 hours before `now`) that pass the strict
    criteria."""
    since = now - WINDOW if since is None else since
    return _offers(since, domains).filter(_passing(_filters(criteria)))


def _strictest(offers, filters):
    """Name of the filter whose removal lets the most offers through, None when removing any one lets none."""
    if not filters:
        return None
    counts = offers.aggregate(**{
        str(index): _count(_passing([other for other in filters if other is not dropped]))
        for index, dropped in enumerate(filters)})
    best_count, strictest = max((counts[str(index)], dropped.name) for index, dropped in enumerate(filters))
    return strictest if best_count else None


def _passing_text(count, period):
    number = format_number(count)
    if count == 1:
        return f"{period}, {number} offre passait ces filtres."
    return f"{period}, {number} offres passaient ces filtres."


def counter_text(criteria, now, domains):
    """The counter line, or None while the database holds no offer at all (the line is then not shown). Yesterday's
    count, unless fewer than LOW_WEEKLY offers pass over the week: the week's count then, said to be thin."""
    if not JobOffer.objects.exists():
        return None
    filters = _filters(criteria)
    passing = _passing(filters)
    offers = _offers(now - WEEK, domains)
    yesterday = Q(collected_at__gte=now - WINDOW)
    counts = offers.aggregate(day=_count(yesterday & passing), week=_count(passing))
    if counts["week"] == 0:
        return _nothing("des 7 derniers jours", _strictest(offers, filters))
    if counts["week"] < LOW_WEEKLY:
        return (_passing_text(counts["week"], "Sur les 7 derniers jours")
                + " C’est peu : élargir la zone ou les contrats en trouvera davantage.")
    if counts["day"] == 0:
        return _nothing("d’hier", _strictest(offers.filter(yesterday), filters))
    text = _passing_text(counts["day"], "Hier")
    if counts["day"] > WIDE:
        text += " C’est large : le bloc suivant va aider à trier."
    return text


def _nothing(window, strictest):
    text = f"Aucune offre {window} ne passe."
    return f"{text} Le filtre le plus strict : {strictest}." if strictest else text

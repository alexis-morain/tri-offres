from datetime import timedelta
from decimal import Decimal
from itertools import combinations

from django.test import TestCase
from django.utils import timezone

from jobs import filters
from jobs.domains import offer_domains
from jobs.filters import counter_text, passing_offers
from jobs.models import JobOffer
from jobs.testing import OfferMaker, make_source
from onboarding import criteria as questionnaire
from onboarding.rome import DOMAINS

PARIS, BOULOGNE, VERSAILLES, MEAUX, LYON, VILLEURBANNE = "75056", "92012", "78646", "77284", "69123", "69266"
NANTES, CARQUEFOU = "44109", "44026"
LOW = " C’est peu : élargir la zone ou les contrats en trouvera davantage."


def contracts(*values):
    return {"version": 1, "contracts": {"values": list(values), "strict": True}}


def where(*cities, distance="city", remote="any", strict=True):
    """Location criteria; a city is a name, or (name, insee) as the autocomplete fills it."""
    rows = []
    for city in cities:
        name, insee = city if isinstance(city, tuple) else (city, "")
        rows.append({"name": name, "insee": insee, "department": "", "region": ""})
    return {"version": 1, "location": {"cities": rows, "distance": distance, "remote": remote, "strict": strict}}


def pay(floor, unit="year", strict=True):
    return {"version": 1, "pay": {"floor": floor, "unit": unit, "strict": strict}}


def hours(value, strict=True):
    return {"version": 1, "hours": {"value": value, "strict": strict}}


def keywords(include=(), exclude=()):
    return {"version": 1, "keywords": {"include": list(include), "exclude": list(exclude), "strict": True}}


class FilterTestCase(OfferMaker, TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.source = make_source(domains=["E", "K", "L"])

    def passing(self, criteria):
        return set(passing_offers(criteria, self.now, ["E", "J"]))


class WindowAndDomainTests(FilterTestCase):
    def test_only_offers_collected_in_the_last_24_hours(self):
        recent = self.offer(ago=timedelta(hours=23))
        self.offer(ago=timedelta(hours=25))
        self.assertEqual(self.passing({"version": 1}), {recent})

    def test_since_widens_the_window(self):
        recent = self.offer(ago=timedelta(hours=23))
        week = self.offer(ago=timedelta(hours=24 * 6))
        self.offer(ago=timedelta(hours=24 * 8))
        since = self.now - timedelta(days=7)
        self.assertEqual(set(passing_offers({"version": 1}, self.now, ["E"], since=since)), {recent, week})
        self.assertEqual(set(passing_offers({"version": 1}, self.now, ["E"], since=None)), {recent})
        self.assertEqual(set(passing_offers({"version": 1}, self.now, ["E"])), {recent})

    def test_sources_sharing_a_domain_or_serving_everyone(self):
        mine = self.offer()
        culture, self.source = self.source, make_source(name="Welcome", domains=["D", "M17"])
        self.offer()
        self.source = make_source(name="Hôpitaux", domains=["J"])
        health = self.offer()
        self.source = make_source(name="Toutes", domains=[])
        shared = self.offer()
        self.source = culture
        self.assertEqual(self.passing({"version": 1}), {mine, health, shared})
        self.assertEqual(set(passing_offers({"version": 1}, self.now, ["M18"])), {shared})
        self.assertEqual(set(passing_offers({"version": 1}, self.now, [])), {shared})

    def test_rome_code_of_the_offer(self):
        self.source = make_source(name="Toutes", domains=[])
        developer = self.offer(rome_code="M1805")
        accountant = self.offer(rome_code="M1203")
        unknown = self.offer(rome_code="")
        self.source = make_source(name="Culture", domains=["E", "K", "L"])
        culture_accountant = self.offer(rome_code="M1203")
        self.assertEqual(set(passing_offers({"version": 1}, self.now, ["M18"])), {developer, unknown})
        self.assertEqual(set(passing_offers({"version": 1}, self.now, ["M"])),
                         {accountant, unknown, culture_accountant})
        self.assertEqual(set(passing_offers({"version": 1}, self.now, ["E"])), {unknown, culture_accountant})

    def test_same_rule_as_offer_domains_on_every_combination(self):
        """The SQL rule of the counter against jobs.domains.offer_domains, the one of the collection report."""
        served = [[], ["E", "K", "L"], ["M18"], ["D", "M17"], ["J"]]
        codes = ["", "M1805", "M1203", "M1302", "D1106", "E1103", "J1506", "K2111", "L1101", "A1203", "C1501",
                 "Z9999", "M180", "M18XX"]
        offers = {}
        for index, domains in enumerate(served):
            self.source = make_source(name=f"Source {index}", domains=domains)
            for code in codes:
                offers[self.offer(rome_code=code).pk] = offer_domains(domains, code)
        codes_of_domains = [code for code, _ in DOMAINS]
        people = [[]] + [[code] for code in codes_of_domains] + [list(pair) for pair in combinations(
            ["E", "K", "M", "M18", "D", "M17", "C", "L"], 2)]
        for person in people:
            with self.subTest(domains=person):
                expected = {pk for pk, reached in offers.items() if reached is None or reached & set(person)}
                found = set(passing_offers({"version": 1}, self.now, person).values_list("pk", flat=True))
                self.assertEqual(found, expected)


class LocationTests(FilterTestCase):
    def setUp(self):
        super().setUp()
        self.paris = self.offer(insee=PARIS, department_code="75")
        self.boulogne = self.offer(insee=BOULOGNE, department_code="92")
        self.versailles = self.offer(insee=VERSAILLES, department_code="78")
        self.meaux = self.offer(insee=MEAUX, department_code="77")
        self.lyon = self.offer(insee=LYON, department_code="69")
        self.unknown = self.offer()
        self.everyone = {self.paris, self.boulogne, self.versailles, self.meaux, self.lyon, self.unknown}

    def test_city_alone(self):
        self.assertEqual(self.passing(where(("Paris", PARIS))), {self.paris, self.unknown})

    def test_within_30_and_60_km_as_the_crow_flies(self):
        near = {self.paris, self.boulogne, self.versailles, self.unknown}
        self.assertEqual(self.passing(where(("Paris", PARIS), distance="30")), near)
        self.assertEqual(self.passing(where(("Paris", PARIS), distance="60")), near | {self.meaux})

    def test_region_by_insee_or_else_by_department(self):
        department_only = self.offer(department_code="93")
        elsewhere = self.offer(department_code="13")
        self.assertEqual(self.passing(where(("Paris", PARIS), distance="region")),
                         {self.paris, self.boulogne, self.versailles, self.meaux, self.unknown, department_only})
        self.assertNotIn(elsewhere, self.passing(where(("Lyon", LYON), distance="region")))
        self.assertIn(self.lyon, self.passing(where(("Lyon", LYON), distance="region")))

    def test_city_without_insee_is_looked_up_by_name(self):
        self.assertEqual(self.passing(where("Lyon", distance="30")), {self.lyon, self.unknown})
        with_department = where("Paris")
        with_department["location"]["cities"][0]["department"] = "75"
        self.assertEqual(self.passing(with_department), {self.paris, self.unknown})

    def test_city_not_found_is_ignored_and_the_others_play(self):
        self.assertEqual(self.passing(where("Saint-Denis")), self.everyone)  # two communes of that name
        self.assertEqual(self.passing(where("Saint-Denis", ("Lyon", LYON))), {self.lyon, self.unknown})

    def test_several_cities(self):
        self.assertEqual(self.passing(where(("Paris", PARIS), ("Lyon", LYON))),
                         {self.paris, self.lyon, self.unknown})

    def test_without_city_the_place_does_not_filter(self):
        self.assertEqual(self.passing(where()), self.everyone)

    def test_preference_does_not_filter(self):
        self.assertEqual(self.passing(where(("Paris", PARIS), strict=False)), self.everyone)


class PlaceWithoutCommuneTests(FilterTestCase):
    """An offer with a department or a region but no commune passes when a commune of that zone is in reach
    (the design plan, decision 2)."""

    def setUp(self):
        super().setUp()
        self.hauts_de_seine = self.offer(department_code="92", region_code="11")
        self.paris = self.offer(department_code="75", region_code="11")
        self.rhone = self.offer(department_code="69", region_code="84")
        self.ile_de_france = self.offer(region_code="11")
        self.auvergne = self.offer(region_code="84")
        self.guadeloupe = self.offer(department_code="971", region_code="01")
        self.nowhere = self.offer()

    def test_within_a_distance(self):
        self.assertEqual(self.passing(where(("Paris", PARIS), distance="30")),
                         {self.hauts_de_seine, self.paris, self.ile_de_france, self.nowhere})
        self.assertEqual(self.passing(where(("Lyon", LYON), distance="30")),
                         {self.rhone, self.auvergne, self.nowhere})

    def test_city_alone(self):
        self.assertEqual(self.passing(where(("Paris", PARIS))), {self.paris, self.ile_de_france, self.nowhere})

    def test_several_cities(self):
        self.assertEqual(self.passing(where(("Paris", PARIS), ("Lyon", LYON), distance="30")),
                         {self.hauts_de_seine, self.paris, self.ile_de_france, self.rhone, self.auvergne,
                          self.nowhere})

    def test_region(self):
        self.assertEqual(self.passing(where(("Paris", PARIS), distance="region")),
                         {self.hauts_de_seine, self.paris, self.ile_de_france, self.nowhere})

    def test_the_department_decides_over_the_region(self):
        wrong_region = self.offer(department_code="69", region_code="11")
        self.assertNotIn(wrong_region, self.passing(where(("Paris", PARIS), distance="30")))

    def test_full_remote_and_preference_do_not_filter(self):
        everyone = set(JobOffer.objects.all())
        self.assertEqual(self.passing(where(("Paris", PARIS), remote="full_remote")), everyone)
        self.assertEqual(self.passing(where(("Paris", PARIS), strict=False)), everyone)

    def test_postal_code_of_the_zone_decides_over_its_department(self):
        """the design plan, decision 2: a commune of the postal code must be in reach."""
        segre = self.offer(zone_postal_code="49500", department_code="49", region_code="52")
        carquefou = self.offer(zone_postal_code="44470", department_code="44", region_code="52")
        anjou = self.offer(department_code="49", region_code="52")
        near_nantes = self.passing(where(("Nantes", NANTES), distance="30"))
        self.assertNotIn(segre, near_nantes)
        self.assertIn(carquefou, near_nantes)
        self.assertIn(anjou, near_nantes)  # without a postal code, the department still decides
        self.assertIn(carquefou, self.passing(where(("Carquefou", CARQUEFOU))))
        self.assertNotIn(carquefou, self.passing(where(("Nantes", NANTES))))
        self.assertIn(segre, self.passing(where(("Nantes", NANTES), distance="region")))

    def test_the_counter_applies_the_same_rule(self):
        JobOffer.objects.exclude(pk__in=[self.rhone.pk, self.auvergne.pk]).delete()
        self.offers(5, insee=PARIS)
        self.assertEqual(counter_text(where(("Paris", PARIS)), self.now, ["E"]),
                         "Hier, 5 offres passaient ces filtres.")
        self.assertEqual(counter_text(where(("Lyon", LYON), distance="30"), self.now, ["E"]),
                         "Sur les 7 derniers jours, 2 offres passaient ces filtres." + LOW)


class RemoteTests(FilterTestCase):
    def setUp(self):
        super().setUp()
        self.full = self.offer(remote_type="full", insee=LYON)
        self.hybrid = self.offer(remote_type="partial", insee=PARIS)
        self.onsite = self.offer(remote_type="none", insee=PARIS)
        self.unknown = self.offer(insee=PARIS)

    def test_full_remote_keeps_full_only_and_the_place_no_longer_filters(self):
        self.assertEqual(self.passing(where(("Paris", PARIS), remote="full_remote")), {self.full, self.unknown})

    def test_hybrid_drops_onsite_only(self):
        self.assertEqual(self.passing(where(remote="hybrid")), {self.full, self.hybrid, self.unknown})

    def test_onsite_and_any_let_everything_through(self):
        for remote in ("onsite", "any"):
            with self.subTest(remote=remote):
                self.assertEqual(len(self.passing(where(remote=remote))), 4)

    def test_preference(self):
        self.assertEqual(len(self.passing(where(remote="full_remote", strict=False))), 4)

    def test_with_the_place(self):
        self.assertEqual(self.passing(where(("Paris", PARIS), remote="hybrid")), {self.hybrid, self.unknown})


class ContractTests(FilterTestCase):
    def setUp(self):
        super().setUp()
        self.offers = {value: self.offer(contract_type=value) for value in JobOffer.Contract.values}
        self.blank = self.offer()
        self.always = {self.offers["AUTRE"], self.blank}

    def test_each_answer(self):
        expected = {
            "cdi": {"CDI"}, "cdd": {"CDD"}, "cdd_projet": {"CDD"}, "fonction_publique": {"FONCTION_PUBLIQUE"},
            "service_civique": {"SERVICE_CIVIQUE", "VOLONTARIAT"}, "freelance": {"FREELANCE"},
            "alternance": {"ALTERNANCE"}, "stage": {"STAGE"}, "interim": {"INTERIM"},
        }
        for answer, types in expected.items():
            with self.subTest(answer=answer):
                self.assertEqual(self.passing(contracts(answer)),
                                 {self.offers[value] for value in types} | self.always)

    def test_every_answer_of_the_questionnaire_has_its_types(self):
        self.assertEqual(set(filters.CONTRACT_TYPES), set(questionnaire.CONTRACTS))

    def test_several(self):
        self.assertEqual(self.passing(contracts("cdi", "stage")),
                         {self.offers["CDI"], self.offers["STAGE"]} | self.always)

    def test_vie_and_via_never_pass(self):
        every = contracts(*filters.CONTRACT_TYPES)
        self.assertNotIn(self.offers["VIE"], self.passing(every))
        self.assertNotIn(self.offers["VIA"], self.passing(every))


class PayTests(FilterTestCase):
    def salary(self, low=None, high=None, period="year", currency="EUR"):
        return self.offer(salary_min=low, salary_max=high, salary_period=period, salary_currency=currency)

    def test_yearly_floor_against_every_period(self):
        cases = [  # (min, max, period, passes a 30 000 floor)
            (25000, 31000, "year", True), (25000, 29000, "year", False), (31000, None, "year", True),
            (2400, 2600, "month", True), (2400, 2450, "month", False),
            (500, 600, "week", True), (500, 550, "week", False),
            (16, 17, "hour", True), (15, 16, "hour", False),
            (100, 110, "day", True),  # a daily salary is not compared to a yearly floor
        ]
        expected = set()
        for low, high, period, passes in cases:
            offer = self.salary(low, high, period)
            if passes:
                expected.add(offer)
        self.assertEqual(self.passing(pay(30000)), expected)

    def test_monthly_floor_is_compared_per_year(self):
        above = self.salary(30000, 31000)
        self.salary(28000, 29000)
        self.assertEqual(self.passing(pay(2500, "month")), {above})

    def test_daily_floor_against_the_day_rate(self):
        rate = self.offer(daily_rate_min=Decimal(400), daily_rate_max=Decimal(500), salary_currency="EUR")
        self.offer(daily_rate_min=Decimal(300), daily_rate_max=Decimal(350), salary_currency="EUR")
        low_only = self.offer(daily_rate_min=Decimal(450), salary_currency="EUR")
        no_currency = self.offer(daily_rate_min=Decimal(300))
        daily_salary = self.salary(400, 460, "day")
        self.salary(200, 220, "day")
        yearly = self.salary(20000, 21000)
        self.assertEqual(self.passing(pay(450, "day")), {rate, low_only, no_currency, daily_salary, yearly})

    def test_offers_without_comparable_amount_pass(self):
        blank = self.offer()
        no_period = self.salary(100, 200, period="")
        dollars = self.salary(100, 200, currency="USD")
        no_currency = self.salary(100, 200, currency="")
        self.salary(100, 200)
        self.assertEqual(self.passing(pay(30000)), {blank, no_period, dollars, no_currency})

    def test_no_floor_or_preference_does_not_filter(self):
        self.salary(100, 200)
        self.assertEqual(len(self.passing(pay(None))), 1)
        self.assertEqual(len(self.passing(pay(30000, strict=False))), 1)


class HoursTests(FilterTestCase):
    def setUp(self):
        super().setUp()
        self.part = self.offer(part_time=True)
        self.full = self.offer(part_time=False)
        self.unknown = self.offer()

    def test_full_time(self):
        self.assertEqual(self.passing(hours("full")), {self.full, self.unknown})

    def test_part_time_only(self):
        self.assertEqual(self.passing(hours("part_only")), {self.part, self.unknown})

    def test_part_time_accepted_and_preferences_do_not_filter(self):
        self.assertEqual(len(self.passing(hours("part_ok"))), 3)
        self.assertEqual(len(self.passing(hours("full", strict=False))), 3)

    def test_experience_does_not_filter(self):
        criteria = {"version": 1, "experience": {"value": "lt1", "strict": True}}
        self.assertEqual(len(self.passing(criteria)), 3)


class CombinedTests(FilterTestCase):
    def test_every_criterion_together(self):
        criteria = {**where(("Paris", PARIS), distance="30", remote="hybrid"), **contracts("cdi"),
                    **pay(30000), **hours("full")}
        good = self.offer(insee=BOULOGNE, remote_type="partial", contract_type="CDI", salary_max=Decimal(35000),
                          salary_period="year", salary_currency="EUR", part_time=False)
        blank = self.offer()
        self.offer(insee=LYON, contract_type="CDI")
        self.offer(remote_type="none")
        self.offer(contract_type="CDD")
        self.offer(salary_max=Decimal(20000), salary_period="year", salary_currency="EUR")
        self.offer(part_time=True)
        self.assertEqual(self.passing(criteria), {good, blank})


class CounterTextTests(FilterTestCase):
    def test_no_offer_at_all_hides_the_line(self):
        self.assertIsNone(counter_text({"version": 1}, self.now, ["E"]))

    def test_plural(self):
        for _ in range(5):
            self.offer()
        self.assertEqual(counter_text({"version": 1}, self.now, ["E"]), "Hier, 5 offres passaient ces filtres.")

    def test_singular(self):
        self.offer()
        for _ in range(4):
            self.offer(ago=timedelta(hours=48))
        self.assertEqual(counter_text({"version": 1}, self.now, ["E"]), "Hier, 1 offre passait ces filtres.")

    def test_few_offers_over_the_week(self):
        self.offer()
        self.offer(ago=timedelta(hours=100))
        self.offer(ago=timedelta(hours=24 * 8))
        self.assertEqual(counter_text({"version": 1}, self.now, ["E"]),
                         "Sur les 7 derniers jours, 2 offres passaient ces filtres." + LOW)

    def test_one_offer_over_the_week(self):
        self.offer(ago=timedelta(hours=100))
        self.assertEqual(counter_text({"version": 1}, self.now, ["E"]),
                         "Sur les 7 derniers jours, 1 offre passait ces filtres." + LOW)

    def test_four_offers_over_the_week_and_five_say_yesterday(self):
        for _ in range(4):
            self.offer()
        self.assertEqual(counter_text({"version": 1}, self.now, ["E"]),
                         "Sur les 7 derniers jours, 4 offres passaient ces filtres." + LOW)
        self.offer(ago=timedelta(hours=100))
        self.assertEqual(counter_text({"version": 1}, self.now, ["E"]), "Hier, 4 offres passaient ces filtres.")

    def test_few_offers_over_the_week_counts_what_passes_the_filters(self):
        for _ in range(5):
            self.offer(ago=timedelta(hours=50), contract_type="CDD")
        self.offer(contract_type="CDI")
        self.assertEqual(counter_text(contracts("cdi"), self.now, ["E"]),
                         "Sur les 7 derniers jours, 1 offre passait ces filtres." + LOW)
        self.assertEqual(counter_text(contracts("cdi", "cdd"), self.now, ["E"]), "Hier, 1 offre passait ces filtres.")

    def test_zero_names_the_strictest_filter(self):
        for _ in range(5):
            self.offer(ago=timedelta(hours=50))
        self.offer(insee=PARIS, contract_type="CDI")
        self.offer(insee=PARIS, contract_type="CDD")
        self.offer(insee=LYON, contract_type="STAGE")
        criteria = {**where(("Marseille", "13055")), **contracts("cdi", "cdd")}
        self.assertEqual(counter_text(criteria, self.now, ["E"]),
                         "Aucune offre d’hier ne passe. Le filtre le plus strict : le lieu.")
        criteria = {**where(("Lyon", LYON)), **contracts("service_civique")}
        self.offer(insee=LYON, contract_type="CDI")
        self.assertEqual(counter_text(criteria, self.now, ["E"]),
                         "Aucune offre d’hier ne passe. Le filtre le plus strict : les contrats.")

    def test_every_filter_has_a_name(self):
        for _ in range(5):
            self.offer(ago=timedelta(hours=50))
        cases = [
            (where(remote="full_remote"), {"remote_type": "none"}, "le télétravail"),
            (pay(30000), {"salary_max": Decimal(100), "salary_period": "year", "salary_currency": "EUR"},
             "le salaire"),
            (hours("full"), {"part_time": True}, "le temps de travail"),
        ]
        for criteria, fields, name in cases:
            with self.subTest(name=name):
                offer = self.offer(**fields)
                self.assertEqual(counter_text(criteria, self.now, ["E"]),
                                 f"Aucune offre d’hier ne passe. Le filtre le plus strict : {name}.")
                offer.delete()

    def test_zero_yesterday_with_a_few_over_the_week(self):
        self.offer(ago=timedelta(hours=30))
        self.assertEqual(counter_text({"version": 1}, self.now, ["E"]),
                         "Sur les 7 derniers jours, 1 offre passait ces filtres." + LOW)

    def test_zero_yesterday_with_enough_over_the_week(self):
        for _ in range(5):
            self.offer(ago=timedelta(hours=30))
        self.assertEqual(counter_text({"version": 1}, self.now, ["E"]), "Aucune offre d’hier ne passe.")

    def test_zero_over_the_week_names_the_strictest_filter_of_the_week(self):
        self.offer(ago=timedelta(hours=100), insee=PARIS, contract_type="CDI")
        self.offer(ago=timedelta(hours=24 * 8), insee=LYON, contract_type="CDI")
        criteria = {**where(("Lyon", LYON)), **contracts("cdi")}
        self.assertEqual(counter_text(criteria, self.now, ["E"]),
                         "Aucune offre des 7 derniers jours ne passe. Le filtre le plus strict : le lieu.")
        self.assertEqual(counter_text({"version": 1}, self.now, ["M18"]),
                         "Aucune offre des 7 derniers jours ne passe.")

    def test_above_150_says_it_is_wide(self):
        JobOffer.objects.bulk_create(
            JobOffer(source=self.source, external_id=f"b{i}", title="Poste", employer="Asso",
                     url="https://example.fr/") for i in range(1200))
        self.assertEqual(counter_text({"version": 1}, self.now, ["E"]),
                         "Hier, 1 200 offres passaient ces filtres. "
                         "C’est large : le bloc suivant va aider à trier.")

    def test_bounded_number_of_queries(self):
        self.offer(insee=PARIS, contract_type="CDI")
        criteria = {**where(("Paris", PARIS), distance="60", remote="hybrid"), **contracts("cdi", "cdd"),
                    **pay(30000), **hours("full")}
        with self.assertNumQueries(3):
            counter_text(criteria, self.now, ["E"])
        with self.assertNumQueries(4):  # nothing passes: one more query names the strictest filter
            counter_text({**criteria, **contracts("stage")}, self.now, ["E"])


class KeywordsTests(FilterTestCase):
    """Strict filter on the title alone: whole words, without accents or capitals."""

    def titled(self, *titles):
        return [self.offer(title=title) for title in titles]

    def test_include_needs_one_word_in_the_title(self):
        sound, light, other = self.titled("Régisseur son", "Technicien lumière", "Comptable")
        self.assertEqual(self.passing(keywords(include=["son", "lumière"])), {sound, light})
        self.assertIn(other, self.passing(keywords()))

    def test_exclude_drops_offers_with_one_word(self):
        stage, kept = self.titled("Stage en régie", "Régisseur")
        self.assertEqual(self.passing(keywords(exclude=["stage"])), {kept})

    def test_whole_word_only(self):
        trainee, stage = self.titled("Stagiaire en régie", "Stage de fin d’études")
        self.assertEqual(self.passing(keywords(exclude=["stage"])), {trainee})
        self.assertEqual(self.passing(keywords(include=["stage"])), {stage})

    def test_phrase(self):
        field, desk = self.titled("Commercial terrain H/F", "Commercial sédentaire terrain")
        self.assertEqual(self.passing(keywords(include=["commercial  terrain"])), {field})

    def test_accents_and_capitals_do_not_matter(self):
        offer, = self.titled("RÉGIE générale")
        self.assertEqual(self.passing(keywords(include=["regie"])), {offer})
        self.assertEqual(self.passing(keywords(include=["GÉNÉRALE"])), {offer})

    def test_gender_marks_are_dropped(self):
        dotted, bracketed, _ = self.titled("Chargé·e de communication", "Chargé(e) de mission (H/F)", "Comptable")
        self.assertEqual(self.passing(keywords(include=["chargé"])), {dotted, bracketed})
        self.assertEqual(self.passing(keywords(include=["Chargé·e"])), {dotted, bracketed})

    def test_special_characters_are_escaped(self):
        cpp, parity, other = self.titled("Développeur C++", "Chargé (h/f) de mission", "Chargé de projet")
        self.assertEqual(self.passing(keywords(include=["c++"])), {cpp})
        self.assertEqual(self.passing(keywords(include=["(h/f)"])), set())  # gender marks are not words
        self.assertEqual(self.passing(keywords(include=[".*"])), set())
        self.assertEqual(self.passing(keywords(include=["h|chargé"])), set())

    def test_include_and_exclude_together(self):
        kept, dropped, lacking = self.titled("Régisseur son", "Régisseur son stage", "Comptable")
        self.assertEqual(self.passing(keywords(include=["son"], exclude=["stage"])), {kept})

    def test_empty_lists_filter_nothing(self):
        offers = self.titled("Régisseur", "Comptable")
        self.assertEqual(self.passing(keywords()), set(offers))

    def test_the_filter_is_named(self):
        for _ in range(5):
            self.offer(ago=timedelta(hours=50), title="Régie générale")
        self.offer(title="Comptable")
        self.assertEqual(counter_text(keywords(include=["régie"]), self.now, ["E"]),
                         "Aucune offre d’hier ne passe. Le filtre le plus strict : vos mots-clés.")

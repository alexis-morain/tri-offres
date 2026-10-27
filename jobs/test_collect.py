"""Import of the actor's items (the design plan). No test reaches the network."""
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from jobs import collect
from jobs.scraper import Run
from jobs.test_scraper import run_stats_record, stats_line
from jobs.actor_sources import ACTOR_SOURCES
from jobs.models import JobOffer, Source
from jobs.testing import make_offer

FIXTURE = Path(__file__).parent / "fixtures" / "actor" / "dataset-sample.json"
NOW = datetime(2026, 10, 13, 22, 0, tzinfo=dt_timezone.utc)


def fixture_items():
    return json.loads(FIXTURE.read_text())


def item(**fields):
    """A clean hellowork item; `fields` override it."""
    return {"id": "abc", "source": "hellowork", "job_id": "hw-1", "title": "Comptable H/F", "company": "Cabinet",
            "url": "https://www.hellowork.com/fr-fr/emplois/1.html", "location_country": "FR",
            "location_city": "Montpellier", "location_postal_code": "34000", "contract_type": "CDI", **fields}


class SourcesMixin:
    """The 16 sources of the actor, as migration 0005 creates them."""

    def setUp(self):
        super().setUp()
        self.sources = collect.actor_sources()
        self.assertEqual(len(self.sources), len(ACTOR_SOURCES))


class ParseItemTests(SourcesMixin, TestCase):
    def parse(self, **fields):
        return collect.parse_item(item(**fields), self.sources)

    def assertRejected(self, reason, **fields):
        parsed = self.parse(**fields)
        self.assertIsNone(parsed.offer)
        self.assertEqual(parsed.reason, reason)

    def test_clean_item(self):
        parsed = self.parse()
        self.assertEqual(parsed.reason, "")
        self.assertEqual(parsed.source_key, "hellowork")
        offer = parsed.offer
        self.assertEqual(offer.source, self.sources["hellowork"])
        self.assertEqual((offer.external_id, offer.title, offer.employer), ("hw-1", "Comptable H/F", "Cabinet"))
        self.assertEqual(offer.contract_type, "CDI")
        self.assertEqual(offer.contract, "CDI")
        self.assertEqual(offer.insee, "34172")
        self.assertEqual(offer.rome_code, "M1203")
        self.assertEqual(offer.match_key, "comptable h f|cabinet|34172")
        self.assertEqual(offer.title_folded, "comptable")
        self.assertEqual(offer.summary, "")

    def test_rejection_reasons(self):
        self.assertRejected("unknown_source", source="jeveuxaider")
        self.assertRejected("unknown_source", source=None)
        self.assertRejected("country", location_country="BE")
        self.assertRejected("contract", contract_type="VIE")
        self.assertRejected("contract", contract_type="VIA")
        self.assertRejected("url", url=None)
        self.assertRejected("url", url="ftp://example.fr/offre")
        self.assertRejected("url", url="https://" + "a" * 1000)
        self.assertRejected("title", title="  \x07 ")
        self.assertRejected("title", title=None)
        self.assertRejected("title_letter", title="$1")
        self.assertRejected("title_letter", title=" 12 / 34 - ")
        self.assertEqual(self.parse(title="Él").reason, "")
        self.assertEqual(collect._reasons({"title_letter": 1, "title": 2}), "titre vide : 2, titre sans lettre : 1")
        self.assertRejected("id", job_id=None, id=None)
        self.assertRejected("id", job_id="", id="")

    def test_disabled_source(self):
        Source.objects.filter(actor_source="hellowork").update(state=Source.State.DISABLED)
        sources = collect.actor_sources()
        self.assertIsNone(collect.parse_item(item(), sources).offer)
        self.assertEqual(collect.parse_item(item(), sources).reason, "disabled_source")

    def test_javascript_url_rejected(self):
        self.assertRejected("url", url="javascript:alert(1)")
        self.assertRejected("url", url="JavaScript://example.fr/%0Aalert(1)")

    def test_apply_url_kept_only_when_http(self):
        self.assertEqual(self.parse(apply_url="https://example.fr/postuler").offer.apply_url,
                         "https://example.fr/postuler")
        self.assertEqual(self.parse(apply_url="javascript:alert(1)").offer.apply_url, "")
        self.assertEqual(self.parse(apply_url="https://" + "a" * 1000).offer.apply_url, "")
        self.assertEqual(self.parse(apply_url=12).offer.apply_url, "")

    def test_country_absent_or_france_passes(self):
        self.assertEqual(self.parse(location_country=None).offer.country, "")
        self.assertEqual(self.parse(location_country="FR").offer.country, "FR")

    def test_identifier_falls_back_to_id_and_is_truncated(self):
        self.assertEqual(self.parse(job_id=None, id="actor-id").offer.external_id, "actor-id")
        self.assertEqual(self.parse(job_id=12345).offer.external_id, "12345")
        self.assertEqual(len(self.parse(job_id="x" * 400).offer.external_id), 255)

    def test_lengths(self):
        offer = self.parse(title="T" * 400, company="C" * 300, location="L" * 300, location_city="V" * 300,
                           location_postal_code="1" * 20, location_department_code="9999", language="fra",
                           source_license="S" * 400, description="D" * 60_000).offer
        self.assertEqual([len(offer.title), len(offer.employer), len(offer.location), len(offer.city)],
                         [300, 200, 200, 200])
        self.assertEqual([len(offer.postal_code), len(offer.language)], [10, 2])
        self.assertEqual(offer.department_code, "")  # « 999 » is no department
        self.assertEqual([len(offer.source_license), len(offer.full_text)], [300, 50_000])

    def test_bidi_and_invisible_formatting_characters_removed(self):
        invisible = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200b\u200c\u200d\u200e\u200f\ufeff"
        offer = self.parse(title=f"Compta\u202eble{invisible} H/F", company=f"\ufeffCabinet\u200b",
                           description=f"Ligne\u2067 1\nLigne 2{invisible}").offer
        self.assertEqual((offer.title, offer.employer, offer.full_text), ("Comptable H/F", "Cabinet",
                                                                          "Ligne 1\nLigne 2"))

    def test_control_characters_and_edges(self):
        offer = self.parse(title=" Comptable\x00 H/F\x1b ", company="\tCabinet\u0085",
                           description=" Ligne 1\r\nLigne\x002 ").offer
        self.assertEqual(offer.title, "Comptable H/F")
        self.assertEqual(offer.employer, "Cabinet")
        self.assertEqual(offer.full_text, "Ligne 1\nLigne2")

    def test_values_of_the_wrong_type_are_dropped(self):
        offer = self.parse(company=["Cabinet"], location={"a": 1}, part_time="yes", remote_type=3,
                           salary_period=None, contract_type=7).offer
        self.assertEqual((offer.employer, offer.location, offer.remote_type), ("", "", ""))
        self.assertIsNone(offer.part_time)
        self.assertEqual((offer.contract_type, offer.contract), ("", ""))

    def test_enumerations(self):
        offer = self.parse(remote_type="partial", salary_period="month", contract_type="VOLONTARIAT",
                           part_time=True).offer
        self.assertEqual((offer.remote_type, offer.salary_period), ("partial", "month"))
        self.assertEqual((offer.contract_type, offer.contract), ("VOLONTARIAT", "Volontariat"))
        self.assertIs(offer.part_time, True)
        offer = self.parse(remote_type="hybrid", salary_period="yearly", contract_type="CDI_CHANTIER").offer
        self.assertEqual((offer.remote_type, offer.salary_period, offer.contract_type, offer.contract),
                         ("", "", "", ""))

    def test_amounts(self):
        offer = self.parse(salary_min=32000, salary_max=35000.456, daily_rate_min=450.5, daily_rate_max=550).offer
        self.assertEqual((offer.salary_min, offer.salary_max), (Decimal("32000.00"), Decimal("35000.46")))
        self.assertEqual((offer.daily_rate_min, offer.daily_rate_max), (Decimal("450.50"), Decimal("550.00")))

    def test_absurd_amounts_are_none(self):
        for value in (0, -1, 10_000_000, 1e300, float("inf"), float("nan"), True, "32000", None, [1]):
            with self.subTest(value=value):
                offer = self.parse(salary_min=value, salary_max=value, daily_rate_min=value,
                                   daily_rate_max=value).offer
                self.assertEqual({offer.salary_min, offer.salary_max, offer.daily_rate_min, offer.daily_rate_max},
                                 {None})

    def test_currency(self):
        self.assertEqual(self.parse(salary_currency="EUR").offer.salary_currency, "EUR")
        for value in ("eur", "EURO", "€", 978, None):
            with self.subTest(value=value):
                self.assertEqual(self.parse(salary_currency=value).offer.salary_currency, "")

    def test_dates(self):
        offer = self.parse(posted_at="2026-10-12T12:00:00+02:00", expires_at="2026-12-01T00:00:00").offer
        self.assertEqual(offer.published_at, datetime(2026, 10, 12, 10, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(offer.expires_at, datetime(2026, 12, 1, tzinfo=dt_timezone.utc))  # naive read as UTC
        offer = self.parse(posted_at="hier", expires_at=1760000000).offer
        self.assertIsNone(offer.published_at)
        self.assertIsNone(offer.expires_at)

    def test_place(self):
        offer = self.parse(location_city="Lyon", location_postal_code="75011", location_department_code="69",
                           location_department="Rhône", location_region="Auvergne-Rhône-Alpes").offer
        self.assertEqual(offer.insee, "")  # the postal code contradicts the city
        self.assertEqual(offer.match_key, "comptable h f|cabinet|lyon")
        self.assertEqual((offer.department_code, offer.department, offer.region), ("69", "Rhône",
                                                                                   "Auvergne-Rhône-Alpes"))
        self.assertEqual(self.parse(location_city="Lyon", location_postal_code=None,
                                    location_department_code="69").offer.insee, "69123")

    def test_department_and_region_without_commune(self):
        """the design plan, decision 2: read by jobs.geo.area_for."""
        def area(**fields):
            offer = self.parse(location_city=None, location_postal_code=None, **fields).offer
            return offer.insee, offer.department_code, offer.region_code

        self.assertEqual(area(location="Rhône (69)"), ("", "69", "84"))
        self.assertEqual(area(location="Rhône (69)", location_department_code="92"), ("", "92", "11"))
        self.assertEqual(area(location="Île-de-France, France", location_region="Île-de-France"), ("", "", "11"))
        self.assertEqual(area(location="France"), ("", "", ""))
        self.assertEqual(area(location="France", location_department_code="2a"), ("", "2A", "94"))
        offer = self.parse().offer  # Montpellier 34000
        self.assertEqual((offer.insee, offer.department_code, offer.region_code), ("34172", "34", "76"))

    def test_commune_and_postal_code_read_in_the_text(self):
        """the design plan, decisions 1 and 2: read by jobs.geo.place_for."""
        def place(**fields):
            offer = self.parse(**{"location_city": None, "location_postal_code": None, **fields}).offer
            return offer.insee, offer.zone_postal_code, offer.department_code, offer.region_code, offer.match_key

        self.assertEqual(place(location="Vienne 38200, FR"), ("38544", "", "38", "84", "comptable h f|cabinet|38544"))
        self.assertEqual(place(location="Le Touquet, France"), ("62826", "", "62", "32",
                                                                "comptable h f|cabinet|62826"))
        self.assertEqual(place(location="49500 Ailleurs"), ("", "49500", "49", "52", "comptable h f|cabinet|"))
        self.assertEqual(place(location="Ailleurs", location_postal_code="49500")[1], "49500")
        self.assertEqual(self.parse().offer.zone_postal_code, "")  # Montpellier 34000, a commune

    def test_unhashable_or_wrong_types_count_as_absent(self):
        for value in (["VIE"], {"a": 1}, 7):
            with self.subTest(contract_type=value):
                offer = self.parse(contract_type=value).offer
                self.assertEqual((offer.contract_type, offer.contract), ("", ""))
        self.assertEqual(self.parse(location_country=["BE"]).offer.country, "")
        offer = self.parse(remote_type=["full"], salary_period=["year"], salary_currency=["EUR"]).offer
        self.assertEqual((offer.remote_type, offer.salary_period, offer.salary_currency), ("", "", ""))
        self.assertRejected("title", title=["Comptable"])
        self.assertRejected("url", url="http://[::1/x")
        self.assertEqual(len(self.parse(job_id=10 ** 300).offer.external_id), 255)
        self.assertEqual(self.parse(job_id=10 ** 5000, id="fallback").offer.external_id, "fallback")

    def test_amounts_checked_after_rounding_and_overflow(self):
        for value in (10 ** 400, -10 ** 400, 0.004, 9_999_999.999, 1e-300):
            with self.subTest(value=value):
                self.assertIsNone(self.parse(salary_min=value).offer.salary_min)
        self.assertEqual(self.parse(salary_min=0.005).offer.salary_min, Decimal("0.01"))
        self.assertEqual(self.parse(salary_min=9_999_999.99).offer.salary_min, Decimal("9999999.99"))

    def test_dates_out_of_range(self):
        for value in ("0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00", "1999-12-31T23:00:00+00:00",
                      "2101-01-01T00:00:00"):
            with self.subTest(value=value):
                self.assertIsNone(self.parse(posted_at=value).offer.published_at)
        self.assertEqual(self.parse(posted_at="2026-10-12").offer.published_at,
                         datetime(2026, 10, 12, tzinfo=dt_timezone.utc))

    def test_urls_pass_django_validation(self):
        for url in ("http://exa mple.com/x", "https://a/", "http://", "https://exa_mple..com/x"):
            with self.subTest(url=url):
                self.assertRejected("url", url=url)
                self.assertEqual(self.parse(apply_url=url).offer.apply_url, "")
        self.assertEqual(self.parse(url="HTTP://EXAMPLE.COM/a").offer.url, "HTTP://EXAMPLE.COM/a")

    def test_unrecognized_title_has_no_rome_code(self):
        self.assertEqual(self.parse(title="Chargé·e de production").offer.rome_code, "")


class ImportTests(SourcesMixin, TestCase):
    def run_import(self, items=None, dry_run=False, batch_size=500):
        return collect.import_items(fixture_items() if items is None else items, self.sources, dry_run=dry_run,
                                    batch_size=batch_size)

    def test_fixture_import(self):
        result = self.run_import()
        self.assertEqual(JobOffer.objects.count(), 19)
        self.assertEqual((result.items, result.read, result.new, result.known), (20, 19, 19, 0))
        self.assertEqual(result.rejected, {"contract": 1})
        wttj = result.by_source["wttj"]
        self.assertEqual((wttj.read, wttj.new, wttj.known, dict(wttj.rejected)), (5, 5, 0, {"contract": 1}))
        self.assertEqual(result.by_source["hellowork"].read, 5)
        self.assertEqual(result.rome, 7)
        offer = JobOffer.objects.get(external_id="test-0005")
        self.assertEqual((offer.title, offer.contract_type, offer.contract, offer.insee), ("Comptable H/F", "", "",
                                                                                          "34172"))
        self.assertEqual(JobOffer.objects.get(external_id="test-0003").salary_min, None)
        eures = JobOffer.objects.get(external_id="VEVTVDAwMDEgOQ")
        self.assertEqual((eures.employer, eures.city, eures.insee), ("", "", ""))
        self.assertTrue(eures.full_text)

    def test_small_batches_give_the_same_import(self):
        result = self.run_import(batch_size=3)
        self.assertEqual((result.new, JobOffer.objects.count()), (19, 19))

    def test_reimport_creates_no_duplicate_and_changes_nothing(self):
        self.run_import()
        JobOffer.objects.filter(external_id="test-0005").update(title="Titre gardé")
        result = self.run_import()
        self.assertEqual((result.read, result.new, result.known), (19, 0, 19))
        self.assertEqual(JobOffer.objects.count(), 19)
        self.assertEqual(JobOffer.objects.get(external_id="test-0005").title, "Titre gardé")

    def test_repeat_inside_the_run_counts_as_known(self):
        result = self.run_import([item(), item(title="Autre titre")])
        self.assertEqual((result.new, result.known, JobOffer.objects.count()), (1, 1, 1))

    def test_dry_run_writes_nothing(self):
        result = self.run_import(dry_run=True)
        self.assertEqual(JobOffer.objects.count(), 0)
        self.assertEqual((result.read, result.new), (19, 19))
        self.assertEqual(len(result.new_offers), 19)

    def test_unknown_source_is_reported_under_its_key(self):
        result = self.run_import([item(source="jeveuxaider"), item(source=None)])
        self.assertEqual(result.by_source["jeveuxaider"].rejected, {"unknown_source": 1})
        self.assertEqual(result.by_source[""].rejected, {"unknown_source": 1})
        self.assertEqual(result.read, 0)


TRAPS = [
    item(job_id="t1", title="T" * 5000), item(job_id="t2", url="https://example.com/" + "a" * 1980),
    item(job_id="t3", apply_url="data:text/html,x"), item(job_id="t4", description="x" * 200_000),
    item(job_id="t5", title="Chef\x00 de\x07 projet\x1b[31m", company="Acme\x00\x85"),
    item(job_id="t6", salary_min="45000", salary_max=10 ** 400), item(job_id="t7", posted_at="2026-13-45"),
    item(job_id="t8", location_country="fr"), item(job_id="t9", contract_type=["VIE"]),
    item(job_id="t10", posted_at="0001-01-01T00:00:00+14:00"), item(job_id="t11", job_id_extra=None, language=["fr"]),
    item(job_id="t12", url="http://localhost/x"), item(job_id="t13", url="http://exa mple.com/x"),
    item(job_id="t14", remote_type=["full"], salary_period={"a": 1}, part_time="true", location_city=3),
]


class RobustImportTests(SourcesMixin, TestCase):
    def test_every_imported_offer_passes_model_validation(self):
        result = collect.import_items(fixture_items() + TRAPS, self.sources)
        self.assertEqual(result.rejected, {"contract": 1, "url": 2})
        self.assertEqual(JobOffer.objects.count(), 19 + 12)
        for offer in JobOffer.objects.all():
            with self.subTest(offer=offer.external_id):
                offer.full_clean()

    def test_a_batch_that_fails_is_written_offer_by_offer(self):
        real = JobOffer.objects.bulk_create

        def bulk_create(offers, **kwargs):
            if any(offer.external_id == "bad" for offer in offers):
                raise DatabaseError("refused")
            return real(offers, **kwargs)

        items = [item(job_id="a"), item(job_id="bad"), item(job_id="b")]
        with mock.patch.object(JobOffer.objects, "bulk_create", side_effect=bulk_create):
            result = collect.import_items(items, self.sources)
        self.assertEqual(sorted(JobOffer.objects.values_list("external_id", flat=True)), ["a", "b"])
        self.assertEqual((result.new, result.rejected), (2, {"unreadable": 1}))
        self.assertEqual(len(result.new_offers), 2)

    def test_items_that_are_not_objects_are_counted(self):
        class Items(list):
            skipped = 2

        result = collect.import_items(Items([item()]), self.sources)
        self.assertEqual(result.rejected, {"not_object": 2})
        self.assertEqual(result.items, 3)

    def test_reading_cut_keeps_the_offers_read(self):
        class Cut(Exception):
            pass

        def items():
            yield item(job_id="a")
            raise Cut("page 2 refusée")

        result = collect.import_items(items(), self.sources, stop_on=Cut)
        self.assertEqual((result.new, result.read_error), (1, "page 2 refusée"))
        self.assertEqual(JobOffer.objects.count(), 1)


class DuplicatesTests(SourcesMixin, TestCase):
    def test_fixture_has_none_the_key_can_see(self):
        # The two « Chargé·e de production » write the employer two ways: the key does not join them.
        result = collect.import_items(fixture_items(), self.sources, dry_run=True)
        self.assertEqual(collect.duplicates(result, NOW), 0)

    def test_same_offer_on_two_sources_in_the_run(self):
        items = [item(), item(source="wttj", job_id="w-1", url="https://www.welcometothejungle.com/1")]
        result = collect.import_items(items, self.sources, dry_run=True)
        self.assertEqual(collect.duplicates(result, NOW), 2)

    def test_same_offer_known_on_another_source_within_seven_days(self):
        old = make_offer(self.sources["wttj"], "w-old", match_key="comptable h f|cabinet|34172")
        result = collect.import_items([item()], self.sources, dry_run=False)
        self.assertEqual(collect.duplicates(result, timezone.now()), 1)
        JobOffer.objects.filter(pk=old.pk).update(collected_at=timezone.now() - timedelta(days=8))
        self.assertEqual(collect.duplicates(result, timezone.now()), 0)

    def test_same_source_is_not_a_duplicate(self):
        make_offer(self.sources["hellowork"], "hw-old", match_key="comptable h f|cabinet|34172")
        result = collect.import_items([item()], self.sources, dry_run=False)
        self.assertEqual(collect.duplicates(result, timezone.now()), 0)


class OffersBySourceAndDomainTests(SourcesMixin, TestCase):
    def test_counts_by_source_and_domain(self):
        culture = self.sources["profilculture"]
        make_offer(culture, "a", rome_code="M1203")  # E, K, L and M
        make_offer(self.sources["wttj"], "b")  # everyone
        make_offer(self.sources["wttj"], "c", rome_code="M1805")  # M18
        old = make_offer(self.sources["wttj"], "d")
        JobOffer.objects.filter(pk=old.pk).update(collected_at=timezone.now() - timedelta(days=8))
        by_source, by_domain = collect.offers_by_source_and_domain(collect.ImportResult(), timezone.now())
        self.assertEqual(by_source, {"ProfilCulture": 1, "Welcome to the Jungle": 2})
        self.assertEqual(by_domain, {"E": 1, "K": 1, "L": 1, "M": 1, None: 1, "M18": 1})

    def test_dry_run_adds_the_new_offers_of_the_run(self):
        result = collect.import_items([item()], self.sources, dry_run=True)
        by_source, by_domain = collect.offers_by_source_and_domain(result, timezone.now())
        self.assertEqual((by_source, by_domain), ({"HelloWork": 1}, {"M": 1}))

    def test_written_run_is_not_counted_twice(self):
        result = collect.import_items([item()], self.sources, dry_run=False)
        by_source, _ = collect.offers_by_source_and_domain(result, timezone.now())
        self.assertEqual(by_source, {"HelloWork": 1})


ZERO = {name: 0 for name in ("pushed", "filtered", "dupes", "cross_dupes", "already_seen", "invalid")}
KEYS = {"wttj", "eures", "meteojob", "taleez", "codeur", "collective", "hellowork"}
# A night of the actor: wttj sound, eures broken, meteojob blind, taleez quiet (nothing new), codeur stopped by its
# time budget after filtering one offer, collective sound; hellowork has no line.
RECORD = run_stats_record(
    wttj=stats_line(pushed=3, already_seen=12), eures=stats_line(**ZERO, failed="HTTP 503; retry"),
    meteojob=stats_line(**ZERO), taleez=stats_line(**{**ZERO, "already_seen": 4}),
    codeur=stats_line(**{**ZERO, "filtered": 1}, timed_out=True), collective=stats_line(**{**ZERO, "pushed": 2}))


class RunStatsLinesTests(SimpleTestCase):
    """Lines of the actor from its RUN_STATS record (spec 4.7)."""

    def test_lines_from_the_record(self):
        stats = run_stats_record(
            wttj=stats_line(), eures=stats_line(failed="HTTP 503", pushed=4), meteojob=stats_line(**ZERO),
            taleez=stats_line(**{**ZERO, "already_seen": 4}), codeur=stats_line(**{**ZERO, "invalid": 2}),
            profilculture=stats_line(timed_out=True), collective=stats_line(**ZERO, timed_out=True),
            hellowork=stats_line(failed=""), ats=stats_line(failed="x"))
        lines = collect.actor_lines(KEYS | {"profilculture"}, stats)
        self.assertEqual(set(lines), KEYS | {"profilculture"})  # ats was not asked
        self.assertEqual((lines["wttj"].pushed, lines["wttj"].cross_dupes, lines["wttj"].already_seen,
                          lines["wttj"].invalid, lines["wttj"].empty), (3, 2, 12, 0, False))
        self.assertEqual((lines["eures"].failed, lines["eures"].empty), ("HTTP 503", True))  # failed despite 4 pushed
        self.assertTrue(lines["meteojob"].empty)
        self.assertFalse(lines["taleez"].empty)
        self.assertFalse(lines["codeur"].empty)  # invalid offers are offers seen
        self.assertEqual((lines["profilculture"].timed_out, lines["profilculture"].empty), (True, False))
        self.assertEqual((lines["collective"].timed_out, lines["collective"].empty), (True, True))
        self.assertEqual((lines["hellowork"].failed, lines["hellowork"].empty), ("?", True))

    def test_failure_reason_is_cleaned_and_cut(self):
        # Words, not one long run: 43 url-safe characters in a row are masked as a token (S18).
        stats = run_stats_record(wttj=stats_line(failed="HTTP\n503\u202e " + "x " * 250))
        self.assertEqual(collect.actor_lines(KEYS, stats)["wttj"].failed,
                         ("HTTP 503 " + "x " * 250)[:collect.MAX_REASON].strip())

    def test_no_record_no_line(self):
        self.assertEqual(collect.actor_lines(KEYS, None), {})


def run(status="SUCCEEDED"):
    return Run(status, 0 if status == "SUCCEEDED" else None if status == "TIMED-OUT" else 1, 630.0)


class SourceTrackingTests(TestCase):
    def setUp(self):
        self.sources = collect.actor_sources()
        self.requested = collect.requested_sources(self.sources)

    def night(self, status="SUCCEEDED", stats=RECORD, dry_run=False, read_error=""):
        result = collect.ImportResult(dry_run=dry_run, read_error=read_error)
        return collect.track_sources(self.requested, run(status), result, NOW, stats=stats)

    def test_without_record_the_counters_stay(self):
        Source.objects.filter(actor_source="eures").update(empty_runs=1)
        self.requested = collect.requested_sources(collect.actor_sources())
        self.assertEqual(self.night(stats=None), [])
        self.assertEqual(self.state("eures"), (1, "active"))
        self.assertEqual(self.state("wttj"), (0, "active"))

    def test_timed_out_source_is_not_empty(self):
        Source.objects.filter(actor_source="profilculture").update(empty_runs=1)
        self.night(stats=run_stats_record(profilculture=stats_line(timed_out=True)))
        self.assertEqual(self.state("profilculture"), (0, "active"))

    def state(self, key):
        source = Source.objects.get(actor_source=key)
        return source.empty_runs, source.state

    def test_requested_sources(self):
        Source.objects.filter(actor_source="wttj").update(state=Source.State.DISABLED)
        keys = [source.actor_source for source in collect.requested_sources(collect.actor_sources())]
        self.assertEqual(keys, sorted(key for key in self.sources if key != "wttj"))

    def test_broken_or_blind_source_fails_on_the_second_night(self):
        self.assertEqual(self.night(), [])
        self.assertEqual((self.state("eures"), self.state("meteojob")), ((1, "active"), (1, "active")))
        failing = self.night()
        self.assertEqual(sorted(source.actor_source for source in failing), ["eures", "meteojob"])
        self.assertEqual((self.state("eures"), self.state("meteojob")), ((2, "failing"), (2, "failing")))
        self.assertTrue(Source.objects.get(actor_source="eures").last_run_at)

    def test_quiet_source_is_healthy_and_failing_comes_back(self):
        Source.objects.filter(actor_source="taleez").update(empty_runs=5, state=Source.State.FAILING)
        Source.objects.filter(actor_source="wttj").update(empty_runs=1)
        self.night()
        self.assertEqual((self.state("taleez"), self.state("wttj")), ((0, "active"), (0, "active")))

    def test_source_missing_from_the_record_is_unchanged(self):
        Source.objects.filter(actor_source="hellowork").update(empty_runs=1)
        self.night()
        self.assertEqual(self.state("hellowork"), (1, "active"))

    def test_nothing_touched_unless_the_run_succeeded_and_was_read_whole(self):
        for kwargs in ({"status": "FAILED"}, {"status": "TIMED-OUT"}, {"read_error": "HTTP 502"}):
            with self.subTest(**kwargs):
                self.assertEqual(self.night(**kwargs), [])
                self.assertFalse(Source.objects.filter(last_run_at__isnull=False).exists())
                self.assertFalse(Source.objects.exclude(empty_runs=0).exists())
        self.assertEqual(collect.track_sources(self.requested, None, collect.ImportResult(), NOW), [])

    def test_dry_run_tells_without_writing(self):
        Source.objects.filter(actor_source="eures").update(empty_runs=1)
        self.requested = collect.requested_sources(collect.actor_sources())
        failing = self.night(dry_run=True)
        self.assertEqual([source.actor_source for source in failing], ["eures"])
        self.assertEqual(self.state("eures"), (1, "active"))
        self.assertFalse(Source.objects.filter(last_run_at__isnull=False).exists())

    def test_disabled_meanwhile_is_left_alone(self):
        Source.objects.filter(actor_source="eures").update(state=Source.State.DISABLED, empty_runs=1)
        self.night()
        self.assertEqual(self.state("eures"), (1, "disabled"))
        self.assertIsNone(Source.objects.get(actor_source="eures").last_run_at)

    def test_next_state(self):
        source = self.sources["wttj"]
        source.empty_runs = 1
        self.assertEqual(collect.next_state(source, True), (2, Source.State.FAILING))
        self.assertEqual(collect.next_state(source, False), (0, Source.State.ACTIVE))
        self.assertEqual(collect.next_state(source, None), (1, Source.State.ACTIVE))

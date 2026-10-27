"""The actor run locally (the design plan): a fake actor (jobs/fixtures/fake_actor) is run by
the real subprocess. No test reaches the network."""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from jobs import scraper

FIXTURE = Path(__file__).parent / "fixtures" / "actor" / "dataset-sample.json"
FAKE_ACTOR = Path(__file__).parent / "fixtures" / "fake_actor"
ACTOR_FIELDS = (
    "id", "source", "source_type", "company", "job_id", "title", "url", "apply_url",
    "location", "location_city", "location_postal_code", "location_department_code",
    "location_department", "location_region", "location_country",
    "remote", "remote_type", "department", "employment_type", "contract_type", "part_time",
    "posted_at", "expires_at", "salary_min", "salary_max", "salary_currency", "salary_period",
    "daily_rate_min", "daily_rate_max", "description", "language", "source_license", "scraped_at",
)  # the actor's schema.FIELDS without "raw", which the actor drops when includeRaw is off
# Enumerations of the actor's schema.py, and the raw codes of its wttj and eures modules.
ACTOR_SOURCE_TYPES = ("ats", "jobboard", "public", "freelance")
ACTOR_CONTRACT_TYPES = ("CDI", "CDD", "STAGE", "ALTERNANCE", "INTERIM", "FREELANCE", "VIE", "VIA",
                        "SERVICE_CIVIQUE", "VOLONTARIAT", "FONCTION_PUBLIQUE", "AUTRE")
ACTOR_REMOTE_TYPES = ("full", "partial", "none")
ACTOR_SALARY_PERIODS = ("year", "month", "week", "day", "hour")
WTTJ_CONTRACTS = ("full_time", "part_time", "temporary", "internship", "apprenticeship", "freelance", "vie",
                  "volunteer", "other")
EURES_CODES = ("directhire", "temporary", "seasonal", "apprenticeship", "internship", "selfemployed", "volunteer")


def stats_line(**fields):
    return {"pushed": 3, "filtered": 1, "dupes": 0, "cross_dupes": 2, "already_seen": 12, "invalid": 0,
            "failed": None, "timed_out": False, "seconds": 40.5, **fields}


def run_stats_record(**sources):
    return {"version": 1, "sources": sources or {"wttj": stats_line()}}


class FakeActorMixin:
    """Each test gets its own copy of the fake actor and its own storage, run by the test's interpreter."""

    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.actor_dir = Path(folder.name) / "actor"
        shutil.copytree(FAKE_ACTOR, self.actor_dir, ignore=shutil.ignore_patterns("__pycache__"))
        self.storage = Path(folder.name) / "storage"
        self.storage.mkdir()
        scraper_settings = override_settings(SCRAPER_DIR=self.actor_dir, SCRAPER_PYTHON=sys.executable,
                                             SCRAPER_STORAGE=self.storage, SCRAPER_PROXY_URL="", SCRAPER_TIMEOUT_S=60)
        scraper_settings.enable()
        self.addCleanup(scraper_settings.disable)
        self.behave()

    def behave(self, **behaviour):
        """What the fake actor does at its next run: items, run_stats (a record or raw text), exit, sleep..."""
        (self.actor_dir / "behaviour.json").write_text(json.dumps(behaviour))

    def seen_by_actor(self, name):
        return json.loads((self.storage / f"fake-{name}.json").read_text())


@override_settings(SCRAPER_MAX_RESULTS_PER_SOURCE=100, SCRAPER_MAX_MINUTES_PER_SOURCE=15)
class BuildInputTests(SimpleTestCase):
    def test_nightly_input(self):
        self.assertEqual(scraper.build_input(["wttj", "eures"]), {
            "sources": ["wttj", "eures"], "countries": ["FR"], "languages": ["fr"], "mode": "new-only",
            "registryKey": "tri-offres", "includeDescription": True, "maxResultsPerSource": 100,
            "maxMinutesPerSource": 15,
        })

    def test_no_contract_filter_sent_to_the_actor(self):
        # The actor would drop offers without a contract type or with AUTRE (decision 13); 4.4 drops VIE and VIA.
        self.assertNotIn("contractTypes", scraper.build_input(["wttj"]))
        self.assertNotIn("contractTypes", scraper.build_input(["wttj"], posted_within_days=7, mode="all"))

    def test_empty_sources_refused_since_the_actor_would_read_them_all(self):
        for sources in ([], (), [""]):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                scraper.build_input(sources)

    def test_measurement_run(self):
        actor_input = scraper.build_input(["wttj"], posted_within_days=7, max_results=500, mode="all")
        self.assertEqual(actor_input["postedWithinDays"], 7)
        self.assertEqual(actor_input["maxResultsPerSource"], 500)
        self.assertEqual(actor_input["mode"], "all")
        self.assertNotIn("postedWithinDays", scraper.build_input(["wttj"]))

    def test_unknown_mode_refused(self):
        with self.assertRaises(ValueError):
            scraper.build_input(["wttj"], mode="everything")

    def test_registry_key_does_not_follow_the_sources(self):
        # Otherwise cutting one source would turn every offer of the others back to "new".
        self.assertEqual(scraper.build_input(["wttj"])["registryKey"], "tri-offres")
        self.assertEqual(scraper.build_input(["wttj", "hellowork"])["registryKey"], "tri-offres")

    @override_settings(SCRAPER_MAX_RESULTS_PER_SOURCE=40, SCRAPER_MAX_MINUTES_PER_SOURCE=5)
    def test_caps_come_from_settings(self):
        actor_input = scraper.build_input(["wttj"])
        self.assertEqual((actor_input["maxResultsPerSource"], actor_input["maxMinutesPerSource"]), (40, 5))



class RunTests(FakeActorMixin, SimpleTestCase):
    INPUT = {"sources": ["wttj"], "registryKey": "tri-offres"}

    def test_success_reads_input_items_and_record(self):
        record = run_stats_record()
        self.behave(items=[{"source": "wttj", "id": "1"}, {"source": "wttj", "id": "2"}], run_stats=record)
        run = scraper.run(self.INPUT)
        self.assertEqual((run.status, run.exit_code), ("SUCCEEDED", 0))
        self.assertGreaterEqual(run.duration_s, 0)
        self.assertEqual(self.seen_by_actor("input"), self.INPUT)
        self.assertEqual([item["id"] for item in scraper.iter_items()], ["1", "2"])
        self.assertEqual(scraper.run_stats(), record)

    def test_failure(self):
        self.behave(items=[{"source": "wttj", "id": "1"}], exit=1)
        run = scraper.run(self.INPUT)
        self.assertEqual((run.status, run.exit_code), ("FAILED", 1))
        self.assertEqual(len(list(scraper.iter_items())), 1)  # what it pushed before failing is read

    def test_timeout_kills_the_actor(self):
        self.behave(items=[{"source": "wttj", "id": "1"}], sleep=30)
        run = scraper.run(self.INPUT, timeout_s=1)
        self.assertEqual((run.status, run.exit_code), ("TIMED-OUT", None))
        self.assertLess(run.duration_s, 10)

    @override_settings(SCRAPER_TIMEOUT_S=1)
    def test_timeout_comes_from_settings(self):
        self.behave(sleep=30)
        self.assertEqual(scraper.run(self.INPUT).status, "TIMED-OUT")

    def test_actor_that_cannot_start(self):
        with override_settings(SCRAPER_PYTHON=str(self.storage / "no-python")), \
                self.assertRaises(scraper.ScraperError) as caught:
            scraper.run(self.INPUT)
        self.assertIn("SCRAPER_PYTHON", str(caught.exception))
        self.assertIsNone(caught.exception.__context__)

    def test_empty_dataset(self):
        self.behave()
        self.assertEqual(scraper.run(self.INPUT).status, "SUCCEEDED")
        items = scraper.iter_items()
        self.assertEqual(list(items), [])
        self.assertEqual((items.skipped, items.unreadable), (0, 0))
        self.assertIsNone(scraper.run_stats())

    def test_unreadable_items_are_counted_and_skipped(self):
        self.behave(items=[{"id": "1"}, {"__raw__": "{pas du json"}, ["liste"], {"__raw__": "\xff\xfe\x00"},
                           "texte", {"id": "2"}])
        scraper.run(self.INPUT)
        items = scraper.iter_items()
        self.assertEqual([item["id"] for item in items], ["1", "2"])  # __metadata__.json is not an item
        self.assertEqual((items.skipped, items.unreadable), (2, 2))

    def test_item_over_the_bound_is_unreadable(self):
        self.behave(items=[{"id": "1", "description": "x" * 200}, {"id": "2"}])
        scraper.run(self.INPUT)
        with mock.patch("jobs.scraper.MAX_ITEM_BYTES", 100):
            items = scraper.iter_items()
            self.assertEqual([item["id"] for item in items], ["2"])
        self.assertEqual(items.unreadable, 1)

    def test_no_dataset_folder_gives_no_item(self):
        self.assertEqual(list(scraper.iter_items()), [])

    def test_default_dataset_and_record_cleared_between_two_runs(self):
        self.behave(items=[{"id": str(number)} for number in range(3)], run_stats=run_stats_record())
        scraper.run(self.INPUT)
        self.behave(items=[{"id": "nouvelle"}])  # this one writes no RUN_STATS
        scraper.run(self.INPUT)
        self.assertEqual([item["id"] for item in scraper.iter_items()], ["nouvelle"])
        self.assertIsNone(scraper.run_stats())

    def test_dataset_of_an_actor_that_dies_early_is_not_the_previous_one(self):
        self.behave(items=[{"id": "ancienne"}], run_stats=run_stats_record())
        scraper.run(self.INPUT)
        self.behave(exit=1)
        scraper.run(self.INPUT)
        self.assertEqual(list(scraper.iter_items()), [])
        self.assertIsNone(scraper.run_stats())

    def test_named_store_kept_between_two_runs(self):
        scraper.run(self.INPUT)
        self.assertEqual(self.seen_by_actor("registry-before"), [])
        scraper.run(self.INPUT)
        self.assertEqual(self.seen_by_actor("registry-before"), ["tri-offres"])

    def test_environment_filtered(self):
        secrets = {"CLAUDE_CODE_OAUTH_TOKEN": "test-oauth-token", "ANTHROPIC_API_KEY": "test-api-key",
                   "RESEND_API_KEY": "test-mail-key", "DJANGO_SECRET_KEY": "secret-test", "APIFY_TOKEN": "apify-test",
                   "DATABASE_PATH": "/tmp/x.sqlite3", "HTTPS_PROXY": "http://parent.example:1",
                   "PYTHONPATH": "/tmp/elsewhere"}
        with mock.patch.dict(os.environ, {**secrets, "LANG": "fr_FR.UTF-8", "TZ": "Europe/Paris"}):
            scraper.run(self.INPUT)
        # macOS adds __CF_USER_TEXT_ENCODING to every process it starts.
        env = {name: value for name, value in self.seen_by_actor("env").items() if name != "__CF_USER_TEXT_ENCODING"}
        self.assertLessEqual(set(env), {*scraper.ENVIRONMENT, "APIFY_LOCAL_STORAGE_DIR"})
        self.assertEqual((env["LANG"], env["TZ"], env["APIFY_LOCAL_STORAGE_DIR"]),
                         ("fr_FR.UTF-8", "Europe/Paris", str(self.storage)))
        self.assertEqual(env["PATH"], os.environ["PATH"])
        for value in secrets.values():
            self.assertNotIn(value, json.dumps(env))

    def test_environment_holds_no_key_of_the_site_even_when_the_parent_has_some(self):
        prefixes = ("ANTHROPIC_", "RESEND_", "DJANGO_", "HEALTHCHECKS_", "LITESTREAM_", "ALERT_", "CLAUDE_")
        polluted = {f"{prefix}{name}": "secret-test" for prefix in prefixes for name in ("API_KEY", "TOKEN", "URL")}
        with mock.patch.dict(os.environ, polluted):
            scraper.run(self.INPUT)
            built = scraper.environment()
        seen = self.seen_by_actor("env")
        for env in (built, seen):
            self.assertEqual([name for name in env if name.startswith(prefixes)], [])
            self.assertNotIn("secret-test", json.dumps(env))

    @override_settings(SCRAPER_PROXY_URL="http://vpn-proxy:8888")
    def test_proxy_given_as_https_and_http_proxy(self):
        scraper.run(self.INPUT)
        env = self.seen_by_actor("env")
        self.assertEqual((env["HTTPS_PROXY"], env["HTTP_PROXY"]), ("http://vpn-proxy:8888", "http://vpn-proxy:8888"))

    def test_log_goes_to_a_file_of_the_storage_cut_at_its_end(self):
        scraper.run(self.INPUT)
        log = (self.storage / "actor.log").read_text()
        self.assertIn("fake actor: started", log)
        self.assertIn("fake actor: a warning", log)  # stderr too
        self.behave(log_bytes=2 * scraper.MAX_LOG_BYTES)
        scraper.run(self.INPUT)
        self.assertLessEqual((self.storage / "actor.log").stat().st_size, scraper.MAX_LOG_BYTES)

    @override_settings(SCRAPER_PROXY_URL="http://user:verysecretpass@vpn-proxy:8888")
    def test_log_cut_inside_a_secret_keeps_no_part_of_it(self):
        path = self.storage / "actor.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "start\nproxy http://user:verysecretpass@vpn-proxy:8888 refused\n"
        end = "\nfake actor: done\n"
        filler = "x" * (scraper.MAX_LOG_BYTES - (len(prefix) - prefix.index("ecretpass")) - len(end))
        # The kept window starts inside "verysecretpass": the remainder "ecretpass@vpn-proxy" matches no pattern.
        path.write_text(prefix + filler + end)
        self.assertTrue(path.read_bytes()[-scraper.MAX_LOG_BYTES:].startswith(b"ecretpass"))
        scraper._cut_log(path)
        kept = path.read_text()
        self.assertNotIn("ecretpass", kept)
        self.assertIn("fake actor: done", kept)


class InstalledTests(SimpleTestCase):
    """The image can be built without the actor (decision D14): the collection says so before any work."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "actor" / "src").mkdir(parents=True)
        (self.root / "actor" / "src" / "__main__.py").write_text("")
        (self.root / "python").write_text("")

    def installed(self, **changes):
        values = {"SCRAPER_DIR": self.root / "actor", "SCRAPER_PYTHON": str(self.root / "python"), **changes}
        with override_settings(**values):
            scraper.require_installed()

    def test_present_actor_passes(self):
        self.installed()

    def test_missing_folder_script_or_interpreter_are_refused_in_french(self):
        for changes in ({"SCRAPER_DIR": self.root / "absent"}, {"SCRAPER_PYTHON": str(self.root / "absent")},
                        {"SCRAPER_DIR": self.root}):
            with self.subTest(changes=changes), self.assertRaises(scraper.ScraperError) as raised:
                self.installed(**changes)
            self.assertEqual(str(raised.exception), "Le collecteur d’offres (actor) n’est pas installé : lancez "
                             "scripts/fetch_actor.sh puis reconstruisez l’image.")


class RunStatsTests(FakeActorMixin, SimpleTestCase):
    """The actor's statistics by source, read in the RUN_STATS record of the default key-value store
    (the design plan, section 1). The SDK writes it without an extension."""

    def read(self, record):
        path = self.storage / "key_value_stores" / "default" / "RUN_STATS"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(record if isinstance(record, bytes) else json.dumps(record).encode())
        return scraper.run_stats()

    def test_complete_record(self):
        record = run_stats_record(wttj=stats_line(), eures=stats_line(failed="HTTP 503", pushed=0),
                                  profilculture=stats_line(timed_out=True, seconds=None, invalid=4))
        stats = self.read(record)
        self.assertEqual(stats, record)
        self.assertIsInstance(stats["sources"]["wttj"]["seconds"], float)

    def test_whole_number_of_seconds_and_unknown_fields(self):
        # JSON writes 40.0 as 40 in some encoders; fields the actor adds later are dropped, never trusted.
        stats = self.read({"version": 1, "extra": "x", "sources": {"wttj": stats_line(seconds=40, note="x")}})
        self.assertEqual(stats, run_stats_record(wttj=stats_line(seconds=40.0)))

    def test_bounds(self):
        self.assertEqual((scraper.RUN_STATS_MAX_COUNT, scraper.RUN_STATS_MAX_SECONDS), (10_000_000, 1_000_000))
        line = stats_line(pushed=9_999_999, invalid=9_999_999, seconds=999_999.5)
        self.assertEqual(self.read(run_stats_record(wttj=line)), run_stats_record(wttj=line))

    def test_absent_record_gives_none(self):
        self.assertIsNone(scraper.run_stats())

    def test_unreadable_record_is_an_error(self):
        for raw in (b"<html>", b"\xff\xfe", b'{"version": 1, "sources": {}, "pad": "' + b"x" * (1024 * 1024) + b'"}',
                    b"1" * 5000):
            with self.subTest(raw=raw[:20]), self.assertRaises(scraper.ScraperError) as caught:
                self.read(raw)
            self.assertIn("RUN_STATS", str(caught.exception))
            self.assertIsNone(caught.exception.__context__)

    def test_record_of_an_unexpected_shape_gives_none(self):
        bad_records = [
            None, [], "RUN_STATS", 12, {}, {"sources": {"wttj": stats_line()}},
            {"version": 2, "sources": {"wttj": stats_line()}}, {"version": True, "sources": {"wttj": stats_line()}},
            {"version": "1", "sources": {"wttj": stats_line()}}, {"version": 1.0, "sources": {"wttj": stats_line()}},
            {"version": 1, "sources": []}, {"version": 1, "sources": None}, {"version": 1, "sources": {"wttj": []}},
        ]
        trapped_keys = ["../wttj", "WTTJ", "wttj ", "wttj\n", "", "a" * 51, "wttj;rm", "wttj-jobs", "été"]
        bad_lines = [
            *({name: value} for name in ("pushed", "filtered", "dupes", "cross_dupes", "already_seen", "invalid")
              for value in ("3", 1.5, True, -1, None)),
            {"failed": 12}, {"failed": True}, {"failed": ["x"]},
            {"timed_out": 0}, {"timed_out": "false"}, {"timed_out": None},
            {"seconds": "12"}, {"seconds": True}, {"seconds": -1}, {"seconds": float("nan")},
            {"seconds": float("inf")},
        ]
        bad_lines += [
            *({name: value} for name in ("pushed", "filtered", "dupes", "cross_dupes", "already_seen", "invalid")
              for value in (10 ** 400, scraper.RUN_STATS_MAX_COUNT)),
            {"seconds": 10 ** 400}, {"seconds": -10 ** 400}, {"seconds": 1e308}, {"seconds": 1_000_000},
            {"seconds": float("-inf")},
        ]
        for record in bad_records:
            with self.subTest(record=record):
                self.assertIsNone(self.read(record))
        for key in trapped_keys:
            with self.subTest(key=key):
                self.assertIsNone(self.read({"version": 1, "sources": {"wttj": stats_line(), key: stats_line()}}))
        for fields in bad_lines:
            with self.subTest(fields=fields):
                self.assertIsNone(self.read(run_stats_record(wttj=stats_line(**fields))))
        for missing in ("pushed", "invalid", "failed", "timed_out", "seconds"):
            with self.subTest(missing=missing):
                line = stats_line()
                del line[missing]
                self.assertIsNone(self.read(run_stats_record(wttj=line)))


class FixtureTests(SimpleTestCase):
    """The fixture was built by the actor's own normalizers (fictitious raw inputs), so 4.4 reads real shapes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.items = json.loads(FIXTURE.read_text())

    def test_fields_types_and_enumerations_follow_the_actor_schema(self):
        self.assertEqual(len(self.items), 20)
        for item in self.items:
            with self.subTest(id=item["id"]):
                self.assertEqual(tuple(item), ACTOR_FIELDS)
                self.assertRegex(item["id"], r"^[0-9a-f]{16}$")
                self.assertIn(item["source_type"], ACTOR_SOURCE_TYPES)
                self.assertIn(item["contract_type"], (*ACTOR_CONTRACT_TYPES, None))
                self.assertIn(item["remote_type"], (*ACTOR_REMOTE_TYPES, None))
                self.assertIn(item["salary_period"], (*ACTOR_SALARY_PERIODS, None))
                for name in ("source", "job_id", "title", "url", "scraped_at"):
                    self.assertIsInstance(item[name], str)
                for name in ("company", "apply_url", "location", "location_city", "location_postal_code",
                             "location_department_code", "location_department", "location_region",
                             "location_country", "department", "employment_type", "posted_at", "expires_at",
                             "salary_currency", "description", "language", "source_license"):
                    self.assertIsInstance(item[name], (str, type(None)), name)
                for name in ("remote", "part_time"):
                    self.assertIsInstance(item[name], (bool, type(None)), name)
                for name in ("salary_min", "salary_max", "daily_rate_min", "daily_rate_max"):
                    self.assertTrue(item[name] is None or type(item[name]) in (int, float), name)
                for name in ("posted_at", "expires_at", "scraped_at"):
                    if item[name]:
                        self.assertIsNotNone(datetime.fromisoformat(item[name]).tzinfo, name)

    def test_cases_needed_by_the_import(self):
        items = self.items
        self.assertLessEqual({"wttj", "hellowork", "eures", "profilculture", "service_civique", "free_work",
                              "free_work_jobs"}, {item["source"] for item in items})
        self.assertLessEqual({"year", "month", "hour", "day", None}, {item["salary_period"] for item in items})
        self.assertTrue(any(item["daily_rate_min"] for item in items))
        self.assertTrue(any(item["part_time"] is True for item in items))
        self.assertLessEqual({"full", "partial"}, {item["remote_type"] for item in items})
        self.assertLessEqual({"VIE", "AUTRE", None}, {item["contract_type"] for item in items})
        self.assertTrue(any(item["company"] is None for item in items))
        self.assertTrue(any(item["location_city"] is None for item in items))
        self.assertTrue(any(len(item["title"]) > 300 for item in items))

    def test_places(self):
        by_place = {(item["location_city"], item["location_postal_code"]): item for item in self.items}
        # 01400 is shared by ten communes; the city lifts the doubt.
        self.assertIn(("Châtillon-sur-Chalaronne", "01400"), by_place)
        # Postal code against the city (Paris's code typed for a mission in Lyon): the department follows the city.
        self.assertEqual(by_place[("Lyon", "75011")]["location_department_code"], "69")
        # Homonyms in 93 and 974: no department chosen.
        self.assertIsNone(by_place[("Saint-Denis", None)]["location_department_code"])

    def test_one_offer_on_two_sources_that_the_actor_did_not_merge(self):
        # Employer written two ways: different for the actor's cross_key (company|title|city), so both came.
        pair = [item for item in self.items if item["title"] == "Chargé·e de production"]
        self.assertEqual({item["source"] for item in pair}, {"profilculture", "hellowork"})
        self.assertEqual({item["location_city"] for item in pair}, {"Lyon"})
        self.assertEqual(len({item["company"] for item in pair}), 2)

    def test_raw_values_as_each_source_gives_them(self):
        for item in self.items:
            source, remote, remote_type = item["source"], item["remote"], item["remote_type"]
            with self.subTest(id=item["id"], source=source):
                if source == "wttj":
                    self.assertIn(item["employment_type"], WTTJ_CONTRACTS)
                    self.assertIsNot(item["part_time"], False)  # full_time means CDI at WTTJ, not full time
                    self.assertEqual(remote, remote_type == "full" if remote_type else None)
                elif source in ("free_work", "free_work_jobs"):
                    self.assertEqual(remote, remote_type == "full" if remote_type else None)
                elif source == "hellowork":
                    self.assertEqual(remote, True if remote_type == "full" else None)
                elif source == "service_civique":
                    self.assertEqual(item["employment_type"], "Service civique")
                    self.assertIsInstance(remote, bool)
                elif source == "eures":
                    self.assertIn(item["employment_type"], EURES_CODES)

    def test_texts_and_identifiers_are_fictitious(self):
        for item in self.items:
            with self.subTest(id=item["id"]):
                self.assertIn("Texte fictif", item["description"])
                # EURES ids are base64 of "<reference> <point>": the reference shows in the apply link.
                self.assertIn("test", f"{item['job_id']} {item['apply_url']}".lower())


class SettingsTests(SimpleTestCase):
    def test_defaults(self):
        root = Path(settings.BASE_DIR)
        self.assertEqual(settings.SCRAPER_DIR, root / "vendor" / "actor")
        self.assertEqual(settings.SCRAPER_PYTHON, str(root / "vendor" / "actor-venv" / "bin" / "python"))
        self.assertEqual(settings.SCRAPER_TIMEOUT_S, 5400)
        self.assertEqual(settings.SCRAPER_MAX_RESULTS_PER_SOURCE, 1000)  # 4.7: 500 is reached in 7 days
        # 30: ProfilCulture, HelloWork and Taleez were cut at 15 on 18/10 (the design plan)
        self.assertEqual(settings.SCRAPER_MAX_MINUTES_PER_SOURCE, 30)

    def test_actor_ref_is_a_whole_commit(self):
        self.assertRegex((Path(settings.BASE_DIR) / "ACTOR_REF").read_text(), r"^[0-9a-f]{40}\n$")

    def test_web_service_never_receives_personal_tokens(self):
        compose = (Path(settings.BASE_DIR) / "docker-compose.yml").read_text()
        self.assertNotRegex(compose, r"(?m)^\s+env_file\s*:")
        self.assertNotRegex(compose, r"(?i)apify")

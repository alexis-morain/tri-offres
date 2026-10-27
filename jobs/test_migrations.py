"""Data migrations of jobs: 0003 (segments become ROME domains), 0005 (sources of the actor, then of kind "apify"),
0007 (their kind becomes "actor"), 0009 (more sources of the actor), 0011 (folded titles)."""
from collections import Counter
from importlib import import_module

from django.test import SimpleTestCase

from config.testing import MigrationTestCase
from jobs.actor_sources import ACTOR_SOURCES

BEFORE = [("jobs", "0002_segment_growth_label")]
AFTER = [("jobs", "0003_source_domains")]


class SourceDomainsMigrationTests(MigrationTestCase):
    migrate_from = BEFORE
    migrate_to = AFTER

    def setUpBeforeMigration(self, apps):
        Source = apps.get_model("jobs", "Source")
        for name, segment in (("Profession Spectacle", "culture"), ("Welcome", "growth"), ("Toutes", "")):
            Source.objects.create(name=name, kind="module", segment=segment)

    def domains(self, apps):
        return dict(apps.get_model("jobs", "Source").objects.values_list("name", "domains"))

    def test_segments_become_domains(self):
        self.assertEqual(self.domains(self.apps),
                         {"Profession Spectacle": ["E", "K", "L"], "Welcome": ["D", "M17"], "Toutes": []})
        fields = {field.name for field in self.apps.get_model("jobs", "Source")._meta.get_fields()}
        self.assertNotIn("segment", fields)

    def test_backwards(self):
        apps = self.migrate(BEFORE)
        segments = dict(apps.get_model("jobs", "Source").objects.values_list("name", "segment"))
        self.assertEqual(segments, {"Profession Spectacle": "culture", "Welcome": "growth", "Toutes": ""})



SCHEMA = [("jobs", "0004_collect_fields")]
SEEDED = [("jobs", "0005_actor_sources")]
MIGRATION_SOURCES = import_module("jobs.migrations.0005_actor_sources").ACTOR_SOURCES


class ActorSourcesMigrationTests(MigrationTestCase):
    migrate_from = SCHEMA
    migrate_to = SEEDED

    def setUpBeforeMigration(self, apps):
        Source = apps.get_model("jobs", "Source")
        Source.objects.create(name="Mon WTTJ", kind="apify", actor_source="wttj", state="disabled", domains=["E"])
        Source.objects.create(name="Profession Spectacle", kind="module")

    def sources(self, apps):
        return apps.get_model("jobs", "Source").objects

    def test_sources_created_with_their_domains(self):
        rows = {row[0]: row for row in
                self.sources(self.apps).filter(kind="apify").values_list("actor_source", "name", "url", "domains")}
        self.assertEqual(set(rows), {key for key, _, _, _ in MIGRATION_SOURCES})
        for key, name, url, domains in MIGRATION_SOURCES:
            if key != "wttj":
                self.assertEqual(rows[key], (key, name, url, domains))
        created = self.sources(self.apps).filter(kind="apify").exclude(actor_source="wttj")
        self.assertEqual(set(created.values_list("state", flat=True)), {"active"})

    def test_existing_source_neither_duplicated_nor_modified(self):
        wttj = self.sources(self.apps).get(actor_source="wttj")
        self.assertEqual((wttj.name, wttj.state, wttj.domains), ("Mon WTTJ", "disabled", ["E"]))

    def test_rerun_creates_nothing(self):
        import_module("jobs.migrations.0005_actor_sources").forwards(self.apps, None)
        self.assertEqual(self.sources(self.apps).filter(kind="apify").count(), len(MIGRATION_SOURCES))

    def test_backwards_keeps_sources_with_offers_and_other_sources(self):
        Source = self.apps.get_model("jobs", "Source")
        hellowork = Source.objects.get(actor_source="hellowork")
        self.apps.get_model("jobs", "JobOffer").objects.create(source=hellowork, external_id="1", title="Régisseur",
                                                               url="https://example.fr/1")
        Source.objects.create(name="Autre actor", kind="apify", actor_source="ailleurs")
        apps = self.migrate(SCHEMA)
        kept = apps.get_model("jobs", "Source").objects.values_list("name", flat=True)
        self.assertEqual(set(kept), {"HelloWork", "Autre actor", "Profession Spectacle"})


class ActorSourcesAlignmentTests(SimpleTestCase):
    def test_module_and_migrations_0005_0009_aligned_today(self):
        """Adapt when a later migration adds a source to jobs/actor_sources.py."""
        self.assertEqual([tuple(source) for source in ACTOR_SOURCES],
                         list(MIGRATION_SOURCES) + list(MORE_MIGRATION_SOURCES))


BEFORE_KIND = [("jobs", "0006_offer_prepass_tracking")]
AFTER_KIND = [("jobs", "0007_source_kind_actor")]


class SourceKindActorMigrationTests(MigrationTestCase):
    """0007: the actor runs locally, its sources change kind from "apify" to "actor"."""
    migrate_from = BEFORE_KIND
    migrate_to = AFTER_KIND

    def setUpBeforeMigration(self, apps):
        Source = apps.get_model("jobs", "Source")
        for key in ("wttj", "hellowork"):
            Source.objects.create(name=key, kind="apify", actor_source=key)
        Source.objects.create(name="Profession Spectacle", kind="module")

    def kinds(self, apps):
        return Counter(apps.get_model("jobs", "Source").objects.values_list("kind", flat=True))

    def test_actor_sources_change_kind(self):
        self.assertEqual(self.kinds(self.apps), {"actor": 2, "module": 1})

    def test_backwards(self):
        apps = self.migrate(BEFORE_KIND)
        self.assertEqual(self.kinds(apps), {"apify": 2, "module": 1})


BEFORE_MORE = [("jobs", "0008_joboffer_region_code")]
AFTER_MORE = [("jobs", "0009_more_actor_sources")]
MORE_MIGRATION_SOURCES = import_module("jobs.migrations.0009_more_actor_sources").ACTOR_SOURCES


class MoreActorSourcesMigrationTests(MigrationTestCase):
    """0009: FHF and the French ATS of the actor's second wave (the design plan)."""
    migrate_from = BEFORE_MORE
    migrate_to = AFTER_MORE

    def setUpBeforeMigration(self, apps):
        Source = apps.get_model("jobs", "Source")
        Source.objects.create(name="Mon Softy", kind="actor", actor_source="softy", state="disabled", domains=["E"])
        Source.objects.create(name="HelloWork", kind="actor", actor_source="hellowork")

    def added(self, apps):
        keys = [key for key, _, _, _ in MORE_MIGRATION_SOURCES]
        return apps.get_model("jobs", "Source").objects.filter(kind="actor", actor_source__in=keys)

    def test_sources_created_with_their_domains(self):
        rows = {row[0]: row for row in self.added(self.apps).values_list("actor_source", "name", "url", "domains")}
        self.assertEqual(set(rows), {"fhf", "digitalrecruiters", "flatchr", "breezy", "join", "softy"})
        for key, name, url, domains in MORE_MIGRATION_SOURCES:
            if key != "softy":
                self.assertEqual(rows[key], (key, name, url, domains))
        self.assertEqual(rows["fhf"][3], ["J"])
        created = self.added(self.apps).exclude(actor_source="softy")
        self.assertEqual(set(created.values_list("state", flat=True)), {"active"})

    def test_existing_source_neither_duplicated_nor_modified(self):
        softy = self.added(self.apps).get(actor_source="softy")
        self.assertEqual((softy.name, softy.state, softy.domains), ("Mon Softy", "disabled", ["E"]))

    def test_rerun_creates_nothing(self):
        import_module("jobs.migrations.0009_more_actor_sources").forwards(self.apps, None)
        self.assertEqual(self.added(self.apps).count(), len(MORE_MIGRATION_SOURCES))

    def test_backwards_keeps_sources_with_offers_and_the_older_ones(self):
        Source = self.apps.get_model("jobs", "Source")
        fhf = Source.objects.get(actor_source="fhf")
        self.apps.get_model("jobs", "JobOffer").objects.create(source=fhf, external_id="1", title="Infirmier",
                                                               url="https://example.fr/1")
        apps = self.migrate(BEFORE_MORE)
        kept = set(apps.get_model("jobs", "Source").objects.values_list("actor_source", flat=True))
        self.assertIn("fhf", kept)
        self.assertNotIn("join", kept)
        self.assertIn("hellowork", kept)


class TitleFoldedMigrationTests(MigrationTestCase):
    migrate_from = [("jobs", "0010_zone_postal_code")]
    migrate_to = [("jobs", "0011_joboffer_title_folded")]

    def setUpBeforeMigration(self, apps):
        source = apps.get_model("jobs", "Source").objects.create(name="Toutes", kind="module")
        apps.get_model("jobs", "JobOffer").objects.create(
            source=source, external_id="1", title="Régisseur Général (H/F)", url="https://example.fr/1")

    def test_existing_offers_get_their_folded_title(self):
        offer = self.apps.get_model("jobs", "JobOffer").objects.get()
        self.assertEqual(offer.title_folded, "regisseur general")

    def test_backwards(self):
        apps = self.migrate([("jobs", "0010_zone_postal_code")])
        self.assertEqual(apps.get_model("jobs", "JobOffer").objects.count(), 1)

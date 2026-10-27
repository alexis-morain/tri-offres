"""`manage.py collecte` (the design plan, the design plan), with the fake
actor run by the real subprocess: no network."""
import json
from collections import Counter
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from jobs.actor_sources import ACTOR_SOURCES
from jobs.models import JobOffer, Source
from jobs.test_scraper import FakeActorMixin, run_stats_record, stats_line
from jobs.testing import make_offer

FIXTURE = Path(__file__).parent / "fixtures" / "actor" / "dataset-sample.json"
ITEMS = json.loads(FIXTURE.read_text())
ABSENT = object()


def night_record():
    """The actor's record for the fixture: meteojob broken, taleez quiet (nothing new), the other empty sources
    blind (nothing seen at all)."""
    pushed = Counter(item["source"] for item in ITEMS)
    lines = {key: stats_line(pushed=pushed[key], filtered=0, dupes=0, cross_dupes=0, already_seen=0)
             for key in sorted(source.key for source in ACTOR_SOURCES)}
    lines["meteojob"] = stats_line(failed="HTTP 503", pushed=0, filtered=0, dupes=0, cross_dupes=0, already_seen=0)
    lines["taleez"]["already_seen"] = 12
    return run_stats_record(**lines)


class CollecteCommandTests(FakeActorMixin, TestCase):
    def night(self, items=ITEMS, exit=0, args=(), run_stats=None, **behaviour):
        """Run the command on a night of the fake actor; (output, input the actor received, error or None).
        RUN_STATS is night_record() unless `run_stats` gives another record, raw text, or ABSENT."""
        run_stats = night_record() if run_stats is None else run_stats
        self.behave(items=items, exit=exit, **behaviour, **({} if run_stats is ABSENT else {"run_stats": run_stats}))
        out = StringIO()
        error = None
        try:
            call_command("collecte", *args, stdout=out)
        except CommandError as raised:
            error = raised
        actor_input = self.seen_by_actor("input") if (self.storage / "fake-input.json").exists() else None
        return out.getvalue(), actor_input, error

    def sources(self):
        return {source.actor_source: source for source in Source.objects.exclude(actor_source="")}

    def assertUntouched(self):
        self.assertFalse(Source.objects.filter(last_run_at__isnull=False).exists())
        self.assertFalse(Source.objects.exclude(empty_runs=0).exists())

    def test_night_run_imports_the_dataset(self):
        output, actor_input, error = self.night()
        self.assertIsNone(error)
        self.assertEqual(JobOffer.objects.count(), 19)
        self.assertEqual(actor_input["sources"], sorted(source.key for source in ACTOR_SOURCES))
        self.assertEqual((actor_input["mode"], actor_input["registryKey"]), ("new-only", "tri-offres"))
        self.assertNotIn("postedWithinDays", actor_input)
        self.assertIn(f"Actor lancé sur {len(ACTOR_SOURCES)} sources, attente de sa fin.", output)

    def test_sources_tracked_from_the_actor_lines(self):
        self.night()
        sources = self.sources()
        self.assertTrue(all(source.last_run_at for source in sources.values()))
        runs = {key: source.empty_runs for key, source in sources.items()}
        self.assertEqual((runs["wttj"], runs["taleez"], runs["meteojob"], runs["codeur"]), (0, 0, 1, 1))
        self.assertEqual(sources["meteojob"].state, Source.State.ACTIVE)

    def test_second_empty_night_turns_failing_and_a_sound_night_brings_it_back(self):
        Source.objects.filter(actor_source="meteojob").update(empty_runs=1)
        Source.objects.filter(actor_source="taleez").update(empty_runs=3, state=Source.State.FAILING)
        output, _, _ = self.night()
        sources = self.sources()
        self.assertEqual((sources["meteojob"].empty_runs, sources["meteojob"].state), (2, Source.State.FAILING))
        self.assertEqual((sources["taleez"].empty_runs, sources["taleez"].state), (0, Source.State.ACTIVE))
        self.assertIn("Passent « En échec » (deux nuits en panne ou sans rien voir) : meteojob.", output)

    def test_disabled_source_neither_asked_nor_imported(self):
        Source.objects.filter(actor_source="hellowork").update(state=Source.State.DISABLED, empty_runs=4)
        output, actor_input, _ = self.night()
        self.assertNotIn("hellowork", actor_input["sources"])
        self.assertEqual(len(actor_input["sources"]), len(ACTOR_SOURCES) - 1)
        self.assertFalse(JobOffer.objects.filter(source__actor_source="hellowork").exists())
        self.assertEqual(JobOffer.objects.count(), 14)
        hellowork = self.sources()["hellowork"]
        self.assertEqual((hellowork.empty_runs, hellowork.last_run_at), (4, None))
        self.assertIn("hellowork (HelloWork) : 0 lue, 0 nouvelle, 0 déjà connue, 5 écartées "
                      "(source désactivée : 5)",
                      output)

    def test_no_active_source(self):
        Source.objects.update(state=Source.State.DISABLED)
        output, actor_input, error = self.night()
        self.assertIn("Aucune source de l’actor active", str(error))
        self.assertIsNone(actor_input)

    def test_dry_run_writes_nothing_but_runs_the_actor(self):
        Source.objects.filter(actor_source="meteojob").update(empty_runs=1)
        before = list(Source.objects.order_by("pk").values_list("empty_runs", "state", "last_run_at"))
        output, actor_input, error = self.night(args=["--dry-run"])
        self.assertIsNone(error)
        self.assertIsNotNone(actor_input)
        self.assertEqual(JobOffer.objects.count(), 0)
        self.assertEqual(list(Source.objects.order_by("pk").values_list("empty_runs", "state", "last_run_at")),
                         before)
        self.assertIn("essai à blanc, rien n’est écrit", output)
        self.assertIn("Passeraient « En échec »", output)
        self.assertIn("collecte : 19 lues, 19 nouvelles", output)
        self.assertNotIn("Aucune offre nouvelle", output)

    def test_second_night_on_the_same_offers(self):
        self.night()
        output, _, _ = self.night()
        self.assertEqual(JobOffer.objects.count(), 19)
        self.assertIn("collecte : 19 lues, 0 nouvelle, 19 déjà connues", output)

    def test_measurement_options(self):
        _, actor_input, _ = self.night(args=["--posted-within-days", "7", "--all-jobs"])
        self.assertEqual((actor_input["postedWithinDays"], actor_input["mode"]), (7, "all"))

    def test_failed_run_imports_exits_non_zero_and_tracks_nothing(self):
        output, _, error = self.night(exit=1)
        self.assertIn("l’actor a fini en FAILED", str(error))
        self.assertEqual(JobOffer.objects.count(), 19)
        self.assertUntouched()
        self.assertIn("Actor lancé en local : FAILED", output)
        self.assertIn("code de sortie 1.", output)
        self.assertIn("collecte : 19 lues", output)

    @override_settings(SCRAPER_TIMEOUT_S=1)
    def test_timed_out_run_imports_what_was_pushed_and_tracks_nothing(self):
        output, _, error = self.night(sleep=30)
        self.assertIn("l’actor a fini en TIMED-OUT", str(error))
        self.assertEqual(JobOffer.objects.count(), 19)
        self.assertUntouched()
        self.assertIn("Actor lancé en local : TIMED-OUT, 1 s, tué à son délai.", output)

    def test_actor_that_cannot_start(self):
        broken = self.storage / "not-executable"
        broken.write_text("")  # present, so the actor counts as installed, but the system refuses to run it
        with override_settings(SCRAPER_PYTHON=str(broken)):
            _, _, error = self.night()
        self.assertIn("Actor impossible à lancer", str(error))
        self.assertEqual(JobOffer.objects.count(), 0)
        self.assertUntouched()

    def test_actor_not_installed_stops_before_any_work(self):
        message = ("Le collecteur d’offres (actor) n’est pas installé : lancez scripts/fetch_actor.sh puis "
                   "reconstruisez l’image.")
        for changes in ({"SCRAPER_PYTHON": str(self.storage / "no-python")},
                        {"SCRAPER_DIR": self.storage / "no-actor"}):
            with self.subTest(changes=changes), override_settings(**changes):
                output, actor_input, error = self.night()
                self.assertEqual(str(error), message)
                self.assertIsNone(actor_input)  # the actor never ran
                self.assertEqual(output, "")
                self.assertEqual(JobOffer.objects.count(), 0)
                self.assertUntouched()
                self.assertFalse((self.storage / "actor.log").exists())

    def test_succeeded_run_without_offer_exits_zero_with_a_warning(self):
        output, _, error = self.night(items=[])
        self.assertIsNone(error)
        self.assertIn("  Aucune offre nouvelle cette nuit.", output)
        self.assertIn("0 élément dans le dataset", output)

    def test_items_not_objects_or_unreadable_are_reported(self):
        output, _, error = self.night(items=ITEMS + ["texte", 12, {"__raw__": "{pas du json"}])
        self.assertIsNone(error)
        self.assertIn("23 éléments dans le dataset : 19 offres lues (19 nouvelles, 0 déjà connue), 4 écartées "
                      "(VIE ou VIA : 1, pas un objet : 2, illisible : 1).", output)

    def test_actor_log_stays_out_of_the_report(self):
        output, _, _ = self.night()
        self.assertNotIn("fake actor", output)
        self.assertIn("fake actor: started", (self.storage / "actor.log").read_text())

    def test_report_on_the_fixture(self):
        make_offer(Source.objects.get(actor_source="wttj"), "older", title="Comptable H/F",
                   employer="Cabinet Exemple", match_key="comptable h f|cabinet exemple|34172", rome_code="M1203")
        output, _, _ = self.night()
        for heading in ("1. Run", "2. Par source", "3. Codes ROME", "4. Doublons", "5. Sur 7 jours"):
            self.assertIn(f"\n{heading}", output)
        self.assertRegex(output, r"  Actor lancé en local : SUCCEEDED, \d+ s, code de sortie 0\.")
        self.assertIn("20 éléments dans le dataset : 19 offres lues (19 nouvelles, 0 déjà connue), 1 écartée "
                      "(VIE ou VIA : 1).", output)
        self.assertIn("  Lignes de l’actor : enregistrement RUN_STATS du run.", output)
        self.assertIn("wttj (Welcome to the Jungle) : 5 lues, 5 nouvelles, 0 déjà connue, 1 écartée "
                      "(VIE ou VIA : 1)"
                      " ; actor : 6 poussées, 0 filtrée, 0 doublon, 0 doublon entre sources, 0 déjà vue, "
                      "0 invalide", output)
        self.assertIn("meteojob (Meteojob) : 0 lue, 0 nouvelle, 0 déjà connue, 0 écartée ; actor : en panne : "
                      "HTTP 503", output)
        self.assertIn("taleez (Taleez) : 0 lue, 0 nouvelle, 0 déjà connue, 0 écartée ; actor : 0 poussée, "
                      "0 filtrée, 0 doublon, 0 doublon entre sources, 12 déjà vues", output)
        self.assertIn("Demandées sans offre lue : agences_livre, breezy, choisir_service_public, codeur, collective, "
                      "digitalrecruiters, emploi_territorial, fhf, flatchr, join, livreshebdo, meteojob, softy, "
                      "taleez, teamtailor.", output)
        self.assertIn("Ensemble : 7 sur 19 (37 %).", output)
        self.assertIn("hellowork : 3 sur 5 (60 %).", output)
        # The hellowork accountant was already on wttj.
        self.assertIn("dans le run ou sur 7 jours : 1 sur 19 (5 %).", output)
        self.assertIn("Welcome to the Jungle : 6", output)
        self.assertIn("ProfilCulture : 1", output)
        self.assertIn("Achats / Comptabilité / Gestion : 2", output)
        self.assertIn("Communication / Multimédia : 1", output)  # with K and L: the culture source of the pair
        self.assertIn("Spectacle : 1", output)
        self.assertIn("Informatique / Télécommunication : 3", output)  # Free-Work serves M18
        self.assertIn("Toutes les personnes : 10", output)
        self.assertTrue(output.rstrip().endswith(
            "collecte : 19 lues, 19 nouvelles, 0 déjà connue, 1 écartée, ROME 7 sur 19 (37 %), 1 doublon, "
            "run SUCCEEDED"))


class RunStatsCommandTests(FakeActorMixin, TestCase):
    """The lines of the actor from its RUN_STATS record (spec 4.7); without it, no line and no counter moves."""

    night = CollecteCommandTests.night
    sources = CollecteCommandTests.sources

    def record(self):
        """Every source sound, but meteojob broken, profilculture stopped by its time budget, codeur with invalid
        offers only."""
        pushed = Counter(item["source"] for item in ITEMS)
        lines = {key: stats_line(pushed=pushed[key], filtered=0, dupes=0, cross_dupes=0, already_seen=3)
                 for key in sorted(source.key for source in ACTOR_SOURCES)}
        lines["meteojob"] = stats_line(failed="HTTP 503", pushed=0)
        lines["profilculture"] = stats_line(pushed=1, timed_out=True)
        lines["codeur"] = stats_line(pushed=0, filtered=0, dupes=0, cross_dupes=0, already_seen=0, invalid=2)
        return run_stats_record(**lines)

    def assertCountersUnchanged(self):
        sources = self.sources()
        self.assertTrue(all(source.last_run_at for source in sources.values()))
        self.assertEqual((sources["meteojob"].empty_runs, sources["codeur"].empty_runs), (1, 0))

    def test_record_gives_the_lines(self):
        Source.objects.filter(actor_source="meteojob").update(empty_runs=1)
        output, _, error = self.night(run_stats=self.record())
        self.assertIsNone(error)
        sources = self.sources()
        self.assertEqual((sources["meteojob"].empty_runs, sources["meteojob"].state), (2, Source.State.FAILING))
        self.assertEqual(sources["profilculture"].empty_runs, 0)  # stopped by its budget, not empty
        self.assertEqual(sources["codeur"].empty_runs, 0)  # invalid offers are offers seen
        self.assertIn("\n2. Par source (offres, puis la ligne de l’actor)\n"
                      "  Lignes de l’actor : enregistrement RUN_STATS du run.\n", output)
        self.assertIn("profilculture (ProfilCulture) : 1 lue, 1 nouvelle, 0 déjà connue, 0 écartée ; actor : "
                      "1 poussée, 1 filtrée, 0 doublon, 2 doublons entre sources, 12 déjà vues, 0 invalide, "
                      "arrêtée par son budget de temps", output)
        self.assertIn("codeur (Codeur.com) : 0 lue, 0 nouvelle, 0 déjà connue, 0 écartée ; actor : 0 poussée, "
                      "0 filtrée, 0 doublon, 0 doublon entre sources, 0 déjà vue, 2 invalides", output)
        self.assertIn("  Arrêtées par leur budget de temps : profilculture.", output)
        self.assertIn("Passent « En échec » (deux nuits en panne ou sans rien voir) : meteojob.", output)

    def test_absent_record_says_so_and_moves_no_counter(self):
        Source.objects.filter(actor_source="meteojob").update(empty_runs=1)
        output, _, error = self.night(run_stats=ABSENT)
        self.assertIsNone(error)  # the offers are in
        self.assertEqual(JobOffer.objects.count(), 19)
        self.assertIn("  Lignes de l’actor : aucune (RUN_STATS absent ou inutilisable), compteurs des sources "
                      "inchangés.", output)
        self.assertNotIn("; actor :", output)
        self.assertCountersUnchanged()

    def test_unreadable_record_says_why(self):
        Source.objects.filter(actor_source="meteojob").update(empty_runs=1)
        output, _, error = self.night(run_stats="<html>")
        self.assertIsNone(error)
        self.assertIn("  Lignes de l’actor : aucune (RUN_STATS illisible : RUN_STATS n’est pas du JSON), compteurs "
                      "des sources inchangés.", output)
        self.assertCountersUnchanged()

    def test_malformed_record_moves_no_counter(self):
        Source.objects.filter(actor_source="meteojob").update(empty_runs=1)
        for record in ({"version": 1, "sources": {"../meteojob": stats_line()}},
                       run_stats_record(wttj=stats_line(seconds=10 ** 400)),
                       run_stats_record(wttj=stats_line(pushed=10 ** 400)),
                       run_stats_record(wttj=stats_line(seconds=1e308))):
            with self.subTest(record=str(record)[:60]):
                output, _, error = self.night(run_stats=record)
                self.assertIsNone(error)
                self.assertIn("(RUN_STATS absent ou inutilisable)", output)
                self.assertTrue(output.rstrip().endswith("run SUCCEEDED"))
                self.assertCountersUnchanged()

    def test_record_of_the_previous_night_is_never_read_again(self):
        self.night(run_stats=self.record())
        output, _, _ = self.night(run_stats=ABSENT)
        self.assertIn("(RUN_STATS absent ou inutilisable)", output)

    def test_failed_run_still_reports_the_record_but_tracks_nothing(self):
        output, _, error = self.night(exit=1, run_stats=self.record())
        self.assertIn("FAILED", str(error))
        self.assertIn("Lignes de l’actor : enregistrement RUN_STATS du run.", output)
        self.assertFalse(Source.objects.filter(last_run_at__isnull=False).exists())

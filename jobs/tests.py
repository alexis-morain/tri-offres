from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from config.testing import PLAIN_STATIC
from jobs.actor_sources import ACTOR_SOURCES
from jobs.models import JobOffer, Source
from jobs.testing import make_offer, make_source
from matching.models import Feedback, Score
from onboarding import rome


class SourceTests(TestCase):
    def test_defaults(self):
        source = make_source()
        self.assertEqual(source.state, Source.State.ACTIVE)
        self.assertEqual(source.domains, [])
        self.assertIsNone(source.owner)

    def test_deleting_owner_keeps_source_offers_and_other_users_data(self):
        # Offers are shared: deleting the user who added a source must not wipe other users' scores.
        owner = User.objects.create_user("owner@example.fr")
        other = User.objects.create_user("other@example.fr")
        source = make_source(owner=owner)
        offer = make_offer(source)
        score = Score.objects.create(user=other, offer=offer, score=7, category=Score.Category.TO_REVIEW)
        feedback = Feedback.objects.create(user=other, offer=offer, verdict=Feedback.Verdict.YES)

        owner.delete()

        source.refresh_from_db()
        self.assertIsNone(source.owner)
        self.assertTrue(JobOffer.objects.filter(pk=offer.pk).exists())
        self.assertTrue(Score.objects.filter(pk=score.pk).exists())
        self.assertTrue(Feedback.objects.filter(pk=feedback.pk).exists())

    def test_collect_fields_defaults(self):
        source = make_source()
        self.assertEqual((source.actor_source, source.empty_runs, source.last_run_at), ("", 0, None))

    def test_actor_source_unique_when_set(self):
        make_source(kind=Source.Kind.ACTOR, actor_source="test_board")
        with self.assertRaises(IntegrityError):
            make_source(name="Doublon", kind=Source.Kind.ACTOR, actor_source="test_board")

    def test_many_sources_without_actor_source(self):
        make_source()
        make_source(name="Autre")
        self.assertEqual(Source.objects.filter(actor_source="").count(), 2)


class ActorSourcesTests(TestCase):
    def test_twenty_two_unique_keys(self):
        keys = [source.key for source in ACTOR_SOURCES]
        self.assertEqual(len(keys), 22)
        self.assertEqual(len(set(keys)), 22)

    def test_domains_normalized(self):
        for source in ACTOR_SOURCES:
            with self.subTest(key=source.key):
                self.assertEqual(rome.normalize_domains(source.domains, cap=None), source.domains)

    def test_seeded_by_the_migration(self):
        seeded = {source.actor_source: source for source in Source.objects.filter(kind=Source.Kind.ACTOR)}
        self.assertEqual(set(seeded), {source.key for source in ACTOR_SOURCES})
        self.assertEqual(seeded["free_work"].domains, ["M18"])
        self.assertEqual(seeded["fhf"].domains, ["J"])
        self.assertEqual(seeded["softy"].domains, [])
        self.assertEqual(seeded["wttj"].state, Source.State.ACTIVE)


class JobOfferTests(TestCase):
    def test_external_id_unique_per_source(self):
        source = make_source()
        make_offer(source, "42")
        with self.assertRaises(IntegrityError):
            make_offer(source, "42")

    def test_same_external_id_allowed_on_another_source(self):
        make_offer(make_source(), "42")
        make_offer(make_source(name="Welcome to the Jungle", kind=Source.Kind.ATS_FEED), "42")
        self.assertEqual(JobOffer.objects.count(), 2)

    def test_new_fields_may_stay_empty(self):
        offer = make_offer(make_source(), employer="")
        offer.refresh_from_db()
        self.assertEqual((offer.contract_type, offer.remote_type, offer.match_key, offer.rome_code), ("", "", "", ""))
        self.assertIsNone(offer.part_time)
        self.assertIsNone(offer.salary_min)
        self.assertIsNone(offer.expires_at)

    def test_long_url_accepted(self):
        url = "https://example.fr/" + "a" * 900
        offer = make_offer(make_source(), url=url, apply_url=url)
        offer.full_clean()

    def test_purge_full_text_blanks_only_the_queryset(self):
        source = make_source()
        old = make_offer(source, "1", full_text="long text")
        empty = make_offer(source, "2", full_text="")
        kept = make_offer(source, "3", full_text="still needed")

        count = JobOffer.objects.filter(pk__in=[old.pk, empty.pk]).purge_full_text()

        self.assertEqual(count, 1)
        old.refresh_from_db()
        kept.refresh_from_db()
        self.assertEqual(old.full_text, "")
        self.assertEqual(kept.full_text, "still needed")


@override_settings(STORAGES=PLAIN_STATIC)
class AdminTestCase(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("alexis@example.fr", "x"))


class SourceAdminTests(AdminTestCase):

    def post(self, domains):
        data = {"name": "Hôpitaux", "kind": "module", "state": "active", "url": "", "domains": domains}
        return self.client.post(reverse("admin:jobs_source_add"), data)

    def test_served_domains_column(self):
        make_source(domains=["E", "K", "L"])
        make_source(name="Toutes")
        html = self.client.get(reverse("admin:jobs_source_changelist")).content.decode()
        self.assertIn("Domaines servis", html)
        self.assertIn("E, K, L", html)
        self.assertIn("Tous", html)

    def test_known_codes_saved_in_the_order_of_the_list(self):
        self.assertEqual(self.post('["J", "E", "J"]').status_code, 302)
        self.assertEqual(Source.objects.get(name="Hôpitaux").domains, ["E", "J"])

    def test_unknown_codes_are_refused(self):
        for value in ('["Z"]', '"E"', '[["E"]]', '{"E": 1}'):
            with self.subTest(value=value):
                response = self.post(value)
                self.assertEqual(response.status_code, 200)
                self.assertIn("domains", response.context["adminform"].form.errors)
        self.assertFalse(Source.objects.filter(name="Hôpitaux").exists())

    def change(self, source, state):
        data = {"name": source.name, "kind": source.kind, "state": state, "url": source.url, "domains": "[]",
                "actor_source": source.actor_source}
        return self.client.post(reverse("admin:jobs_source_change", args=[source.pk]), data)

    def test_reactivation_resets_empty_runs(self):
        source = make_source(state=Source.State.FAILING, empty_runs=2)
        self.assertEqual(self.change(source, "active").status_code, 302)
        source.refresh_from_db()
        self.assertEqual((source.state, source.empty_runs), (Source.State.ACTIVE, 0))

    def test_other_edits_keep_empty_runs(self):
        for before, after in (("active", "active"), ("failing", "disabled")):
            with self.subTest(before=before, after=after):
                source = make_source(name=f"{before} {after}", state=before, empty_runs=1)
                self.assertEqual(self.change(source, after).status_code, 302)
                source.refresh_from_db()
                self.assertEqual(source.empty_runs, 1)

    def test_collect_columns(self):
        response = self.client.get(reverse("admin:jobs_source_changelist"), {"q": "wttj"})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for column in ("Source de l’actor", "Nuits sans offre", "Dernière collecte", "Welcome to the Jungle"):
            self.assertIn(column, html)


class JobOfferAdminTests(AdminTestCase):

    def test_changelist_columns(self):
        make_offer(make_source(), city="Lyon", contract_type=JobOffer.Contract.CDD, rome_code="L1301")
        response = self.client.get(reverse("admin:jobs_joboffer_changelist"), {"q": "L1301"})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for text in ("Ville", "Type de contrat", "Code ROME", "Lyon", "CDD", "L1301", "Chargée de production"):
            self.assertIn(text, html)

    def test_filters(self):
        response = self.client.get(reverse("admin:jobs_joboffer_changelist"), {"remote_type__exact": "full"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["cl"].list_filter),
                         ["source", "contract_type", "remote_type", "country", "rome_by_claude"])

    def test_prepass_fields_read_only(self):
        offer = make_offer(make_source(), rome_code="J1506", rome_by_claude=True, summary="Soins en service de nuit.")
        response = self.client.get(reverse("admin:jobs_joboffer_change", args=[offer.pk]))
        self.assertEqual(response.status_code, 200)
        form = response.context["adminform"].form
        self.assertNotIn("rome_by_claude", form.fields)
        self.assertNotIn("summary_written_at", form.fields)
        html = response.content.decode()
        self.assertIn("Code ROME par Claude", html)
        self.assertIn("Pré-passe faite le", html)

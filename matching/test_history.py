"""« Mes offres »: the person's verdicts by tab, their application status and note (plans/2026-10-27-mes-offres-suivi.md).
Offers are fictitious."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.testing import make_finished_profile
from config.testing import PLAIN_STATIC, assert_french_copy, assert_unique_ids
from jobs.testing import OfferMaker, make_source
from matching.models import Application, Feedback, Score

HISTORY = "/mes-offres/"
YES, NO, MAYBE = Feedback.Verdict.YES, Feedback.Verdict.NO, Feedback.Verdict.MAYBE


@override_settings(STORAGES=PLAIN_STATIC)
class HistoryTests(OfferMaker, TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.source = make_source(name="Source fictive")
        self.user = make_finished_profile("nora@example.fr").user
        self.client.force_login(self.user)

    def judged(self, verdict=YES, user=None, **fields):
        offer = self.offer(**fields)
        user = user or self.user
        Score.objects.create(user=user, offer=offer, score=7, category=Score.Category.TO_REVIEW)
        Feedback.objects.create(user=user, offer=offer, verdict=verdict)
        return offer

    def page(self, tab=""):
        response = self.client.get(HISTORY + (f"?avis={tab}" if tab else ""))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def save(self, offer, **data):
        return self.client.post(f"{HISTORY}{offer.pk}/", {"verdict": YES, "avis": YES, **data})

    def test_each_tab_lists_its_verdicts_latest_first(self):
        old = self.judged(YES, title="Régisseuse ancienne")
        Feedback.objects.filter(offer=old).update(created_at=self.now - timedelta(days=3))
        self.judged(YES, title="Régisseuse récente")
        self.judged(NO, title="Comptable")
        html = self.page()
        self.assertLess(html.index("Régisseuse récente"), html.index("Régisseuse ancienne"))
        self.assertNotIn("Comptable", html)
        self.assertIn("Comptable", self.page(NO))
        self.assertNotIn("Régisseuse", self.page(MAYBE))

    def test_a_changed_or_cancelled_verdict_counts_as_its_latest_standing_one(self):
        offer = self.judged(YES, title="Chargée de diffusion")
        Feedback.objects.create(user=self.user, offer=offer, verdict=NO)
        self.assertNotIn("Chargée de diffusion", self.page(YES))
        Feedback.objects.filter(offer=offer, verdict=NO).update(cancelled=True)
        self.assertIn("Chargée de diffusion", self.page(YES))
        Feedback.objects.filter(offer=offer).update(cancelled=True)
        for tab in (YES, NO, MAYBE):
            self.assertNotIn("Chargée de diffusion", self.page(tab))

    def test_another_persons_verdicts_are_not_shown(self):
        other = make_finished_profile("autre@example.fr").user
        self.judged(YES, user=other, title="Offre de quelqu’un d’autre")
        self.assertNotIn("quelqu’un d’autre", self.page())

    def test_unknown_tab_falls_back_to_yes_and_page_is_french_with_unique_ids(self):
        self.judged(YES, title="Régisseuse")
        html = self.page("<script>")
        self.assertIn("Régisseuse", html)
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    def test_status_and_note_are_saved_and_shown(self):
        offer = self.judged(YES, title="Régisseuse")
        response = self.save(offer, status=Application.Status.INTERVIEW, note="Appeler Jeanne mardi")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], f"{HISTORY}?avis=yes#offre-{offer.pk}")
        row = Application.objects.get(user=self.user, offer=offer)
        self.assertEqual((row.status, row.note), (Application.Status.INTERVIEW, "Appeler Jeanne mardi"))
        html = self.page()
        self.assertIn("Appeler Jeanne mardi", html)
        self.assertRegex(html, r'<option value="interview" selected>Entretien</option>')

    def test_the_note_is_escaped(self):
        offer = self.judged(YES)
        self.save(offer, status=Application.Status.TO_APPLY, note="<b>gras</b>")
        self.assertIn("&lt;b&gt;gras&lt;/b&gt;", self.page())

    def test_changing_the_verdict_moves_the_offer_and_stays_on_the_tab(self):
        offer = self.judged(YES, title="Régisseuse")
        response = self.client.post(f"{HISTORY}{offer.pk}/", {"verdict": NO, "avis": YES})
        self.assertEqual(response["Location"], f"{HISTORY}?avis=yes#offre-{offer.pk}")
        self.assertEqual(Feedback.objects.current_verdict(self.user, offer.pk), NO)
        self.assertIn("Régisseuse", self.page(NO))

    def test_bad_input_is_refused_and_writes_nothing(self):
        offer = self.judged(YES)
        for data in ({"verdict": "peut-etre"}, {"status": "embauche"}, {"note": "x" * 1001}):
            self.assertEqual(self.save(offer, **data).status_code, 400)
        self.assertFalse(Application.objects.exists())

    def test_an_offer_without_the_persons_score_is_a_404(self):
        other = make_finished_profile("autre@example.fr").user
        offer = self.judged(YES, user=other)
        self.assertEqual(self.save(offer, status=Application.Status.APPLIED).status_code, 404)
        self.assertEqual(self.client.post(f"{HISTORY}999999/", {"verdict": YES}).status_code, 404)
        self.assertFalse(Application.objects.exists())

    def test_signed_out_is_sent_to_sign_in(self):
        self.client.logout()
        self.assertEqual(self.client.get(HISTORY).status_code, 302)

    def test_menu_has_both_entries(self):
        html = self.page()
        self.assertRegex(html, r'<a href="/tri/"[^>]*>À trier</a>\s*'
                               r'<a href="/mes-offres/"[^>]*aria-current="page"[^>]*>Mes offres</a>')

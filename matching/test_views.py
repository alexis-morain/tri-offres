"""The sort page and its two POST (sub-step 6.1): matching/views.py. Contract: the design plan
and its « Exigences de security-review ». Offers are fictitious."""
import json
import re
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Profile, User
from accounts.testing import make_finished_profile
from config.testing import PLAIN_STATIC, assert_french_copy, assert_unique_ids
from jobs.testing import OfferMaker, make_source
from matching import deck
from matching.models import Feedback, Score, ScoringRun
from onboarding import flow

SORT, VERDICT, UNDO = "/tri/", "/tri/avis/", "/tri/annuler/"
JSON = {"HTTP_ACCEPT": "application/json"}
PARIS = ZoneInfo("Europe/Paris")
ATTRIBUTION = "Données Source fictive, licence ouverte"
EMPTY = "Tout est trié. Les prochaines offres arrivent avec le mail de demain matin."
YES, NO, MAYBE = Feedback.Verdict.YES, Feedback.Verdict.NO, Feedback.Verdict.MAYBE
CARD_KEYS = {"id", "title", "employer", "location", "contract", "date", "score", "reason", "summary", "exploration",
             "source", "attribution", "url"}


@override_settings(STORAGES=PLAIN_STATIC)
class SortTestCase(OfferMaker, TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.source = make_source(name="Source fictive")
        self.user = make_finished_profile("nora@example.fr").user
        self.client.force_login(self.user)

    def offer_defaults(self, number):
        return {"source_license": ATTRIBUTION}

    def scored(self, score=7, user=None, reason="Vous retrouvez la régie que vous cherchez.", exploration=False,
               **fields):
        offer = self.offer(**fields)
        Score.objects.create(user=user or self.user, offer=offer, score=score, reason=reason,
                             category=Score.Category.TO_REVIEW, is_exploration=exploration)
        return offer

    def post(self, path, data=None, client=None, json_mode=False, **extra):
        return (client or self.client).post(path, data or {}, **(JSON if json_mode else {}), **extra)

    def verdict(self, offer, verdict=YES, **kwargs):
        return self.post(VERDICT, {"offer": offer.pk if hasattr(offer, "pk") else offer, "verdict": verdict}, **kwargs)

    def rows(self, user=None):
        return Feedback.objects.filter(user=user or self.user)

    def cards(self, response):
        match = re.search(r'<script id="cards" type="application/json">(.*?)</script>', response.content.decode(),
                          re.DOTALL)
        return json.loads(match.group(1))


class AccessTests(SortTestCase):
    """Requirements 4, 5, 7, 16."""

    def setUp(self):
        super().setUp()
        self.one = self.scored()

    def attempts(self, client):
        """Every request that must write nothing, as a list of (response, json_mode)."""
        return [(client.get(SORT), False), (client.post(VERDICT, {"offer": self.one.pk, "verdict": "yes"}), False),
                (client.post(UNDO, {"offer": self.one.pk}), False),
                (client.post(VERDICT, {"offer": self.one.pk, "verdict": "yes"}, **JSON), True),
                (client.post(UNDO, {"offer": self.one.pk}, **JSON), True)]

    def assert_refused(self, client, location):
        for response, json_mode in self.attempts(client):
            with self.subTest(json=json_mode, path=response.request["PATH_INFO"]):
                if json_mode:
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json(), {"ok": False})
                else:
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response["Location"].startswith(location), response["Location"])
        self.assertEqual(Feedback.objects.count(), 0)

    def test_anonymous_is_sent_to_the_login_page_or_gets_401_in_json(self):
        self.assert_refused(Client(), "/")

    def test_a_deactivated_account_is_refused(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assert_refused(self.client, "/")

    def test_an_account_without_profile_is_redirected_never_a_500(self):
        bare = User.objects.create_user("bare@example.fr")
        client = Client()
        client.force_login(bare)
        self.assert_refused(client, "/demarrer/")
        self.assertFalse(Profile.objects.filter(user=bare).exists())

    def test_unfinished_questionnaire_is_sent_back_and_writes_nothing(self):
        profile = Profile.objects.get(user=self.user)
        Profile.objects.filter(pk=profile.pk).update(onboarding_completed_at=None)
        profile.refresh_from_db()
        self.assertFalse(flow.is_finished(profile))
        self.assert_refused(self.client, flow.resume_url(profile))

    def test_unfinished_profile_resumes_where_it_stopped(self):
        profile = Profile.objects.get(user=self.user)
        Profile.objects.filter(pk=profile.pk).update(onboarding_completed_at=None, onboarding_step="lieu")
        profile.refresh_from_db()
        response = self.client.get(SORT)
        self.assertRedirects(response, flow.resume_url(profile), fetch_redirect_response=False)

    def test_paused_and_unsubscribed_accounts_still_sort(self):
        for status in (User.Status.PAUSED, User.Status.UNSUBSCRIBED):
            with self.subTest(status=status):
                User.objects.filter(pk=self.user.pk).update(status=status)
                self.assertEqual(self.client.get(SORT).status_code, 200)
                self.assertEqual(self.verdict(self.one, json_mode=True).json(), {"ok": True})
                self.rows().delete()

    def test_methods(self):
        self.assertEqual(self.client.head(SORT).status_code, 200)
        self.assertEqual(self.client.post(SORT).status_code, 405)
        for path in (VERDICT, UNDO):
            for method in ("get", "put", "delete", "patch"):
                with self.subTest(path=path, method=method):
                    response = getattr(self.client, method)(path, {"offer": self.one.pk, "verdict": "yes"})
                    self.assertEqual(response.status_code, 405)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_fields_are_read_from_the_post_body_only(self):
        self.assertEqual(self.client.post(f"{VERDICT}?offer={self.one.pk}&verdict=yes").status_code, 400)
        self.assertEqual(Feedback.objects.count(), 0)


class CsrfTests(SortTestCase):
    """Requirement 6."""

    def setUp(self):
        super().setUp()
        self.one = self.scored()
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def token(self):
        self.client.get(SORT)
        return self.client.cookies["csrftoken"].value

    def test_both_posts_refuse_a_missing_token(self):
        for json_mode in (False, True):
            for path in (VERDICT, UNDO):
                with self.subTest(path=path, json=json_mode):
                    response = self.post(path, {"offer": self.one.pk, "verdict": "yes"}, json_mode=json_mode)
                    self.assertEqual(response.status_code, 403)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_both_posts_refuse_a_foreign_origin_even_with_a_valid_token(self):
        token = self.token()
        for json_mode in (False, True):
            for path in (VERDICT, UNDO):
                with self.subTest(path=path, json=json_mode):
                    response = self.post(path, {"offer": self.one.pk, "verdict": "yes"}, json_mode=json_mode,
                                         HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://evil.example")
                    self.assertEqual(response.status_code, 403)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_a_valid_token_from_the_site_goes_through(self):
        token = self.token()
        response = self.post(VERDICT, {"offer": self.one.pk, "verdict": "yes"}, json_mode=True,
                             HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="http://testserver")
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(self.rows().count(), 1)

    def test_the_page_carries_the_token_for_the_form_and_the_script(self):
        self.assertContains(self.client.get(SORT), "csrfmiddlewaretoken")


class VerdictTests(SortTestCase):
    def setUp(self):
        super().setUp()
        self.one = self.scored()

    def test_without_javascript_a_303_back_to_the_sort(self):
        response = self.verdict(self.one, NO)
        self.assertRedirects(response, SORT, status_code=303, fetch_redirect_response=False)
        self.assertEqual(Feedback.objects.current_verdict(self.user, self.one), NO)

    def test_in_json_a_constant_body(self):
        for verdict in (YES, NO, MAYBE):
            response = self.verdict(self.one, verdict, json_mode=True)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(Feedback.objects.current_verdict(self.user, self.one), MAYBE)

    def test_the_redirect_ignores_next_and_referer(self):
        for path, data in ((VERDICT, {"offer": self.one.pk, "verdict": "yes"}), (UNDO, {"offer": self.one.pk})):
            response = self.post(path, {**data, "next": "https://evil.example"}, HTTP_REFERER="https://evil.example/x")
            self.assertEqual((response.status_code, response["Location"]), (303, SORT))
            self.assertEqual(self.post(f"{path}?next=//evil.example", data).get("Location"), SORT)

    def test_same_verdict_twice_is_one_row(self):
        self.verdict(self.one)
        self.verdict(self.one)
        self.assertEqual(self.rows().count(), 1)

    def test_offer_of_another_person_or_nobody_gives_the_same_404(self):
        other = make_finished_profile("lea@example.fr").user
        theirs = self.scored(user=other)
        responses = [self.verdict(theirs.pk, json_mode=True), self.verdict(theirs.pk + 1000, json_mode=True),
                     self.verdict(theirs.pk), self.verdict(theirs.pk + 1000)]
        self.assertEqual([r.status_code for r in responses], [404] * 4)
        self.assertEqual(responses[0].content, responses[1].content)
        self.assertEqual(responses[0].json(), {"ok": False})
        self.assertEqual(responses[2].content, responses[3].content)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_offer_must_be_one_to_eighteen_ascii_digits(self):
        for value in ("", "abc", "+5", "-5", "1_0", "1.0", "5 ", " 5", "5\n", "٣", "５", "0x5", str(10 ** 30),
                      "1" * 19):
            with self.subTest(value=value):
                for path in (VERDICT, UNDO):
                    response = self.post(path, {"offer": value, "verdict": "yes"}, json_mode=True)
                    self.assertEqual((response.status_code, response.json()), (400, {"ok": False}))
                    self.assertEqual(self.post(path, {"offer": value, "verdict": "yes"}).status_code, 400)
        self.assertEqual(self.post(VERDICT, {"verdict": "yes"}, json_mode=True).status_code, 400)
        self.assertEqual(self.post(UNDO, json_mode=True).status_code, 400)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_an_unknown_verdict_is_400(self):
        for value in ("", "YES", "oui", "yes ", "yes\n"):
            with self.subTest(value=value):
                response = self.verdict(self.one, value, json_mode=True)
                self.assertEqual((response.status_code, response.json()), (400, {"ok": False}))
        self.assertEqual(self.post(VERDICT, {"offer": self.one.pk}).status_code, 400)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_cap_gives_429_and_no_row(self):
        others = [self.scored() for _ in range(deck.DAILY_CAP)]
        Feedback.objects.bulk_create([Feedback(user=self.user, offer=offer, verdict=YES) for offer in others])
        response = self.verdict(self.one, json_mode=True)
        self.assertEqual((response.status_code, response.json()), (429, {"ok": False}))
        self.assertEqual(self.verdict(self.one).status_code, 429)
        self.assertEqual(self.rows().count(), deck.DAILY_CAP)

    def test_the_501st_is_refused(self):
        offers = [self.scored() for _ in range(deck.DAILY_CAP - 1)]
        for offer in offers:
            deck.give(self.user, offer.pk, YES)
        self.assertEqual(self.verdict(self.one, json_mode=True).status_code, 200)
        extra = self.scored()
        self.assertEqual(self.verdict(extra, json_mode=True).status_code, 429)
        self.assertEqual(self.rows().count(), deck.DAILY_CAP)


class UndoTests(SortTestCase):
    def setUp(self):
        super().setUp()
        self.first, self.second = self.scored(), self.scored()
        deck.give(self.user, self.first.pk, YES)
        deck.give(self.user, self.second.pk, NO)

    def test_json_gives_the_offer_then_null_and_the_other_offer_is_kept(self):
        response = self.post(UNDO, {"offer": self.first.pk}, json_mode=True)
        self.assertEqual(response.json(), {"ok": True, "offer": self.first.pk})
        again = self.post(UNDO, {"offer": self.first.pk}, json_mode=True)
        self.assertEqual((again.status_code, again.json()), (200, {"ok": True, "offer": None}))
        self.assertEqual(Feedback.objects.current_verdict(self.user, self.second), NO)
        self.assertEqual(self.rows().filter(cancelled=True).count(), 1)

    def test_without_javascript_a_303_back_to_the_sort(self):
        response = self.post(UNDO, {"offer": self.second.pk})
        self.assertRedirects(response, SORT, status_code=303, fetch_redirect_response=False)
        self.assertIsNone(Feedback.objects.current_verdict(self.user, self.second))

    def test_without_javascript_the_page_offers_to_undo_the_latest_verdict(self):
        page = self.client.get(SORT).content.decode()
        self.assertRegex(page, re.compile(rf'action="{UNDO}".*?name="offer" value="{self.second.pk}".*?'
                                          r"Annuler mon dernier avis", re.DOTALL))
        self.post(UNDO, {"offer": self.second.pk})
        self.assertIn(f'name="offer" value="{self.first.pk}"', self.client.get(SORT).content.decode())
        self.post(UNDO, {"offer": self.first.pk})
        page = self.client.get(SORT).content.decode()
        self.assertRegex(page, r'<form [^>]*action="/tri/annuler/"[^>]* hidden>')  # still there for the script
        self.assertNotIn(f'name="offer" value="{self.first.pk}"', page)

    def test_the_offer_of_another_person_changes_nothing(self):
        other = make_finished_profile("lea@example.fr").user
        theirs = self.scored(user=other)
        Feedback.objects.create(user=other, offer=theirs, verdict=YES)
        response = self.post(UNDO, {"offer": theirs.pk}, json_mode=True)
        self.assertEqual(response.json(), {"ok": True, "offer": None})
        self.assertFalse(self.rows(other).get().cancelled)

    def test_cap_gives_429_and_no_change(self):
        extra = [self.scored() for _ in range(deck.DAILY_CAP)]
        Feedback.objects.bulk_create([Feedback(user=self.user, offer=offer, verdict=YES) for offer in extra])
        response = self.post(UNDO, {"offer": self.first.pk}, json_mode=True)
        self.assertEqual((response.status_code, response.json()), (429, {"ok": False}))
        self.assertFalse(self.rows().filter(cancelled=True).exists())


class HeaderTests(SortTestCase):
    """Requirement 9."""

    def test_no_store_everywhere_and_the_default_referrer_policy_stays(self):
        offer = self.scored()
        responses = [self.client.get(SORT), self.client.head(SORT), self.verdict(offer), self.verdict(offer, MAYBE,
                                                                                                    json_mode=True),
                     self.post(UNDO, {"offer": offer.pk}), self.post(UNDO, {"offer": offer.pk}, json_mode=True),
                     self.post(VERDICT, {}), self.client.get(VERDICT), Client().get(SORT)]
        for response in responses:
            with self.subTest(status=response.status_code, path=response.request["PATH_INFO"]):
                self.assertIn("no-store", response["Cache-Control"])
                self.assertEqual(response["X-Frame-Options"], "DENY")
                self.assertEqual(response["Referrer-Policy"], "same-origin")


class PageTests(SortTestCase):
    def test_shows_the_best_offer_with_its_source_and_a_link(self):
        self.scored(6, title="Moins bonne offre")
        best = self.scored(9, title="Régisseuse de festival", employer="Festival fictif", location="Nantes",
                           contract="CDI", url="https://example.fr/offre/42",
                           apply_url="https://postuler.example.fr/secret")
        html = self.client.get(SORT).content.decode()
        self.assertIn("Régisseuse de festival", html)
        self.assertIn("Festival fictif", html)
        self.assertIn("Source fictive", html)
        self.assertIn(ATTRIBUTION, html)
        self.assertRegex(html, r'<a [^>]*href="https://example\.fr/offre/42"[^>]*rel="noopener noreferrer nofollow"')
        self.assertIn("2 offres", html)
        self.assertNotIn("postuler.example.fr", html)
        self.assertIn(f'name="offer" value="{best.pk}"', html)
        for verdict in ("yes", "maybe", "no"):
            self.assertIn(f'name="verdict" value="{verdict}"', html)

    def test_a_single_offer_is_singular(self):
        self.scored()
        self.assertContains(self.client.get(SORT), ">1 offre<")

    def test_empty_deck(self):
        response = self.client.get(SORT)
        self.assertContains(response, EMPTY)
        self.assertContains(response, 'href="/ma-recherche/"')
        self.assertEqual(self.cards(response), [])
        self.scored()
        self.rows().delete()
        deck.give(self.user, Score.objects.get().offer_id, YES)
        self.assertContains(self.client.get(SORT), EMPTY)

    def test_house_style(self):
        self.scored()
        html = self.client.get(SORT).content.decode()
        assert_french_copy(self, html)
        assert_unique_ids(self, html)
        self.assertEqual(self.client.get(SORT).context["queue_key"], f"tri.queue.{self.user.pk}")
        self.rows().delete()
        Score.objects.all().delete()
        assert_french_copy(self, self.client.get(SORT).content.decode())

    def test_only_this_persons_scores_are_shown(self):
        other = make_finished_profile("lea@example.fr").user
        self.scored(user=other, title="Offre de Léa")
        mine = self.scored(title="Mon offre")
        response = self.client.get(SORT)
        self.assertEqual([card["id"] for card in self.cards(response)], [mine.pk])
        self.assertNotContains(response, "Offre de Léa")

    def test_cards_hold_the_deck_with_exactly_the_allowed_keys(self):
        offers = [self.scored(8, title=f"Offre {n}", summary="Un poste de régie. Trois mois.") for n in range(3)]
        cards = self.cards(self.client.get(SORT))
        self.assertEqual(len(cards), 3)
        for card in cards:
            self.assertEqual(set(card), CARD_KEYS)
        card = next(c for c in cards if c["id"] == offers[0].pk)
        self.assertEqual({k: card[k] for k in ("title", "score", "summary", "exploration", "source", "attribution")},
                         {"title": "Offre 0", "score": 8, "summary": "Un poste de régie. Trois mois.",
                          "exploration": False, "source": "Source fictive", "attribution": ATTRIBUTION})

    def test_cards_never_hold_what_must_stay_private(self):
        self.scored(full_text="TEXTE-COMPLET-SECRET", rome_code="L1234", apply_url="https://postuler.example.fr/x")
        response = self.client.get(SORT)
        html = response.content.decode()
        for secret in ("TEXTE-COMPLET-SECRET", "L1234", "postuler.example.fr", "apply_url", "full_text", "rome_code"):
            self.assertNotIn(secret, html)

    def test_a_hostile_title_stays_data(self):
        hostile = "</script><img src=x onerror=alert(1)>"
        self.scored(title=hostile, employer="<b>Gras</b>")
        response = self.client.get(SORT)
        html = response.content.decode()
        self.assertNotIn(hostile, html)
        self.assertNotIn("<img", html)
        self.assertNotIn("<b>Gras</b>", html)
        card = self.cards(response)[0]
        self.assertEqual((card["title"], card["employer"]), (hostile, "<b>Gras</b>"))

    def test_only_http_links_reach_the_page_and_the_cards(self):
        for url in ("javascript:alert(1)", "data:text/html,x", "//x.example/a", "https://exa mple.fr/a"):
            Score.objects.all().delete()
            self.scored(url=url)
            response = self.client.get(SORT)
            self.assertEqual(self.cards(response)[0]["url"], "", url)
            self.assertNotRegex(response.content.decode(), r'<a class="sort-card__link"[^>]*href=')
            self.assertNotContains(response, 'href="javascript')
        Score.objects.all().delete()
        self.scored(url="https://example.fr/ok")
        self.assertEqual(self.cards(self.client.get(SORT))[0]["url"], "https://example.fr/ok")

    def test_a_reason_or_summary_with_a_link_is_not_shown(self):
        self.scored(reason="Postulez sur exemple.fr/postuler dès maintenant.", summary="Écrivez à rh@example.fr.")
        card = self.cards(self.client.get(SORT))[0]
        self.assertEqual((card["reason"], card["summary"]), ("", ""))
        html = self.client.get(SORT).content.decode()
        self.assertNotIn("exemple.fr/postuler", html)
        self.assertNotIn("rh@example.fr", html)

    def test_expired_and_old_offers_are_not_in_the_cards(self):
        self.scored(expires_at=timezone.now() - timedelta(hours=1))
        self.scored(ago=timedelta(days=40))
        self.assertEqual(self.cards(self.client.get(SORT)), [])


class SheetTests(SortTestCase):
    """Spec 6.2: the sheet, its actions and the account menu."""

    def html(self):
        return self.client.get(SORT).content.decode()

    def test_one_h1_the_count_the_ai_mention_and_the_menu_entry_before_ma_recherche(self):
        self.scored()
        self.scored()
        html = self.html()
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn("<h1>Vos offres à trier</h1>", html)
        self.assertRegex(html, r'class="typed[^"]*"[^>]*data-sort-count>2 offres<')
        self.assertIn("Notes générées par IA, la décision vous appartient.", html)
        self.assertRegex(html, r'href="/tri/"[^>]*aria-current="page"[^>]*>Mes offres</a>\s*'
                               r'<a href="/ma-recherche/"[^>]*>Ma recherche</a>')

    def test_the_sheet_is_named_by_its_title_and_shows_every_field(self):
        self.scored(8, title="Régisseuse de festival", employer="Festival fictif", location="Nantes", contract="CDI",
                    summary="Un poste de régie. Trois mois.", url="https://example.fr/offre/42",
                    published_at=timezone.make_aware(timezone.datetime(2026, 10, 14, 10)))
        html = self.html()
        self.assertRegex(html, r'<article class="card card--sheet sort-card"[^>]*aria-labelledby="sort-title-\d+"')
        self.assertRegex(
            html, r'<h2 class="sort-card__title"[^>]*id="sort-title-\d+"[^>]*>Régisseuse de festival</h2>')
        for text in ("Festival fictif", "Nantes", "CDI", "publiée le 14/10", "8 sur 10",
                     "Un poste de régie. Trois mois.", "Vous retrouvez la régie que vous cherchez.",
                     "Source fictive", ATTRIBUTION):
            self.assertIn(text, html)
        self.assertEqual(html.count("gauge__notch--on"), 8)
        self.assertRegex(html, r'<a [^>]*href="https://example\.fr/offre/42"[^>]*target="_blank"[^>]*>\s*Voir l’offre'
                               r'<span class="visually-hidden">, nouvel onglet</span>')

    def test_empty_parts_are_hidden_not_missing_so_the_script_can_fill_them(self):
        self.scored(reason="", title="Offre nue", location="", contract="", summary="", url="",
                    source_license="")
        html = self.html()
        for marker in ('data-if="exploration"', 'data-if="reason"', 'data-field="summary"', 'data-field="location"',
                       'data-field="contract"', 'data-field="date"', 'data-field="attribution"', "data-link"):
            with self.subTest(marker=marker):
                self.assertRegex(html, rf'<[a-z]+ [^>]*{marker}[^>]* hidden')

    def test_an_exploration_says_so(self):
        self.scored(exploration=True)
        self.assertNotRegex(self.html(), r'<p [^>]*data-if="exploration"[^>]* hidden')
        self.assertIn("Une piste à côté", self.html())
        Score.objects.all().delete()
        self.scored()
        self.assertRegex(self.html(), r'<p [^>]*data-if="exploration"[^>]* hidden')

    def test_three_actions_in_order_with_full_labels_for_assistive_tech(self):
        self.scored()
        html = self.html()
        buttons = re.findall(
            r'<button class="button([^"]*)" type="submit" name="verdict" value="(\w+)"( aria-label="[^"]+")?>', html)
        self.assertEqual([(value, label) for _, value, label in buttons],
                         [("no", ' aria-label="Non, pas pour moi"'), ("maybe", ""),
                          ("yes", ' aria-label="Oui, ça m’intéresse"')])
        self.assertEqual([classes.strip() for classes, _, _ in buttons],
                         ["button--secondary", "button--secondary", ""])
        for full, short in (("Pas pour moi", "Non"), ("Ça m’intéresse", "Oui")):  # the short ones sit under 30rem
            self.assertRegex(html, rf'sort-actions__full">{full}</span>\s*<span class="sort-actions__short" '
                                   rf'aria-hidden="true">{short}<')
        self.assertIn("Peut-être</button>", html)
        self.assertIn("Annuler mon dernier avis", html)

    def test_the_script_gets_the_queue_key_the_count_and_the_end_block(self):
        self.scored()
        html = self.html()
        self.assertIn(f'data-queue-key="tri.queue.{self.user.pk}"', html)
        self.assertRegex(html, r'<div [^>]*data-sort-end[^>]* hidden')
        self.assertIn('aria-live="polite"', html)
        self.assertIn("Modifier ma recherche", html)

    def test_the_empty_paquet_shows_its_end_without_count_or_card(self):
        html = self.html()
        self.assertNotRegex(html, r'<div [^>]*data-sort-end[^>]* hidden')
        self.assertIn(EMPTY, html)
        self.assertNotIn("<article", html)


LEARNED, CLEAR = "/tri/appris/", "/tri/appris/effacer/"
LEARNED_TEXT = "Vous choisissez surtout des postes de médiation en CDI."


class LearnedTestCase(SortTestCase):
    def set_learned(self, text=LEARNED_TEXT, own=False, user=None):
        Profile.objects.filter(user=user or self.user).update(learned_summary=text, learned_summary_own=own)

    def stored(self, user=None):
        return Profile.objects.get(user=user or self.user)

    def card(self, html):
        start = html.index('<article class="card card--green sort__learned"')
        return html[start:html.index("</article>", start)]


class LearnedCardTests(LearnedTestCase):
    def test_no_card_without_a_learned_summary(self):
        self.scored()
        self.assertNotIn("sort__learned", self.client.get(SORT).content.decode())

    def test_green_card_above_the_sheet_with_the_ai_label_and_its_two_actions(self):
        self.scored()
        self.set_learned()
        html = self.client.get(SORT).content.decode()
        card = self.card(html)
        self.assertLess(html.index("sort__learned"), html.index("data-sort-stage"))
        self.assertIn("Ce que j’ai appris de vos choix", card)
        self.assertIn(LEARNED_TEXT, card)
        self.assertIn("Rédigé par IA", card)
        self.assertIn(f'<a href="{LEARNED}">Corriger</a>', card)
        self.assertIn(f'action="{CLEAR}"', card)
        self.assertIn("csrfmiddlewaretoken", card)
        self.assertRegex(card, r"<button [^>]*type=\"submit\">Effacer</button>")
        assert_unique_ids(self, html)
        assert_french_copy(self, html)

    def test_the_persons_own_text_has_no_ai_label(self):
        self.scored()
        self.set_learned(own=True)
        card = self.card(self.client.get(SORT).content.decode())
        self.assertIn(LEARNED_TEXT, card)
        self.assertNotIn("par IA", card)

    def test_card_also_on_the_empty_page(self):
        self.set_learned()
        self.assertIn(LEARNED_TEXT, self.client.get(SORT).content.decode())

    def test_text_is_escaped(self):
        self.set_learned('<script>alert("x")</script> & <b>gras</b>', own=True)
        html = self.client.get(SORT).content.decode()
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<b>gras</b>", html)
        self.assertIn("&lt;script&gt;alert", html)

    def test_someone_elses_text_is_never_shown(self):
        other = make_finished_profile("paul@example.fr").user
        self.set_learned("Texte de Paul.", user=other)
        self.assertNotIn("Texte de Paul.", self.client.get(SORT).content.decode())


class LearnedFormTests(LearnedTestCase):
    def test_form_is_prefilled_and_escaped(self):
        self.set_learned('Mon texte <script>alert("x")</script>')
        html = self.client.get(LEARNED).content.decode()
        self.assertRegex(html, r'<textarea name="text"[^>]*>\s*Mon texte &lt;script&gt;')
        self.assertNotIn("<script>alert", html)
        self.assertIn("800", html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertIn(f'action="{LEARNED}"', html)
        self.assertIn(f'href="{SORT}"', html)
        assert_unique_ids(self, html)
        assert_french_copy(self, html)

    def test_post_saves_the_text_as_the_persons_own(self):
        before = timezone.now()
        response = self.post(LEARNED, {"text": "Je veux surtout la médiation, pas la billetterie."})
        self.assertRedirects(response, SORT, status_code=303, fetch_redirect_response=False)
        profile = self.stored()
        self.assertEqual(profile.learned_summary, "Je veux surtout la médiation, pas la billetterie.")
        self.assertTrue(profile.learned_summary_own)
        self.assertGreaterEqual(profile.learned_summary_at, before)

    def test_the_text_is_kept_as_written_not_filtered(self):
        text = "Voir exemple.fr <b>ici</b> & là"
        self.post(LEARNED, {"text": text})
        self.assertEqual(self.stored().learned_summary, text)

    def test_800_characters_pass_801_do_not(self):
        self.assertEqual(self.post(LEARNED, {"text": "a" * 800}).status_code, 303)
        self.assertEqual(len(self.stored().learned_summary), 800)
        self.set_learned("Ancien texte.")
        response = self.post(LEARNED, {"text": "a" * 801})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Faites plus court")
        self.assertEqual(self.stored().learned_summary, "Ancien texte.")

    def test_a_blank_text_clears(self):
        self.set_learned()
        self.post(LEARNED, {"text": "   "})
        profile = self.stored()
        self.assertEqual((profile.learned_summary, profile.learned_summary_own), ("", False))
        self.assertIsNotNone(profile.learned_summary_at)

    def test_only_get_and_post(self):
        self.assertEqual(self.client.put(LEARNED).status_code, 405)
        self.assertEqual(self.client.delete(LEARNED).status_code, 405)

    def test_nothing_goes_through_the_on_demand_scoring(self):
        with mock.patch("matching.on_demand.watch") as watch, mock.patch("matching.on_demand.start") as start:
            self.post(LEARNED, {"text": "Mon texte."})
            self.post(CLEAR)
        watch.assert_not_called()
        start.assert_not_called()
        self.assertEqual(ScoringRun.objects.count(), 0)

    def test_other_people_are_untouched(self):
        other = make_finished_profile("paul@example.fr").user
        self.set_learned("Texte de Paul.", user=other)
        self.post(LEARNED, {"text": "Mon texte."})
        self.post(CLEAR)
        self.assertEqual(self.stored(other).learned_summary, "Texte de Paul.")


class LearnedClearTests(LearnedTestCase):
    def test_clear_empties_the_text_and_goes_back_to_the_sort_page(self):
        self.set_learned(own=True)
        before = timezone.now()
        response = self.post(CLEAR)
        self.assertRedirects(response, SORT, status_code=303, fetch_redirect_response=False)
        profile = self.stored()
        self.assertEqual((profile.learned_summary, profile.learned_summary_own), ("", False))
        self.assertGreaterEqual(profile.learned_summary_at, before)
        self.assertNotIn("sort__learned", self.client.get(SORT).content.decode())

    def test_get_is_refused(self):
        self.set_learned()
        self.assertEqual(self.client.get(CLEAR).status_code, 405)
        self.assertEqual(self.stored().learned_summary, LEARNED_TEXT)


class LearnedAccessTests(LearnedTestCase):
    def requests(self, client):
        return [client.get(LEARNED), client.post(LEARNED, {"text": "Intrus."}), client.post(CLEAR)]

    def test_anonymous_is_sent_to_the_login_page_and_nothing_is_written(self):
        self.set_learned()
        for response in self.requests(Client()):
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/"), response["Location"])
        self.assertEqual(self.stored().learned_summary, LEARNED_TEXT)

    def test_a_person_in_the_questionnaire_is_sent_back_to_it(self):
        self.set_learned()
        Profile.objects.filter(user=self.user).update(onboarding_step="")
        for response in self.requests(self.client):
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 302)
                self.assertNotEqual(response["Location"], SORT)
        self.assertEqual(self.stored().learned_summary, LEARNED_TEXT)

    def test_no_store(self):
        for response in (self.client.get(LEARNED), self.post(LEARNED, {"text": "a"}), self.post(CLEAR),
                         self.client.get(CLEAR), Client().get(LEARNED)):
            with self.subTest(status=response.status_code, path=response.request["PATH_INFO"]):
                self.assertIn("no-store", response["Cache-Control"])


class LearnedCsrfTests(LearnedTestCase):
    def setUp(self):
        super().setUp()
        self.set_learned()
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def test_posts_refuse_a_missing_token(self):
        self.assertEqual(self.post(LEARNED, {"text": "Intrus."}).status_code, 403)
        self.assertEqual(self.post(CLEAR).status_code, 403)
        self.assertEqual(self.stored().learned_summary, LEARNED_TEXT)

    def test_posts_refuse_a_foreign_origin(self):
        self.client.get(LEARNED)
        token = self.client.cookies["csrftoken"].value
        for path in (LEARNED, CLEAR):
            response = self.post(path, {"text": "Intrus.", "csrfmiddlewaretoken": token},
                                 HTTP_ORIGIN="https://evil.example")
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.stored().learned_summary, LEARNED_TEXT)


class TrialNoticeTests(SortTestCase):
    """The line of a finished trial (sub-step 8.2). The trial exists outside the beta only; fixed dates far enough
    from the real clock for either side."""

    NOTICE = ("Votre essai est terminé : aucune nouvelle offre n’arrive. "
              "Vos avis restent possibles sur les offres déjà là.")
    DONE = "Tout est trié."
    OVER = datetime(2020, 1, 7, tzinfo=PARIS)
    RUNNING = datetime(2099, 1, 7, tzinfo=PARIS)

    def setUp(self):
        super().setUp()
        self.end(self.OVER)

    def end(self, when):
        User.objects.filter(pk=self.user.pk).update(trial_ends_at=when)

    def html(self):
        return self.client.get(SORT).content.decode()

    @override_settings(BETA=False)
    def test_the_line_sits_above_the_deck(self):
        self.scored()
        html = self.html()
        self.assertEqual(html.count(self.NOTICE), 1)
        self.assertLess(html.index(self.NOTICE), html.index("data-sort-stage"))
        self.assertIn("Notes générées par IA", html)
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    @override_settings(BETA=False)
    def test_the_line_is_in_the_empty_state_and_the_promise_of_a_next_mail_is_gone(self):
        html = self.html()
        self.assertEqual(html.count(self.NOTICE), 1)
        self.assertNotIn(EMPTY, html)
        self.assertIn(self.DONE, html)
        self.assertNotIn("mail de demain matin", html)
        self.assertIn('href="/ma-recherche/"', html)

    @override_settings(BETA=False)
    def test_a_running_trial_has_no_line(self):
        self.end(self.RUNNING)
        for scored in (False, True):
            with self.subTest(scored=scored):
                if scored:
                    self.scored()
                html = self.html()
                self.assertNotIn("essai", html)
                self.assertIn(EMPTY, html)

    @override_settings(BETA=False)
    def test_without_a_date_there_is_no_line(self):
        self.end(None)
        self.assertNotIn("essai", self.html())
        self.assertIn(EMPTY, self.html())

    @override_settings(BETA=True)
    def test_in_beta_a_past_date_is_not_read(self):
        for scored in (False, True):
            with self.subTest(scored=scored):
                if scored:
                    self.scored()
                html = self.html()
                self.assertNotIn("essai", html)
                self.assertIn(EMPTY, html)

    @override_settings(BETA=False)
    def test_an_opinion_is_still_taken_and_undone_after_the_end(self):
        offer = self.scored()
        response = self.verdict(offer, YES, json_mode=True)
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True}))
        self.assertEqual(self.rows().count(), 1)
        response = self.post(UNDO, {"offer": offer.pk}, json_mode=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rows().filter(cancelled=False).count(), 0)

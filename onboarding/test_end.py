"""The end of the questionnaire (sub-step 3.7): the appointment screen, the welcome mail and where sign-in leads."""
import datetime as dt
import re
from html import unescape
from unittest import mock

from django.core import mail as django_mail
from django.test import TestCase, override_settings
from django.utils import timezone

import mail
from accounts import events
from accounts.models import OnboardingEvent, Profile, User
from accounts.testing import make_profile
from accounts.views import after_login_url
from config.testing import assert_french_copy, assert_unique_ids, visible_text
from jobs.testing import make_offer, make_source
from llm import fake
from matching.models import Score, ScoringRun
from onboarding import finish, schedule
from onboarding.testing import MY_SEARCH, VALIDATE, SummaryTestCase
from onboarding.tests import END, START, SUMMARY
from onboarding.views import _understood_card

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
MY_DATA = "/mes-donnees/"
PARIS = schedule.PARIS


def text(html):
    return " ".join(visible_text(html).split())


def at(now):
    """onboarding/finish.py's clock frozen at `now` (the rest of Django keeps the real one, sessions included)."""
    return mock.patch("onboarding.finish.timezone", **{"now.return_value": now})


def stamp_of(html):
    """(class, accessible name, printed date) of the end screen's stamp."""
    match = re.search(r'<span class="(stamp[^"]*)" role="img"\s+aria-label="([^"]+)">([^<]+)<', html)
    return match.groups()


@override_settings(EMAIL_BACKEND=LOCMEM, DEV_EMAILS=[], SITE_URL="https://tri.example.fr")
class EndTestCase(SummaryTestCase):
    def setUp(self):
        super().setUp()
        self.ready()

    def finish(self):
        """« Ça me va », then the end screen. Returns its HTML."""
        self.assertRedirects(self.client.post(VALIDATE), END, fetch_redirect_response=False)
        return self.page(END)

    def welcome_mails(self):
        return [message for message in django_mail.outbox if message.subject.startswith("Bienvenue")]


class EndScreenTests(EndTestCase):
    def test_not_reachable_before_ca_me_va(self):
        self.assertRedirects(self.client.get(END), SUMMARY, fetch_redirect_response=False)
        self.assertIsNone(self.profile.onboarding_completed_at)
        self.assertEqual(django_mail.outbox, [])

    def test_first_visit_records_the_end_once(self):
        before = timezone.now()
        self.finish()
        completed = self.profile.onboarding_completed_at
        self.assertGreaterEqual(completed, before)
        self.page(END)
        self.assertEqual(self.profile.onboarding_completed_at, completed)
        self.assertEqual(self.event_names().count(events.ONBOARDING_COMPLETED), 1)

    def test_appointment_sentence_and_stamp(self):
        html = self.finish()
        delivery = schedule.first_delivery(self.profile.onboarding_completed_at)
        day = schedule.describe_delivery(delivery, timezone.now())
        page = text(html)
        self.assertIn("<h1 class=\"question__title\">Votre premier tri arrive</h1>", html)
        self.assertIn(f"Il arrive {day}, vers 7 h, depuis noreply@example.org.", page)
        _, name, printed = stamp_of(html)
        self.assertEqual(name, f"Il arrive {day}, vers 7 h, depuis noreply@example.org.")
        self.assertEqual(printed, schedule.stamp_text(delivery))
        self.assertIn('class="stamp__small">vers 7 h</span>', html)
        self.assertIn("<title>Votre premier tri arrive, tri-offres</title>", html)
        self.assertNotIn("confetti", html.lower())

    def test_stamp_is_pressed_on_the_first_visit_only(self):
        self.assertIn('class="stamp stamp--press"', self.finish())
        html = self.page(END)
        self.assertIn('class="stamp" role="img"', html)
        self.assertNotIn("stamp--press", html)

    def test_friday_evening_waits_for_monday(self):
        friday_evening = dt.datetime(2026, 10, 9, 20, tzinfo=PARIS)
        self.assertRedirects(self.client.post(VALIDATE), END, fetch_redirect_response=False)
        with at(friday_evening):
            html = self.page(END)
        self.assertEqual(self.profile.onboarding_completed_at, friday_evening)
        self.assertEqual(stamp_of(html)[2], "LUN. 12 OCT. 2026")
        page = text(html)
        self.assertIn("Il arrive lundi 12 octobre, vers 7 h", page)
        # Not tomorrow: the welcome sentence does not promise a sort "de demain".
        self.assertIn("sortez-le, le premier tri suivra.", page)
        self.assertEqual(self.welcome_mails()[0].subject, "Bienvenue : votre premier tri arrive lundi 12 octobre")

    def test_rendezvous_fields(self):
        friday_evening = dt.datetime(2026, 10, 9, 20, tzinfo=PARIS)
        meeting = finish.rendezvous(friday_evening, now=friday_evening)
        self.assertEqual(meeting, {"delivery": dt.datetime(2026, 10, 12, 7, tzinfo=PARIS), "day": "lundi 12 octobre",
                                   "stamp": "LUN. 12 OCT. 2026", "is_tomorrow": False, "passed": False})
        tuesday_evening = dt.datetime(2026, 10, 13, 20, tzinfo=PARIS)
        self.assertTrue(finish.rendezvous(tuesday_evening, now=tuesday_evening)["is_tomorrow"])
        self.assertTrue(finish.rendezvous(friday_evening, now=dt.datetime(2026, 10, 12, 7, tzinfo=PARIS))["passed"])

    def test_after_the_first_delivery_no_past_date(self):
        friday_evening = dt.datetime(2026, 10, 9, 20, tzinfo=PARIS)
        with at(friday_evening):
            self.finish()
        with at(dt.datetime(2026, 10, 14, 9, tzinfo=PARIS)):
            html = self.page(END)
        page = text(html)
        self.assertIn("Vos tris arrivent chaque jour ouvré vers 7 h, depuis noreply@example.org.", page)
        self.assertNotIn("Il arrive", page)
        self.assertNotIn("vient de partir", page)
        self.assertNotIn("stamp--press", html)

    def test_no_animation_on_a_first_visit_after_the_delivery(self):
        friday_evening = dt.datetime(2026, 10, 9, 20, tzinfo=PARIS)
        self.assertRedirects(self.client.post(VALIDATE), END, fetch_redirect_response=False)
        Profile.objects.filter(user=self.user).update(onboarding_completed_at=friday_evening)
        with at(dt.datetime(2026, 10, 14, 9, tzinfo=PARIS)):
            html = self.page(END)
        self.assertEqual(stamp_of(html)[0], "stamp")

    def test_welcome_sentence_when_the_first_sort_is_tomorrow(self):
        tuesday_evening = dt.datetime(2026, 10, 13, 20, tzinfo=PARIS)
        with at(tuesday_evening):
            page = text(self.finish())
        self.assertIn("Il arrive demain, mercredi 14 octobre, vers 7 h, depuis noreply@example.org.", page)
        self.assertIn("Un mail de bienvenue vient de partir : s’il est dans les indésirables, sortez-le, "
                      "le tri de demain suivra.", page)

    def test_the_whole_file_beside_the_stamp_read_only(self):
        html = self.finish()
        drawer = html[html.index('<details class="drawer drawer--wide-only"'):html.index("</details>")]
        self.assertEqual(re.findall(r'class="card card--(\w+)"', drawer), ["pink", "blue", "yellow", "green"])
        self.assertIn("Lyon, jusqu’à 30 km", drawer)
        self.assertIn("J’ai compris que vous cherchez un poste où vous parlez aux publics.", drawer)
        self.assertIn("Synthèse rédigée par IA", drawer)
        for absent in ("data-counter", "Modifier", "card--filed", "<a "):
            self.assertNotIn(absent, drawer)
        assert_unique_ids(self, html)

    def test_the_file_never_credits_the_persons_own_text_to_the_ai(self):
        own = "Je cherche un poste en médiathèque.\n\nPas de vente."
        Profile.objects.filter(user=self.user).update(summary_text=own)
        html = self.finish()
        drawer = html[html.index('<details class="drawer drawer--wide-only"'):html.index("</details>")]
        self.assertIn("<p>Je cherche un poste en médiathèque.</p>", drawer)
        self.assertIn("Vous avez modifié ce texte vous-même.", drawer)
        self.assertNotIn("Synthèse rédigée par IA", drawer)

    def test_the_green_card_before_any_summary_promises_the_night(self):
        card = _understood_card(Profile(summary_text="", summary_sentences=[]))
        self.assertEqual(card["paragraphs"], ["Je rédige cette synthèse cette nuit."])
        self.assertFalse(card["ai_label"])

    def test_links_to_my_search_and_my_data(self):
        html = self.finish()
        self.assertIn(f'<a href="{MY_SEARCH}">Modifier ma recherche</a>', html)
        self.assertIn(f'<a href="{MY_DATA}">Mes données</a>', html)

    def test_house_style(self):
        html = self.finish()
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    def test_start_leads_to_the_end_then_to_my_search(self):
        self.client.post(VALIDATE)
        self.assertRedirects(self.client.get(START), END, fetch_redirect_response=False)
        self.page(END)
        self.assertRedirects(self.client.get(START), MY_SEARCH, fetch_redirect_response=False)


RUNNING = ScoringRun.State.RUNNING


class FirstSortTests(EndTestCase):
    """The first sort on the end screen (spec 6.3): the run still going, the offers ready, nothing close."""
    REFRESH = '<meta http-equiv="refresh" content="10">'
    RUNNING = "Je note vos premières offres. Cette page se met à jour toute seule."
    EMPTY = "Je n’ai rien trouvé d’assez proche dans les offres du mois. Je cherche chaque nuit."

    def sort_end(self, state=None, scored=0, ago=dt.timedelta(minutes=1)):
        """« Ça me va », the run it asked for replaced by one in `state` (none for None), `scored` scores, then the
        end screen. Returns its HTML."""
        self.client.post(VALIDATE)
        ScoringRun.objects.all().delete()
        if state:
            ScoringRun.objects.create(user=self.user, kind=ScoringRun.Kind.ON_DEMAND, state=state,
                                      started_at=timezone.now() - ago)
        for number in range(scored):
            self.score(number)
        return self.page(END)

    def score(self, number):
        offer = make_offer(make_source(domains=[]), external_id=str(number), url=f"https://example.fr/{number}")
        Score.objects.create(user=self.user, offer=offer, score=7, category=Score.Category.TO_REVIEW)

    def test_running(self):
        html = self.sort_end(RUNNING)
        self.assertIn(self.REFRESH, html)
        self.assertIn(f'role="status">{self.RUNNING}', " ".join(html.split()))
        self.assertIn(f'<a href="{END}">Voir si elles sont prêtes</a>', html)
        self.assertIn("<h1 class=\"question__title\">Votre premier tri arrive</h1>", html)
        self.assertNotIn("Trier mes", html)

    def test_a_run_running_for_more_than_ten_minutes_no_longer_refreshes(self):
        html = self.sort_end(RUNNING, ago=dt.timedelta(minutes=11))
        self.assertNotIn(self.REFRESH, html)
        self.assertNotIn(self.RUNNING, text(html))

    def test_ready(self):
        html = self.sort_end(ScoringRun.State.DONE, scored=3)
        page = text(html)
        self.assertIn("<h1 class=\"question__title\">Vos premières offres sont prêtes</h1>", html)
        self.assertIn("<title>Vos premières offres sont prêtes, tri-offres</title>", html)
        self.assertIn("J’ai noté 3 offres assez proches de votre recherche.", page)
        self.assertRegex(html, r'<a class="button" href="/tri/">Trier mes 3 premières offres</a>')
        self.assertNotIn(self.REFRESH, html)
        self.assertIn("Il arrive", page)  # the stamp and the tips follow, unchanged
        self.assertEqual(stamp_of(html)[0], "stamp stamp--press")

    def test_ready_after_a_partial_run_and_for_one_offer(self):
        html = self.sort_end(ScoringRun.State.PARTIAL, scored=1)
        self.assertIn("J’ai noté 1 offre assez proche de votre recherche.", text(html))
        self.assertRegex(html, r'Trier ma première offre</a>')

    def test_offers_to_sort_are_never_hidden(self):
        html = self.sort_end(None, scored=1)  # no run at all: night scores, or notices
        self.assertIn("Vos premières offres sont prêtes", html)
        self.assertNotIn(self.REFRESH, html)

    def test_a_failed_or_refused_run_still_shows_the_offers_waiting(self):
        for state in (ScoringRun.State.FAILED, ScoringRun.State.REFUSED):
            with self.subTest(state=state):
                self.assertIn("Vos premières offres sont prêtes", self.sort_end(state, scored=1))

    def test_a_refused_run_without_offers_adds_nothing(self):
        html = self.sort_end(ScoringRun.State.REFUSED)
        self.assertNotIn(self.EMPTY, text(html))
        self.assertNotIn(self.REFRESH, html)
        self.assertNotIn("Trier m", html)

    def test_a_running_run_waits_even_with_offers_already_there(self):
        html = self.sort_end(RUNNING, scored=1)
        self.assertIn(self.REFRESH, html)
        self.assertNotIn("Trier ma", html)

    def test_empty(self):
        html = self.sort_end(ScoringRun.State.DONE)
        self.assertIn(self.EMPTY, text(html))
        self.assertNotIn(self.REFRESH, html)
        self.assertNotIn("Trier m", html)

    def test_nothing_more_without_a_run_or_after_a_failure(self):
        for state in (None, ScoringRun.State.FAILED):
            with self.subTest(state=state):
                html = self.sort_end(state)
                page = text(html)
                for absent in (self.RUNNING, self.EMPTY, "Trier m", "prêtes"):
                    self.assertNotIn(absent, page)
                self.assertNotIn(self.REFRESH, html)

    def test_a_refresh_does_not_press_the_stamp_again(self):
        self.assertIn("stamp--press", self.sort_end(RUNNING))
        self.assertNotIn("stamp--press", self.page(END))

    def test_the_view_never_calls_claude(self):
        self.sort_end(RUNNING)
        fake.calls.clear()
        self.page(END)
        self.assertEqual(fake.calls, [])

    def test_house_style_in_each_state(self):
        for state, scored in ((RUNNING, 0), (ScoringRun.State.DONE, 0), (ScoringRun.State.DONE, 2)):
            with self.subTest(state=state, scored=scored):
                Score.objects.all().delete()
                html = self.sort_end(state, scored)
                assert_french_copy(self, html)
                assert_unique_ids(self, html)


class MailTipsTests(EndTestCase):
    GMAIL = "Si le mail arrive dans l’onglet Promotions, glissez-le vers Principale."
    OUTLOOK = "Ajoutez noreply@example.org à vos expéditeurs approuvés."
    APPLE = "Ajoutez noreply@example.org à vos VIP."

    def test_tip_by_domain(self):
        cases = {
            "nora@gmail.com": [self.GMAIL], "nora@googlemail.com": [self.GMAIL],
            "nora@outlook.fr": [self.OUTLOOK], "nora@hotmail.co.uk": [self.OUTLOOK],
            "nora@live.be": [self.OUTLOOK], "nora@msn.com": [self.OUTLOOK],
            "nora@icloud.com": [self.APPLE], "nora@me.com": [self.APPLE], "nora@mac.com": [self.APPLE],
            "nora@example.fr": [self.GMAIL, self.OUTLOOK, self.APPLE],
            "nora@mail.outlook.fr": [self.GMAIL, self.OUTLOOK, self.APPLE],
            "nora@liveexample.fr": [self.GMAIL, self.OUTLOOK, self.APPLE],
        }
        for email, expected in cases.items():
            with self.subTest(email=email):
                self.assertEqual([tip for _, tip in finish.mail_tips(email)], expected)

    def test_one_tip_on_screen_for_a_known_provider(self):
        User.objects.filter(pk=self.user.pk).update(email="nora@gmail.com")
        page = text(self.finish())
        self.assertIn(self.GMAIL, page)
        self.assertNotIn(self.OUTLOOK, page)
        self.assertNotIn("<ul class=\"end__tips\">", page)

    def test_the_three_tips_in_a_list_otherwise(self):
        html = self.finish()
        tips = html[html.index('<ul class="end__tips">'):]
        tips = text(tips[:tips.index("</ul>")])
        for tip in (f"Gmail : {self.GMAIL}", f"Outlook : {self.OUTLOOK}", f"Apple Mail : {self.APPLE}"):
            self.assertIn(tip, tips)


class WelcomeMailTests(EndTestCase):
    def test_sent_once(self):
        self.finish()
        self.page(END)
        self.page(END)
        self.assertEqual(len(self.welcome_mails()), 1)
        self.assertEqual(self.event_names().count(events.WELCOME_SENT), 1)
        self.assertEqual(self.welcome_mails()[0].to, ["nora@example.fr"])

    def test_content(self):
        self.finish()
        message = self.welcome_mails()[0]
        day = finish.rendezvous(timezone.now())["day"]
        self.assertEqual(message.subject, f"Bienvenue : votre premier tri arrive {day}")
        self.assertEqual(message.reply_to, ["contact@example.org"])
        html = message.alternatives[0][0]
        for part in (message.body, unescape(text(html))):
            with self.subTest(part=part[:20]):
                part = " ".join(part.split())
                self.assertIn(f"Votre premier tri arrive {day}, vers 7 h, depuis noreply@example.org.",
                              part.replace(" ", " "))
                self.assertIn("Gmail : Si le mail arrive dans l’onglet Promotions, glissez-le vers Principale.", part)
                self.assertIn("Outlook : Ajoutez noreply@example.org à vos expéditeurs approuvés.", part)
                self.assertIn("Apple Mail : Ajoutez noreply@example.org à vos VIP.", part)
                self.assertIn("Une question, si vous avez trente secondes : qu’est-ce qui vous ferait dire que ce "
                              "premier mail était réussi ? Répondez simplement à ce message.", part)
                assert_french_copy(self, part)
        self.assertIn("https://tri.example.fr/ma-recherche/", message.body)
        # One link only, to « Ma recherche »: nothing an antivirus could click to answer for the person.
        self.assertEqual(re.findall(r'href="([^"]+)"', html), ["https://tri.example.fr/ma-recherche/"])
        self.assertNotRegex(html.lower(), r">\s*(oui|non)\s*<")

    def test_a_failed_mail_is_tried_again_on_the_next_visit(self):
        with mock.patch("onboarding.finish.mail.send", side_effect=mail.MailDeliveryFailed("down")):
            page = text(self.finish())
        self.assertNotIn(events.WELCOME_SENT, self.event_names())
        self.assertIn("Le mail de bienvenue n’est pas encore parti. Je réessaie à votre prochaine visite.", page)
        self.assertNotIn("vient de partir", page)
        self.assertIsNotNone(self.profile.onboarding_completed_at)
        self.assertIn("Un mail de bienvenue vient de partir", text(self.page(END)))
        self.assertEqual(len(self.welcome_mails()), 1)
        self.assertEqual(self.event_names().count(events.WELCOME_SENT), 1)

    def test_any_exception_releases_the_claim_and_logs_the_class_only(self):
        with mock.patch("onboarding.finish.mail.send", side_effect=RuntimeError("secret detail")), \
                self.assertLogs("onboarding.finish") as logs:
            self.finish()
        self.assertNotIn(events.WELCOME_SENT, self.event_names())
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("secret detail", " ".join(logs.output))

    def test_my_search_tries_again(self):
        with mock.patch("onboarding.finish.mail.send", side_effect=mail.MailDeliveryFailed("down")):
            self.finish()
        self.page(MY_SEARCH)
        self.page(MY_SEARCH)
        self.assertEqual(len(self.welcome_mails()), 1)
        self.assertEqual(self.event_names().count(events.WELCOME_SENT), 1)

    def test_a_retry_counts_the_delivery_from_now(self):
        with at(dt.datetime(2026, 10, 9, 20, tzinfo=PARIS)), \
                mock.patch("onboarding.finish.mail.send", side_effect=mail.MailDeliveryFailed("down")):
            self.finish()
        with at(dt.datetime(2026, 10, 14, 20, tzinfo=PARIS)):
            self.page(MY_SEARCH)
        self.assertEqual(self.welcome_mails()[0].subject,
                         "Bienvenue : votre premier tri arrive demain, jeudi 15 octobre")

    @override_settings(CLAUDE_ENGINE="oauth", DEV_EMAILS=["alexis@example.fr"])
    def test_oauth_engine_never_mails_outside_dev_emails(self):
        self.finish()
        self.assertEqual(django_mail.outbox, [])
        self.assertNotIn(events.WELCOME_SENT, self.event_names())


class AfterLoginTests(EndTestCase):
    def test_questionnaire_until_the_end_then_the_sort(self):
        self.assertEqual(after_login_url(self.user), START)
        self.client.post(VALIDATE)
        self.assertEqual(after_login_url(self.user), START)
        self.page(END)
        self.assertEqual(after_login_url(self.user), "/tri/")

    def test_home_sends_a_finished_person_to_the_sort(self):
        self.finish()
        self.assertRedirects(self.client.get("/"), "/tri/", fetch_redirect_response=False)

    def test_a_new_account_goes_to_the_questionnaire(self):
        other = User.objects.create_user("lea@example.fr")
        self.assertEqual(after_login_url(other), START)


class EventsTests(EndTestCase):
    def test_completed_event_is_tied_to_the_person(self):
        self.finish()
        self.assertTrue(OnboardingEvent.objects.filter(user=self.user, name=events.ONBOARDING_COMPLETED).exists())


@override_settings(EMAIL_BACKEND=LOCMEM, DEV_EMAILS=[], SITE_URL="https://tri.example.fr")
class TrialDateTests(TestCase):
    """finish.complete() sets the end of the trial with the end of the questionnaire, outside the beta only (8.1)."""

    NOW = dt.datetime(2026, 10, 20, 13, 0, tzinfo=dt.timezone.utc)  # Tue 20 Oct, 15:00 in Paris
    END = dt.datetime(2026, 10, 26, 23, 0, tzinfo=dt.timezone.utc)  # Tue 27 Oct, 00:00 in Paris (UTC+1 by then)

    def setUp(self):
        self.profile = make_profile("nora@example.fr", onboarding_completed_at=None)
        self.user = self.profile.user

    def complete(self, now=None):
        with at(now or self.NOW):
            return finish.complete(self.profile)

    def trial_ends_at(self):
        return User.objects.get(pk=self.user.pk).trial_ends_at

    @override_settings(BETA=False)
    def test_the_date_is_set_at_the_end_of_the_questionnaire(self):
        self.complete()
        self.assertEqual(self.trial_ends_at(), self.END)
        self.assertEqual(self.profile.onboarding_completed_at, self.NOW)
        self.assertEqual(self.user.trial_ends_at, self.END)  # the instance the welcome mail reads (8.2)

    @override_settings(BETA=True)
    def test_in_beta_nothing_is_written_on_the_account(self):
        self.complete()
        self.assertIsNone(self.trial_ends_at())
        self.assertEqual(self.profile.onboarding_completed_at, self.NOW)
        self.assertEqual(len(django_mail.outbox), 1)  # the welcome mail goes out as before

    @override_settings(BETA=False)
    def test_the_team_gets_no_date(self):
        for email, fields in (("staff@example.fr", {"is_staff": True}), ("root@example.fr", {"is_superuser": True})):
            with self.subTest(fields=fields):
                profile = make_profile(email, onboarding_completed_at=None)
                User.objects.filter(pk=profile.user_id).update(**fields)
                with at(self.NOW):
                    finish.complete(profile)
                profile.refresh_from_db()
                self.assertIsNotNone(profile.onboarding_completed_at)
                self.assertIsNone(User.objects.get(pk=profile.user_id).trial_ends_at)

    @override_settings(BETA=False, DEV_EMAILS=["nora@example.fr"])
    def test_a_dev_account_gets_a_date_like_anyone(self):
        self.complete()
        self.assertEqual(self.trial_ends_at(), self.END)

    @override_settings(BETA=False)
    def test_a_second_visit_does_not_move_the_date(self):
        self.complete()
        self.complete(self.NOW + dt.timedelta(days=3))
        self.assertEqual(self.trial_ends_at(), self.END)
        self.assertEqual(self.profile.onboarding_completed_at, self.NOW)

    @override_settings(BETA=False)
    def test_a_date_set_by_hand_is_not_overwritten(self):
        by_hand = self.NOW + dt.timedelta(days=30)
        User.objects.filter(pk=self.user.pk).update(trial_ends_at=by_hand)
        self.complete()
        self.assertEqual(self.trial_ends_at(), by_hand)
        self.assertEqual(self.profile.onboarding_completed_at, self.NOW)

    def test_a_questionnaire_finished_in_beta_never_gets_a_date_afterwards(self):
        with override_settings(BETA=True):
            self.complete()
        with override_settings(BETA=False):
            self.complete(self.NOW + dt.timedelta(days=9))
            self.assertIsNone(self.trial_ends_at())

    @override_settings(BETA=False)
    def test_a_failing_write_on_the_account_leaves_the_end_unrecorded(self):
        with mock.patch.object(finish, "User") as users:
            users.objects.filter.return_value.update.side_effect = RuntimeError("boom")
            with self.assertRaises(RuntimeError):
                self.complete()
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.onboarding_completed_at)
        self.assertFalse(OnboardingEvent.objects.filter(user=self.user, name=events.ONBOARDING_COMPLETED).exists())
        self.assertIsNone(self.trial_ends_at())
        self.assertEqual(django_mail.outbox, [])
        self.complete()  # the next visit records both
        self.assertEqual(self.trial_ends_at(), self.END)
        self.assertEqual(self.profile.onboarding_completed_at, self.NOW)

    @override_settings(BETA=False)
    def test_the_account_write_is_conditional_on_the_questionnaire_write(self):
        Profile.objects.filter(pk=self.profile.pk).update(onboarding_completed_at=self.NOW)  # already recorded
        self.profile.refresh_from_db()
        with mock.patch.object(finish, "User") as users:
            self.complete(self.NOW + dt.timedelta(days=1))
        users.objects.filter.assert_not_called()

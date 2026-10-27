"""Account deletion (sub-step 7.1, requirements E12 to E16 of the design plan): the service, the
confirmation page, the page that follows, and the confirmation mail. Nothing leaves: locmem backend, fake engine."""
from datetime import timedelta
from unittest import mock

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.core import mail as django_mail
from django.test import Client, TestCase, override_settings
from django.utils import timezone

import mail
from accounts import auth, deletion, events
from accounts.models import LoginCode, OnboardingEvent, Profile, User, WaitlistEntry
from accounts.testing import make_finished_profile, make_login_code
from config.testing import PLAIN_STATIC, assert_french_copy, assert_unique_ids, visible_text
from jobs.models import JobOffer, Source
from jobs.testing import make_offer, make_source
from matching.models import Delivery, Feedback, Score, ScoringRun

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
CONFIRM = "/mes-donnees/supprimer/"
DONE = "/compte-supprime/"
MY_DATA = "/mes-donnees/"
EMAIL = "nora@example.fr"


def text(html):
    return " ".join(visible_text(html).split())


def fill(user):
    """One row of everything that depends on `user`."""
    source = make_source(name="Source fictive", owner=user)
    offer = make_offer(source, external_id=f"offer-{user.pk}")
    Score.objects.create(user=user, offer=offer, score=8, reason="Une raison.", category=Score.Category.TO_REVIEW)
    Feedback.objects.create(user=user, offer=offer, verdict=Feedback.Verdict.YES)
    Delivery.objects.create(user=user, date=timezone.localdate(), offer_count=1)
    ScoringRun.objects.create(user=user, kind=ScoringRun.Kind.ON_DEMAND, state=ScoringRun.State.RUNNING)
    OnboardingEvent.objects.create(user=user, name=events.CODE_ENTERED)
    return source, offer


def count_rows(user):
    return {model.__name__: model.objects.filter(user=user).count()
            for model in (Profile, Score, Feedback, Delivery, ScoringRun, OnboardingEvent)}


def session_users():
    return [Session.objects.get(pk=pk).get_decoded().get("_auth_user_id") for pk in Session.objects.values_list(
        "pk", flat=True)]


@override_settings(EMAIL_BACKEND=LOCMEM, DEV_EMAILS=[], CLAUDE_ENGINE="fake", LOGIN_MAIL_DAILY_CAP=60,
                   STORAGES=PLAIN_STATIC)
class DeletionTestCase(TestCase):
    def setUp(self):
        self.user = make_finished_profile(EMAIL).user
        self.other = make_finished_profile("paul@example.fr").user
        self.source, self.offer = fill(self.user)
        self.other_source, self.other_offer = fill(self.other)

    def delete(self, user=None):
        with self.captureOnCommitCallbacks(execute=True):
            deletion.delete_account(user or self.user)

    def login(self, user=None):
        client = Client()
        client.force_login(user or self.user)
        return client


class ServiceTests(DeletionTestCase):
    def test_everything_of_the_person_goes_and_nobody_else(self):
        other_rows = count_rows(self.other)
        key = self.user.pk
        self.delete()
        self.assertFalse(User.objects.filter(pk=key).exists())
        for model in (Profile, Score, Feedback, Delivery, ScoringRun, OnboardingEvent):
            with self.subTest(model=model.__name__):
                self.assertFalse(model.objects.filter(user_id=key).exists())
        self.assertEqual(count_rows(self.other), other_rows)
        self.assertEqual(set(other_rows.values()), {1})

    def test_offers_and_sources_stay(self):
        self.delete()
        self.assertTrue(JobOffer.objects.filter(pk=self.offer.pk).exists())
        source = Source.objects.get(pk=self.source.pk)
        self.assertIsNone(source.owner)
        self.assertEqual(Source.objects.get(pk=self.other_source.pk).owner, self.other)

    def test_every_session_of_the_person_goes(self):
        self.login()
        self.login()
        other_client = self.login(self.other)
        Session.objects.create(session_key="garbage", session_data="not a session", expire_date=timezone.now()
                               + timedelta(days=1))
        self.assertEqual(sorted(filter(None, session_users())), sorted([str(self.user.pk)] * 2 + [str(self.other.pk)]))
        self.delete()
        self.assertEqual(sorted(filter(None, session_users())), [str(self.other.pk)])
        self.assertTrue(Session.objects.filter(pk="garbage").exists())
        self.assertEqual(other_client.get(MY_DATA).status_code, 200)

    def test_expired_sessions_of_the_person_go_too(self):
        client = self.login()
        Session.objects.update(expire_date=timezone.now() - timedelta(days=1))
        self.delete()
        self.assertFalse(Session.objects.exists())

    def test_login_codes_older_than_a_day_are_erased_the_younger_ones_anonymized(self):
        make_login_code(EMAIL, ago=3 * 24 * 3600)
        make_login_code(EMAIL, ago=2 * 24 * 3600)
        young_token, _ = make_login_code(EMAIL, ago=3600)
        LoginCode.objects.create(email="NORA@Example.fr", code_hash="a" * 64, token_hash="b" * 64,
                                 expires_at=timezone.now(), sent_at=timezone.now())
        make_login_code("paul@example.fr", ago=3 * 24 * 3600)
        make_login_code("paul@example.fr", ago=60)
        before_cap_count = LoginCode.objects.filter(sent_at__gt=timezone.now() - timedelta(hours=24)).count()
        self.delete()
        self.assertFalse(LoginCode.objects.filter(email__iexact=EMAIL).exists())
        self.assertEqual(LoginCode.objects.filter(email="paul@example.fr").count(), 2)
        # The two rows of the last day, and the one that counts the confirmation mail.
        self.assertEqual(LoginCode.objects.filter(email="").count(), 3)
        hashes = list(LoginCode.objects.filter(email="").values_list("token_hash", flat=True))
        self.assertEqual(len(hashes), len(set(hashes)))
        self.assertNotIn("b" * 64, hashes)
        self.assertEqual(LoginCode.objects.filter(email="", code_hash="a" * 64).count(), 0)
        # The site's 24 h cap still counts the mails sent to this address.
        after = LoginCode.objects.filter(sent_at__gt=timezone.now() - timedelta(hours=24)).count()
        self.assertGreaterEqual(after, before_cap_count)
        # Nothing can sign in with what is left.
        self.assertIsNone(auth.link_details(young_token))

    def test_the_waiting_list_entry_goes(self):
        WaitlistEntry.objects.create(email="Nora@example.fr")
        WaitlistEntry.objects.create(email="paul@example.fr")
        self.delete()
        self.assertEqual(list(WaitlistEntry.objects.values_list("email", flat=True)), ["paul@example.fr"])

    def test_admin_log_entries_about_the_account_go(self):
        staff = User.objects.create_user("staff@example.fr", is_staff=True)
        entries = {}
        for name, obj in (("user", self.user), ("profile", self.user.profile), ("other", self.other),
                          ("other_profile", self.other.profile)):
            entries[name] = LogEntry.objects.create(
                user=staff, content_type=ContentType.objects.get_for_model(obj), object_id=str(obj.pk),
                object_repr=str(obj), action_flag=CHANGE)
        made_by_person = LogEntry.objects.create(
            user=self.user, content_type=ContentType.objects.get_for_model(self.other), object_id=str(self.other.pk),
            object_repr="x", action_flag=CHANGE)
        self.delete()
        remaining = set(LogEntry.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, {entries["other"].pk, entries["other_profile"].pk})
        self.assertNotIn(made_by_person.pk, remaining)

    def test_all_or_nothing(self):
        self.login()
        make_login_code(EMAIL, ago=3 * 24 * 3600)
        WaitlistEntry.objects.create(email=EMAIL)
        with mock.patch.object(User, "delete", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError), self.captureOnCommitCallbacks(execute=True):
                deletion.delete_account(self.user)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertEqual(Session.objects.count(), 1)
        self.assertTrue(LoginCode.objects.filter(email=EMAIL).exists())
        self.assertTrue(WaitlistEntry.objects.filter(email=EMAIL).exists())
        self.assertEqual(django_mail.outbox, [])

    def test_the_person_can_sign_up_again_with_the_same_address(self):
        make_login_code(EMAIL, ago=60)
        self.delete()
        token, _ = make_login_code(EMAIL)
        client = Client()
        response = client.post(f"/connexion/lien/{token}/")
        self.assertEqual(response.status_code, 302)
        again = User.objects.get(email=EMAIL)
        self.assertEqual(count_rows(again)["Score"], 0)
        self.assertEqual(Profile.objects.filter(user=again).count(), 1)


class ConfirmationMailTests(DeletionTestCase):
    def test_mail_goes_after_the_commit_to_the_address_read_before(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            deletion.delete_account(self.user)
        self.assertEqual(django_mail.outbox, [])
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        message, = django_mail.outbox
        self.assertEqual(message.to, [EMAIL])
        self.assertEqual(message.subject, "Votre compte tri-offres est supprimé")
        body = message.body + message.alternatives[0][0]
        self.assertNotIn("http", body)
        self.assertNotIn("/connexion/", body)
        self.assertIn("supprimé", message.body)
        self.assertEqual(message.reply_to, ["contact@example.org"])
        assert_french_copy(self, message.alternatives[0][0])

    def test_a_mail_failure_changes_nothing_and_is_logged_by_key_only(self):
        key = self.user.pk
        for error in (mail.MailDeliveryFailed("provider says nora@example.fr is wrong"), mail.MailRefused("no")):
            with self.subTest(error=type(error).__name__):
                user = make_finished_profile(f"temp{error.__class__.__name__}@example.fr").user
                user_key = user.pk
                with mock.patch.object(mail, "send", side_effect=error), \
                        self.assertLogs("accounts.deletion", "WARNING") as logs:
                    self.delete(user)
                self.assertFalse(User.objects.filter(pk=user_key).exists())
                self.assertIn(str(user_key), logs.output[0])
                self.assertNotIn("@", " ".join(logs.output))
        self.assertTrue(User.objects.filter(pk=key).exists())

    def test_the_mail_counts_in_the_site_cap(self):
        with override_settings(LOGIN_MAIL_DAILY_CAP=3):
            LoginCode.objects.create(email="a@example.fr", code_hash="a" * 64, token_hash="1" * 64,
                                     expires_at=timezone.now(), sent_at=timezone.now())
            sent = LoginCode.objects.filter(sent_at__isnull=False).count()
            self.delete()
            self.assertEqual(len(django_mail.outbox), 1)
            # One more row counts toward the cap, and it holds no address.
            self.assertEqual(LoginCode.objects.filter(sent_at__isnull=False).count(), sent + 1)
            self.assertFalse(LoginCode.objects.filter(email=EMAIL).exists())
            third = make_finished_profile("third@example.fr").user
            self.delete(third)  # 2 rows counted, below the cap of 3: this one is sent
            self.assertEqual(len(django_mail.outbox), 2)
            fourth = make_finished_profile("fourth@example.fr").user
            with self.assertLogs("accounts.deletion", "ERROR") as logs:
                self.delete(fourth)  # 3 rows counted: the cap is reached
            self.assertIn("LOGIN_MAIL_DAILY_CAP", logs.output[0])
            self.assertFalse(User.objects.filter(pk=fourth.pk).exists())
            self.assertEqual(len(django_mail.outbox), 2)

    def test_the_cap_reached_deletes_all_the_same(self):
        with override_settings(LOGIN_MAIL_DAILY_CAP=1):
            LoginCode.objects.create(email="a@example.fr", code_hash="a" * 64, token_hash="1" * 64,
                                     expires_at=timezone.now(), sent_at=timezone.now())
            with self.assertLogs("accounts.deletion", "ERROR"):
                self.delete()
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertEqual(django_mail.outbox, [])

    def test_a_refused_mail_is_not_counted(self):
        sent = LoginCode.objects.filter(sent_at__isnull=False).count()
        with mock.patch.object(mail, "send", side_effect=mail.MailDeliveryFailed("x")):
            self.delete()
        self.assertEqual(LoginCode.objects.filter(sent_at__isnull=False).count(), sent)


class ConfirmPageTests(DeletionTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_get_asks_and_changes_nothing(self):
        response = self.client.get(CONFIRM)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        page = text(html)
        self.assertIn("<title>Supprimer mon compte, tri-offres</title>", html)
        self.assertIn("définitif", page)
        self.assertIn("immédiat", page)
        self.assertIn("30 jours", page)
        for kept in ("réponses", "CV", "synthèses", "avis", "notes", "mails"):
            self.assertIn(kept, page)
        self.assertIn(f'<form method="post" action="{CONFIRM}"', html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertIn("Supprimer définitivement mon compte</button>", html)
        self.assertRegex(html, rf'href="{MY_DATA}"><svg[^>]*>.*?</svg>Annuler</a>')
        self.assertNotIn("nora@example.fr", html.split("<main")[1])
        assert_french_copy(self, html)
        assert_unique_ids(self, html)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertEqual(django_mail.outbox, [])

    def test_head_and_other_methods_change_nothing(self):
        self.assertEqual(self.client.put(CONFIRM).status_code, 405)
        self.assertEqual(self.client.delete(CONFIRM).status_code, 405)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_post_erases_signs_out_and_goes_to_the_last_page(self):
        other_browser = self.login()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(CONFIRM)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], DONE)
        self.assertFalse(User.objects.filter(email=EMAIL).exists())
        self.assertEqual([message.to for message in django_mail.outbox], [[EMAIL]])
        for client in (self.client, other_browser):
            self.assertTrue(client.get(MY_DATA)["Location"].startswith("/?"))
        self.assertEqual(count_rows(self.other)["Score"], 1)

    def test_post_without_the_csrf_token_is_refused(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        response = strict.post(CONFIRM)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertEqual(Score.objects.filter(user=self.user).count(), 1)

    def test_post_with_the_token_of_the_page_is_accepted(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        html = strict.get(CONFIRM).content.decode()
        token = html.split('name="csrfmiddlewaretoken" value="')[1].split('"')[0]
        response = strict.post(CONFIRM, {"csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 303)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        anonymous = Client()
        self.assertTrue(anonymous.get(CONFIRM)["Location"].startswith("/?"))
        self.assertTrue(anonymous.post(CONFIRM)["Location"].startswith("/?"))
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_staff_and_superusers_are_refused_and_nothing_is_erased(self):
        for field in ("is_staff", "is_superuser"):
            with self.subTest(field=field):
                person = make_finished_profile(f"{field}@example.fr").user
                User.objects.filter(pk=person.pk).update(**{field: True})
                client = Client()
                client.force_login(person)
                self.assertEqual(client.get(CONFIRM).status_code, 403)
                self.assertEqual(client.post(CONFIRM).status_code, 403)
                self.assertTrue(User.objects.filter(pk=person.pk).exists())
                self.assertTrue(Profile.objects.filter(user=person).exists())
        self.assertEqual(django_mail.outbox, [])

    def test_the_confirm_page_is_not_cached(self):
        self.assertIn("no-store", self.client.get(CONFIRM)["Cache-Control"])


@override_settings(STORAGES=PLAIN_STATIC)
class LastPageTests(TestCase):
    def test_public_fixed_and_private(self):
        response = Client().get(DONE)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex")
        self.assertEqual(response["Referrer-Policy"], "strict-origin")
        self.assertIn("<title>Compte supprimé, tri-offres</title>", html)
        self.assertIn("<h1", html)
        self.assertNotIn("@", html.split("<main")[1].split("</main>")[0])
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    def test_the_same_page_for_a_signed_in_person_without_their_data(self):
        user = make_finished_profile(EMAIL).user
        client = Client()
        client.force_login(user)
        html = client.get(DONE).content.decode()
        self.assertNotIn(EMAIL, html)
        self.assertNotIn("Se déconnecter", html)

    def test_post_is_refused(self):
        self.assertEqual(Client().post(DONE).status_code, 405)


@override_settings(STORAGES=PLAIN_STATIC)
class MyDataLinkTests(TestCase):
    def test_link_to_the_deletion_page(self):
        user = make_finished_profile(EMAIL).user
        self.client.force_login(user)
        html = self.client.get(MY_DATA).content.decode()
        danger = html[html.index('<div class="danger-zone">'):]
        self.assertIn(f'<a class="button button--danger" href="{CONFIRM}">Supprimer mon compte</a>', danger)
        self.assertNotIn("écrivez à", html)
        self.assertNotIn("mailto:", html)

    def test_no_link_for_the_team(self):
        for field in ("is_staff", "is_superuser"):
            with self.subTest(field=field):
                user = make_finished_profile(f"{field}@example.fr").user
                User.objects.filter(pk=user.pk).update(**{field: True})
                self.client.force_login(user)
                html = self.client.get(MY_DATA).content.decode()
                self.assertNotIn(CONFIRM, html)
                self.assertIn("La suppression de votre compte se fait par l’administration du site.", text(html))

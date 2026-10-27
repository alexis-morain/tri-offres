"""Unsubscribe and re-subscribe (sub-step 7.2, requirements E1 to E11 of the design plan): the signed
token, the two pages and the one-click view, the mail headers' source, the log filter and the « Mes données » button.
Nothing leaves: locmem backend, fake engine."""
import re
from datetime import timedelta
from unittest import mock

from django.core import mail as django_mail
from django.core import signing
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from accounts import unsubscribe
from accounts.log_filters import redact
from accounts.models import User
from accounts.testing import make_finished_profile, make_profile
from config.testing import PLAIN_STATIC, assert_french_copy, assert_unique_ids, visible_text

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
SITE = "https://tri.example.fr"
EMAIL = "nora@example.fr"
FORM = "application/x-www-form-urlencoded"
ONE_CLICK_BODY = "List-Unsubscribe=One-Click"
DONE = "C’est fait, vous ne recevrez plus de mail."
MY_DATA = "/mes-donnees/"
RESUBSCRIBE = "/mes-donnees/reabonner/"


def page_path(token):
    return f"/desinscription/{token}/"


def one_click_path(token):
    return f"/desinscription/{token}/un-clic/"


def without_csrf(html):
    return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', 'name="csrfmiddlewaretoken" value="X"', html)


def text(html):
    return " ".join(visible_text(html).split())


class UnsubscribeTestCase(TestCase):
    def setUp(self):
        self.user = make_profile(EMAIL).user
        self.token = unsubscribe.make_token(self.user)

    def status(self, user=None):
        return User.objects.get(pk=(user or self.user).pk).status


@override_settings(SITE_URL=SITE)
class TokenTests(UnsubscribeTestCase):
    def test_round_trip(self):
        self.assertEqual(unsubscribe.read_token(self.token), self.user)

    def test_carries_the_key_and_the_join_date_never_the_address(self):
        payload = signing.loads(self.token, salt="tri-offres.unsubscribe", max_age=None)
        self.assertEqual(payload, [self.user.pk, int(self.user.date_joined.timestamp())])
        self.assertNotIn(EMAIL, self.token)

    def test_token_fits_the_url_pattern(self):
        self.assertRegex(self.token, r"^[A-Za-z0-9_:.-]{1,200}$")

    def test_tampered_truncated_or_foreign_tokens_read_nothing(self):
        payload, timestamp, signature = self.token.split(":")
        flipped = "A" if signature[0] != "A" else "B"
        other_salt = signing.dumps([self.user.pk, int(self.user.date_joined.timestamp())], salt="other")
        other_user = signing.dumps([self.user.pk + 1, 0], salt=unsubscribe.SALT)
        for value in (f"{payload}:{timestamp}:{flipped}{signature[1:]}", self.token[:-4], self.token[:10], "",
                      "x", "a:b:c", other_salt, other_user, signing.dumps("text", salt=unsubscribe.SALT),
                      signing.dumps([self.user.pk], salt=unsubscribe.SALT),
                      signing.dumps(["1", "2"], salt=unsubscribe.SALT),
                      signing.dumps([True, 1], salt=unsubscribe.SALT),
                      signing.dumps({"a": 1}, salt=unsubscribe.SALT)):
            with self.subTest(value=value[:20]):
                self.assertIsNone(unsubscribe.read_token(value))

    def test_never_expires(self):
        with mock.patch("django.core.signing.time.time", return_value=1_000_000):
            old = unsubscribe.make_token(self.user)
        self.assertEqual(unsubscribe.read_token(old), self.user)

    def test_old_secret_key_in_the_fallbacks_still_reads(self):
        with override_settings(SECRET_KEY="old-key-for-tests-0123456789abcdef0123456789abcdef"):
            old = unsubscribe.make_token(self.user)
        self.assertIsNone(unsubscribe.read_token(old))
        with override_settings(SECRET_KEY_FALLBACKS=["old-key-for-tests-0123456789abcdef0123456789abcdef"]):
            self.assertEqual(unsubscribe.read_token(old), self.user)

    def test_deleted_account_reads_nothing(self):
        User.objects.filter(pk=self.user.pk).delete()
        self.assertIsNone(unsubscribe.read_token(self.token))

    def test_account_recreated_with_the_same_key_reads_nothing(self):
        pk = self.user.pk
        User.objects.filter(pk=pk).delete()
        again = User.objects.create_user(EMAIL, pk=pk, date_joined=self.user.date_joined + timedelta(seconds=5))
        self.assertEqual(again.pk, pk)
        self.assertIsNone(unsubscribe.read_token(self.token))

    def test_account_table_never_reuses_a_key(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite only")
        with connection.cursor() as cursor:
            cursor.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'accounts_user'")
            self.assertIn("AUTOINCREMENT", cursor.fetchone()[0])

    def test_urls_are_built_from_site_url(self):
        self.assertEqual(unsubscribe.unsubscribe_url(self.user), f"{SITE}/desinscription/{self.token}/")
        self.assertEqual(unsubscribe.one_click_url(self.user), f"{SITE}/desinscription/{self.token}/un-clic/")

    def test_mail_headers_only_over_https(self):
        self.assertEqual(unsubscribe.mail_headers(self.user), {
            "List-Unsubscribe": f"<{SITE}/desinscription/{self.token}/un-clic/>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
        with override_settings(SITE_URL="http://localhost:8000"):
            self.assertEqual(unsubscribe.mail_headers(self.user), {})


@override_settings(SITE_URL=SITE, STORAGES=PLAIN_STATIC, EMAIL_BACKEND=LOCMEM, DEV_EMAILS=[], CLAUDE_ENGINE="fake")
class PageTests(UnsubscribeTestCase):
    def setUp(self):
        super().setUp()
        self.client = Client(enforce_csrf_checks=True)

    def post(self, token, client=None):
        """The button's POST: the form fetched first, so the CSRF cookie and token are real."""
        client = client or self.client
        page = client.get(page_path(token))
        csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        return client.post(page_path(token), {"csrfmiddlewaretoken": csrf})

    def test_get_asks_and_changes_nothing(self):
        response = self.client.get(page_path(self.token))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(text(html).count("Ne plus recevoir le mail du matin"), 1)
        self.assertIn("Arrêter les mails", html)
        self.assertIn('method="post"', html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertIn("«&nbsp;Mes données&nbsp;»", html)
        assert_french_copy(self, html)
        assert_unique_ids(self, html)
        self.assertEqual(self.status(), User.Status.ACTIVE)

    def test_private_headers(self):
        for response in (self.client.get(page_path(self.token)), self.post(self.token)):
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(response["X-Robots-Tag"], "noindex")
            self.assertEqual(response["Referrer-Policy"], "strict-origin")

    def test_get_page_is_the_same_for_a_good_and_a_forged_token_and_shows_nothing_of_the_person(self):
        good = self.client.get(page_path(self.token)).content.decode()
        forged = self.client.get(page_path("forged:token:value")).content.decode()
        self.assertEqual(without_csrf(good), without_csrf(forged))
        for secret in (EMAIL, self.token):
            self.assertNotIn(secret, good)

    def test_token_outside_the_pattern_is_a_404(self):
        for token in ("a" * 201, "bad%20token", "bad+token", "bad%2Ftoken", "caf%C3%A9"):
            with self.subTest(token=token):
                self.assertEqual(self.client.get(f"/desinscription/{token}/").status_code, 404)
                self.assertEqual(self.client.post(f"/desinscription/{token}/").status_code, 404)

    def test_post_unsubscribes_without_touching_anything_else(self):
        before = User.objects.filter(pk=self.user.pk).values().get()
        response = self.post(self.token)
        self.assertEqual(response.status_code, 200)
        self.assertIn(DONE, text(response.content.decode()))
        after = User.objects.filter(pk=self.user.pk).values().get()
        self.assertEqual({key for key in before if before[key] != after[key]}, {"status"})
        self.assertEqual(after["status"], User.Status.UNSUBSCRIBED)
        self.assertTrue(after["is_active"])
        self.assertFalse(User.objects.get(pk=self.user.pk).has_usable_password())
        self.assertEqual(django_mail.outbox, [])

    def test_post_needs_the_csrf_token(self):
        self.assertEqual(self.client.post(page_path(self.token)).status_code, 403)
        self.assertEqual(self.status(), User.Status.ACTIVE)

    def test_a_paused_account_is_unsubscribed_too(self):
        User.objects.filter(pk=self.user.pk).update(status=User.Status.PAUSED)
        self.post(self.token)
        self.assertEqual(self.status(), User.Status.UNSUBSCRIBED)

    def test_post_answers_the_same_for_every_token(self):
        gone = make_profile("gone@example.fr").user
        gone_token = unsubscribe.make_token(gone)
        User.objects.filter(pk=gone.pk).delete()
        done = make_profile("done@example.fr").user
        User.objects.filter(pk=done.pk).update(status=User.Status.UNSUBSCRIBED)
        bodies = []
        for token in (self.token, "forged:token:value", gone_token, unsubscribe.make_token(done), self.token):
            response = self.post(token, Client(enforce_csrf_checks=True))
            self.assertEqual(response.status_code, 200)
            bodies.append(without_csrf(response.content.decode()))
        self.assertEqual(len(set(bodies)), 1)
        self.assertEqual(self.status(), User.Status.UNSUBSCRIBED)

    def test_post_leaves_other_people_alone(self):
        paul = make_profile("paul@example.fr").user
        self.post(self.token)
        self.assertEqual(self.status(paul), User.Status.ACTIVE)

    def test_other_methods_are_refused(self):
        for method in ("put", "delete", "patch"):
            with self.subTest(method=method), self.assertLogs("django.request", "WARNING"):
                self.assertEqual(getattr(Client(), method)(page_path(self.token)).status_code, 405)
        self.assertEqual(self.status(), User.Status.ACTIVE)

    def test_done_page_shows_nothing_of_the_person(self):
        html = self.post(self.token).content.decode()
        for secret in (EMAIL, self.token):
            self.assertNotIn(secret, html)
        assert_french_copy(self, html)
        assert_unique_ids(self, html)


@override_settings(SITE_URL=SITE, STORAGES=PLAIN_STATIC)
class OneClickTests(UnsubscribeTestCase):
    def setUp(self):
        super().setUp()
        self.client = Client(enforce_csrf_checks=True)  # the view is exempt: no token is sent

    def one_click(self, token=None, body=ONE_CLICK_BODY, content_type=FORM):
        return self.client.post(one_click_path(token or self.token), body, content_type=content_type)

    def test_unsubscribes_with_an_empty_200(self):
        response = self.one_click()
        self.assertEqual((response.status_code, response.content), (200, b""))
        self.assertEqual(self.status(), User.Status.UNSUBSCRIBED)

    def test_content_type_may_carry_a_charset(self):
        self.assertEqual(self.one_click(content_type=FORM + "; charset=UTF-8").status_code, 200)
        self.assertEqual(self.status(), User.Status.UNSUBSCRIBED)

    def test_same_empty_200_for_a_forged_token_or_a_deleted_account(self):
        User.objects.filter(pk=self.user.pk).delete()
        for token in (self.token, "forged:token:value"):
            response = self.one_click(token)
            self.assertEqual((response.status_code, response.content), (200, b""))

    def test_paused_account_is_unsubscribed_and_a_second_call_changes_nothing(self):
        User.objects.filter(pk=self.user.pk).update(status=User.Status.PAUSED)
        self.assertEqual(self.one_click().status_code, 200)
        self.assertEqual(self.one_click().status_code, 200)
        self.assertEqual(self.status(), User.Status.UNSUBSCRIBED)

    def test_wrong_body_or_type_is_a_400_without_effect(self):
        cases = [("List-Unsubscribe=Other", FORM), ("", FORM), ("List-Unsubscribe=one-click", FORM),
                 ("other=One-Click", FORM), ("List-Unsubscribe=One-Click&x=1&List-Unsubscribe=No", FORM),
                 (ONE_CLICK_BODY, "text/plain"), ('{"List-Unsubscribe": "One-Click"}', "application/json"),
                 (ONE_CLICK_BODY, "multipart/form-data; boundary=x")]
        for body, content_type in cases:
            with self.subTest(body=body, content_type=content_type):
                self.assertEqual(self.one_click(body=body, content_type=content_type).status_code, 400)
        multipart = self.client.post(one_click_path(self.token), {"List-Unsubscribe": "One-Click"})
        self.assertEqual(multipart.status_code, 400)  # the right field in the wrong encoding
        self.assertEqual(self.status(), User.Status.ACTIVE)

    def test_get_and_head_are_refused_without_effect(self):
        for method in ("get", "head", "put"):
            with self.subTest(method=method), self.assertLogs("django.request", "WARNING"):
                self.assertEqual(getattr(self.client, method)(one_click_path(self.token)).status_code, 405)
        self.assertEqual(self.status(), User.Status.ACTIVE)

    def test_token_outside_the_pattern_is_a_404(self):
        self.assertEqual(self.one_click("a" * 201).status_code, 404)

    def test_headers_keep_the_response_private(self):
        response = self.one_click()
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "strict-origin")

    def test_it_is_the_only_csrf_exempt_view(self):
        def callbacks(patterns):
            for pattern in patterns:
                if isinstance(pattern, URLResolver):
                    yield from callbacks(pattern.url_patterns)
                elif isinstance(pattern, URLPattern):
                    yield pattern.callback

        exempt = {f"{view.__module__}.{view.__name__}" for view in callbacks(get_resolver().url_patterns)
                  if getattr(view, "csrf_exempt", False)}
        self.assertEqual(exempt, {"accounts.views.unsubscribe_one_click"})


class LogRedactionTests(SimpleTestCase):
    def test_unsubscribe_tokens_are_masked_in_both_paths(self):
        for path in ("/desinscription/{}/", "/desinscription/{}/un-clic/"):
            line = f'Method Not Allowed (HEAD): {path.format("Wy1hLDJd:1vAbC:sig_-")}'
            self.assertEqual(redact(line), f"Method Not Allowed (HEAD): {path.format('[redacted]')}")

    def test_sign_in_links_are_still_masked(self):
        self.assertEqual(redact("/connexion/lien/abc_-/"), "/connexion/lien/[redacted]/")

    def test_other_paths_are_untouched(self):
        self.assertEqual(redact("/mes-donnees/reabonner/"), "/mes-donnees/reabonner/")


@override_settings(SITE_URL=SITE, STORAGES=PLAIN_STATIC)
class LogTests(UnsubscribeTestCase):
    def test_token_is_redacted_from_request_logs(self):
        for client_call in (lambda: self.client.head(page_path(self.token)),
                            lambda: self.client.get(one_click_path(self.token))):
            with self.assertLogs("django.request", level="WARNING") as logs:
                self.assertEqual(client_call().status_code, 405)
            output = "\n".join(logs.output)
            self.assertIn("/desinscription/[redacted]/", output)
            self.assertNotIn(self.token, output)

    def test_token_is_redacted_from_csrf_logs(self):
        with self.assertLogs("django.security.csrf", level="WARNING") as logs:
            Client(enforce_csrf_checks=True).post(page_path(self.token))
        output = "\n".join(logs.output)
        self.assertIn("/desinscription/[redacted]/", output)
        self.assertNotIn(self.token, output)

    def test_unsubscribing_logs_the_key_only(self):
        with self.assertLogs("accounts.unsubscribe", level="INFO") as logs:
            self.client.post(one_click_path(self.token), ONE_CLICK_BODY, content_type=FORM)
        self.assertIn(f"Account {self.user.pk} unsubscribed.", logs.output[0])
        for secret in (EMAIL, self.token):
            self.assertNotIn(secret, logs.output[0])


@override_settings(SITE_URL=SITE, STORAGES=PLAIN_STATIC)
class ResubscribeTests(TestCase):
    def setUp(self):
        self.user = make_finished_profile(EMAIL).user
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def set_status(self, status):
        User.objects.filter(pk=self.user.pk).update(status=status)

    def status(self):
        return User.objects.get(pk=self.user.pk).status

    def csrf_post(self, path=RESUBSCRIBE):
        page = self.client.get(MY_DATA).content.decode()
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
        return self.client.post(path, {"csrfmiddlewaretoken": token})

    def test_my_data_says_the_mails_are_on(self):
        html = self.client.get(MY_DATA).content.decode()
        self.assertIn("Vous recevez le mail du matin", text(html))
        self.assertNotIn("Recevoir de nouveau le mail du matin", html)

    def test_my_data_offers_the_button_once_unsubscribed(self):
        self.set_status(User.Status.UNSUBSCRIBED)
        html = self.client.get(MY_DATA).content.decode()
        self.assertIn("Vous ne recevez plus de mail", text(html))
        self.assertIn("Recevoir de nouveau le mail du matin", html)
        self.assertIn(f'action="{RESUBSCRIBE}"', html)
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    def test_my_data_shows_nothing_about_mails_for_a_paused_account(self):
        self.set_status(User.Status.PAUSED)
        html = text(self.client.get(MY_DATA).content.decode())
        for wanted in ("Vous recevez le mail du matin", "Vous ne recevez plus de mail"):
            self.assertNotIn(wanted, html)

    def test_post_resubscribes_and_goes_back_to_my_data(self):
        self.set_status(User.Status.UNSUBSCRIBED)
        response = self.csrf_post()
        self.assertEqual((response.status_code, response["Location"]), (303, MY_DATA))
        self.assertEqual(self.status(), User.Status.ACTIVE)
        self.assertIn(self.user, User.objects.mailable())

    def test_only_an_unsubscribed_account_changes(self):
        self.set_status(User.Status.PAUSED)
        self.csrf_post()
        self.assertEqual(self.status(), User.Status.PAUSED)

    def test_post_needs_a_session_get_is_refused_and_csrf_is_enforced(self):
        self.set_status(User.Status.UNSUBSCRIBED)
        self.assertEqual(self.client.post(RESUBSCRIBE).status_code, 403)  # no CSRF token
        with self.assertLogs("django.request", "WARNING"):
            self.assertEqual(self.client.get(RESUBSCRIBE).status_code, 405)
        self.assertEqual(self.status(), User.Status.UNSUBSCRIBED)
        signed_out = Client()
        response = signed_out.post(RESUBSCRIBE)
        self.assertEqual((response.status_code, response["Location"].split("?")[0]), (302, "/"))
        self.assertEqual(self.status(), User.Status.UNSUBSCRIBED)

    def test_resubscribing_leaves_other_people_alone(self):
        paul = make_profile("paul@example.fr").user
        User.objects.filter(pk=paul.pk).update(status=User.Status.UNSUBSCRIBED)
        self.set_status(User.Status.UNSUBSCRIBED)
        self.csrf_post()
        self.assertEqual(User.objects.get(pk=paul.pk).status, User.Status.UNSUBSCRIBED)


    def end_trial(self):
        past = timezone.now() - timedelta(days=1)
        User.objects.filter(pk=self.user.pk).update(trial_ends_at=past)

    @override_settings(BETA=False)
    def test_resubscribing_after_the_trial_ends_does_not_bring_the_mails_back(self):
        self.end_trial()
        self.set_status(User.Status.UNSUBSCRIBED)
        self.csrf_post()
        self.assertEqual(self.status(), User.Status.ACTIVE)
        self.assertNotIn(self.user, User.objects.mailable())

    @override_settings(BETA=True)
    def test_resubscribing_with_beta_on_brings_the_mails_back(self):
        self.end_trial()
        self.set_status(User.Status.UNSUBSCRIBED)
        self.csrf_post()
        self.assertIn(self.user, User.objects.mailable())


@override_settings(BETA=False, SITE_URL=SITE, STORAGES=PLAIN_STATIC)
class UnsubscribeAfterTheTrialTests(UnsubscribeTestCase):
    def setUp(self):
        super().setUp()
        past = timezone.now() - timedelta(days=1)
        User.objects.filter(pk=self.user.pk).update(trial_ends_at=past)
        self.client = Client(enforce_csrf_checks=True)

    def test_one_click_still_unsubscribes(self):
        response = self.client.post(one_click_path(self.token), ONE_CLICK_BODY, content_type=FORM)
        self.assertEqual((response.status_code, response.content), (200, b""))
        self.assertEqual(self.status(), User.Status.UNSUBSCRIBED)

    def test_the_page_still_unsubscribes(self):
        page = self.client.get(page_path(self.token))
        csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        self.client.post(page_path(self.token), {"csrfmiddlewaretoken": csrf})
        self.assertEqual(self.status(), User.Status.UNSUBSCRIBED)

"""The screens around the OpenRouter key (step B): the education screen, the tutorial, the limit advice after the
connection, « Mes données », the deletion screen and the privacy page."""
import re
from datetime import timedelta
from html import unescape

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import openrouter
from accounts.models import OpenRouterKey, Profile
from accounts.testing import make_finished_profile
from config.testing import PLAIN_STATIC, assert_french_copy, assert_unique_ids, visible_text

REVOKE = "pensez à révoquer la clé chez OpenRouter"


def spaced(value):
    return value.replace("\xa0", " ")


def plain(response):
    return " ".join(unescape(visible_text(response.content.decode())).replace("\xa0", " ").split())


@override_settings(CLAUDE_ENGINE="api", STORAGES=PLAIN_STATIC)
class ScreenCase(TestCase):
    def setUp(self):
        self.user = make_finished_profile().user
        self.client.force_login(self.user)

    def connect(self, **fields):
        return OpenRouterKey.objects.create(user=self.user, encrypted_key="x", fingerprint="a" * 64, **fields)

    def get(self, name):
        response = self.client.get(reverse(name))
        self.assertEqual(response.status_code, 200)
        return response


class EducationScreenTests(ScreenCase):
    def test_it_says_the_five_points_with_the_figures_from_the_constants(self):
        response = self.get("accounts:openrouter_connect")
        text = plain(response)
        self.assertIn(f"environ {spaced(openrouter.SIGNUP_COST)}", text)
        self.assertIn(f"{spaced(openrouter.MONTH_COST_MAX)} au plus", text)
        self.assertIn(f"ajoutez {spaced(openrouter.ADVISED_CREDIT)} et posez une limite sur la clé", text)
        self.assertIn("Nous ne dépensons jamais au-delà du crédit et de la limite que vous avez posée", text)
        self.assertIn("attention à la recharge automatique d’OpenRouter", text)
        self.assertIn("la notation s’arrête et nous vous prévenons par mail", text)
        self.assertNotIn("sans frais supplémentaires", text)
        self.assertEqual(response.content.decode().count('class="education__point"'), 5)

    def test_the_figures_are_dollars_in_constants(self):
        for value in (openrouter.SIGNUP_COST, openrouter.MONTH_COST_MAX, openrouter.ADVISED_CREDIT):
            self.assertRegex(value, r"^\d+(,\d+)? \$$")

    def test_it_has_the_button_and_a_link_to_the_tutorial(self):
        html = self.get("accounts:openrouter_connect").content.decode()
        self.assertIn(reverse("accounts:openrouter_start"), html)
        self.assertIn("Connecter OpenRouter", html)
        self.assertIn(reverse("accounts:openrouter_tutorial"), html)

    def test_house_style(self):
        html = self.get("accounts:openrouter_connect").content.decode()
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    def test_a_connected_account_sees_its_state_not_the_lesson(self):
        self.connect()
        text = plain(self.get("accounts:openrouter_connect"))
        self.assertIn("OpenRouter est connecté depuis le", text)
        self.assertNotIn("Pourquoi", text)


class TutorialTests(ScreenCase):
    def test_the_tutorial_is_a_static_page_of_numbered_steps(self):
        html = self.get("accounts:openrouter_tutorial").content.decode()
        self.assertEqual(reverse("accounts:openrouter_tutorial"), "/openrouter/tuto/")
        self.assertEqual(html.count("<ol"), 1)
        text = plain(self.client.get("/openrouter/tuto/"))
        for step in ("compte OpenRouter", "crédit", "Revenir", "limite", "dépense"):
            self.assertIn(step, text)
        assert_french_copy(self, html)
        assert_unique_ids(self, html)

    def test_it_opens_without_an_account_too(self):
        self.client.logout()
        self.assertEqual(self.client.get("/openrouter/tuto/").status_code, 200)

    def test_it_links_back_to_the_connection(self):
        self.assertIn(reverse("accounts:openrouter_connect"),
                      self.get("accounts:openrouter_tutorial").content.decode())


class DoneScreenTests(ScreenCase):
    def test_it_asks_for_a_limit_with_the_link_of_the_key_then_goes_on(self):
        row = self.connect()
        response = self.get("accounts:openrouter_done")
        html = response.content.decode()
        self.assertIn("Posez une limite sur votre clé", plain(response))
        self.assertIn(openrouter.key_url(row.fingerprint), html)
        self.assertIn("Continuer", html)
        assert_french_copy(self, html)

    def test_a_person_still_in_the_questionnaire_continues_there(self):
        self.connect()
        Profile.objects.filter(user=self.user).update(onboarding_step="", onboarding_completed_at=None)
        self.assertIn(f'href="{reverse("onboarding:start")}"', self.get("accounts:openrouter_done").content.decode())

    def test_a_finished_account_continues_to_the_sort_page(self):
        self.connect()
        self.assertIn('href="/tri/"', self.get("accounts:openrouter_done").content.decode())


class MyDataTests(ScreenCase):
    def test_without_a_key_the_row_says_so_and_offers_the_connection(self):
        response = self.get("onboarding:my_data")
        self.assertIn("Pas connecté", plain(response))
        self.assertIn(reverse("accounts:openrouter_connect"), response.content.decode())
        self.assertNotIn("Déconnecter OpenRouter", response.content.decode())

    def test_a_connected_account_sees_the_date_the_link_and_the_disconnect_button(self):
        row = self.connect(connected_at=timezone.now() - timedelta(days=3))
        response = self.get("onboarding:my_data")
        html = response.content.decode()
        text = plain(response)
        self.assertIn(f"Connecté depuis le {timezone.localtime(row.connected_at).day} ", text)
        self.assertIn(openrouter.key_url(row.fingerprint), html)
        self.assertRegex(html, r'<form[^>]*action="/openrouter/deconnecter/"[^>]*method="post"|'
                               r'<form[^>]*method="post"[^>]*action="/openrouter/deconnecter/"')
        self.assertIn("Déconnecter OpenRouter", html)
        self.assertIn(REVOKE, text)
        assert_unique_ids(self, html)
        assert_french_copy(self, html)

    def test_a_refusal_in_progress_is_shown_with_the_link(self):
        row = self.connect(refused_since=timezone.now())
        response = self.get("onboarding:my_data")
        self.assertIn("OpenRouter refuse votre clé", plain(response))
        self.assertIn(openrouter.key_url(row.fingerprint), response.content.decode())

    def test_the_trial_row_is_gone(self):
        text = plain(self.get("onboarding:my_data"))
        self.assertNotIn("Essai", text)
        self.assertNotIn("essai", text)

    def test_the_disconnect_button_works_from_there(self):
        self.connect()
        self.client.post(reverse("accounts:openrouter_disconnect"))
        self.assertFalse(OpenRouterKey.objects.exists())


class DeleteScreenTests(ScreenCase):
    def test_the_deletion_screen_tells_to_revoke_the_key(self):
        text = plain(self.get("onboarding:delete_account"))
        self.assertIn(REVOKE, text)

    def test_with_a_key_it_links_to_the_page_of_the_key(self):
        row = self.connect()
        self.assertIn(openrouter.key_url(row.fingerprint), self.get("onboarding:delete_account").content.decode())


class PrivacyTests(ScreenCase):
    def test_openrouter_is_named_as_a_processor_and_the_prepass_stays_with_the_service(self):
        text = plain(self.get("accounts:privacy"))
        self.assertIn("OpenRouter", text)
        self.assertRegex(text, r"passent par votre compte OpenRouter")
        self.assertRegex(text, r"résumé de chaque offre.{0,120}compte du service")

    def test_the_trial_is_gone(self):
        text = plain(self.get("accounts:privacy"))
        self.assertNotIn("Fin d’essai", text)
        self.assertIsNone(re.search(r"votre essai", text))

    def test_the_stored_key_is_listed(self):
        self.assertIn("Clé OpenRouter", plain(self.get("accounts:privacy")))

"""Privacy page and legal notice (sub-step 7.3, the design plan): public pages written from the
code (the durations are read from the constants that purge), settings-driven publisher lines, footer links on every
page, and the mail line of « Mes données » for an account whose questionnaire is not finished."""
from html import unescape
from unittest import mock

from django.conf import settings
from django.test import Client, TestCase, override_settings

from accounts import deletion, journal, trial
from accounts.management.commands import purge_login_codes
from accounts.models import User
from accounts.testing import make_finished_profile, make_profile
from config.backups import BACKUP_DAYS
from config.testing import PLAIN_STATIC, assert_french_copy, assert_unique_ids, visible_text
from jobs.models import FULL_TEXT_DAYS, OFFER_DAYS

PRIVACY = "/confidentialite/"
LEGAL = "/mentions-legales/"
MY_DATA = "/mes-donnees/"
CLOUDFLARE = "Cloudflare, Inc., 101 Townsend St, San Francisco, CA 94107, États-Unis"


def text(response):
    return " ".join(visible_text(response.content.decode()).split())


@override_settings(STORAGES=PLAIN_STATIC)
class PrivacyPageTests(TestCase):
    def test_public_and_french(self):
        response = Client().get(PRIVACY)
        self.assertEqual(response.status_code, 200)
        assert_french_copy(self, response.content.decode())
        assert_unique_ids(self, response.content.decode())

    def test_no_longer_a_draft(self):
        self.assertNotIn("sera complétée", text(self.client.get(PRIVACY)))

    def test_durations_are_the_ones_the_code_applies(self):
        page = text(self.client.get(PRIVACY))
        for wanted in (f"{FULL_TEXT_DAYS} jours", f"{OFFER_DAYS} jours",
                       f"{purge_login_codes.KEEP.days} jours",
                       f"{settings.SESSION_COOKIE_AGE // 86400} jours",
                       f"{int(deletion.CODE_KEEP.total_seconds() // 3600)} heures", f"{BACKUP_DAYS} jours",
                       f"{journal.KEEP.days} jours"):
            with self.subTest(wanted=wanted):
                self.assertIn(wanted, page)

    def test_backups_say_where_they_go_and_for_how_long(self):
        page = text(self.client.get(PRIVACY))
        for wanted in ("Cloudflare R2", "Union européenne", f"gardée {BACKUP_DAYS} jours",
                       f"au plus {BACKUP_DAYS} jours", "Journal des comptes", "empreinte de l’adresse",
                       "hors des sauvegardes", "Échecs de connexion à l’administration"):
            with self.subTest(wanted=wanted):
                self.assertIn(wanted, page)

    def test_data_without_a_purge_says_so_and_invents_no_duration(self):
        self.assertIn("tant que votre compte existe", text(self.client.get(PRIVACY)))

    def test_recipients_hosting_and_rights(self):
        page = text(self.client.get(PRIVACY))
        for wanted in ("Anthropic", "Resend", "Cloudflare", "geo.api.gouv.fr", "serveur personnel en France",
                       "Supprimer mon compte", "Ma recherche", "Mes données", "CNIL", "sauvegardes",
                       "aucun outil de mesure d’audience"):
            with self.subTest(wanted=wanted):
                self.assertIn(wanted, page)
        self.assertContains(self.client.get(PRIVACY), 'href="https://www.cnil.fr"')

    def test_contact_is_the_reply_address(self):
        self.assertContains(self.client.get(PRIVACY), f"mailto:{settings.MAIL_REPLY_TO}")


@override_settings(STORAGES=PLAIN_STATIC)
class PrivacyTrialTests(TestCase):
    """The trial in the privacy page (sub-step 8.2): outside the beta only, nothing changes in the beta."""

    ROW = ("Fin d’essai La date de fin de votre essai de 7 jours, pour arrêter le mail du matin ce jour-là. "
           "Gardée tant que votre compte existe. Après l’essai, votre compte et vos données restent "
           "jusqu’à ce que vous le supprimiez dans « Mes données ».")
    OPPOSITION = "et vous vous réabonnez depuis « Mes données » pendant votre essai."
    BEFORE = "et vous vous réabonnez depuis « Mes données ». Le mail de code"

    def page(self):
        html = self.client.get(PRIVACY).content.decode()
        return " ".join(unescape(visible_text(html)).replace("\xa0", " ").split())

    @override_settings(BETA=False)
    def test_outside_the_beta_the_row_follows_the_address_and_the_opposition_says_during_the_trial(self):
        response = self.client.get(PRIVACY)
        page = " ".join(unescape(visible_text(response.content.decode())).replace("\xa0", " ").split())
        self.assertIn(self.ROW, page)
        self.assertIn(self.OPPOSITION, page)
        self.assertNotIn(self.BEFORE, page)
        self.assertLess(page.index("Adresse mail"), page.index("Fin d’essai"))
        self.assertLess(page.index("Fin d’essai"), page.index("Réponses au questionnaire"))
        assert_french_copy(self, response.content.decode())
        assert_unique_ids(self, response.content.decode())

    @override_settings(BETA=False)
    def test_the_number_of_days_is_read_from_the_constant(self):
        with mock.patch.object(trial, "TRIAL_DAYS", 5):
            self.assertIn("votre essai de 5 jours", self.page())

    @override_settings(BETA=True)
    def test_in_the_beta_the_page_is_the_one_of_before(self):
        page = self.page()
        self.assertNotIn("Fin d’essai", page)
        self.assertNotIn("votre essai", page)  # "essais répétés" (admin sign-in failures) is not the trial
        self.assertIn(self.BEFORE, page)

    def test_the_default_is_the_beta(self):
        self.assertNotIn("votre essai", self.page())


@override_settings(STORAGES=PLAIN_STATIC)
class LegalNoticeTests(TestCase):
    def test_public_and_french(self):
        response = Client().get(LEGAL)
        self.assertEqual(response.status_code, 200)
        assert_french_copy(self, response.content.decode())
        assert_unique_ids(self, response.content.decode())

    def test_fixed_lines(self):
        page = text(self.client.get(LEGAL))
        for wanted in (CLOUDFLARE, "serveur personnel", "AGPL-3.0", settings.MAIL_REPLY_TO,
                       "Directeur de la publication"):
            with self.subTest(wanted=wanted):
                self.assertIn(wanted, page)

    @override_settings(LEGAL_NAME="Jeanne Exemple", LEGAL_ADDRESS="", LEGAL_SIRET="")
    def test_empty_settings_hide_their_lines(self):
        page = text(self.client.get(LEGAL))
        self.assertIn("Jeanne Exemple", page)
        self.assertNotIn("Adresse", page)
        self.assertNotIn("SIRET", page)

    @override_settings(LEGAL_NAME="Jeanne Exemple", LEGAL_ADDRESS="1 rue des Essais, 75001 Paris",
                       LEGAL_SIRET="123 456 789 00012")
    def test_set_settings_are_shown(self):
        page = text(self.client.get(LEGAL))
        self.assertIn("1 rue des Essais, 75001 Paris", page)
        self.assertIn("SIRET 123 456 789 00012", page)

    def test_defaults(self):
        self.assertEqual(settings.LEGAL_NAME, "Nom de l’éditeur")
        self.assertEqual(settings.LEGAL_EMAIL, settings.MAIL_REPLY_TO)
        self.assertEqual((settings.LEGAL_ADDRESS, settings.LEGAL_SIRET), ("", ""))


@override_settings(STORAGES=PLAIN_STATIC)
class FooterTests(TestCase):
    def test_every_kind_of_page_links_both_pages(self):
        user = make_finished_profile("nora@example.fr").user
        signed_in = Client()
        signed_in.force_login(user)
        pages = [(Client(), "/"), (Client(), PRIVACY), (Client(), LEGAL), (signed_in, MY_DATA)]
        for client, path in pages:
            with self.subTest(path=path):
                html = client.get(path).content.decode()
                self.assertIn(f'href="{PRIVACY}"', html.split("<footer", 1)[1])
                self.assertIn(f'href="{LEGAL}"', html.split("<footer", 1)[1])


@override_settings(STORAGES=PLAIN_STATIC)
class MailLineTests(TestCase):
    def line(self, profile):
        client = Client()
        client.force_login(profile.user)
        return text(client.get(MY_DATA))

    def test_finished_active_account_receives_the_mail(self):
        page = self.line(make_finished_profile("nora@example.fr"))
        self.assertIn("Vous recevez le mail du matin.", page)

    def test_unfinished_active_account_is_told_when_it_starts(self):
        page = self.line(make_profile("lea@example.fr", onboarding_completed_at=None))
        self.assertNotIn("Vous recevez le mail du matin", page)
        self.assertIn("Vous recevrez le mail du matin dès que le questionnaire sera terminé.", page)

    def test_unfinished_unsubscribed_account_keeps_its_button(self):
        profile = make_profile("lea@example.fr", onboarding_completed_at=None)
        User.objects.filter(pk=profile.user.pk).update(status=User.Status.UNSUBSCRIBED)
        self.assertIn("Vous ne recevez plus de mail.", self.line(profile))

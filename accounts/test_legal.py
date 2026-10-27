"""Privacy page and legal notice (sub-step 7.3, the design plan): public pages written from the
code (the durations are read from the constants that purge), settings-driven publisher lines, footer links on every
page, and the mail line of « Mes données » for an account whose questionnaire is not finished."""
from django.conf import settings
from django.test import Client, TestCase, override_settings

from accounts import deletion, journal
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

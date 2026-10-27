from django.contrib import admin
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.testing import make_login_code
from accounts.models import LoginCode, OnboardingEvent, Profile, User
from config.testing import PLAIN_STATIC
from matching.models import ScoringRun
from supervision.models import AlertSent, TaskRun

# Admins that only show records: nobody types a login code, a funnel event or a scoring run by hand.
READ_ONLY_MODELS = {AlertSent, LoginCode, OnboardingEvent, ScoringRun, TaskRun}


class UserManagerTests(TestCase):
    def test_create_user_normalizes_email_to_lowercase(self):
        user = User.objects.create_user("  Nora.Dupont@Example.FR ")
        self.assertEqual(user.email, "nora.dupont@example.fr")

    def test_create_user_is_passwordless(self):
        user = User.objects.create_user("nora@example.fr")
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_create_user_requires_email(self):
        with self.assertRaises(ValueError):
            User.objects.create_user("")

    def test_duplicate_email_rejected_case_insensitively(self):
        User.objects.create_user("nora@example.fr")
        with self.assertRaises(IntegrityError):
            User.objects.create_user("NORA@example.fr")

    def test_create_superuser_has_password_and_flags(self):
        user = User.objects.create_superuser("Admin@Example.fr", "pw-for-admin")
        self.assertEqual(user.email, "admin@example.fr")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("pw-for-admin"))

    def test_defaults(self):
        user = User.objects.create_user("nora@example.fr")
        self.assertEqual(user.status, User.Status.ACTIVE)
        self.assertIsNotNone(user.date_joined)

    def test_get_by_natural_key_is_case_insensitive(self):
        user = User.objects.create_user("nora@example.fr")
        self.assertEqual(User.objects.get_by_natural_key("Nora@Example.fr"), user)


class MailableTests(TestCase):
    def test_only_active_users_with_active_status_are_mailable(self):
        mailable = User.objects.create_user("nora@example.fr")
        User.objects.create_user("paused@example.fr", status=User.Status.PAUSED)
        User.objects.create_user("unsub@example.fr", status=User.Status.UNSUBSCRIBED)
        User.objects.create_user("blocked@example.fr", is_active=False)
        self.assertQuerySetEqual(User.objects.mailable(), [mailable])


class EmailCaseInsensitivityTests(TestCase):
    """Every write path stores one lowercase address, and the database refuses case variants."""

    def test_plain_create_stores_lowercase_email(self):
        user = User.objects.create(email="  Bypass@Example.FR ")
        user.refresh_from_db()
        self.assertEqual(user.email, "bypass@example.fr")

    def test_plain_create_then_create_user_with_case_variant_rejected(self):
        User.objects.create(email="Bypass@Example.FR")
        with self.assertRaises(IntegrityError):
            User.objects.create_user("bypass@example.fr")

    def test_database_constraint_rejects_case_variant_bypassing_save(self):
        # bulk_create never calls save(), so only the database constraint can stop this.
        User.objects.create_user("bypass@example.fr")
        with self.assertRaises(IntegrityError):
            User.objects.bulk_create([User(email="Bypass@Example.FR")])

    def test_plain_create_is_passwordless(self):
        user = User.objects.create(email="nora@example.fr")
        user.refresh_from_db()
        self.assertFalse(user.has_usable_password())


class ProfileTests(TestCase):
    def test_defaults_are_empty(self):
        profile = Profile.objects.create(user=User.objects.create_user("nora@example.fr"))
        self.assertEqual(profile.hard_criteria, {})
        self.assertEqual(profile.weighted_preferences, {})
        self.assertEqual(profile.cv_profile, {})
        self.assertIsNone(profile.cv_processed_at)
        self.assertEqual((profile.seek_text, profile.refuse_text), ("", ""))

    def test_one_profile_per_user(self):
        user = User.objects.create_user("nora@example.fr")
        Profile.objects.create(user=user)
        with self.assertRaises(IntegrityError):
            Profile.objects.create(user=user)


@override_settings(STORAGES=PLAIN_STATIC)
class AdminSmokeTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin@example.fr", "pw-for-admin"))

    def test_every_registered_changelist_and_add_page_loads(self):
        User.objects.create_user("nora@example.fr")
        for model in admin.site._registry:
            info = (model._meta.app_label, model._meta.model_name)
            for view in ("changelist", "add"):
                with self.subTest(model=model.__name__, view=view):
                    response = self.client.get(reverse("admin:%s_%s_%s" % (*info, view)))
                    expected = 403 if view == "add" and model in READ_ONLY_MODELS else 200
                    self.assertEqual(response.status_code, expected)

    def test_login_code_admin_never_shows_hashes(self):
        token, _ = make_login_code("nora@example.fr")
        row = LoginCode.objects.get()
        for url in (reverse("admin:accounts_logincode_changelist"),
                    reverse("admin:accounts_logincode_change", args=[row.pk])):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn("nora@example.fr", html)
                for secret in (row.code_hash, row.token_hash, token):
                    self.assertNotIn(secret, html)

    def test_profile_change_page_shows_the_questionnaire_answers(self):
        profile = Profile.objects.create(user=User.objects.create_user("nora@example.fr"), seek_text="Un musée")
        html = self.client.get(reverse("admin:accounts_profile_change", args=[profile.pk])).content.decode()
        for name in ("domains", "hard_criteria", "weighted_preferences", "seek_text", "refuse_text", "career_change"):
            self.assertIn(f'name="{name}"', html)
        self.assertIn("Un musée", html)

    def profile_form(self, profile, **changes):
        data = {"user": profile.user.pk, "hard_criteria": "{}", "weighted_preferences": "{}", "seek_text": "",
                "refuse_text": "", "cv_profile": "{}", "learned_summary": "", "previous_job": "",
                "onboarding_step": "", **changes}
        return self.client.post(reverse("admin:accounts_profile_change", args=[profile.pk]), data)

    def test_profile_answers_that_do_not_fit_their_schema_are_refused(self):
        profile = Profile.objects.create(user=User.objects.create_user("nora@example.fr"))
        for field, value in (("hard_criteria", '{"location": "Lyon"}'), ("weighted_preferences", '{"values": ["x"]}'),
                             ("domains", '["Z"]'), ("domains", '"E"'), ("domains", '["E", "K", "L", "D"]')):
            with self.subTest(field=field):
                response = self.profile_form(profile, **{field: value})
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["adminform"].form.errors)
        profile.refresh_from_db()
        self.assertEqual((profile.hard_criteria, profile.weighted_preferences), ({}, {}))

    def test_profile_answers_that_fit_are_saved_normalized(self):
        profile = Profile.objects.create(user=User.objects.create_user("nora@example.fr"))
        response = self.profile_form(profile, hard_criteria='{"hours": {"value": "full", "strict": true}}',
                                     weighted_preferences='{"values": ["team"]}', domains='["L", "E", "L"]')
        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.domains, ["E", "L"])
        self.assertEqual(profile.hard_criteria, {"version": 1, "hours": {"value": "full", "strict": True}})
        self.assertEqual(profile.weighted_preferences, {"version": 1, "values": ["team"]})

    def test_profile_list_shows_the_domain_codes(self):
        Profile.objects.create(user=User.objects.create_user("nora@example.fr"), domains=["E", "J"])
        html = self.client.get(reverse("admin:accounts_profile_changelist")).content.decode()
        self.assertIn("Domaines", html)
        self.assertIn("E, J", html)

    def test_user_change_page_loads_for_passwordless_user(self):
        user = User.objects.create_user("nora@example.fr")
        response = self.client.get(reverse("admin:accounts_user_change", args=[user.pk]))
        self.assertEqual(response.status_code, 200)

    def test_admin_can_create_passwordless_user(self):
        response = self.client.post(
            reverse("admin:accounts_user_add"),
            {"email": "New@Example.fr", "status": "active", "is_active": "on"},
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="new@example.fr")
        self.assertFalse(user.has_usable_password())


class FreeTextMigrationTests(TransactionTestCase):
    """0005 splits free_text in two: what it held goes to seek_text."""

    before = [("accounts", "0004_onboarding_profile")]
    after = [("accounts", "0005_profile_seek_refuse_text")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_free_text_is_copied_to_seek_text(self):
        apps = self.migrate(self.before)
        user = apps.get_model("accounts", "User").objects.create(email="nora@example.fr", password="!")
        apps.get_model("accounts", "Profile").objects.create(user=user, free_text="Un poste en médiathèque")
        apps = self.migrate(self.after)
        profile = apps.get_model("accounts", "Profile").objects.get()
        self.assertEqual((profile.seek_text, profile.refuse_text), ("Un poste en médiathèque", ""))
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

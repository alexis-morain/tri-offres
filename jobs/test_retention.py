from datetime import timedelta
from io import StringIO

from django.contrib.admin import helpers
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from jobs.management.commands.purge_offres import Command
from jobs.models import FULL_TEXT_DAYS, OFFER_DAYS, JobOffer, Source
from jobs.testing import make_offer, make_source
from jobs.tests import AdminTestCase
from matching.models import Feedback, Score

MINUTE = timedelta(minutes=1)


class RetentionTestCase(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.source = make_source()
        self.user = User.objects.create_user("lea@example.fr")
        self.count = 0

    def offer(self, age, expires_at=None, full_text="texte fictif"):
        """An offer collected `age` before now (collected_at is auto_now_add, hence the update)."""
        self.count += 1
        offer = make_offer(self.source, str(self.count), full_text=full_text, expires_at=expires_at)
        JobOffer.objects.filter(pk=offer.pk).update(collected_at=self.now - age)
        return offer

    def text(self, offer):
        return JobOffer.objects.get(pk=offer.pk).full_text

    def exists(self, offer):
        return JobOffer.objects.filter(pk=offer.pk).exists()

    def seed_night(self):
        """One text to clear, one offer to delete, one kept for its feedback: 2 texts, 1 deleted, 1 kept."""
        self.offer(timedelta(days=8))
        self.offer(timedelta(days=60))
        kept = self.offer(timedelta(days=60))
        Feedback.objects.create(user=self.user, offer=kept, verdict=Feedback.Verdict.MAYBE)


class SelectionTests(RetentionTestCase):
    def test_stale(self):
        at_limit = self.offer(timedelta(days=OFFER_DAYS))
        old = self.offer(timedelta(days=OFFER_DAYS) + MINUTE)
        expired = self.offer(timedelta(days=1), expires_at=self.now - MINUTE)
        self.offer(timedelta(days=1), expires_at=self.now)
        self.offer(timedelta(days=1), expires_at=self.now + MINUTE)
        self.assertEqual(set(JobOffer.objects.stale(self.now)), {old, expired})
        self.assertNotIn(at_limit, JobOffer.objects.stale(self.now))

    def test_full_text_expired(self):
        self.offer(timedelta(days=FULL_TEXT_DAYS))
        old = self.offer(timedelta(days=FULL_TEXT_DAYS) + MINUTE)
        expired = self.offer(timedelta(days=1), expires_at=self.now - MINUTE)
        self.offer(timedelta(days=1))
        self.assertEqual(set(JobOffer.objects.full_text_expired(self.now)), {old, expired})


class PurgeTests(RetentionTestCase):
    def test_constants(self):
        self.assertEqual((FULL_TEXT_DAYS, OFFER_DAYS), (7, 45))

    def test_full_text_kept_until_seven_days(self):
        before = self.offer(timedelta(days=7) - MINUTE)
        after = self.offer(timedelta(days=7) + MINUTE)
        result = JobOffer.objects.purge(self.now)
        self.assertEqual(self.text(before), "texte fictif")
        self.assertEqual(self.text(after), "")
        self.assertEqual(result.texts, 1)

    def test_exact_limits_are_kept(self):
        text = self.offer(timedelta(days=FULL_TEXT_DAYS))
        offer = self.offer(timedelta(days=OFFER_DAYS), full_text="")
        self.assertEqual(JobOffer.objects.purge(self.now), (0, 0, 0))
        self.assertEqual(self.text(text), "texte fictif")
        self.assertTrue(self.exists(offer))

    def test_full_text_blanked_whether_scored_or_not(self):
        scored = self.offer(timedelta(days=8))
        Score.objects.create(user=self.user, offer=scored, score=7, category=Score.Category.TO_REVIEW)
        self.offer(timedelta(days=8))
        self.assertEqual(JobOffer.objects.purge(self.now).texts, 2)
        self.assertEqual(self.text(scored), "")

    def test_offer_deleted_after_forty_five_days_with_its_scores(self):
        before = self.offer(timedelta(days=45) - MINUTE, full_text="")
        after = self.offer(timedelta(days=45) + MINUTE, full_text="")
        Score.objects.create(user=self.user, offer=after, score=7, category=Score.Category.TO_REVIEW)
        result = JobOffer.objects.purge(self.now)
        self.assertTrue(self.exists(before))
        self.assertFalse(self.exists(after))
        self.assertFalse(Score.objects.exists())
        self.assertEqual((result.deleted, result.kept), (1, 0))

    def test_expired_offer_deleted_future_one_kept(self):
        expired = self.offer(timedelta(days=1), expires_at=self.now - MINUTE)
        future = self.offer(timedelta(days=1), expires_at=self.now + MINUTE)
        self.assertEqual(JobOffer.objects.purge(self.now).deleted, 1)
        self.assertFalse(self.exists(expired))
        self.assertTrue(self.exists(future))
        self.assertEqual(self.text(future), "texte fictif")

    def test_offer_with_feedback_kept_without_its_text(self):
        # Expired after two days: still within the 7 days, its text goes all the same.
        recent = self.offer(timedelta(days=2), expires_at=self.now - MINUTE)
        old = self.offer(timedelta(days=60), full_text="")
        for offer in (recent, old):
            Feedback.objects.create(user=self.user, offer=offer, verdict=Feedback.Verdict.YES)
        result = JobOffer.objects.purge(self.now)
        self.assertTrue(self.exists(recent) and self.exists(old))
        self.assertEqual(self.text(recent), "")
        self.assertEqual(JobOffer.objects.get(pk=recent.pk).title, "Chargée de production")
        self.assertEqual((result.texts, result.deleted, result.kept), (1, 0, 2))

    def test_cancelled_feedback_keeps_the_offer_too(self):
        offer = self.offer(timedelta(days=60))
        Feedback.objects.create(user=self.user, offer=offer, verdict=Feedback.Verdict.NO, cancelled=True)
        Feedback.objects.create(user=self.user, offer=offer, verdict=Feedback.Verdict.NO, cancelled=True)
        result = JobOffer.objects.purge(self.now)
        self.assertTrue(self.exists(offer))
        self.assertEqual(result.kept, 1)

    def test_dry_run_counts_without_writing(self):
        self.seed_night()
        dry = JobOffer.objects.purge(self.now, dry_run=True)
        self.assertEqual(JobOffer.objects.exclude(full_text="").count(), 3)
        self.assertEqual(JobOffer.objects.count(), 3)
        self.assertEqual(JobOffer.objects.purge(self.now), dry)
        self.assertEqual((dry.texts, dry.deleted, dry.kept), (2, 1, 1))


class PurgeCommandTests(RetentionTestCase):
    def run_command(self, *args):
        out = StringIO()
        call_command("purge_offres", *args, stdout=out)
        return out.getvalue()

    def setUp(self):
        super().setUp()
        self.seed_night()

    def test_writes_one_line(self):
        self.assertEqual(self.run_command(),
                         "purge : 2 textes effacés, 1 offres supprimées (1 gardées pour leur avis)\n")
        self.assertEqual(JobOffer.objects.count(), 2)
        self.assertFalse(JobOffer.objects.exclude(full_text="").exists())

    def test_dry_run(self):
        self.assertEqual(self.run_command("--dry-run"), "purge (--dry-run, rien d’écrit) : 2 textes effacés, "
                         "1 offres supprimées (1 gardées pour leur avis)\n")
        self.assertEqual(JobOffer.objects.count(), 3)

    def test_help_follows_the_constants(self):
        self.assertIn(f"plus de {FULL_TEXT_DAYS} jours", Command.help)
        self.assertIn(f"{OFFER_DAYS} jours", Command.help)


class OfferPropertiesTests(TestCase):
    def test_origin_url_is_the_offer_page_never_the_apply_url(self):
        offer = make_offer(make_source(), url="https://example.fr/offre", apply_url="https://ats.example.fr/postuler")
        self.assertEqual(offer.origin_url, "https://example.fr/offre")

    def test_attribution(self):
        source = make_source()
        self.assertEqual(make_offer(source, "1").attribution, "")
        licensed = make_offer(source, "2", source_license="Licence Ouverte, France Travail")
        self.assertEqual(licensed.attribution, "Licence Ouverte, France Travail")


class CutSourceActionTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.source = make_source(name="Site qui refuse")
        self.other = make_source(name="Autre site")
        user = User.objects.create_user("lea@example.fr")
        for number in range(3):
            offer = make_offer(self.source, str(number))
            Feedback.objects.create(user=user, offer=offer, verdict=Feedback.Verdict.YES)
        self.untouched = make_offer(self.other, "x")

    def post(self, **extra):
        data = {"action": "cut_and_delete_offers", helpers.ACTION_CHECKBOX_NAME: [self.source.pk], **extra}
        return self.client.post(reverse("admin:jobs_source_changelist"), data)

    def test_confirmation_page_shows_the_count_and_changes_nothing(self):
        changelist = self.client.get(reverse("admin:jobs_source_changelist")).content.decode()
        self.assertIn("Couper et supprimer ses offres", changelist)
        response = self.post()
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Site qui refuse", html)
        self.assertIn("3 offres", html)
        self.assertEqual(JobOffer.objects.filter(source=self.source).count(), 3)
        self.source.refresh_from_db()
        self.assertEqual(self.source.state, Source.State.ACTIVE)

    def test_confirmed_cuts_the_source_and_deletes_its_offers_and_feedback(self):
        response = self.post(post="yes")
        self.assertEqual(response.status_code, 302)
        self.source.refresh_from_db()
        self.assertEqual(self.source.state, Source.State.DISABLED)
        self.assertFalse(JobOffer.objects.filter(source=self.source).exists())
        self.assertFalse(Feedback.objects.exists())
        self.assertTrue(JobOffer.objects.filter(pk=self.untouched.pk).exists())
        self.other.refresh_from_db()
        self.assertEqual(self.other.state, Source.State.ACTIVE)

    def test_reserved_to_superusers(self):
        staff = User.objects.create_user("equipe@example.fr", "x", is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(content_type__app_label="jobs"))
        self.client.force_login(staff)
        changelist = self.client.get(reverse("admin:jobs_source_changelist")).content.decode()
        self.assertNotIn("cut_and_delete_offers", changelist)
        self.post(post="yes")
        self.source.refresh_from_db()
        self.assertEqual(self.source.state, Source.State.ACTIVE)
        self.assertEqual(JobOffer.objects.filter(source=self.source).count(), 3)

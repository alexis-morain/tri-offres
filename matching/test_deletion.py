"""An account deleted while a job runs (requirement E16 of the design plan): neither the command
nor the thread falls, and the other accounts are served. The deletion is made at the exact point the race would
hit: after the account was picked, before the write."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail as django_mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts import deletion
from accounts.models import User
from accounts.testing import make_profile
from jobs.testing import OfferMaker, make_source
from matching import delivery, night, on_demand, scoring
from matching.models import Delivery, Score, ScoringRun
from matching.test_night import NightTestCase
from matching.test_on_demand import OnDemandTestCase

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
State, Error = ScoringRun.State, ScoringRun.Error


@override_settings(EMAIL_BACKEND=LOCMEM, CLAUDE_ENGINE="fake", DEV_EMAILS=[])
class EnvoiDeletionTests(OfferMaker, TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.source = make_source()
        self.nora = make_profile("nora@example.fr").user
        self.paul = make_profile("paul@example.fr").user

    def test_a_person_deleted_after_the_picking_is_skipped_and_the_others_are_served(self):
        for user in (self.nora, self.paul):
            Score.objects.create(user=user, offer=self.offer(), score=7, category=Score.Category.TO_REVIEW)
        real_pick, deleted = delivery.pick, []

        def pick_then_delete_the_other(user, now):
            picked = real_pick(user, now)
            if not deleted:
                gone = self.paul if user == self.nora else self.nora
                deleted.append(gone)
                deletion.delete_account(gone)
            return picked

        out = StringIO()
        with mock.patch.object(delivery, "pick", pick_then_delete_the_other):
            call_command("envoi", "--date", "2026-10-19", stdout=out)
        gone, = deleted
        survivor = self.paul if gone == self.nora else self.nora
        self.assertEqual([message.to for message in django_mail.outbox], [[survivor.email]])
        self.assertEqual(list(Delivery.objects.values_list("user_id", flat=True)), [survivor.pk])
        self.assertIn("1 mails envoyés, 0 vides, 0 en échec", out.getvalue())
        self.assertFalse(User.objects.filter(pk=gone.pk).exists())


class OnDemandDeletionTests(OnDemandTestCase):
    def test_a_run_whose_account_is_gone_is_not_found_and_not_raised(self):
        run = self.make_run(State.RUNNING, ago=timedelta(minutes=1))
        deletion.delete_account(self.user)
        self.assertFalse(ScoringRun.objects.filter(pk=run.pk).exists())
        on_demand.execute(run.pk)  # a thread started by request_run, the account deleted before it ran
        self.assertFalse(ScoringRun.objects.exists())

    def test_a_deletion_during_the_run_leaves_no_run_and_no_score_and_no_error(self):
        other = self.other_user("lea@example.fr")
        self.offers(3)
        real = scoring.score_batch
        key = self.user.pk

        def delete_then_score(user, batch, run, timeouts):
            if user == self.user:
                deletion.delete_account(self.user)
            return real(user, batch, run, timeouts)

        with mock.patch.object(scoring, "score_batch", delete_then_score), \
                self.assertNoLogs("matching.on_demand", "ERROR"):
            self.assertEqual(self.request(), "started")
        self.assertFalse(ScoringRun.objects.filter(user_id=key).exists())
        self.assertFalse(Score.objects.filter(user_id=key).exists())
        self.assertFalse(User.objects.filter(pk=key).exists())
        self.assertTrue(User.objects.filter(pk=other.pk).exists())

    def test_finishing_a_run_that_was_deleted_does_not_insert_it_again(self):
        run = self.make_run(State.RUNNING)
        deletion.delete_account(self.user)
        run.finish(State.DONE)
        run.record_usage(mock.Mock(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                   cache_creation_input_tokens=0))
        run.save_if_exists(["offers_scored"])
        self.assertFalse(ScoringRun.objects.exists())


class NightDeletionTests(NightTestCase):
    def setUp(self):
        super().setUp()
        self.nora = make_profile("nora@example.fr")
        self.paul = make_profile("paul@example.fr")

    def test_a_person_deleted_before_the_write_of_a_batch_is_skipped_the_other_is_scored(self):
        self.offers(25)
        real = night._apply_scores
        deleted = []

        def delete_then_apply(prepared, output):
            if prepared.user == self.nora.user and not deleted:
                deleted.append(True)
                deletion.delete_account(self.nora.user)
            return real(prepared, output)

        with mock.patch.object(night, "_apply_scores", delete_then_apply):
            report = self.run_night(phases=["personnes"])
        self.assertTrue(deleted)
        self.assertFalse(User.objects.filter(pk=self.nora.user_id).exists())
        self.assertFalse(Score.objects.filter(user_id=self.nora.user_id).exists())
        self.assertFalse(ScoringRun.objects.filter(user_id=self.nora.user_id).exists())
        self.assertEqual(Score.objects.filter(user=self.paul.user).count(), 25)
        paul_run = ScoringRun.objects.get(user=self.paul.user)
        self.assertEqual((paul_run.state, paul_run.offers_scored), (State.DONE, 25))
        self.assertEqual([person.account for person in report.people], [self.nora.user_id, self.paul.user_id])

    def test_the_report_names_people_by_key_never_by_address(self):
        self.offers(3)
        report = self.run_night(phases=["personnes"])
        labels = [person.label for person in report.people]
        self.assertEqual(labels, [f"compte {self.nora.user_id}", f"compte {self.paul.user_id}"])
        lines = "\n".join(night.format_report(report))
        self.assertNotIn("@", lines)
        report = self.run_night(phases=["personnes"], dry_run=True)
        self.assertNotIn("@", "\n".join(night.format_report(report)))

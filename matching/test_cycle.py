"""The whole loop, locally (sub-step 6.6): night, morning mail, verdicts on the sort page, next night, learned summary,
third night. Fake engine, locmem mail backend, a pinned clock. Offers and addresses are fictitious."""
from datetime import date, datetime, timezone as dt_timezone
from io import StringIO
from unittest import mock

from django.core import mail as django_mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.testing import make_finished_profile
from jobs import summaries
from jobs.testing import make_offer, make_source
from llm import fake
from matching import learned, night, scoring
from matching.models import Delivery, Feedback, Score
from matching.testing import scores_answer, summaries_answer

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
SUNDAY_NOON = datetime(2026, 10, 18, 12, tzinfo=dt_timezone.utc)
MONDAY_NIGHT, MONDAY_MORNING = (datetime(2026, 10, 19, 3, tzinfo=dt_timezone.utc),
                                datetime(2026, 10, 19, 7, tzinfo=dt_timezone.utc))
MONDAY_NOON = datetime(2026, 10, 19, 12, tzinfo=dt_timezone.utc)
TUESDAY_NIGHT, WEDNESDAY_NIGHT = (datetime(2026, 10, 20, 3, tzinfo=dt_timezone.utc),
                                  datetime(2026, 10, 21, 3, tzinfo=dt_timezone.utc))
YES, NO, MAYBE = Feedback.Verdict.YES, Feedback.Verdict.NO, Feedback.Verdict.MAYBE
LEARNED = "Vous choisissez surtout des postes de régie en CDI."


@override_settings(EMAIL_BACKEND=LOCMEM, CLAUDE_ENGINE="fake", DEV_EMAILS=[], SITE_URL="https://tri.example.fr")
class CycleTests(TestCase):
    def setUp(self):
        fake.reset()
        self.addCleanup(fake.reset)
        fake.set_response(summaries.TASK, summaries_answer)
        fake.set_response(scoring.TASK, scores_answer)
        fake.set_response(learned.TASK, {"sentences": [LEARNED]})
        retry_patch = mock.patch.object(night, "RETRY_DELAY", 0)
        retry_patch.start()
        self.addCleanup(retry_patch.stop)
        self.source = make_source(domains=["E"])
        self.user = make_finished_profile("nora@example.fr").user
        self.client.force_login(self.user)

    def collect(self, prefix, count, when):
        return [make_offer(self.source, external_id=f"{prefix}{index}", title=f"Régisseuse {prefix}{index}",
                           employer=f"Théâtre {prefix}{index}", full_text="Texte fictif.", collected_at=when)
                for index in range(count)]

    def run_command(self, name, at, *args):
        """Runs a command with the clock pinned to `at`; the fake calls it made."""
        fake.calls.clear()
        with mock.patch("django.utils.timezone.now", return_value=at):
            call_command(name, *args, stdout=StringIO())
        return list(fake.calls)

    def scoring_prompt(self, calls):
        return "\n".join(call.user_content for call in calls if call.task == scoring.TASK)

    def send_verdict(self, offer, verdict, at):
        with mock.patch("django.utils.timezone.now", return_value=at):
            response = self.client.post("/tri/avis/", {"offer": offer.pk, "verdict": verdict})
        self.assertEqual(response.status_code, 303)

    def test_mail_swipe_next_night_learned_summary(self):
        first = self.collect("A", 4, SUNDAY_NOON)

        # 1. night 1: four notes, no example yet, nothing learned
        calls = self.run_command("notation", MONDAY_NIGHT)
        self.assertEqual(Score.objects.filter(user=self.user).count(), 4)
        prompt = self.scoring_prompt(calls)
        self.assertNotIn("<exemples>", prompt)
        self.assertNotIn("<appris>", prompt)

        # 2. the Monday morning mail: one mail, the sort page link, no verdict link
        self.run_command("envoi", MONDAY_MORNING, "--date", "2026-10-19")
        self.assertEqual(len(django_mail.outbox), 1)
        body = django_mail.outbox[0].body
        self.assertIn("https://tri.example.fr/tri/", body)
        self.assertNotIn("/tri/avis", body)
        self.assertNotIn("verdict", body)
        self.assertEqual(Delivery.objects.get(user=self.user).offer_count, 4)
        self.assertEqual(set(Score.objects.values_list("sent_on", flat=True)), {date(2026, 10, 19)})

        # 3. verdicts on the sort page: yes, no, maybe, and one cancelled
        yes, no, maybe, cancelled = first
        self.send_verdict(yes, "yes", MONDAY_MORNING)
        self.send_verdict(no, "no", MONDAY_MORNING)
        self.send_verdict(maybe, "maybe", MONDAY_MORNING)
        self.send_verdict(cancelled, "yes", MONDAY_MORNING)
        with mock.patch("django.utils.timezone.now", return_value=MONDAY_MORNING):
            response = self.client.post("/tri/annuler/", {"offer": cancelled.pk})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(Feedback.objects.filter(user=self.user, cancelled=False).count(), 3)
        self.assertTrue(Feedback.objects.get(offer=cancelled).cancelled)

        # 4. night 2: the three judged offers as examples with their verdict, not the cancelled one; the offers
        # already scored are not scored again
        second = self.collect("B", 3, MONDAY_NOON)
        calls = self.run_command("notation", TUESDAY_NIGHT)
        prompt = self.scoring_prompt(calls)
        examples = prompt[prompt.index("<exemples>"):prompt.index("</exemples>")]
        for offer, word in ((yes, "oui"), (no, "non"), (maybe, "peut-être")):
            self.assertRegex(examples, rf'<exemple avis="{word}">\n[^<]*{offer.title}')
        self.assertNotIn(cancelled.title, examples)
        self.assertNotIn("<appris>", prompt)
        offers_part = prompt[prompt.index("<offres>"):]
        for offer in second:
            self.assertIn(offer.title, offers_part)
        for offer in first:
            self.assertNotIn(offer.title, offers_part)
        self.assertEqual(Score.objects.filter(user=self.user).count(), 7)

        # 5. five verdicts in all: the weekly command writes the summary, the next night reads it under <appris>
        self.assertEqual(self.run_command("synthese_apprise", TUESDAY_NIGHT, "--dry-run"), [])
        self.send_verdict(second[0], "yes", TUESDAY_NIGHT)
        self.send_verdict(second[1], "no", TUESDAY_NIGHT)
        calls = self.run_command("synthese_apprise", TUESDAY_NIGHT)
        self.assertEqual([call.task for call in calls], [learned.TASK])
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.learned_summary, LEARNED)
        self.collect("C", 1, TUESDAY_NIGHT)
        calls = self.run_command("notation", WEDNESDAY_NIGHT)
        self.assertIn(f"<appris>\n{LEARNED}\n</appris>", self.scoring_prompt(calls))

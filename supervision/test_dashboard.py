"""The admin dashboard (sub-step 9.3): the figures of supervision/dashboard.py on a given `now`, then the page."""
import datetime as dt
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile, User
from accounts.testing import make_profile
from config.testing import PLAIN_STATIC, assert_french_copy, assert_unique_ids
from jobs.models import Source
from jobs.testing import make_offer, make_source
from matching.models import Feedback
from supervision import dashboard
from supervision.models import TaskRun

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 21, 10, 0, tzinfo=UTC)  # noon in Paris


class DashboardBase(TestCase):
    now = NOW

    def setUp(self):
        self.source = make_source()
        self.offer_count = 0

    def person(self, email="nora@example.fr", **fields):
        """A mailable account with a finished questionnaire."""
        profile = make_profile(email)
        if fields:
            User.objects.filter(pk=profile.user_id).update(**fields)
        return User.objects.get(pk=profile.user_id)

    def swipe(self, user, when, cancelled=False):
        """A feedback row given `when` (created_at is auto_now_add, hence the update)."""
        self.offer_count += 1
        offer = make_offer(self.source, external_id=str(self.offer_count))
        row = Feedback.objects.create(user=user, offer=offer, verdict=Feedback.Verdict.YES, cancelled=cancelled)
        Feedback.objects.filter(pk=row.pk).update(created_at=when)

    def actor_source(self, key, **fields):
        """The row of an actor source: the migrations already seed them, one per key."""
        source, _ = Source.objects.update_or_create(actor_source=key, defaults={"kind": Source.Kind.ACTOR, **fields})
        return source

    def login(self, user, when):
        User.objects.filter(pk=user.pk).update(last_login=when)

    def build(self, now=None):
        return dashboard.build(now or self.now)


class CountTests(DashboardBase):
    def test_staff_and_superusers_are_left_out(self):
        self.person("nora@example.fr")
        self.person("lea@example.fr")
        self.person("equipe@example.fr", is_staff=True)
        User.objects.create_superuser("admin@example.fr", "pw-for-admin")
        figures = self.build()
        self.assertEqual(figures["signed_up"], 2)
        self.assertEqual(figures["subscribers"], 2)
        self.assertEqual(figures["finished"], 2)

    def test_finished_questionnaires_follow_the_completion_stamp(self):
        self.person("nora@example.fr")
        Profile.objects.create(user=User.objects.create_user("lea@example.fr"))
        User.objects.create_user("sans-profil@example.fr")
        figures = self.build()
        self.assertEqual(figures["signed_up"], 3)
        self.assertEqual(figures["finished"], 1)

    def test_subscribers_are_the_mailable_accounts_and_unsubscribed_are_counted_apart(self):
        self.person("nora@example.fr")
        self.person("pause@example.fr", status=User.Status.PAUSED)
        self.person("parti@example.fr", status=User.Status.UNSUBSCRIBED)
        self.person("inactif@example.fr", is_active=False)
        figures = self.build()
        self.assertEqual(figures["signed_up"], 4)
        self.assertEqual(figures["subscribers"], 1)
        self.assertEqual(figures["unsubscribed"], 1)

    def test_empty_database(self):
        figures = self.build()
        self.assertEqual((figures["signed_up"], figures["finished"], figures["subscribers"]), (0, 0, 0))
        self.assertEqual(figures["active"], 0)
        self.assertIsNone(figures["active_percent"])
        self.assertEqual(len(figures["days"]), 14)
        self.assertEqual(figures["failing_sources"], [])
        self.assertEqual(figures["runs"], [])


class ActiveTests(DashboardBase):
    def test_a_swipe_makes_a_person_active(self):
        nora = self.person("nora@example.fr")
        self.person("lea@example.fr")
        self.swipe(nora, NOW - timedelta(days=2))
        self.assertEqual(self.build()["active"], 1)

    def test_a_login_makes_a_person_active(self):
        nora = self.person("nora@example.fr")
        self.person("lea@example.fr")
        self.login(nora, NOW - timedelta(days=3))
        self.assertEqual(self.build()["active"], 1)

    def test_a_swipe_and_a_login_count_the_person_once(self):
        nora = self.person("nora@example.fr")
        self.swipe(nora, NOW - timedelta(days=1))
        self.swipe(nora, NOW - timedelta(days=2))
        self.login(nora, NOW - timedelta(days=1))
        self.assertEqual(self.build()["active"], 1)

    def test_a_cancelled_swipe_does_not_count(self):
        nora = self.person("nora@example.fr")
        self.swipe(nora, NOW - timedelta(days=1), cancelled=True)
        self.assertEqual(self.build()["active"], 0)

    def test_the_seven_day_edge(self):
        inside, outside = self.person("dedans@example.fr"), self.person("dehors@example.fr")
        self.login(inside, NOW - timedelta(days=7))
        self.login(outside, NOW - timedelta(days=7, seconds=1))
        self.assertEqual(self.build()["active"], 1)
        swiper_in, swiper_out = self.person("a@example.fr"), self.person("b@example.fr")
        self.swipe(swiper_in, NOW - timedelta(days=7))
        self.swipe(swiper_out, NOW - timedelta(days=7, seconds=1))
        self.assertEqual(self.build()["active"], 2)

    def test_staff_activity_is_not_counted(self):
        self.person("nora@example.fr")
        staff = self.person("equipe@example.fr", is_staff=True)
        self.login(staff, NOW)
        self.swipe(staff, NOW)
        self.assertEqual(self.build()["active"], 0)

    def test_only_subscribers_can_be_active_so_the_share_stays_under_a_hundred(self):
        nora = self.person("nora@example.fr")
        gone = self.person("parti@example.fr", status=User.Status.UNSUBSCRIBED)
        self.login(nora, NOW)
        self.login(gone, NOW)
        figures = self.build()
        self.assertEqual(figures["active"], 1)
        self.assertEqual(figures["active_percent"], 100)

    def test_the_share_of_subscribers(self):
        people = [self.person(f"p{n}@example.fr") for n in range(5)]
        for person in people[:2]:
            self.login(person, NOW - timedelta(days=1))
        figures = self.build()
        self.assertEqual((figures["active"], figures["subscribers"], figures["active_percent"]), (2, 5, 40))


class DaysTests(DashboardBase):
    def test_fourteen_days_newest_first_with_empty_days(self):
        nora, lea = self.person("nora@example.fr"), self.person("lea@example.fr")
        self.swipe(nora, NOW - timedelta(hours=1))
        self.swipe(nora, NOW - timedelta(hours=2))
        self.swipe(lea, NOW - timedelta(hours=3))
        self.swipe(nora, NOW - timedelta(days=3))
        days = self.build()["days"]
        self.assertEqual(len(days), 14)
        self.assertEqual(days[0]["day"], dt.date(2026, 10, 21))
        self.assertEqual(days[-1]["day"], dt.date(2026, 10, 8))
        self.assertEqual((days[0]["feedback"], days[0]["people"]), (3, 2))
        self.assertEqual((days[1]["feedback"], days[1]["people"]), (0, 0))
        self.assertEqual((days[3]["feedback"], days[3]["people"]), (1, 1))

    def test_cancelled_and_staff_swipes_and_older_ones_are_left_out(self):
        nora = self.person("nora@example.fr")
        staff = self.person("equipe@example.fr", is_staff=True)
        self.swipe(nora, NOW, cancelled=True)
        self.swipe(staff, NOW)
        self.swipe(nora, NOW - timedelta(days=20))
        self.assertEqual(sum(day["feedback"] for day in self.build()["days"]), 0)

    def test_days_are_cut_at_midnight_in_paris_across_the_clock_change(self):
        # Summer time ends on Sunday 25/10/2026 at 03:00: the 25th is 25 hours long, the 26th starts at 23:00 UTC.
        nora = self.person("nora@example.fr")
        now = dt.datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
        self.swipe(nora, dt.datetime(2026, 10, 24, 22, 30, tzinfo=UTC))  # 00:30 on the 25th
        self.swipe(nora, dt.datetime(2026, 10, 25, 22, 30, tzinfo=UTC))  # 23:30 on the 25th
        self.swipe(nora, dt.datetime(2026, 10, 25, 23, 30, tzinfo=UTC))  # 00:30 on the 26th
        by_day = {day["day"]: day["feedback"] for day in self.build(now)["days"]}
        self.assertEqual((by_day[dt.date(2026, 10, 24)], by_day[dt.date(2026, 10, 25)],
                          by_day[dt.date(2026, 10, 26)]), (0, 2, 1))

    def test_the_first_day_starts_at_midnight_in_paris(self):
        nora = self.person("nora@example.fr")
        now = dt.datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
        self.swipe(nora, dt.datetime(2026, 10, 13, 21, 30, tzinfo=UTC))  # 23:30 on the 13th: out of the 14 days
        self.swipe(nora, dt.datetime(2026, 10, 13, 22, 30, tzinfo=UTC))  # 00:30 on the 14th: in
        days = self.build(now)["days"]
        self.assertEqual(days[-1]["day"], dt.date(2026, 10, 14))
        self.assertEqual(days[-1]["feedback"], 1)
        self.assertEqual(sum(day["feedback"] for day in days), 1)

    def test_today_follows_paris_not_utc(self):
        nora = self.person("nora@example.fr")
        now = dt.datetime(2026, 10, 21, 22, 30, tzinfo=UTC)  # 00:30 on the 22nd in Paris
        self.swipe(nora, now)
        days = self.build(now)["days"]
        self.assertEqual((days[0]["day"], days[0]["feedback"]), (dt.date(2026, 10, 22), 1))


class SourceAndRunTests(DashboardBase):
    def test_failing_sources_only(self):
        self.actor_source("wttj", name="Welcome to the Jungle", state=Source.State.FAILING, empty_runs=3,
                          last_run_at=NOW - timedelta(hours=8))
        self.actor_source("hellowork", name="Désactivée", state=Source.State.DISABLED, empty_runs=5)
        self.actor_source("jobup", name="Active")
        rows = self.build()["failing_sources"]
        self.assertEqual([row["name"] for row in rows], ["Welcome to the Jungle"])
        self.assertEqual((rows[0]["actor_source"], rows[0]["empty_runs"]), ("wttj", 3))
        self.assertEqual(rows[0]["last_run_at"], NOW - timedelta(hours=8))
        self.assertEqual(rows[0]["pk"], Source.objects.get(actor_source="wttj").pk)

    def test_the_name_of_a_source_added_by_a_person_is_not_passed_on(self):
        owner = self.person("nora@example.fr")
        make_source(name="Chez nora@example.fr", state=Source.State.FAILING, owner=owner)
        rows = self.build()["failing_sources"]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("nora", rows[0]["name"])

    def make_run(self, day, status="ok", steps=(), slot="nuit", seconds=60):
        started = dt.datetime.combine(day, dt.time(2, 0), tzinfo=UTC)
        return TaskRun.objects.create(slot=slot, day=day, started_at=started, status=status, steps=list(steps),
                                      finished_at=started + timedelta(seconds=seconds))

    def test_fourteen_latest_runs_with_the_names_of_failed_steps(self):
        for number in range(16):
            self.make_run(dt.date(2026, 10, 1) + timedelta(days=number))
        self.make_run(dt.date(2026, 10, 20), status="failed", slot="matin", steps=[
            {"name": "envoi", "status": "failed", "seconds": 2, "error": "boom"},
            {"name": "autre", "status": "ok", "seconds": 1, "error": ""}])
        runs = self.build()["runs"]
        self.assertEqual(len(runs), 14)
        failed = next(run for run in runs if run["status"] == "failed")
        self.assertEqual(failed["failed_steps"], ["envoi"])
        self.assertEqual(failed["slot"], "Matin")
        self.assertEqual(failed["duration"], timedelta(seconds=60))
        self.assertNotIn("boom", repr(runs))

    def test_a_run_still_going_has_no_duration(self):
        run = self.make_run(dt.date(2026, 10, 21), status="running")
        TaskRun.objects.filter(pk=run.pk).update(finished_at=None)
        self.assertIsNone(self.build()["runs"][0]["duration"])


@override_settings(STORAGES=PLAIN_STATIC)
class PageTests(DashboardBase):
    url = "/admin/tableau/"

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_superuser("admin@example.fr", "pw-for-admin")

    def get_as_staff(self):
        self.client.force_login(self.staff)
        return self.client.get(self.url)

    def test_the_url_lives_under_the_admin(self):
        self.assertEqual(reverse("admin_tableau"), self.url)

    def test_anonymous_goes_to_the_admin_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("admin:login")))

    def test_a_signed_in_person_without_staff_status_is_refused(self):
        self.client.force_login(self.person("nora@example.fr"))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("admin:login")))

    def test_staff_sees_the_page_without_cache(self):
        response = self.get_as_staff()
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-cache", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(response, "Tableau de bord")

    def test_only_get_is_allowed(self):
        self.client.force_login(self.staff)
        for method in (self.client.post, self.client.put, self.client.delete):
            with self.subTest(method=method.__name__):
                self.assertEqual(method(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url).headers["Allow"], "GET")

    def test_figures_are_shown(self):
        people = [self.person(f"p{n}@example.fr") for n in range(5)]
        for person in people[:2]:
            self.login(person, timezone.now())
        html = self.get_as_staff().content.decode()
        self.assertIn("2 sur 5, 40 %", html)
        self.assertIn("Critère de la bêta : la moitié au moins", html)
        self.assertIn("Les ouvertures de mail ne sont pas comptées.", html)

    def test_no_failing_source_and_no_run(self):
        html = self.get_as_staff().content.decode()
        self.assertIn("Aucune source en échec", html)

    def test_failing_source_is_listed_with_a_link_to_its_page(self):
        source = self.actor_source("wttj", name="Welcome to the Jungle", state=Source.State.FAILING, empty_runs=3)
        html = self.get_as_staff().content.decode()
        self.assertIn("Welcome to the Jungle", html)
        self.assertIn("wttj", html)
        self.assertIn(reverse("admin:jobs_source_change", args=[source.pk]), html)
        self.assertNotIn("Aucune source en échec", html)

    def test_disabled_source_is_not_listed(self):
        self.actor_source("hellowork", name="Désactivée", state=Source.State.DISABLED)
        html = self.get_as_staff().content.decode()
        self.assertNotIn("Désactivée", html)
        self.assertIn("Aucune source en échec", html)

    def test_nothing_personal_and_no_third_party_text_reaches_the_page(self):
        nora = self.person("nora@example.fr")
        Profile.objects.filter(user=nora).update(seek_text="Je cherche un poste de régisseuse", previous_job="Cheffe")
        self.swipe(nora, timezone.now())
        make_source(name="Chez nora@example.fr", state=Source.State.FAILING, owner=nora)
        run = TaskRun.objects.create(slot="nuit", day=dt.date(2026, 10, 21), started_at=timezone.now(),
                                     finished_at=timezone.now(), status="failed", steps=[
            {"name": "collecte", "status": "failed", "seconds": 2,
             "error": "CommandError: raison-tierce nora@example.fr <script>"}])
        html = self.get_as_staff().content.decode()
        self.assertNotIn("@", html)
        for text in ("nora", "régisseuse", "Cheffe", "raison-tierce", "CommandError", "<script>"):
            self.assertNotIn(text, html)
        self.assertIn("collecte", html)  # the name of the failed step is shown
        self.assertEqual(run.steps[0]["name"], "collecte")

    def test_copy_and_ids(self):
        self.actor_source("wttj", name="Welcome to the Jungle", state=Source.State.FAILING, empty_runs=3,
                          last_run_at=timezone.now())
        TaskRun.objects.create(slot="nuit", day=dt.date(2026, 10, 21), started_at=timezone.now(),
                               finished_at=timezone.now(), status="failed", steps=[{"name": "collecte",
                                                                                    "status": "failed"}])
        html = self.get_as_staff().content.decode()
        content = html.split('id="content"', 1)[1]
        assert_french_copy(self, content)
        assert_unique_ids(self, html)

    def test_the_admin_home_links_to_the_dashboard(self):
        self.client.force_login(self.staff)
        html = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn(f'href="{reverse("admin_tableau")}"', html)
        self.assertIn("Tableau de bord", html)
        self.assertIn("Utilisateurs", html)  # the original home is still there

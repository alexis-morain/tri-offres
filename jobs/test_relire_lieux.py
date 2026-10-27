"""`manage.py relire_lieux` (the design plan, decision 3)."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from jobs.models import JobOffer
from jobs.testing import make_offer, make_source


class RelireLieuxTests(TestCase):
    def setUp(self):
        source = make_source()
        self.rhone = make_offer(source, "1", location="Rhône (69)")
        self.ile_de_france = make_offer(source, "2", location="Île-de-France, France", region="Île-de-France")
        self.known = make_offer(source, "3", location="Lyon (69)", department_code="69", region_code="84")
        self.unknown_code = make_offer(source, "4", location="Nulle part", department_code="999")
        self.nowhere = make_offer(source, "5", location="France")
        self.touquet = make_offer(source, "6", location="Le Touquet, France")
        self.segre = make_offer(source, "7", location="49500 Ailleurs", department_code="49", region_code="52")

    def run_command(self, *args):
        out = StringIO()
        call_command("relire_lieux", *args, stdout=out)
        return out.getvalue()

    def places(self):
        return {offer.pk: (offer.insee, offer.zone_postal_code, offer.department_code, offer.region_code)
                for offer in JobOffer.objects.all()}

    def test_department_and_region_read_again(self):
        out = self.run_command()
        self.assertEqual(self.places(), {
            self.rhone.pk: ("", "", "69", "84"), self.ile_de_france.pk: ("", "", "", "11"),
            self.known.pk: ("69123", "", "69", "84"), self.unknown_code.pk: ("", "", "", ""),
            self.nowhere.pk: ("", "", "", ""), self.touquet.pk: ("62826", "", "62", "32"),
            self.segre.pk: ("", "49500", "49", "52")})
        self.assertEqual(out, "relire_lieux : 7 offres relues, 6 changent (commune : 2, code postal : 1, "
                              "département : 3, région : 3)\n")

    def test_second_run_changes_nothing(self):
        self.run_command()
        self.assertEqual(self.run_command(), "relire_lieux : 7 offres relues, 0 changent (commune : 0, "
                                             "code postal : 0, département : 0, région : 0)\n")

    def test_dry_run_writes_nothing(self):
        before = self.places()
        out = self.run_command("--dry-run")
        self.assertEqual(self.places(), before)
        self.assertEqual(out, "relire_lieux (--dry-run, rien d’écrit) : 7 offres relues, 6 changeraient "
                              "(commune : 2, code postal : 1, département : 3, région : 3)\n")

    def test_other_fields_untouched(self):
        self.run_command()
        self.rhone.refresh_from_db()
        self.assertEqual((self.rhone.location, self.rhone.insee, self.rhone.title),
                         ("Rhône (69)", "", "Chargée de production"))

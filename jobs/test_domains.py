from django.test import SimpleTestCase

from jobs.domains import offer_domains
from jobs.rome_titles import rome_code_for


class OfferDomainsTests(SimpleTestCase):
    def test_source_with_domains_and_no_rome_code(self):
        self.assertEqual(offer_domains(["E", "K", "L"], ""), frozenset({"E", "K", "L"}))

    def test_source_with_domains_and_a_rome_code_adds_its_domain(self):
        self.assertEqual(offer_domains(["M18"], "M1805"), frozenset({"M18"}))
        self.assertEqual(offer_domains(["E", "K", "L"], "D1106"), frozenset({"E", "K", "L", "D"}))

    def test_general_source_with_a_rome_code_reaches_that_domain_only(self):
        self.assertEqual(offer_domains([], "M1805"), frozenset({"M18"}))

    def test_general_source_without_rome_code_reaches_everyone(self):
        self.assertIsNone(offer_domains([], ""))
        self.assertIsNone(offer_domains([], None))

    def test_unknown_rome_code_counts_as_none(self):
        self.assertIsNone(offer_domains([], "Z9999"))
        self.assertEqual(offer_domains(["E"], "Z9999"), frozenset({"E"}))
        self.assertIsNone(offer_domains([], "M180"))
        self.assertEqual(offer_domains(["E"], "M18XX"), frozenset({"E"}))

    def test_accountant_on_a_culture_source(self):
        code = rome_code_for("Comptable")
        self.assertTrue(code.startswith("M12"))
        self.assertEqual(offer_domains(["E", "K", "L"], code), frozenset({"E", "K", "L", "M"}))

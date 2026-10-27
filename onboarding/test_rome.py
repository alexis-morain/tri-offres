from django.test import SimpleTestCase

from onboarding import rome


class ListsTests(SimpleTestCase):
    def test_twenty_two_domains_and_a_hundred_and_ten_professional_domains(self):
        self.assertEqual(len(rome.DOMAINS), 22)
        self.assertEqual(len(rome.PROFESSIONAL_DOMAINS), 110)
        self.assertEqual(len(rome.DOMAIN_LABELS), 22)
        self.assertEqual(len(rome.PROFESSIONAL_DOMAIN_LABELS), 110)

    def test_the_domains_partition_the_professional_domains_exactly(self):
        seen = []
        for code, _ in rome.DOMAINS:
            with self.subTest(domain=code):
                parts = rome.professional_domains(code)
                self.assertTrue(parts)
                seen += [part for part, _ in parts]
        self.assertEqual(len(seen), len(set(seen)))  # disjoint
        self.assertEqual(sorted(seen), sorted(rome.PROFESSIONAL_DOMAIN_LABELS))  # exact

    def test_the_partial_domains(self):
        self.assertEqual([code for code, _ in rome.professional_domains("M")], ["M11", "M12"])
        self.assertNotIn("C15", dict(rome.professional_domains("C")))
        self.assertNotIn("L14", dict(rome.professional_domains("L")))
        self.assertEqual(rome.professional_domains("M18"), [("M18", "Systèmes d’information et de télécommunication")])

    def test_labels_use_french_typography(self):
        for code, label in rome.DOMAINS + rome.PROFESSIONAL_DOMAINS:
            with self.subTest(code=code):
                self.assertNotIn("'", label)
                self.assertNotIn("—", label)


class NormalizeDomainsTests(SimpleTestCase):
    def test_none_is_an_empty_list(self):
        self.assertEqual(rome.normalize_domains(None), [])
        self.assertEqual(rome.normalize_domains([]), [])

    def test_known_codes_in_the_order_of_the_list_without_duplicates(self):
        self.assertEqual(rome.normalize_domains(["L", "E", "K"]), ["E", "K", "L"])
        self.assertEqual(rome.normalize_domains(["M17", "D", "M17"]), ["D", "M17"])
        self.assertEqual(rome.normalize_domains(["M18"]), ["M18"])

    def test_three_at_most(self):
        self.assertEqual(rome.normalize_domains(["E", "K", "L", "E"]), ["E", "K", "L"])
        with self.assertRaises(ValueError):
            rome.normalize_domains(["E", "K", "L", "D"])

    def test_the_screen_caps_at_two(self):
        self.assertEqual(rome.MAX_DOMAINS, 2)

    def test_anything_else_is_refused(self):
        for value in ("E", {"E": True}, ["e"], ["E11"], ["Z"], [1], [["E"]], 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rome.normalize_domains(value)


class DomainForRomeTests(SimpleTestCase):
    def test_examples(self):
        for code, domain in [("M1203", "M"), ("M1805", "M18"), ("C1501", "C15"), ("L1401", "L14"), ("L1301", "L"),
                             ("A1101", "A"), ("M1101", "M")]:
            with self.subTest(code=code):
                self.assertEqual(rome.domain_for_rome(code), domain)

    def test_each_professional_domain_leads_to_exactly_one_domain(self):
        for professional, _ in rome.PROFESSIONAL_DOMAINS:
            with self.subTest(professional=professional):
                domain = rome.domain_for_rome(professional + "01")
                self.assertIn(domain, rome.DOMAIN_LABELS)
                owners = [code for code, _ in rome.DOMAINS if professional in dict(rome.professional_domains(code))]
                self.assertEqual(owners, [domain])

    def test_unknown_or_malformed_codes_give_none(self):
        for code in ["", None, "M", "M12", "M120", "M12034", "Z1101", "A1901", "m1203", " M1203", "M12a3", 1203]:
            with self.subTest(code=code):
                self.assertIsNone(rome.domain_for_rome(code))


class IsRomeCodeTests(SimpleTestCase):
    def test_a_known_professional_domain_and_two_digits(self):
        for code in ["M1203", "M1805", "C1501", "A1101", "N4401"]:
            with self.subTest(code=code):
                self.assertTrue(rome.is_rome_code(code))
        for code in ["", None, "M180", "M18XX", "Z9999", "A1901", "m1203", "M12034", "M1203\n", "M12٣4", 1203]:
            with self.subTest(code=code):
                self.assertFalse(rome.is_rome_code(code))

    def test_the_sql_pattern_is_the_same_rule(self):
        import re
        for code in ["M1203", "M180", "M18XX", "Z9999", "A1901", "M1203\n", "M12٣4"]:
            with self.subTest(code=code):
                self.assertEqual(bool(re.search(rome.ROME_CODE_PATTERN, code)), rome.is_rome_code(code))

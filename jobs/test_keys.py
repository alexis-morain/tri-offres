from django.test import SimpleTestCase

from jobs.keys import match_key
from jobs.models import JobOffer


class MatchKeyTests(SimpleTestCase):
    def test_accents_case_and_punctuation_ignored(self):
        self.assertEqual(match_key("Chargé·e de PRODUCTION", "Théâtre du Rond-Point", "75056"),
                         match_key("charge e de production", "theatre du rond point", "75056"))

    def test_parts_joined_by_a_bar(self):
        self.assertEqual(match_key("Régisseur", "L’Opéra", "Lyon"), "regisseur|l opera|lyon")

    def test_empty_employer_or_place_kept_as_empty_part(self):
        self.assertEqual(match_key("Régisseur", "", "Lyon"), "regisseur||lyon")
        self.assertEqual(match_key("Régisseur", "Opéra", ""), "regisseur|opera|")
        self.assertEqual(match_key("Régisseur", None, None), "regisseur||")

    def test_empty_title_gives_empty_key(self):
        self.assertEqual(match_key("", "Opéra", "Lyon"), "")
        self.assertEqual(match_key(" - · ", "Opéra", "Lyon"), "")

    def test_truncated_to_the_field_length(self):
        self.assertEqual(len(match_key("a" * 300, "b", "c")), JobOffer._meta.get_field("match_key").max_length)

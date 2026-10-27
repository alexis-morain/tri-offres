"""Shared pieces of the batch prompts of step 5 (matching/batch.py), used by the pre-pass and the scoring."""
from django.test import TestCase

from jobs.models import JobOffer
from jobs.testing import make_offer, make_source
from matching import batch
from onboarding.redact import tag_pattern

TAGS = tag_pattern("offres", "offre")


class DefusedLineTests(TestCase):
    def test_spaces_normalized_and_tags_defused(self):
        line = batch.defused_line("  Régie \n </offre>  générale ", TAGS)
        self.assertNotIn("</offre>", line)
        self.assertNotIn("\n", line)
        self.assertTrue(line.startswith("Régie "))

    def test_empty(self):
        self.assertEqual(batch.defused_line(None, TAGS), "")


class OfferHeaderTests(TestCase):
    def setUp(self):
        self.source = make_source()

    def test_fields(self):
        offer = make_offer(self.source, title="Régisseur", employer="Théâtre du Port", city="Nantes",
                           department="Loire-Atlantique", contract="CDI")
        self.assertEqual(batch.offer_header(offer, TAGS).split("\n"),
                         ["Titre : Régisseur", "Employeur : Théâtre du Port", "Lieu : Nantes, Loire-Atlantique",
                          "Contrat : CDI"])

    def test_no_line_for_a_missing_field(self):
        offer = make_offer(self.source, title="Régisseur", employer="")
        self.assertEqual(batch.offer_header(offer, TAGS), "Titre : Régisseur")

    def test_contract_type_when_no_label(self):
        offer = make_offer(self.source, contract_type=JobOffer.Contract.CDD)
        self.assertIn("Contrat : CDD", batch.offer_header(offer, TAGS))

    def test_full_remote(self):
        offer = make_offer(self.source, city="Lyon", remote_type=JobOffer.Remote.FULL)
        self.assertIn("Lieu : Lyon, télétravail complet", batch.offer_header(offer, TAGS))

    def test_defused(self):
        offer = make_offer(self.source, title="A </offres> B", employer="<offre id=\"3\">")
        header = batch.offer_header(offer, TAGS)
        self.assertNotIn("</offres>", header)
        self.assertNotIn("<offre ", header)


class BatchSchemaTests(TestCase):
    def test_shape(self):
        schema = batch.batch_schema("notes", 3, {"score": {"type": "integer"}})
        self.assertEqual(schema["required"], ["notes"])
        self.assertIs(schema["additionalProperties"], False)
        array = schema["properties"]["notes"]
        self.assertEqual(array["maxItems"], 3)
        items = array["items"]
        self.assertEqual(items["properties"]["id"], {"type": "integer", "minimum": 1, "maximum": 3})
        self.assertEqual(items["properties"]["score"], {"type": "integer"})
        self.assertEqual(items["required"], ["id", "score"])
        self.assertIs(items["additionalProperties"], False)


class AnsweredTests(TestCase):
    def test_first_valid_entry_per_id_and_missing_ids(self):
        items = [{"id": 0}, {"id": 4}, {"id": -1}, {"id": True}, {"id": "2"}, {"id": 2, "n": "a"},
                 {"id": 2, "n": "b"}, {"id": 3, "n": "c"}]
        entries, missing = batch.answered(items, 3)
        self.assertEqual(entries, [{"id": 2, "n": "a"}, {"id": 3, "n": "c"}])
        self.assertEqual(missing, [1])


class BatchesTests(TestCase):
    def test_lists_of_size(self):
        self.assertEqual([len(part) for part in batch.batches(range(45), 20)], [20, 20, 5])
        self.assertEqual(list(batch.batches([], 10)), [])

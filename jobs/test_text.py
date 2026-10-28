from django.test import SimpleTestCase

from jobs.text import fold, job_words, strip_marks


class StripMarksTests(SimpleTestCase):
    def test_gender_and_inclusive_marks_after_a_letter(self):
        for raw, expected in [
            ("Chargé(e) de production", "Chargé de production"),
            ("Chargé·e de production", "Chargé de production"),
            ("Chargé.e de production", "Chargé de production"),
            ("Chargé-e de production", "Chargé de production"),
            ("Conseiller(ère) de vente", "Conseiller de vente"),
            ("Vendeur/euse en boulangerie", "Vendeur en boulangerie"),
            ("Infirmier·e diplômé·e d’État", "Infirmier diplômé d’État"),
            ("Technicien(ne) de maintenance", "Technicien de maintenance"),
            ("Directeur·rice", "Directeur"),
            ("Animateur·trice", "Animateur"),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(fold(strip_marks(raw)), fold(expected))

    def test_h_f_marks(self):
        for raw in ["Comptable H/F", "Comptable (H/F)", "Comptable F/H", "Comptable (h/f/x)", "Comptable H / F",
                    "Comptable - HF", "Comptable H/F/X"]:
            with self.subTest(raw=raw):
                self.assertEqual(fold(strip_marks(raw)), "comptable")

    def test_words_that_only_look_like_marks_are_kept(self):
        for raw in ["Chef de projet e-commerce", "Chef de projet e-santé", "Coach e-sport", "Électricien CFO/CFA",
                    "E-commerçant", "Port-en-Bessin", "H", "24h/24", "Chef de projet RH"]:
            with self.subTest(raw=raw):
                self.assertEqual(strip_marks(raw), raw)

    def test_empty(self):
        self.assertEqual(strip_marks(""), "")
        self.assertEqual(strip_marks(None), "")


class JobWordsTests(SimpleTestCase):
    def test_feminine_meets_masculine(self):
        for feminine, masculine in [("Infirmière", "infirmier"), ("Conseillères", "conseiller"),
                                    ("Vendeuses", "vendeur"), ("Directrice", "directeur"), ("Technicienne", "technicien"),
                                    ("Programmatrices", "programmateur")]:
            with self.subTest(feminine=feminine):
                self.assertEqual(job_words(feminine), job_words(masculine))

    def test_marks_give_no_word(self):
        self.assertEqual(job_words("Administrateur.rice (H/F)"), {"administrateur"})
        self.assertEqual(job_words("Régisseur.se principal.e"), {"regisseur", "principal"})

    def test_plural_and_short_words(self):
        self.assertEqual(job_words("Les régisseurs du lieu"), {"regisseur"})

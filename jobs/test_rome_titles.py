import importlib.util
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from jobs import rome_titles

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_rome_titles.py"


def load_script():
    spec = importlib.util.spec_from_file_location("build_rome_titles", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildScriptTests(SimpleTestCase):
    """The rules of scripts/build_rome_titles.py."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.script = load_script()

    def test_label_without_slash_is_kept(self):
        self.assertEqual(self.script.variants("Comptable"), (["Comptable"], None))

    def test_masculine_and_feminine_forms_word_by_word(self):
        for label, forms in [
            ("Conducteur / Conductrice d’engins agricoles",
             ["Conducteur d’engins agricoles", "Conductrice d’engins agricoles"]),
            ("Technicien / Technicienne agricole", ["Technicien agricole", "Technicienne agricole"]),
            ("Débardeur forestier / Débardeuse forestière", ["Débardeur forestier", "Débardeuse forestière"]),
            ("Assistant conducteur / Assistante conductrice de travaux",
             ["Assistant conducteur de travaux", "Assistante conductrice de travaux"]),
            ("Directeur adjoint / Directrice adjointe de la communication",
             ["Directeur adjoint de la communication", "Directrice adjointe de la communication"]),
        ]:
            with self.subTest(label=label):
                self.assertEqual(self.script.variants(label), (forms, None))

    def test_only_the_last_word_on_the_left_has_its_feminine_on_the_right(self):
        for label, forms in [
            ("Responsable régional / Régionale santé publique",
             ["Responsable régional santé publique", "Responsable Régionale santé publique"]),
            ("Analyste Concepteur / Conceptrice informatique",
             ["Analyste Concepteur informatique", "Analyste Conceptrice informatique"]),
            ("Garde Forestier / Forestière", ["Garde Forestier", "Garde Forestière"]),
            ("Responsable Commercial / Commerciale", ["Responsable Commercial", "Responsable Commerciale"]),
        ]:
            with self.subTest(label=label):
                self.assertEqual(self.script.variants(label), (forms, None))

    def test_right_part_repeating_the_end_of_the_left_one(self):
        for label, forms in [("Homme sage-femme / Sage-femme", ["Homme sage-femme", "Sage-femme"])]:
            with self.subTest(label=label):
                self.assertEqual(self.script.variants(label), (forms, None))

    def test_labels_the_rule_cannot_read_are_kept_whole(self):
        for label in ["Chef / Cheffe d’équipe en plastique / Caoutchouc",  # two slashes
                      "Responsable d’atelier en chimie / Pharmacie",  # not a feminine on the right
                      "Pilote d’installation d’emboutissage/découpe",
                      "Cadre Technico-commercial / CadreTechnico-commerciale"]:
            with self.subTest(label=label):
                self.assertEqual(self.script.variants(label), ([label], "unreadable"))

    def test_labels_with_parentheses_kept_whole_and_read_with_and_without_them(self):
        self.assertEqual(self.script.variants("Agent / Agente technique (espaces verts)"), ([
            "Agent / Agente technique (espaces verts)",
            "Agent technique (espaces verts)", "Agente technique (espaces verts)",
            "Agent technique", "Agente technique",
        ], "parentheses"))
        self.assertEqual(self.script.variants("Aide d’élevage cunicole (lapins)"), ([
            "Aide d’élevage cunicole (lapins)", "Aide d’élevage cunicole",
        ], "parentheses"))

    def test_build_keeps_ambiguous_and_vague_forms_as_sentinels(self):
        rows = [
            ("A1101", "Conducteur / Conductrice d’engins agricoles"),
            ("A1101", "Conducteur / Conductrice d’engins agricoles"),  # same label twice: one entry
            ("M1203", "Comptable"),
            ("N4101", "Chauffeur / Chauffeuse"),
            ("N4102", "Chauffeur / Chauffeuse"),  # two fiches: ambiguous
            ("M1302", "Responsable"),  # too vague
            ("M1302", "Agent / Agente technique (espaces verts)"),
        ]
        titles, report = self.script.build(rows)
        self.assertEqual(titles["conducteur d engins agricoles"], "A1101")
        self.assertEqual(titles["conductrice d engins agricoles"], "A1101")
        self.assertEqual(titles["comptable"], "M1203")
        self.assertIsNone(titles["chauffeur"])
        self.assertIsNone(titles["chauffeuse"])
        self.assertIsNone(titles["responsable"])
        self.assertIsNone(titles["agent technique"])  # too vague although the ROME has it once
        self.assertIsNone(titles["chef de projet"])  # vague forms are sentinels even when the ROME lacks them
        self.assertEqual(titles["agent technique espaces verts"], "M1302")
        self.assertEqual(report["ambiguous"], ["chauffeur", "chauffeuse"])
        self.assertIn("responsable", report["vague"])
        self.assertIn("agent technique", report["vague"])
        self.assertEqual(report["parentheses"], ["Agent / Agente technique (espaces verts)"])
        self.assertEqual(report["unreadable"], [])

    def test_build_strips_marks_from_labels_like_titles(self):
        titles, _ = self.script.build([("J1501", "Aide-soignant(e)")])
        self.assertEqual(titles["aide soignant"], "J1501")


# Folded titles -> code, None for a sentinel (ambiguous or too vague).
TABLE = {
    "comptable": "M1203",
    "infirmier en soins generaux": "J1506",
    "infirmier": "J1506",
    "infirmiere": "J1506",
    "charge de production": "L1302",
    "developpeur": "M1805",
    "aide soignant": "J1501",
    "vendeur en boulangerie": "D1106",
    "boulanger": "D1102",
    "patissier": "D1104",
    "e commercant": "D1401",
    "chef de projet": None,
    "chef de projet digital": "M1803",
    "plongeur sous marin": None,
    "electricien": None,
    "agent": None,
}


class RomeCodeForTests(SimpleTestCase):
    def setUp(self):
        rome_titles._table.cache_clear()
        patcher = mock.patch.object(rome_titles, "_load", return_value={"titles": TABLE})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(rome_titles._table.cache_clear)

    def code(self, title):
        return rome_titles.rome_code_for(title)

    def test_exact_title(self):
        self.assertEqual(self.code("Comptable"), "M1203")

    def test_longest_known_run_wins(self):
        self.assertEqual(self.code("Infirmier en soins généraux de nuit"), "J1506")
        self.assertEqual(self.code("Chargé de production"), "L1302")
        self.assertEqual(self.code("Chef de projet digital"), "M1803")

    def test_gender_and_inclusive_marks(self):
        for title in ["Chargé(e) de production", "Chargé·e de production", "Chargé.e de production",
                      "Chargé-e de production", "Chargé de production H/F", "Chargé de production F/H",
                      "Chargé de production (H/F/X)"]:
            with self.subTest(title=title):
                self.assertEqual(self.code(title), "L1302")
        self.assertEqual(self.code("Aide-soignant(e)"), "J1501")
        self.assertEqual(self.code("Vendeur/euse en boulangerie"), "D1106")
        self.assertEqual(self.code("E-commerçant H/F"), "D1401")

    def test_a_sentinel_as_longest_match_gives_none(self):
        self.assertIsNone(self.code("Chef de projet"))
        self.assertIsNone(self.code("Chef de projet e-commerce"))
        self.assertIsNone(self.code("Plongeur sous-marin"))
        self.assertIsNone(self.code("Agent comptable"))  # same length, first in the title: the sentinel

    def test_segments(self):
        self.assertEqual(self.code("Comptable H/F - Lyon"), "M1203")
        self.assertEqual(self.code("Lyon | Chargé de production, CDI"), "L1302")
        self.assertEqual(self.code("Infirmier / Infirmière en soins généraux"), "J1506")
        self.assertEqual(self.code("Boulanger (Pâtissier)"), None)  # two segments, same length, two fiches
        self.assertEqual(self.code("Chef de projet digital – Comptable"), "M1803")  # longest segment wins
        self.assertIsNone(self.code("Lyon - Chef de projet - CDI"))

    def test_one_word_match_only_with_modifiers_around(self):
        self.assertEqual(self.code("Comptable senior H/F"), "M1203")
        self.assertEqual(self.code("Comptable en CDI temps plein"), "M1203")
        self.assertEqual(self.code("Infirmier de nuit"), "J1506")
        self.assertIsNone(self.code("Comptable fournisseurs"))
        self.assertIsNone(self.code("Boulanger pâtissier"))
        self.assertIsNone(self.code("Développeur Python"))

    def test_nothing_known_gives_none(self):
        for title in ["Data Engineer", "", None, " - / "]:
            with self.subTest(title=title):
                self.assertIsNone(self.code(title))


class RealFileTests(SimpleTestCase):
    """jobs/data/rome-titles.json.gz itself, expected codes checked against the ROME."""

    def test_file_header(self):
        data = rome_titles._load()
        self.assertEqual(data["version"], "v61, 15/06/2026")
        self.assertIn("Licence Ouverte", data["source"])
        self.assertGreater(len(data["titles"]), 15000)

    def test_obvious_titles(self):
        for title, code in [
            ("Comptable H/F", "M1203"),
            ("Comptable H/F - Lyon", "M1203"),
            ("Infirmier / Infirmière en soins généraux", "J1506"),
            ("Chargé(e) de production de spectacles", "L1311"),
            ("Chargé·e de production de spectacles", "L1311"),
            ("Data Engineer", "M1811"),  # a ROME title since ROME 4.0
        ]:
            with self.subTest(title=title):
                self.assertEqual(rome_titles.rome_code_for(title), code)

    def test_long_exact_titles(self):
        for title, code in LONG_TITLES:
            with self.subTest(title=title):
                self.assertEqual(rome_titles.rome_code_for(title), code)

    def test_unclear_titles_give_none(self):
        for title in ["Chargé(e) de production", "Chargé·e de production", "Stage marketing", "Responsable", ""]:
            with self.subTest(title=title):
                self.assertIsNone(rome_titles.rome_code_for(title))

    def test_false_codes_found_by_the_review(self):
        for title, code in REVIEW_CASES:
            with self.subTest(title=title):
                self.assertEqual(rome_titles.rome_code_for(title), code)

    def test_loaded_once(self):
        rome_titles.rome_code_for("Comptable")
        rome_titles.rome_code_for("Infirmier")
        self.assertEqual(rome_titles._load.cache_info().misses, 1)


# Checked on the ROME v61: the longest title of the file, and a long exact one.
LONG_TITLES = [
    ("Expert de haut niveau du soutien de la mise en œuvre des armes de dissuasion d’un sous-marin", "K1734"),
    ("Agent technique de laboratoire de métrologie en mécanique et travail des métaux", "H1535"),
]
# False codes found by the review, with what they give now (None, or an appellation of the ROME).
REVIEW_CASES = [
    ("Électricien bâtiment H/F", None),
    ("Électricien CFO/CFA", None),
    ("Designer UX H/F", None),
    ("UX designer", None),
    ("Agent technique polyvalent", None),
    ("Chef de projet", None),
    ("Lyon - Chef de projet - CDI", None),
    ("Chef de projet e-santé", "M1880"),  # « Chef / Cheffe de projet e-santé »
    ("Chef de projet e-commerce", None),
    ("Chef de projet développement économique", None),  # ambiguous in the ROME
    ("Ambassadeur de marque", None),
    ("Responsable commercial", "M1707"),  # « Responsable Commercial / Commerciale »
    ("Sage-femme H/F", "J1104"),  # « Homme sage-femme / Sage-femme »
]


class CodeForAppellationTests(SimpleTestCase):
    """The appellation Claude gives (step 5, decision Q26): an exact lookup, never cut into segments."""

    def test_known_appellation_masculine_and_feminine(self):
        self.assertEqual(rome_titles.code_for_appellation("développeur web"), "M1855")
        self.assertEqual(rome_titles.code_for_appellation("développeuse web"), "M1855")

    def test_accents_capitals_spaces_and_marks(self):
        for text in ["Infirmier en soins généraux", "INFIRMIÈRE EN SOINS GÉNÉRAUX",
                     "  infirmier   en soins generaux ", "Infirmier(ère) en soins généraux"]:
            with self.subTest(text=text):
                self.assertEqual(rome_titles.code_for_appellation(text), "J1506")

    def test_sentinel_unknown_and_empty(self):
        for text in ["Chef de projet", "Licorne de la croissance", "", "   "]:
            with self.subTest(text=text):
                self.assertIsNone(rome_titles.code_for_appellation(text))

    def test_segments_are_not_looked_up(self):
        for text in ["chef de projet - Lyon", "Comptable - Lyon", "Lyon - Comptable", "Comptable senior"]:
            with self.subTest(text=text):
                self.assertIsNone(rome_titles.code_for_appellation(text))
        self.assertEqual(rome_titles.code_for_appellation("Comptable"), "M1203")


class CodeForClaudeTests(SimpleTestCase):
    """The appellation and the fiche code Claude gives (the design plan, section 2)."""

    def test_exact_appellation_wins_over_the_code(self):
        self.assertEqual(rome_titles.code_for_claude("Comptable", "J1506"), "M1203")
        self.assertEqual(rome_titles.code_for_claude("Infirmière en soins généraux", ""), "J1506")

    def test_sentinel_appellation_gives_no_code(self):
        # « chef de projet » is a title of the ROME leading to several fiches: the doubt stays.
        self.assertIsNone(rome_titles.code_for_claude("Chef de projet", "M1814"))

    def test_code_accepted_with_a_word_of_its_fiche(self):
        self.assertEqual(rome_titles.code_for_claude("Infirmier", "J1506"), "J1506")
        self.assertEqual(rome_titles.code_for_claude("Infirmiers de nuit", "J1506"), "J1506")
        self.assertEqual(rome_titles.code_for_claude("Régisseur", "L1512"), "L1512")

    def test_code_refused_without_a_word_of_its_fiche(self):
        for appellation in ["Régisseur", "Chargé de mission", "", "  "]:
            with self.subTest(appellation=appellation):
                self.assertIsNone(rome_titles.code_for_claude(appellation, "J1506"))

    def test_generic_job_words_do_not_vouch_for_a_code(self):
        # « responsable » is a word of M1507 (« responsable paie ») and of hundreds of fiches: it proves nothing.
        for appellation in ["Responsable des zorglubs", "Technicien zorglub", "Agent zorglub"]:
            with self.subTest(appellation=appellation):
                self.assertIsNone(rome_titles.code_for_claude(appellation, "M1507"))
        self.assertEqual(rome_titles.code_for_claude("Gestionnaire de paie", "M1507"), "M1507")
        self.assertEqual(rome_titles.code_for_claude("Chargé de production", "L1311"), "L1311")

    def test_code_refused_when_unknown_malformed_or_empty(self):
        for code in ["Z9999", "J9999", "j1506", " J1506", "J15", "infirmier", "", None, 1506]:
            with self.subTest(code=code):
                self.assertIsNone(rome_titles.code_for_claude("Infirmier", code))

    def test_judgement_of_claudes_code(self):
        """the design plan, section 3: what became of the fiche code, counts only."""
        judge = rome_titles.judge_claude_code
        self.assertEqual(judge("Comptable", "J1506"), (rome_titles.TITLE, "M1203"))
        self.assertEqual(judge("Chef de projet", "M1814"), (rome_titles.TITLE, None))  # a sentinel title
        self.assertEqual(judge("Infirmier", ""), (rome_titles.EMPTY, None))
        self.assertEqual(judge("Infirmier", "  "), (rome_titles.EMPTY, None))
        self.assertEqual(judge("Infirmier", None), (rome_titles.EMPTY, None))
        for code in ["J9999", "infirmier", "J15", 1506]:
            with self.subTest(code=code):
                self.assertEqual(judge("Infirmier", code), (rome_titles.UNKNOWN, None))
        self.assertEqual(judge("Régisseur", "J1506"), (rome_titles.REFUSED, None))
        self.assertEqual(judge("", "J1506"), (rome_titles.REFUSED, None))
        self.assertEqual(judge("Infirmier", "J1506"), (rome_titles.ACCEPTED, "J1506"))

    def test_fiche_words(self):
        words = rome_titles.fiche_words()
        self.assertIn("infirmier", words["J1506"])
        self.assertIn("infirmiere", words["J1506"])
        self.assertNotIn("dans", set().union(*words.values()))
        self.assertTrue(all(code for code in words))


class DisplayLabelsBuildTests(SimpleTestCase):
    """scripts/build_rome_titles.py: the labels the search screen suggests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.script = load_script()

    def labels(self, rows):
        titles, _ = self.script.build(rows)
        return self.script.display_labels(rows, titles)

    def test_each_gender_form_with_its_code_and_a_typographic_apostrophe(self):
        self.assertEqual(self.labels([("E1103", "Chargé / Chargée d'études en marketing")]),
                         {"Chargé d’études en marketing": "E1103", "Chargée d’études en marketing": "E1103"})

    def test_ambiguous_and_vague_forms_are_left_out(self):
        rows = [("A1101", "Opérateur / Opératrice"), ("M1203", "Comptable"), ("C1201", "Comptable")]
        self.assertEqual(self.labels(rows), {})

    def test_the_whole_label_with_a_slash_only_when_no_other_form_reads(self):
        labels = self.labels([("A1203", "Agent / Agente technique (espaces verts)"),
                              ("H2201", "Pilote d’installation d’emboutissage/découpe")])
        self.assertIn("Agente technique (espaces verts)", labels)
        self.assertNotIn("Agent / Agente technique (espaces verts)", labels)
        self.assertIn("Pilote d’installation d’emboutissage/découpe", labels)


class SuggestTests(SimpleTestCase):
    """jobs.rome_titles.suggest() on the real file: what the search screen offers while a person types."""

    def test_starts_of_words_without_accents_nor_capitals(self):
        found = rome_titles.suggest("CHARGEE etu MARK")
        self.assertIn("Chargée d’études en marketing", found)
        self.assertTrue(all("marketing" in label.lower() for label in found), found)

    def test_a_word_inside_another_does_not_match(self):
        self.assertNotIn("Assistant en télémarketing", rome_titles.suggest("marketing"))

    def test_eight_at_most_shortest_first(self):
        found = rome_titles.suggest("marketing")
        self.assertEqual(len(found), rome_titles.SUGGESTIONS)
        self.assertEqual(found, sorted(found, key=len))

    def test_nothing_for_a_letter_or_no_word(self):
        for query in ["", "a", " - ", "%%"]:
            with self.subTest(query=query):
                self.assertEqual(rome_titles.suggest(query), [])

    def test_every_label_gives_its_code_back(self):
        """A suggestion picked is looked up again at validation (code_for_appellation): never a sentinel."""
        for label, code in rome_titles.labels().items():
            self.assertEqual(rome_titles.code_for_appellation(label), code, label)

from django.test import SimpleTestCase

from onboarding import criteria

GROWTH_FULL = {
    "version": 1,
    "offer_kind": "both",
    "location": {"cities": [{"name": "Lyon", "insee": "69123", "department": "69", "region": "84"},
                            {"name": "Saint-Étienne", "insee": "", "department": "", "region": ""}],
                 "distance": "30", "remote": "hybrid", "strict": True},
    "contracts": {"values": ["cdi", "freelance"], "strict": True},
    "pay": {"floor": 450, "unit": "day", "strict": False},
    "hours": {"value": "full", "strict": True},
    "experience": {"value": "3_4", "strict": True},
    "keywords": {"include": ["régie", "son"], "exclude": ["stage"], "strict": True},
}


class NormalizeTests(SimpleTestCase):
    def test_empty_is_version_one_with_nothing_answered(self):
        self.assertEqual(criteria.empty(), {"version": 1})

    def test_nothing_stored_normalizes_to_empty(self):
        self.assertEqual(criteria.normalize({}), criteria.empty())
        self.assertEqual(criteria.normalize(None), criteria.empty())

    def test_full_criteria_round_trip(self):
        self.assertEqual(criteria.normalize(GROWTH_FULL), GROWTH_FULL)

    def test_no_floor_is_null(self):
        data = {"version": 1, "pay": {"floor": None, "unit": "year", "strict": True}}
        self.assertEqual(criteria.normalize(data), data)

    def test_invalid_data_is_refused(self):
        bad = [
            {"version": 2},
            {"version": 1, "unknown": 1},
            {"version": 1, "offer_kind": "internship"},
            {"version": 1, "location": {"cities": [{"name": "A"}] * 4, "distance": "30", "remote": "any",
                                        "strict": True}},
            {"version": 1, "location": {"cities": [], "distance": "100", "remote": "any", "strict": True}},
            {"version": 1, "location": {"cities": [{"name": ""}], "distance": "30", "remote": "any",
                                        "strict": True}},
            {"version": 1, "contracts": {"values": [], "strict": True}},
            {"version": 1, "contracts": {"values": ["cdi"], "strict": False}},
            {"version": 1, "contracts": {"values": ["cdi"]}},
            {"version": 1, "contracts": {"values": ["cdi", "bogus"], "strict": True}},
            {"version": 1, "pay": {"floor": "24000", "unit": "year", "strict": True}},
            {"version": 1, "pay": {"floor": 24000, "unit": "week", "strict": True}},
            {"version": 1, "hours": {"value": "half", "strict": True}},
            {"version": 1, "experience": {"value": "3_4", "strict": "yes"}},
            # Shapes that once raised something else than ValueError.
            {"version": 1, "keywords": {"include": [], "exclude": []}},
            {"version": 1, "keywords": {"include": [], "exclude": [], "strict": False}},
            {"version": 1, "keywords": {"include": "son", "exclude": [], "strict": True}},
            {"version": 1, "keywords": {"include": [["son"]], "exclude": [], "strict": True}},
            {"version": 1, "keywords": {"include": ["x"] * 11, "exclude": [], "strict": True}},
            {"version": 1, "keywords": {"include": ["a" * 41], "exclude": [], "strict": True}},
            {"version": 1, "keywords": {"include": [], "exclude": ["12", " "], "strict": True}},
            {"version": 1, "keywords": {"include": ["Régie", "regie"], "exclude": [], "strict": True}},
            ["location"],
            {"version": 1, "location": "Lyon"},
            {"version": 1, "offer_kind": ["job"]},
            {"version": 1, "contracts": {"values": [["cdi"]], "strict": True}},
            {"version": 1, "hours": {"value": {"full": True}, "strict": True}},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                criteria.normalize(data)

    def test_city_without_codes_gets_empty_strings(self):
        data = {"version": 1, "location": {"cities": [{"name": "Lyon"}], "distance": "city", "remote": "any",
                                           "strict": True}}
        self.assertEqual(criteria.normalize(data)["location"]["cities"],
                         [{"name": "Lyon", "insee": "", "department": "", "region": ""}])


class DefaultContractsTests(SimpleTestCase):
    def test_follow_the_offer_kind(self):
        self.assertEqual(criteria.default_contracts(["M18"], "job"), ["cdi", "cdd"])
        self.assertEqual(criteria.default_contracts(["M18"], "mission"), ["freelance"])
        self.assertEqual(criteria.default_contracts(["D", "M17"], "both"), ["cdi", "cdd", "freelance"])
        self.assertEqual(criteria.default_contracts([], None), ["cdi", "cdd"])

    def test_the_culture_niche_adds_its_contracts_to_jobs(self):
        self.assertEqual(criteria.default_contracts(["E", "J"], "job"),
                         ["cdi", "cdd", "cdd_projet", "fonction_publique"])
        self.assertEqual(criteria.default_contracts(["L"], "both"),
                         ["cdi", "cdd", "cdd_projet", "fonction_publique", "freelance"])
        self.assertEqual(criteria.default_contracts(["K"], "mission"), ["freelance"])


class InterimTests(SimpleTestCase):
    def test_interim_follows_the_project_contracts_and_is_never_prechecked(self):
        codes = list(criteria.CONTRACTS)
        self.assertEqual(codes[codes.index("cdd_projet") + 1], "interim")
        self.assertEqual(criteria.CONTRACTS["interim"], "Intérim")
        for domains in ([], ["E"], ["M18"], ["D", "M17"]):
            for kind in ("job", "mission", "both", None):
                self.assertNotIn("interim", criteria.default_contracts(domains, kind))

    def test_described(self):
        data = {"version": 1, "contracts": {"values": ["cdd", "interim"], "strict": True}}
        self.assertIn(("contracts", "Contrats", "CDD, Intérim"), criteria.describe(criteria.normalize(data)))


class KeywordsTests(SimpleTestCase):
    def test_spaces_are_collapsed_and_the_limits_are_inclusive(self):
        data = {"version": 1, "keywords": {"include": ["  commercial   terrain "] + [f"mot{n}" for n in range(9)],
                                           "exclude": ["a" * 40], "strict": True}}
        keywords = criteria.normalize(data)["keywords"]
        self.assertEqual(keywords["include"][0], "commercial terrain")
        self.assertEqual(len(keywords["include"]), 10)
        self.assertEqual(keywords["exclude"], ["a" * 40])

    def test_empty_lists_are_valid(self):
        data = {"version": 1, "keywords": {"include": [], "exclude": [], "strict": True}}
        self.assertEqual(criteria.normalize(data), data)

    def test_described(self):
        rows = {key: text for key, _, text in criteria.describe(GROWTH_FULL)}
        self.assertEqual(rows["keywords"], "Exigés : régie, son ; écartés : stage")
        only = {"version": 1, "keywords": {"include": [], "exclude": ["stage"], "strict": True}}
        self.assertEqual(dict((k, t) for k, _, t in criteria.describe(only))["keywords"], "Écartés : stage")
        none = {"version": 1, "keywords": {"include": [], "exclude": [], "strict": True}}
        self.assertEqual(dict((k, t) for k, _, t in criteria.describe(none))["keywords"], "Aucun")


class DescribeTests(SimpleTestCase):
    def rows(self, data):
        return {key: (label, text) for key, label, text in criteria.describe(data)}

    def test_every_criterion_described_in_order(self):
        keys = [key for key, _, _ in criteria.describe(GROWTH_FULL)]
        self.assertEqual(keys, ["offer_kind", "location", "contracts", "keywords", "pay", "hours", "experience"])

    def test_the_offer_kind_is_always_there(self):
        self.assertEqual(criteria.describe(criteria.empty())[0], ("offer_kind", "Type d’offres", "Non renseigné"))
        self.assertEqual(self.rows({"version": 1, "offer_kind": "job"})["offer_kind"][1], "Postes salariés")

    def test_texts(self):
        rows = self.rows(GROWTH_FULL)
        self.assertEqual(rows["offer_kind"][1], "Postes salariés et missions")
        self.assertEqual(rows["location"], ("Où", "Lyon et Saint-Étienne, jusqu’à 30 km, télétravail : hybride"))
        self.assertEqual(rows["contracts"], ("Contrats", "CDI, Freelance ou mission"))
        self.assertEqual(rows["pay"], ("Rémunération", "450\u00a0€ par jour au moins (préférence)"))
        self.assertEqual(rows["hours"], ("Temps de travail", "Temps plein"))
        self.assertEqual(rows["experience"], ("Expérience", "3 à 4 ans"))

    def test_skipped_criteria_say_so(self):
        rows = self.rows(criteria.empty())
        self.assertEqual(rows["location"][1], "Non renseigné")
        self.assertEqual(rows["pay"][1], "Non renseigné")

    def test_remote_any_reads_on_its_own(self):
        data = {"version": 1, "location": {"cities": [{"name": "Lyon"}], "distance": "city", "remote": "any",
                                           "strict": True}}
        self.assertEqual(self.rows(data)["location"][1], "Lyon, la ville seule, télétravail : peu importe")

    def test_full_remote_without_city(self):
        data = {"version": 1, "location": {"cities": [], "distance": "30", "remote": "full_remote", "strict": True}}
        self.assertEqual(self.rows(data)["location"][1], "Télétravail complet seulement")

    def test_amounts_use_french_thousands(self):
        data = {"version": 1, "pay": {"floor": 24000, "unit": "year", "strict": True}}
        self.assertEqual(self.rows(data)["pay"][1], "24\u202f000\u00a0€ brut par an au moins")

    def test_no_floor(self):
        data = {"version": 1, "pay": {"floor": None, "unit": "year", "strict": True}}
        self.assertEqual(self.rows(data)["pay"][1], "Pas de plancher")


class PayConsequenceTests(SimpleTestCase):
    def test_strict_floor(self):
        self.assertEqual(
            criteria.pay_consequence({"floor": 24000, "unit": "year", "strict": True}),
            "Les offres sous 24\u202f000\u00a0€ brut par an ne vous seront pas montrées. "
            "Celles qui n’affichent pas de salaire restent visibles.")

    def test_preference(self):
        self.assertEqual(criteria.pay_consequence({"floor": 2000, "unit": "month", "strict": False}),
                         "Les offres sous 2\u202f000\u00a0€ brut par mois seront moins bien notées.")

    def test_no_floor(self):
        self.assertEqual(criteria.pay_consequence({"floor": None, "unit": "year", "strict": True}),
                         "Aucune offre n’est écartée pour son salaire.")

    def test_nothing_saved(self):
        self.assertEqual(criteria.pay_consequence(None), "")


class LocationConsequenceTests(SimpleTestCase):
    def test_distances(self):
        for distance in ("30", "60"):
            self.assertEqual(criteria.location_consequence(distance),
                             "Distance à vol d’oiseau. Les offres dont la commune n’est pas reconnue restent "
                             "visibles.")
        for distance in ("city", "region"):
            self.assertEqual(criteria.location_consequence(distance),
                             "Les offres dont la commune n’est pas reconnue restent visibles.")

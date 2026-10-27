from django.test import SimpleTestCase

from onboarding import choices, preferences, rome

FULL = {"version": 1, "values": ["meaning", "team"], "jobs": ["mediation", {"custom": "Régisseuse son"}],
        "structures": ["association_ess", "culture"], "sectors": ["real_estate", "culture_media"]}


class ChoicesTests(SimpleTestCase):
    def test_lists_of_the_plan(self):
        self.assertEqual(len(choices.VALUES), 8)
        self.assertEqual(len(choices.JOBS["culture"]), 9)
        self.assertEqual(len(choices.JOBS["growth"]), 7)
        self.assertEqual([code for code, _ in choices.STRUCTURES],
                         ["company", "startup", "association_ess", "culture", "public_sector", "health_social",
                          "education"])
        self.assertEqual(len(choices.SECTORS), 14)
        self.assertEqual(set(choices.EXAMPLES), {"seek", "refuse"})
        self.assertEqual([len(pair) for pair in choices.EXAMPLES.values()], [2, 2])

    def test_codes_are_unique_everywhere(self):
        codes = [code for code, _ in choices.VALUES + choices.STRUCTURES + choices.SECTORS
                 + rome.PROFESSIONAL_DOMAINS]
        codes += [code for items in choices.JOBS.values() for code, _ in items]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(len(choices.LABELS), len(codes))
        for code in codes:
            rome_code = code in rome.PROFESSIONAL_DOMAIN_LABELS
            self.assertEqual(code, code.upper() if rome_code else code.lower(), code)

    def test_shown_labels_start_with_a_capital(self):
        shown = choices.shown(choices.JOBS["growth"])
        self.assertIn(("sdr_bdr", "SDR ou BDR"), shown)
        self.assertIn(("revops", "RevOps"), shown)
        for code, label in (choices.shown(choices.VALUES) + choices.shown(choices.STRUCTURES)
                            + choices.shown(choices.SECTORS)):
            self.assertEqual(label[0], label[0].upper(), code)

    def test_no_straight_apostrophe_nor_em_dash(self):
        texts = [label for _, label in choices.VALUES + choices.STRUCTURES + choices.SECTORS]
        texts += [label for items in choices.JOBS.values() for _, label in items]
        texts += [niche["label"] for niche in choices.NICHES.values()]
        texts += [text for pair in choices.EXAMPLES.values() for text in pair]
        for text in texts:
            self.assertNotIn("'", text)
            self.assertNotIn("—", text)


class JobGroupsTests(SimpleTestCase):
    def test_one_group_per_domain_with_its_professional_domains(self):
        self.assertEqual(choices.job_groups(["M18"]), [
            ("Informatique / Télécommunication", [("M18", "Systèmes d’information et de télécommunication")])])
        groups = choices.job_groups(["J", "N"])
        self.assertEqual([label for label, _ in groups], ["Santé", "Transport / Logistique"])
        self.assertEqual([code for code, _ in groups[0][1]], ["J11", "J12", "J13", "J14", "J15"])

    def test_a_niche_list_follows_when_one_of_its_domains_is_chosen(self):
        groups = choices.job_groups(["E", "J"])
        self.assertEqual([label for label, _ in groups],
                         ["Communication / Multimédia", "Santé", "Plus précis, pour la culture et l’ESS"])
        self.assertIn(("mediation", "Médiation"), groups[-1][1])
        self.assertEqual([label for label, _ in choices.job_groups(["M17"])],
                         ["Marketing / Stratégie commerciale", "Plus précis, pour le growth et la vente"])
        both = [label for label, _ in choices.job_groups(["D", "K"])]
        self.assertEqual(both[2:], ["Plus précis, pour la culture et l’ESS",
                                    "Plus précis, pour le growth et la vente"])

    def test_no_domain_no_group(self):
        self.assertEqual(choices.job_groups([]), [])

    def test_job_codes(self):
        self.assertEqual(choices.job_codes(["M18"]), {"M18"})
        codes = choices.job_codes(["L"])
        self.assertIn("L11", codes)
        self.assertIn("mediation", codes)
        self.assertNotIn("L14", codes)
        self.assertNotIn("sdr_bdr", codes)


class NormalizeTests(SimpleTestCase):
    def test_empty_and_full(self):
        self.assertEqual(preferences.normalize({}), {"version": 1})
        self.assertEqual(preferences.normalize(None), {"version": 1})
        self.assertEqual(preferences.empty(), {"version": 1})
        self.assertEqual(preferences.normalize(FULL), FULL)

    def test_sectors_like_structures_up_to_three_and_may_be_empty(self):
        self.assertEqual(preferences.normalize({"sectors": []})["sectors"], [])
        codes = [code for code, _ in choices.SECTORS]
        self.assertEqual(preferences.normalize({"sectors": codes[:3]})["sectors"], codes[:3])
        for data in ({"sectors": codes[:4]}, {"sectors": ["culture"]}, {"sectors": [codes[0], codes[0]]},
                     {"sectors": "industry"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                preferences.normalize(data)

    def test_structures_may_be_empty_values_and_jobs_may_not(self):
        self.assertEqual(preferences.normalize({"structures": []})["structures"], [])
        for key in ("values", "jobs"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                preferences.normalize({key: []})

    def test_caps(self):
        values = [code for code, _ in choices.VALUES]
        culture_jobs = [code for code, _ in choices.JOBS["culture"]]
        structures = [code for code, _ in choices.STRUCTURES]
        postes = [{"custom": poste} for poste in ("Libraire", "Médiatrice culturelle", "Régisseuse son", "Comptable")]
        for key, items in (("values", values[:4]), ("jobs", postes[:3] + culture_jobs[:6]), ("jobs", postes),
                           ("structures", structures[:6])):
            with self.subTest(key=key, items=items), self.assertRaises(ValueError):
                preferences.normalize({key: items})
        preferences.normalize({"values": values[:3], "jobs": postes[:3] + culture_jobs[:5],
                               "structures": structures[:5]})

    def test_unknown_codes_keys_and_versions_are_refused(self):
        for data in ({"values": ["bogus"]}, {"jobs": ["bogus"]}, {"structures": ["mediation"]}, {"other": 1},
                     {"version": 2}, {"values": "meaning"}, {"jobs": [{"name": "x"}]}, {"jobs": [3]},
                     {"values": ["meaning", "meaning"]}, ["values"], {"values": [["meaning"]]},
                     {"values": [{"custom": "Sens"}]}, {"jobs": [{"custom": ["x"]}]}, {"structures": "startup"},
                     {"structures": ["publisher"]}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                preferences.normalize(data)

    def test_custom_job_rules(self):
        self.assertEqual(preferences.normalize({"jobs": [{"custom": "  Régisseuse \n son "}]})["jobs"],
                         [{"custom": "Régisseuse son"}])
        for text in ("", "   ", "123", "x" * 151):
            with self.subTest(text=text), self.assertRaises(ValueError):
                preferences.normalize({"jobs": [{"custom": text}]})
        preferences.normalize({"jobs": [{"custom": "x" * 150}]})

    def test_custom_job_helper(self):
        self.assertEqual(preferences.clean_custom_job("  chargée   de  mécénat "), "chargée de mécénat")
        self.assertIsNone(preferences.clean_custom_job("42"))


class IsEmptyTests(SimpleTestCase):
    def test_empty_values(self):
        for value in (None, {}, preferences.empty(), {"version": 1, "values": [], "jobs": []}, "abîmé", []):
            with self.subTest(value=value):
                self.assertTrue(preferences.is_empty(value))

    def test_one_choice_is_enough(self):
        for value in ({"version": 1, "values": ["team"]}, {"jobs": [{"custom": "Régisseuse son"}]},
                      {"version": 1, "structures": ["culture"]}):
            with self.subTest(value=value):
                self.assertFalse(preferences.is_empty(value))


class WithPostesTests(SimpleTestCase):
    DATA = {"version": 1, "values": ["meaning"], "jobs": [{"custom": "Libraire"}, "E11", "mediation"],
            "structures": ["culture"]}

    def test_the_postes_first_then_the_chips_still_offered(self):
        self.assertEqual(preferences.with_postes(self.DATA, ["Libraire"], ["E", "J"]), self.DATA)
        self.assertEqual(preferences.with_postes(self.DATA, ["Bibliothécaire", "Libraire"], ["K"]),
                         {**self.DATA, "jobs": [{"custom": "Bibliothécaire"}, {"custom": "Libraire"}, "mediation"]})
        self.assertEqual(preferences.with_postes(self.DATA, ["Comptable"], ["M"])["jobs"], [{"custom": "Comptable"}])

    def test_values_and_structures_hold_for_any_domain(self):
        data = {"version": 1, "structures": []}
        self.assertEqual(preferences.with_postes(data, ["Comptable"], ["M"]),
                         {"version": 1, "jobs": [{"custom": "Comptable"}], "structures": []})


class PostesTests(SimpleTestCase):
    def test_the_typed_jobs_in_their_order(self):
        self.assertEqual(preferences.postes({"jobs": [{"custom": "Libraire"}, "E11", {"custom": "Comptable"}]}),
                         ["Libraire", "Comptable"])
        for data in ({"version": 1}, {"jobs": ["E11"]}, None, "abîmé", {"jobs": "E11"}):
            with self.subTest(data=data):
                self.assertEqual(preferences.postes(data), [])

    def test_their_row(self):
        self.assertEqual(preferences.describe_postes({"jobs": [{"custom": "libraire"}, {"custom": "Comptable"}]}),
                         "Libraire\u202f; Comptable")
        self.assertEqual(preferences.describe_postes({"jobs": ["E11"]}), "Non renseigné")


class JobItemTests(SimpleTestCase):
    def test_code_or_typed(self):
        self.assertFalse(preferences.is_custom("mediation"))
        self.assertTrue(preferences.is_custom({"custom": "Libraire"}))
        self.assertEqual(preferences.job_label("mediation"), "médiation")
        self.assertEqual(preferences.job_label({"custom": "Libraire"}), "Libraire")


class DescribeTests(SimpleTestCase):
    def test_rows_in_screen_order(self):
        self.assertEqual(preferences.describe(FULL), [
            ("values", "Ce qui compte", "Le sens du travail, l’équipe"),
            ("jobs", "Métiers", "Médiation"),  # the chips only: the postes typed have their own row
            ("structures", "Structures", "Association et ESS, culture"),
            ("sectors", "Secteurs", "Immobilier, culture et médias"),
        ])

    def test_not_answered_and_none_chosen(self):
        rows = preferences.describe({"version": 1, "structures": []})
        self.assertEqual(rows[0][2], "Non renseigné")
        self.assertEqual(rows[1][2], "Aucune en particulier")
        self.assertEqual(rows[2], ("sectors", "Secteurs", "Non renseigné"))
        self.assertEqual(preferences.describe({"version": 1, "sectors": []})[2][2], "Aucune en particulier")

    def test_no_jobs_row_without_chips(self):
        rows = preferences.describe({"version": 1, "jobs": [{"custom": "Libraire"}]})
        self.assertEqual([key for key, _, _ in rows], ["values", "structures", "sectors"])

    def test_growth_labels_keep_their_capitals(self):
        rows = preferences.describe({"jobs": ["growth_marketing", "sdr_bdr", "revops"]})
        self.assertEqual(rows[1][2], "Growth marketing\u202f; SDR ou BDR\u202f; RevOps")

    def test_jobs_are_capitalised_and_split_by_semicolons(self):
        rows = preferences.describe({"jobs": [{"custom": "développeuse web"}, "E11", "mediation", "G13"]})
        self.assertEqual(rows[1][2], "Édition et communication\u202f; Médiation\u202f; Conception, commercialisation "
                                     "et vente de produits touristiques")

    def test_words_rows(self):
        self.assertEqual(preferences.describe_words("", "Pas de vente."), [
            ("seek_text", "Ce que vous cherchez", "Pas encore rempli."),
            ("refuse_text", "Ce que vous refusez", "Pas de vente."),
        ])
        long = "mot " * 100
        text = preferences.describe_words(long, "")[0][2]
        self.assertLessEqual(len(text), 120)
        self.assertTrue(text.endswith("…"))

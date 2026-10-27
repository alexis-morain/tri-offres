import importlib.util
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from jobs import geo

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_communes.py"

# [insee, name, department, [postal codes], latitude, longitude, population, region]
COMMUNES = [
    ["75056", "Paris", "75", ["75001", "75008", "75116"], 48.8589, 2.347, 2103778, "11"],
    ["69123", "Lyon", "69", ["69001", "69003"], 45.758, 4.8351, 519127, "84"],
    ["13055", "Marseille", "13", ["13001", "13008"], 43.2803, 5.3806, 886040, "93"],
    ["93066", "Saint-Denis", "93", ["93200", "93210"], 48.9378, 2.3657, 149077, "11"],
    ["97411", "Saint-Denis", "974", ["97400"], -20.9434, 55.4444, 155634, "04"],
    ["42218", "Saint-Étienne", "42", ["42000", "42100"], 45.4241, 4.3665, 173136, "84"],
    ["01001", "L’Abergement-Clémenciat", "01", ["01400"], 46.1534, 4.926, 832, "84"],
    ["01400", "Châtillon-sur-Chalaronne", "01", ["01400"], 46.1176, 4.9578, 5000, "84"],
    ["01999", "Nulle Part", "01", ["01998"], None, None, 0, "84"],
    ["01998", "Abergement-le-Grand", "01", ["01997"], None, None, 0, "84"],
    ["01997", "Abergement-le-Petit", "01", ["01996"], None, None, 0, "84"],
    ["97701", "Saint-Barthélemy", "977", ["97133"], 17.9, -62.8, 10000, None],
]


class FakeTableTests(SimpleTestCase):
    def setUp(self):
        patcher = mock.patch.object(geo, "_communes", return_value=COMMUNES)
        patcher.start()
        self.addCleanup(patcher.stop)
        for cached in (geo._index, geo.communes_within, geo.departments_within, geo._area_names):
            cached.cache_clear()
            self.addCleanup(cached.cache_clear)

    def test_postal_code_with_one_commune(self):
        self.assertEqual(geo.commune_for("Paris", "75008"), "75056")
        self.assertEqual(geo.commune_for("", "13008"), "13055")
        self.assertEqual(geo.commune_for("Lyon 3e", "69003"), "69123")

    def test_postal_code_shared_decided_by_the_name(self):
        self.assertEqual(geo.commune_for("Chatillon sur Chalaronne", "01400"), "01400")
        self.assertEqual(geo.commune_for("L'Abergement-Clémenciat", "01400"), "01001")
        self.assertIsNone(geo.commune_for("Ailleurs", "01400"))
        self.assertIsNone(geo.commune_for("", "01400"))

    def test_postal_code_contradicting_the_city_gives_none(self):
        self.assertIsNone(geo.commune_for("Ailleurs", "69003"))
        self.assertIsNone(geo.commune_for("Lyon", "75008"))
        self.assertIsNone(geo.commune_for("Saint-Denis", "13008"))

    def test_unknown_postal_code_falls_back_on_its_department_then_the_city_alone(self):
        self.assertEqual(geo.commune_for("Saint-Denis", "93999"), "93066")  # generic or Cedex code
        self.assertEqual(geo.commune_for("Saint-Denis", "97499"), "97411")  # overseas: three digits
        self.assertIsNone(geo.commune_for("Saint-Denis", "75999"))  # not in the department, not unique
        self.assertEqual(geo.commune_for("Paris", "99999"), "75056")  # unique in France
        self.assertEqual(geo.commune_for("Lyon", "6900"), "69123")  # not a postal code: the city alone
        self.assertIsNone(geo.commune_for("", "93999"))

    def test_blank_postal_code_and_department_are_absent(self):
        self.assertEqual(geo.commune_for("Lyon", "   "), "69123")
        self.assertEqual(geo.commune_for(" Saint-Denis ", " ", " 93 "), "93066")

    def test_department_without_postal_code(self):
        self.assertEqual(geo.commune_for("Saint-Denis", department_code="93"), "93066")
        self.assertEqual(geo.commune_for("Saint-Denis", department_code="974"), "97411")
        self.assertIsNone(geo.commune_for("Saint-Denis", department_code="75"))

    def test_city_alone_only_when_unique_in_france(self):
        self.assertEqual(geo.commune_for("Lyon"), "69123")
        self.assertIsNone(geo.commune_for("Saint-Denis"))
        self.assertIsNone(geo.commune_for("Atlantis"))
        self.assertIsNone(geo.commune_for(""))
        self.assertIsNone(geo.commune_for(None))

    def test_saint_and_st_are_the_same(self):
        self.assertEqual(geo.commune_for("St Etienne"), "42218")
        self.assertEqual(geo.commune_for("ST-ÉTIENNE", "42000"), "42218")
        self.assertEqual(geo.commune_for("St Denis", department_code="93"), "93066")

    def test_arrondissements_are_the_commune(self):
        for city in ["Paris 8e", "Paris 08", "Paris 16ème", "Paris 1er Arrondissement", "Paris (75)"]:
            with self.subTest(city=city):
                self.assertEqual(geo.commune_for(city), "75056")
        self.assertEqual(geo.commune_for("Lyon 3ème"), "69123")
        self.assertEqual(geo.commune_for("Marseille 13e Arrondissement"), "13055")

    def test_communes_named_alike_in_one_department_give_the_department(self):
        self.assertEqual(tuple(geo.place_for("Abergement, France")), ("", "", "01", "84"))
        self.assertEqual(tuple(geo.place_for("Châtillon, France")), ("01400", "", "01", "84"))

    def test_postal_codes_of(self):
        self.assertEqual(geo.postal_codes_of(["01001", "01400", "00000"]), frozenset({"01400"}))
        self.assertEqual(geo.postal_codes_of(["69123"]), frozenset({"69001", "69003"}))

    def test_point_and_distance(self):
        self.assertEqual(geo.point("75056"), (48.8589, 2.347))
        self.assertIsNone(geo.point("01999"))
        self.assertIsNone(geo.point("00000"))
        self.assertIsNone(geo.point(None))
        self.assertEqual(geo.distance_km("75056", "75056"), 0)
        self.assertIsNone(geo.distance_km("75056", "01999"))
        self.assertIsNone(geo.distance_km("75056", ""))

    def test_regions(self):
        self.assertEqual(geo.region_for("69123"), "84")
        self.assertIsNone(geo.region_for("00000"))
        self.assertEqual(geo.region_for_department("974"), "04")
        self.assertEqual(geo.region_for_department("01"), "84")
        self.assertIsNone(geo.region_for_department("00"))
        self.assertIsNone(geo.region_for_department(""))
        self.assertEqual(geo.region_for_department(" 01 "), "84")
        self.assertIsNone(geo.region_for_department("977"))  # overseas collectivity: no region
        self.assertIsNone(geo.region_for("97701"))

    def test_departments_of_regions(self):
        self.assertEqual(geo.departments_of_regions({"84"}), frozenset({"69", "42", "01"}))
        self.assertEqual(geo.departments_of_regions({"11", "04"}), frozenset({"75", "93", "974"}))
        self.assertEqual(geo.departments_of_regions(set()), frozenset())

    def test_communes_within(self):
        self.assertEqual(geo.communes_within("01001", 10), frozenset({"01001", "01400"}))
        self.assertEqual(geo.communes_within("75056", 15), frozenset({"75056", "93066"}))
        self.assertEqual(geo.communes_within("01999", 1000), frozenset())
        self.assertEqual(geo.communes_within("00000", 1000), frozenset())


class AreaTests(SimpleTestCase):
    """The department, else the region, of an offer without a commune (the design plan,
    decision 2), on the real file."""

    def area(self, location, postal_code="", department_code="", region=""):
        return tuple(geo.area_for(location, postal_code, department_code, region))

    def test_department_code_in_the_text(self):
        self.assertEqual(self.area("Rhône (69)"), ("69", "84"))
        self.assertEqual(self.area("Guadeloupe (971)"), ("971", "01"))
        self.assertEqual(self.area("Saint-Ouen (93)"), ("93", "11"))
        self.assertEqual(self.area("Haute Garonne (31), France"), ("31", "76"))
        self.assertEqual(self.area("Bastia (2B)"), ("2B", "94"))
        self.assertEqual(self.area("Sarthe - 72"), ("72", "52"))
        self.assertEqual(self.area("Val d'Oronaye - 04"), ("04", "93"))

    def test_postal_code_in_the_text_when_known(self):
        self.assertEqual(self.area("Vienne 38200, FR"), ("38", "84"))
        self.assertEqual(self.area("Recherche de la ville en cours... (13230)"), ("13", "93"))
        self.assertEqual(self.area("Sevilla (41092)"), ("", ""))  # not a French postal code
        self.assertEqual(self.area("Monaco 98000, France"), ("", ""))

    def test_department_name(self):
        self.assertEqual(self.area("Lot, France"), ("46", "76"))
        self.assertEqual(self.area("Deux-Sèvres, France"), ("79", "75"))
        self.assertEqual(self.area("Meurthe et Moselle"), ("54", "44"))
        self.assertEqual(self.area("Seine St Denis"), ("93", "11"))
        self.assertEqual(self.area("Corse du Sud"), ("2A", "94"))
        self.assertEqual(self.area("Réunion"), ("974", "04"))
        self.assertEqual(self.area("La Réunion"), ("", "04"))  # also a commune of Lot-et-Garonne: the region
        self.assertEqual(self.area("Paris, France"), ("75", "11"))

    def test_department_name_that_is_a_commune_elsewhere_gives_nothing(self):
        for name in ("Vienne", "Loire, France", "Landes", "Aube"):
            with self.subTest(name=name):
                self.assertEqual(self.area(name), ("", ""))
        self.assertEqual(self.area("Loire (42)"), ("42", "84"))

    def test_region_name(self):
        self.assertEqual(self.area("Île-de-France, France"), ("", "11"))
        self.assertEqual(self.area("Ile de France"), ("", "11"))
        self.assertEqual(self.area("Auvergne-Rhône-Alpes, France"), ("", "84"))
        self.assertEqual(self.area("Évry, Île-de-France"), ("", "11"))
        self.assertEqual(self.area("Provence-Alpes-Côte d’Azur"), ("", "93"))
        self.assertEqual(self.area("PACA"), ("", "93"))
        self.assertEqual(self.area("France; Île-de-France"), ("", "11"))  # a semicolon separates parts too
        self.assertEqual(self.area("France; Rhône (69)"), ("69", "84"))
        self.assertEqual(self.area("Corse"), ("", "94"))

    def test_fields_of_the_actor_first(self):
        self.assertEqual(self.area("Paris (75)", department_code="69"), ("69", "84"))
        self.assertEqual(self.area("", postal_code="38200"), ("38", "84"))
        self.assertEqual(self.area("", postal_code="92042"), ("92", "11"))  # Cedex: its department
        self.assertEqual(self.area("", postal_code="20999"), ("", ""))  # 2A or 2B
        self.assertEqual(self.area("", region="Île-de-France"), ("", "11"))
        self.assertEqual(self.area("Rhône (69)", region="Bretagne"), ("69", "84"))  # the department decides
        self.assertEqual(self.area("Rhône (69)", department_code="AB"), ("69", "84"))  # unknown code: ignored

    def test_overseas_collectivity_has_a_department_but_no_region(self):
        self.assertEqual(self.area("Saint-Barthélemy (977)"), ("977", ""))

    def test_nothing_readable(self):
        for location in ("", "France", "Télétravail", "N/C", "Ventiseri (20)", "Buenos Aires (C1054AAV)",
                         "Saint-Denis, France", "Canberra", ", France", "75000 (00)"):
            with self.subTest(location=location):
                self.assertEqual(self.area(location), ("", ""))
        self.assertEqual(self.area(None), ("", ""))

    def test_every_department_and_region_has_a_name(self):
        departments = {code for code, region in geo._index().region_of_department.items() if region}
        self.assertEqual(set(geo.DEPARTMENT_NAMES), departments)
        self.assertEqual(set(geo.REGION_NAMES), set(geo._index().region_of_department.values()) - {None})
        for code, name in geo.DEPARTMENT_NAMES.items():
            self.assertEqual(geo.region_for_department(code), self.area(f"{name} ({code})")[1])

    def test_departments_within(self):
        self.assertEqual(geo.departments_within("75056", 0), frozenset({"75"}))
        self.assertTrue({"75", "92", "93", "94"} <= geo.departments_within("75056", 15))
        self.assertNotIn("69", geo.departments_within("75056", 60))
        self.assertEqual(geo.departments_within("00000", 60), frozenset())
        self.assertEqual(geo.departments_of(["75056", "69123", "00000"]), frozenset({"75", "69"}))


class PlaceTests(SimpleTestCase):
    """The commune named in the text of the place, the postal code of the zone, overseas codes
    (the design plan, decisions 1 to 3), on the real file."""

    def place(self, location, city="", postal_code="", department_code="", region=""):
        return tuple(geo.place_for(location, city, postal_code, department_code, region))

    def test_cases_of_the_third_trial(self):
        self.assertEqual(self.place("49500 SEGRE"), ("49331", "", "49", "52"))  # Segré-en-Anjou Bleu
        self.assertEqual(self.place("97866 SAINT-PAUL"), ("97415", "", "974", "04"))  # the only one overseas
        self.assertEqual(self.place("Le Touquet, France"), ("62826", "", "62", "32"))

    def test_commune_named_in_the_text(self):
        self.assertEqual(self.place("Lezennes, France"), ("59346", "", "59", "32"))
        self.assertEqual(self.place("Avranches, France"), ("50025", "", "50", "28"))
        self.assertEqual(self.place("Avranches (50)"), ("50025", "", "50", "28"))
        self.assertEqual(self.place("Vienne, France", postal_code="38200"), ("38544", "", "38", "84"))
        self.assertEqual(self.place("Vienne 38200, FR", postal_code="38200"), ("38544", "", "38", "84"))
        self.assertEqual(self.place("Quimperlé 29394, France"), ("29233", "", "29", "53"))  # Cedex
        self.assertEqual(self.place("53204 CHATEAU GONTIER"), ("53062", "", "53", "52"))  # merged in 2019
        self.assertEqual(self.place("53200 Château-Gontier Cedex"), ("53062", "", "53", "52"))
        self.assertEqual(self.place("24410 SAINT-AULAYE"), ("24376", "", "24", "75"))
        self.assertEqual(self.place("Cugand (85) - Pays de la Loire"), ("85076", "", "85", "52"))  # -la-Bernardière
        self.assertEqual(self.place("Paris, France"), ("75056", "", "75", "11"))

    def test_the_area_named_beside_bounds_the_commune(self):
        # Not Évry of Yonne; in Île-de-France, Évry-Courcouronnes (91) and Évry-Grégy-sur-Yerre (77): the region.
        self.assertEqual(self.place("Évry, Île-de-France"), ("", "", "", "11"))
        self.assertEqual(self.place("Ancenis, Pays de la Loire"), ("44003", "", "44", "52"))
        self.assertEqual(self.place("Évry, Bretagne"), ("", "", "", "53"))

    def test_the_commune_of_the_actor_comes_first(self):
        self.assertEqual(self.place("Le Touquet, France", city="Lyon"), ("69123", "", "69", "84"))
        self.assertEqual(self.place("Le Touquet, France", "Lyon", "69003"), ("69123", "", "69", "84"))

    def test_ambiguous_names_give_no_commune(self):
        self.assertEqual(self.place("Saint-Denis, France"), ("", "", "", ""))
        self.assertEqual(self.place("Saint-Denis, France", city="Saint-Denis"), ("", "", "", ""))
        self.assertEqual(self.place("Vienne, France"), ("", "", "", ""))  # also a department
        self.assertEqual(self.place("Saint, France"), ("", "", "", ""))
        self.assertEqual(self.place("Le, France"), ("", "", "", ""))
        self.assertEqual(self.place("Valence"), ("", "", "", ""))  # Charente, Drôme, Tarn-et-Garonne
        self.assertEqual(self.place("Saint-Paul"), ("", "", "", ""))
        self.assertEqual(self.place("Gironde (33)"), ("", "", "33", "75"))  # not Gironde-sur-Dropt
        self.assertEqual(self.place("Gironde, France"), ("", "", "33", "75"))
        self.assertEqual(self.place("Loire, France"), ("", "", "", ""))

    def test_a_place_abroad_names_no_commune(self):
        for location in ("Lausanne, Suisse", "Allemagne", "Paris, Texas, USA", "Orléans, Ontario", "Monaco",
                         "Bruxelles, Belgique", "Montréal, Québec, Canada"):
            with self.subTest(location=location):
                self.assertEqual(self.place(location), ("", "", "", ""))

    def test_areas_are_not_communes(self):
        self.assertEqual(self.place("Île-de-France, France"), ("", "", "", "11"))
        self.assertEqual(self.place("Rhône (69)"), ("", "", "69", "84"))
        self.assertEqual(self.place("Guadeloupe (971)"), ("", "", "971", "01"))
        self.assertEqual(self.place("Haute Garonne (31), France"), ("", "", "31", "76"))
        self.assertEqual(self.place("France"), ("", "", "", ""))
        for location in ("", "Télétravail", "Canberra", ", France", "Buenos Aires (C1054AAV)", "Monaco 98000"):
            with self.subTest(location=location):
                self.assertEqual(self.place(location), ("", "", "", ""))
        self.assertEqual(self.place(None), ("", "", "", ""))

    def test_known_postal_code_without_commune_is_the_zone(self):
        self.assertEqual(self.place("38480 Pont-de-Beauvoisin"), ("", "38480", "38", "84"))
        self.assertEqual(self.place("49500 Ailleurs"), ("", "49500", "49", "52"))
        self.assertEqual(self.place("", postal_code="49500"), ("", "49500", "49", "52"))
        self.assertEqual(self.place("", city="Ailleurs", postal_code="38200"), ("", "38200", "38", "84"))
        self.assertEqual(self.place("Valence (26)", city="Valence", postal_code="26500"),
                         ("", "26500", "26", "84"))
        self.assertEqual(self.place("", city="Lyon", postal_code="75011", department_code="69"),
                         ("", "", "69", "84"))  # the postal code contradicts the department: no zone

    def test_unknown_overseas_postal_code_gives_its_department(self):
        self.assertEqual(self.place("97199 Nulle-Part"), ("", "", "971", "01"))
        self.assertEqual(self.place("Nulle-Part 97699"), ("", "", "976", "06"))
        self.assertEqual(self.place("", postal_code="97499"), ("", "", "974", "04"))
        self.assertEqual(self.place("97866 Nulle-Part"), ("", "", "", ""))  # 978: no department of the file
        self.assertEqual(self.place("Sevilla (41092)"), ("", "", "", ""))

    def test_postal_codes_of(self):
        self.assertEqual(geo.postal_codes_of(["49331", "00000"]), frozenset({"49500", "49520"}))
        self.assertEqual(geo.postal_codes_of([]), frozenset())


class RealFileTests(SimpleTestCase):
    """jobs/data/communes.json.gz itself."""

    def test_header(self):
        self.assertIn("Licence Ouverte", geo._load()["source"])
        self.assertGreater(len(geo._communes()), 34000)

    def test_near_paris(self):
        boulogne = geo.commune_for("Boulogne-Billancourt", "92100")
        self.assertEqual(boulogne, "92012")
        self.assertLess(geo.distance_km(boulogne, "75056"), 30)
        self.assertEqual(geo.commune_for("Versailles", "78000"), "78646")
        self.assertLess(geo.distance_km("78646", "75056"), 30)
        self.assertEqual(geo.commune_for("Villeurbanne", "69100"), "69266")
        self.assertLess(geo.distance_km("69266", "69123"), 30)

    def test_paris_lyon(self):
        self.assertTrue(390 < geo.distance_km(geo.commune_for("Paris"), geo.commune_for("Lyon")) < 400)

    def test_generic_and_cedex_postal_codes(self):
        self.assertEqual(geo.commune_for("Paris", "75000"), "75056")
        self.assertEqual(geo.commune_for("Marseille", "13000"), "13055")
        self.assertEqual(geo.commune_for("Perpignan", "66931"), "66136")
        self.assertEqual(geo.commune_for("Chasseneuil-du-Poitou", "86961"), "86062")
        self.assertIsNone(geo.commune_for("Futuroscope Chasseneuil", "86961"))  # no commune of that name
        self.assertEqual(geo.commune_for("Lyon", "6900"), "69123")
        self.assertEqual(geo.commune_for("Ajaccio", "20999"), "2A004")  # Corsica: 2A or 2B by the name
        self.assertEqual(geo.commune_for("Bastia", "20999"), "2B033")

    def test_known_cases(self):
        self.assertEqual(geo.commune_for("Marseille", "13008"), "13055")
        self.assertIsNone(geo.commune_for("Saint-Denis"))
        self.assertEqual(geo.commune_for("Saint-Denis", "93200"), "93066")
        self.assertEqual(geo.commune_for("St Etienne", "42000"), "42218")
        self.assertEqual(geo.commune_for("Paris", "00000"), "75056")  # unknown code: the city alone
        self.assertIsNone(geo.commune_for("Lyon", "75008"))
        self.assertIsNone(geo.commune_for("Toulouse", "75016"))
        self.assertIsNone(geo.commune_for("Bordeaux", "69001"))
        self.assertEqual(geo.commune_for("Paris 8e", "75008"), "75056")

    def test_communes_within(self):
        self.assertIn("92012", geo.communes_within("75056", 30))
        self.assertNotIn("69123", geo.communes_within("75056", 60))
        self.assertIn("75056", geo.communes_within("75056", 0))

    def test_regions(self):
        self.assertEqual(geo.region_for("75056"), "11")
        self.assertEqual(geo.region_for_department("2A"), "94")
        self.assertIsNone(geo.region_for_department("977"))

    def test_insee_starts_with_the_department(self):
        """jobs/filters.py finds a region's offers by the department prefix of their INSEE code."""
        self.assertTrue(all(commune[geo.INSEE].startswith(commune[geo.DEPARTMENT]) for commune in geo._communes()))
        self.assertIn("2A", geo.departments_of_regions({"94"}))

    def test_loaded_once(self):
        geo.point("75056")
        geo.commune_for("Lyon")
        self.assertEqual(geo._load.cache_info().misses, 1)


class BuildScriptTests(SimpleTestCase):
    """scripts/build_communes.py writes its rows in the layout jobs.geo reads."""

    def test_row_layout(self):
        spec = importlib.util.spec_from_file_location("build_communes", SCRIPT)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        row = script.row({"code": "69123", "nom": "Lyon", "codeDepartement": "69", "codesPostaux": ["69003", "69001"],
                          "centre": {"type": "Point", "coordinates": [4.835123, 45.758049]}, "population": 5,
                          "codeRegion": "84"})
        self.assertEqual((row[geo.INSEE], row[geo.NAME], row[geo.DEPARTMENT], row[geo.POSTAL_CODES]),
                         ("69123", "Lyon", "69", ["69001", "69003"]))
        self.assertEqual((row[geo.LATITUDE], row[geo.LONGITUDE], row[geo.POPULATION], row[geo.REGION]),
                         (45.758, 4.8351, 5, "84"))
        overseas = script.row({"code": "97701", "nom": "Saint-Barthélemy", "codeDepartement": "977",
                               "codeRegion": "977"})
        self.assertIsNone(overseas[geo.REGION])
        self.assertIsNone(overseas[geo.LATITUDE])

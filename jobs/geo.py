"""Communes and distances: the commune of an offer from its city and postal code, and how far apart two communes are.

jobs/data/communes.json.gz holds every French commune with its postal codes and the point of its centre;
scripts/build_communes.py builds it from geo.api.gouv.fr. Distances are as the crow flies, centre to centre. A wrong
commune is worse than none (a false 30 km lets the wrong offers through or hides the right ones): anything unclear
gives None and the offer passes the distance filter.
"""
import bisect
import gzip
import json
import math
import re
from functools import cache, lru_cache
from pathlib import Path
from typing import NamedTuple

from jobs.text import fold

DATA = Path(__file__).resolve().parent / "data" / "communes.json.gz"
EARTH_RADIUS_KM = 6371
# Layout of a commune in the file, shared with scripts/build_communes.py (this module imports nothing from Django).
INSEE, NAME, DEPARTMENT, POSTAL_CODES, LATITUDE, LONGITUDE, POPULATION, REGION = range(8)
# « Paris 8e », « Lyon 3ème », « Marseille 13e Arrondissement », « Nantes (44) »: the trailing number goes.
_TRAILING_NUMBER = re.compile(r" \d+(?:e|er|eme)?(?: (?:e|er|eme|arrondissement))*$")
_SAINT = {"st": "saint", "ste": "sainte"}


@cache
def _load():
    with gzip.open(DATA, "rt", encoding="utf-8") as data:
        return json.load(data)


def _communes():
    return _load()["communes"]


@lru_cache(maxsize=65536)
def _name_key(name):
    """Folded name, « St » and « Ste » read as « Saint » and « Sainte », arrondissement number dropped."""
    key = _TRAILING_NUMBER.sub("", fold(name))
    return " ".join(_SAINT.get(word, word) for word in key.split())


class _Index(NamedTuple):
    by_insee: dict
    by_postal_code: dict
    by_name: dict
    by_department: dict
    region_of_department: dict
    names: list  # every name key, sorted: the start of a name is found by bisection


@cache
def _index():
    index = _Index(by_insee={}, by_postal_code={}, by_name={}, by_department={}, region_of_department={}, names=[])
    for commune in _communes():
        index.by_insee[commune[INSEE]] = commune
        for postal_code in commune[POSTAL_CODES]:
            index.by_postal_code.setdefault(postal_code, []).append(commune)
        index.by_name.setdefault(_name_key(commune[NAME]), []).append(commune)
        index.by_department.setdefault(commune[DEPARTMENT], []).append(commune)
        index.region_of_department.setdefault(commune[DEPARTMENT], commune[REGION])
    index.names.extend(sorted(index.by_name))
    return index


def _only(communes):
    return communes[0][INSEE] if len(communes) == 1 else None


def _departments_of_postal_code(postal_code):
    """Departments a five-digit postal code belongs to: its first two digits, three overseas, 2A or 2B for 20."""
    if not re.fullmatch(r"[0-9]{5}", postal_code):
        return ()
    if postal_code.startswith("20"):
        return ("2A", "2B")
    return (postal_code[:3],) if postal_code.startswith(("97", "98")) else (postal_code[:2],)


def commune_for(city, postal_code="", department_code=""):
    """INSEE code of the commune, None unless one commune clearly fits. A given city must match the commune's name
    (arrondissements and « St » included): a known postal code that contradicts it gives None. A known postal code
    decides first. An unknown one (generic « 75000 », Cedex) only points to its department: the name within it,
    then the name alone if unique in France. A given department keeps the name within it. Never the most populated
    of two namesakes."""
    index = _index()
    name = _name_key(city)
    postal_code, department_code = (postal_code or "").strip(), (department_code or "").strip()
    if postal_code in index.by_postal_code:
        communes = index.by_postal_code[postal_code]
        if name:
            communes = [commune for commune in communes if _name_key(commune[NAME]) == name]
        return _only(communes)
    if not name:
        return None
    communes = index.by_name.get(name, [])
    if department_code:
        return _only([commune for commune in communes if commune[DEPARTMENT] == department_code])
    departments = _departments_of_postal_code(postal_code)
    found = _only([commune for commune in communes if commune[DEPARTMENT] in departments]) if departments else None
    return found or _only(communes)


def point(insee):
    """(latitude, longitude) of the commune's centre, None when unknown."""
    commune = _index().by_insee.get(insee)
    if commune is None or commune[LATITUDE] is None:
        return None
    return commune[LATITUDE], commune[LONGITUDE]


def _haversine(a, b):
    lat_a, lon_a, lat_b, lon_b = map(math.radians, (*a, *b))
    h = math.sin((lat_b - lat_a) / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin((lon_b - lon_a) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def distance_km(insee_a, insee_b):
    """Distance in km between the centres of two communes, None when one of them has no point."""
    a, b = point(insee_a), point(insee_b)
    if a is None or b is None:
        return None
    return _haversine(a, b)


@lru_cache(maxsize=256)
def communes_within(insee, km):
    """INSEE codes of the communes whose centre is at most `km` from the centre of `insee`, itself included.
    Empty when `insee` has no point."""
    centre = point(insee)
    if centre is None:
        return frozenset()
    return frozenset(
        commune[INSEE] for commune in _communes()
        if commune[LATITUDE] is not None and _haversine(centre, (commune[LATITUDE], commune[LONGITUDE])) <= km
    )


def region_for(insee):
    """Region code of a commune, None when unknown or outside the regions (overseas collectivities)."""
    commune = _index().by_insee.get(insee)
    return commune[REGION] if commune else None


def region_for_department(code):
    """Region code of a department, taken from its communes; None when unknown or outside the regions."""
    return _index().region_of_department.get((code or "").strip())


def departments_of_regions(regions):
    """Codes of the departments in these regions. A commune's INSEE code starts with its department's code."""
    return frozenset(code for code, region in _index().region_of_department.items() if region in regions)


def departments_of(communes):
    """Codes of the departments of these communes (INSEE codes), unknown ones left out."""
    by_insee = _index().by_insee
    return frozenset(by_insee[insee][DEPARTMENT] for insee in communes if insee in by_insee)


@lru_cache(maxsize=256)
def departments_within(insee, km):
    """Codes of the departments holding at least one commune whose centre is at most `km` from the centre of
    `insee`. Empty when `insee` has no point."""
    return departments_of(communes_within(insee, km))


# The place of an offer without a commune (the design plan, decision 2): its department, else
# its region, read from the actor's fields then from the text of the place.
DEPARTMENT_NAMES = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence", "05": "Hautes-Alpes",
    "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes", "09": "Ariège", "10": "Aube", "11": "Aude",
    "12": "Aveyron", "13": "Bouches-du-Rhône", "14": "Calvados", "15": "Cantal", "16": "Charente",
    "17": "Charente-Maritime", "18": "Cher", "19": "Corrèze", "2A": "Corse-du-Sud", "2B": "Haute-Corse",
    "21": "Côte-d’Or", "22": "Côtes-d’Armor", "23": "Creuse", "24": "Dordogne", "25": "Doubs", "26": "Drôme",
    "27": "Eure", "28": "Eure-et-Loir", "29": "Finistère", "30": "Gard", "31": "Haute-Garonne", "32": "Gers",
    "33": "Gironde", "34": "Hérault", "35": "Ille-et-Vilaine", "36": "Indre", "37": "Indre-et-Loire", "38": "Isère",
    "39": "Jura", "40": "Landes", "41": "Loir-et-Cher", "42": "Loire", "43": "Haute-Loire", "44": "Loire-Atlantique",
    "45": "Loiret", "46": "Lot", "47": "Lot-et-Garonne", "48": "Lozère", "49": "Maine-et-Loire", "50": "Manche",
    "51": "Marne", "52": "Haute-Marne", "53": "Mayenne", "54": "Meurthe-et-Moselle", "55": "Meuse", "56": "Morbihan",
    "57": "Moselle", "58": "Nièvre", "59": "Nord", "60": "Oise", "61": "Orne", "62": "Pas-de-Calais",
    "63": "Puy-de-Dôme", "64": "Pyrénées-Atlantiques", "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales",
    "67": "Bas-Rhin", "68": "Haut-Rhin", "69": "Rhône", "70": "Haute-Saône", "71": "Saône-et-Loire", "72": "Sarthe",
    "73": "Savoie", "74": "Haute-Savoie", "75": "Paris", "76": "Seine-Maritime", "77": "Seine-et-Marne",
    "78": "Yvelines", "79": "Deux-Sèvres", "80": "Somme", "81": "Tarn", "82": "Tarn-et-Garonne", "83": "Var",
    "84": "Vaucluse", "85": "Vendée", "86": "Vienne", "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne",
    "90": "Territoire de Belfort", "91": "Essonne", "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis",
    "94": "Val-de-Marne", "95": "Val-d’Oise", "971": "Guadeloupe", "972": "Martinique", "973": "Guyane",
    "974": "La Réunion", "976": "Mayotte",
}
REGION_NAMES = {
    "01": "Guadeloupe", "02": "Martinique", "03": "Guyane", "04": "La Réunion", "06": "Mayotte",
    "11": "Île-de-France", "24": "Centre-Val de Loire", "27": "Bourgogne-Franche-Comté", "28": "Normandie",
    "32": "Hauts-de-France", "44": "Grand Est", "52": "Pays de la Loire", "53": "Bretagne", "75": "Nouvelle-Aquitaine",
    "76": "Occitanie", "84": "Auvergne-Rhône-Alpes", "93": "Provence-Alpes-Côte d’Azur", "94": "Corse",
}
OVERSEAS_DEPARTMENTS = frozenset(code for code in DEPARTMENT_NAMES if code.startswith("97"))
_DEPARTMENT_ALIASES = {"974": ["Réunion"]}
_REGION_ALIASES = {"04": ["Réunion"], "11": ["IDF"], "24": ["Centre"], "93": ["PACA"]}
# « (69) », « (971) », « (2A) »; « Sarthe - 72 » at the end of a part; a postal code of five digits.
_CODE_IN_BRACKETS = re.compile(r"\(\s*([0-9]{2,3}|2[AB])\s*\)", re.IGNORECASE)
_CODE_AFTER_DASH = re.compile(r"\s[-–]\s*([0-9]{2,3}|2[AB])$", re.IGNORECASE)
_POSTAL_CODE = re.compile(r"(?<![0-9])[0-9]{5}(?![0-9])")


class Area(NamedTuple):
    department: str  # a code of the communes file, "" when unknown
    region: str  # a region code, "" when unknown or outside the regions (overseas collectivities)


@cache
def _area_names():
    """{name key: (department code, "") or ("", region code)}. A department name that is also the name of a commune
    of another department (« Vienne », « Loire ») is left out: alone, it does not tell which. A department wins
    over a region of the same name (Guadeloupe)."""
    by_name = _index().by_name
    names = {}
    for code, name in REGION_NAMES.items():
        for alias in [name, *_REGION_ALIASES.get(code, [])]:
            names[_name_key(alias)] = Area("", code)
    for code, name in DEPARTMENT_NAMES.items():
        for alias in [name, *_DEPARTMENT_ALIASES.get(code, [])]:
            key = _name_key(alias)
            if all(commune[DEPARTMENT] == code for commune in by_name.get(key, [])):
                names[key] = Area(code, "")
    return names


def _known_department(code):
    code = (code or "").strip().upper()
    return code if code in _index().region_of_department else ""


def _single(departments):
    return next(iter(departments)) if len(set(departments)) == 1 else ""


def _department_code_in(part):
    """The known department a part of the place gives by its code, in brackets or after a dash; "" when none."""
    for pattern in (_CODE_IN_BRACKETS, _CODE_AFTER_DASH):
        match = pattern.search(part)
        if match and _known_department(match.group(1)):
            return _known_department(match.group(1))
    return ""


def _department_in_text(location):
    """The department the text of the place names: a code in brackets or after a dash, a known postal code or an
    overseas one, then the name of a part between commas."""
    # shortcut: a place listing several cities (« Lille; Lyon ») gets the first one, read each part if it matters.
    parts = [part.strip() for part in re.split(r"[,;]", location)]
    for part in parts:
        if department := _department_code_in(part):
            return department
    by_postal_code = _index().by_postal_code
    for postal_code in _POSTAL_CODE.findall(location):
        # Only a postal code of the file, or an overseas one: a foreign one (« Sevilla (41092) ») says nothing.
        found = (_single([commune[DEPARTMENT] for commune in by_postal_code.get(postal_code, [])])
                 or _single(set(_departments_of_postal_code(postal_code)) & OVERSEAS_DEPARTMENTS))
        if found:
            return found
    for part in parts:
        department = _area_names().get(_name_key(part), Area("", "")).department
        if department:
            return department
    return ""


def _region_named(*texts):
    for text in texts:
        for part in re.split(r"[,;]", text or ""):
            region = _area_names().get(_name_key(part), Area("", "")).region
            if region:
                return region
    return ""


def area_for(location, postal_code="", department_code="", region=""):
    """The department, else the region, of an offer, from the actor's fields first (department code, postal code,
    region name), then from the text of the place. Anything unclear gives "": the offer then passes the distance
    filter as an offer without any place."""
    location = location or ""
    department = (_known_department(department_code)
                  or _known_department(_single(_departments_of_postal_code((postal_code or "").strip())))
                  or _department_in_text(location))
    if department:
        return Area(department, region_for_department(department) or "")
    return Area("", _region_named(region, location))


# The commune named in the text of the place, when the actor gave none, and the postal code of the zone of an offer
# without a commune (the design plan, decisions 1 and 2).
_PIECES = re.compile(r"\s[-–]\s")
_NOT_A_NAME = frozenset({"france", "fr"})
# Countries and regions abroad that offers name: a place naming one of them names no French commune (« Lausanne,
# Suisse », « Allemagne », not Allemagne-en-Provence), when the actor gave no country.
_ABROAD = frozenset({
    "allemagne", "angleterre", "argentine", "australie", "autriche", "belgique", "bresil", "cameroun", "canada",
    "chine", "cote d ivoire", "dubai", "emirats arabes unis", "espagne", "etats unis", "germany", "inde", "irlande",
    "italie", "japon", "luxembourg", "maroc", "mexique", "monaco", "ontario", "pays bas", "pologne", "portugal",
    "quebec", "royaume uni", "senegal", "singapour", "suisse", "switzerland", "texas", "tunisie", "uk", "usa",
})


class Place(NamedTuple):
    insee: str  # the commune, "" when unknown
    zone_postal_code: str  # a postal code of the file, only for an offer without a commune
    department: str
    region: str


@cache
def _area_departments():
    """{name key: departments it designates} for every department and region name, the ambiguous ones that
    _area_names leaves out included (« Vienne », « Loire »)."""
    names = {}
    for code, name in REGION_NAMES.items():
        for alias in [name, *_REGION_ALIASES.get(code, [])]:
            names.setdefault(_name_key(alias), set()).update(departments_of_regions({code}))
    for code, name in DEPARTMENT_NAMES.items():
        for alias in [name, *_DEPARTMENT_ALIASES.get(code, [])]:
            names.setdefault(_name_key(alias), set()).add(code)
    return names


def _names_in(part):
    """Name keys of a part of the place once its codes are dropped: « 53204 CHATEAU GONTIER CEDEX » gives
    « chateau gontier », « Cugand (85) - Pays de la Loire » gives « cugand » then « pays de la loire »."""
    for pattern in (_CODE_IN_BRACKETS, _CODE_AFTER_DASH, _POSTAL_CODE):
        part = pattern.sub(" ", part)
    keys = []
    for piece in _PIECES.split(part):
        words = _name_key(piece).split()
        while words and (words[-1].isdigit() or words[-1] == "cedex"):
            words.pop()
        key = " ".join(words)
        if re.search("[a-z]", key) and key not in _NOT_A_NAME:
            keys.append(key)
    return keys


def _pool(postal_codes, department, zone):
    """The communes a name is looked for in, None for all of France: those of the known postal codes, else of the
    department, else of the department of an unknown postal code (Cedex), overseas as a whole for an unknown code
    starting with 97, else of the departments of `zone` (the area named elsewhere in the place). Empty when a postal
    code points nowhere (« Monaco 98000 »)."""
    index = _index()
    known = [code for code in postal_codes if code in index.by_postal_code]
    if known:
        return [commune for code in known for commune in index.by_postal_code[code]]
    departments = {department} if department else set()
    if not departments:
        for code in postal_codes:
            departments.update(OVERSEAS_DEPARTMENTS if code.startswith("97") else _departments_of_postal_code(code))
    if not departments and not postal_codes:
        departments = set(zone)
    departments &= index.by_department.keys()
    if departments:
        return [commune for code in sorted(departments) for commune in index.by_department[code]]
    return [] if postal_codes or zone else None


def _named(key, pool):
    """Communes called `key`, else those whose name starts with the words of `key` (« Le Touquet » for
    Le Touquet-Paris-Plage). A department or region name is never the start of a commune name (« Gironde » is not
    Gironde-sur-Dropt), and in all of France it only stands for a commune of that zone (« Paris »; never « Vienne »,
    a department and a commune of Isère)."""
    zone = _area_departments().get(key)
    if pool is not None:
        exact = [commune for commune in pool if _name_key(commune[NAME]) == key]
        if exact or zone is not None:
            return exact
        return [commune for commune in pool if _name_key(commune[NAME]).startswith(key + " ")]
    index, start = _index(), key + " "
    found = list(index.by_name.get(key, []))
    if not found and zone is None:
        for name in index.names[bisect.bisect_left(index.names, start):]:
            if not name.startswith(start):
                break
            found += index.by_name[name]
    if zone is not None and any(commune[DEPARTMENT] not in zone for commune in found):
        return []
    return found


def _commune_in_text(location, postal_code, department_code, zone):
    """(INSEE code, department) of the first commune name the text of the place gives, each part between commas
    looked up within its own postal code or department code, else the actor's, else the departments of `zone`: one
    commune gives both, several in one department give the department alone, several departments give nothing."""
    for part in re.split(r"[,;]", location):
        codes = _POSTAL_CODE.findall(part) or ([postal_code] if _POSTAL_CODE.fullmatch(postal_code) else [])
        pool = _pool(codes, _department_code_in(part) or department_code, zone)
        for key in _names_in(part):
            communes = _named(key, pool)
            if len(communes) == 1:
                return communes[0][INSEE], communes[0][DEPARTMENT]
            if communes:
                return "", _single([commune[DEPARTMENT] for commune in communes])
    return "", ""


def _zone_postal_code(location, postal_code, department):
    """The actor's postal code if the file knows it, else the first known one in the text of the place; "" when it
    contradicts `department`."""
    by_postal_code = _index().by_postal_code
    for code in [postal_code, *_POSTAL_CODE.findall(location)]:
        if code in by_postal_code:
            agrees = not department or any(commune[DEPARTMENT] == department for commune in by_postal_code[code])
            return code if agrees else ""
    return ""


def _abroad(location):
    return any(key in _ABROAD for part in re.split(r"[,;]", location) for key in _names_in(part))


def place_for(location, city="", postal_code="", department_code="", region=""):
    """Where an offer is: the commune of the actor's fields (commune_for), else the one the text of the place names;
    without a commune, the postal code of the zone and the department or region (area_for, else the department of
    communes named alike). The department of a commune is its own, whatever the actor says. The text of a place
    that names a country abroad is not read, only the actor's fields."""
    location, postal_code = location or "", (postal_code or "").strip()
    if _abroad(location):
        location = ""
    insee = commune_for(city, postal_code, department_code) or ""
    area = area_for(location, postal_code, department_code, region)
    named_department = ""
    if not insee:
        zone = {area.department} if area.department else departments_of_regions({area.region} - {""})
        insee, named_department = _commune_in_text(location, postal_code, _known_department(department_code), zone)
    if insee:
        department = _index().by_insee[insee][DEPARTMENT]
        return Place(insee, "", department, region_for_department(department) or "")
    department = area.department or named_department
    zone = _zone_postal_code(location, postal_code, department)
    if department:
        return Place("", zone, department, region_for_department(department) or "")
    return Place("", zone, "", area.region)


def postal_codes_of(communes):
    """Postal codes of these communes (INSEE codes), unknown ones left out."""
    by_insee = _index().by_insee
    return frozenset(code for insee in communes if insee in by_insee for code in by_insee[insee][POSTAL_CODES])

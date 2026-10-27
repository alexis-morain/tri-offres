"""`Profile.hard_criteria`: the answers of block 1 ("Ce que vous excluez") and the offer kind.

Schema, version 1. A missing key means the criterion was not answered ("Non renseigné").

    {"version": 1,
     "offer_kind": "job" | "mission" | "both",        # asked on the search screen, for every domain
     "location": {"cities": [{"name": str,            # 0 to 3 cities, name never empty
                              "insee": str,           # commune code from geo.api.gouv.fr, "" without JavaScript
                              "department": str,      # "" when unknown
                              "region": str}],        # "" when unknown
                  "distance": "city" | "30" | "60" | "region",
                  "remote": "onsite" | "hybrid" | "full_remote" | "any",
                  "strict": bool},
     "contracts": {"values": [contract code, ...],    # at least one
                   "strict": True},                   # always True: no preference switch, same shape as the rest
     "keywords": {"include": [str],                   # 0 to 10 words each, 40 characters at most, a letter in each
                  "exclude": [str],                   # a word may hold spaces (a phrase)
                  "strict": True},                    # always True, as for contracts
     "pay": {"floor": int | None,                     # None: no floor, nothing excluded for pay
             "unit": "year" | "month" | "day",        # gross per year or month, day rate for missions
             "strict": bool},
     "hours": {"value": "full" | "part_ok" | "part_only", "strict": bool},
     "experience": {"value": "lt1" | "1_2" | "3_4" | "5_9" | "10_plus", "strict": bool}}

Keywords are matched on the title alone, whole words, without accents or capitals (jobs/filters.py). The same word
(by `fold`) twice in a list is refused.

`strict` is False when the person turned on "Plutôt une préférence": the criterion then only weighs in the
score and excludes nothing.

`describe()` turns criteria into plain rows, without Claude: the file's cards use it, and so will part A of the
summary (sub-step 3.6).
"""

import re

from jobs.text import fold
from onboarding import choices

VERSION = 1

OFFER_KINDS = {"job": "Un poste salarié", "mission": "Des missions", "both": "Les deux"}
DISTANCES = {"city": "La ville seule", "30": "Jusqu’à 30 km", "60": "Jusqu’à 60 km", "region": "Toute la région"}
REMOTE = {"onsite": "Sur place possible", "hybrid": "Hybride", "full_remote": "Télétravail complet seulement",
          "any": "Peu importe"}
PAY_UNITS = {"year": "Brut par an", "month": "Brut par mois", "day": "Par jour (TJM)"}
HOURS = {"full": "Temps plein", "part_ok": "Temps partiel accepté", "part_only": "Temps partiel seulement"}
EXPERIENCE = {"lt1": "Moins d’un an", "1_2": "1 à 2 ans", "3_4": "3 à 4 ans", "5_9": "5 à 9 ans",
              "10_plus": "10 ans et plus"}
MAX_CITIES = 3
MAX_KEYWORDS = 10
MAX_KEYWORD_LENGTH = 40

CONTRACTS = {
    "cdi": "CDI",
    "cdd": "CDD",
    "cdd_projet": "CDD de projet ou d’usage",
    "interim": "Intérim",
    "fonction_publique": "Fonction publique (contractuel)",
    "service_civique": "Service civique",
    "freelance": "Freelance ou mission",
    "alternance": "Alternance",
    "stage": "Stage",
}
# Contracts checked on the contracts screen while nothing is saved yet, by offer kind.
DEFAULT_CONTRACTS = {"job": {"cdi", "cdd"}, "mission": {"freelance"}, "both": {"cdi", "cdd", "freelance"}}
# Added for the culture niche when the person looks for salaried posts.
CULTURE_CONTRACTS = {"cdd_projet", "fonction_publique"}

# Offer kinds that open the day rate (TJM) unit.
DAY_RATE_KINDS = {"mission", "both"}

NOT_ANSWERED = "Non renseigné"
PREFERENCE_SUFFIX = " (préférence)"
_OFFER_KIND_TEXT = {"job": "Postes salariés", "mission": "Missions", "both": "Postes salariés et missions"}
# Phrasings for sentences, not labels: the unit inside "24 000 € brut par an".
_UNIT_TEXT = {"year": "brut par an", "month": "brut par mois", "day": "par jour"}


def empty():
    return {"version": VERSION}


def default_contracts(domains, offer_kind):
    """The contracts prechecked for these domains and offer kind, in the order of CONTRACTS."""
    kind = offer_kind if offer_kind in DEFAULT_CONTRACTS else "job"
    chosen = set(DEFAULT_CONTRACTS[kind])
    if kind in ("job", "both") and choices.NICHES["culture"]["domains"] & set(domains):
        chosen |= CULTURE_CONTRACTS
    return [code for code in CONTRACTS if code in chosen]


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _strict(value):
    _require(isinstance(value, bool), "strict must be a boolean")
    return value


def _one_of(value, allowed, name):
    # Every allowed value is a string: anything else (a list, an object) is refused, never looked up.
    _require(isinstance(value, str) and value in allowed, f"{name}: unexpected value {value!r}")
    return value


def _city(data):
    _require(isinstance(data, dict), "a city must be an object")
    name = data.get("name")
    _require(isinstance(name, str) and name.strip(), "a city needs a name")
    city = {"name": name.strip()}
    for key in ("insee", "department", "region"):
        value = data.get(key) or ""
        _require(isinstance(value, str), f"city {key} must be a string")
        city[key] = value
    return city


def _keywords(values, name):
    _require(isinstance(values, list) and len(values) <= MAX_KEYWORDS, f"{name}: at most {MAX_KEYWORDS} words")
    words = []
    for value in values:
        _require(isinstance(value, str), f"{name}: a word must be a string")
        word = " ".join(value.split())
        _require(re.search(r"[^\W\d_]", word) and len(word) <= MAX_KEYWORD_LENGTH, f"{name}: unexpected word {value!r}")
        words.append(word)
    _require(len({fold(word) for word in words}) == len(words), f"{name}: the same word twice")
    return words


def normalize(data):
    """Validated copy of `data` in the version 1 schema. Raises ValueError when anything does not fit."""
    data = data or {}
    _require(isinstance(data, dict), "criteria must be an object")
    _require(data.get("version", VERSION) == VERSION, "unsupported criteria version")
    unknown = set(data) - {"version", "offer_kind", "location", "contracts", "keywords", "pay", "hours", "experience"}
    _require(not unknown, f"unknown keys {sorted(unknown)}")
    for key in ("location", "contracts", "keywords", "pay", "hours", "experience"):
        _require(isinstance(data.get(key, {}), dict), f"{key} must be an object")
    result = empty()
    if "offer_kind" in data:
        result["offer_kind"] = _one_of(data["offer_kind"], OFFER_KINDS, "offer_kind")
    if "location" in data:
        location = data["location"]
        cities = location.get("cities", [])
        _require(isinstance(cities, list) and len(cities) <= MAX_CITIES, f"at most {MAX_CITIES} cities")
        result["location"] = {"cities": [_city(city) for city in cities],
                              "distance": _one_of(location.get("distance"), DISTANCES, "distance"),
                              "remote": _one_of(location.get("remote"), REMOTE, "remote"),
                              "strict": _strict(location.get("strict"))}
    if "contracts" in data:
        values = data["contracts"].get("values")
        _require(isinstance(values, list) and values, "at least one contract")
        _require(data["contracts"].get("strict") is True, "contracts are always strict")
        result["contracts"] = {"values": [_one_of(value, CONTRACTS, "contract") for value in values], "strict": True}
    if "keywords" in data:
        _require(data["keywords"].get("strict") is True, "keywords are always strict")
        result["keywords"] = {"include": _keywords(data["keywords"].get("include"), "include"),
                              "exclude": _keywords(data["keywords"].get("exclude"), "exclude"), "strict": True}
    if "pay" in data:
        pay = data["pay"]
        floor = pay.get("floor")
        _require(floor is None or (isinstance(floor, int) and not isinstance(floor, bool) and floor > 0),
                 "pay floor must be a positive integer or null")
        result["pay"] = {"floor": floor, "unit": _one_of(pay.get("unit"), PAY_UNITS, "unit"),
                         "strict": _strict(pay.get("strict"))}
    for key, allowed in (("hours", HOURS), ("experience", EXPERIENCE)):
        if key in data:
            result[key] = {"value": _one_of(data[key].get("value"), allowed, key),
                           "strict": _strict(data[key].get("strict"))}
    return result


def format_number(number):
    """24000 -> "24 000", thousands split by a narrow no-break space."""
    return f"{number:,}".replace(",", "\u202f")


def format_amount(amount):
    """24000 -> "24 000 €", with a no-break space before the sign."""
    return format_number(amount) + "\u00a0€"


def join_names(names):
    """Names joined by commas, the last one by « et »: "Lyon, Nantes et Lille"."""
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + " et " + names[-1]


def _lower_first(label):
    return label[:1].lower() + label[1:]


def _location_text(location):
    names = [city["name"] for city in location["cities"]]
    remote = f"télétravail : {_lower_first(REMOTE[location['remote']])}"
    if not names:
        return REMOTE[location["remote"]] if location["remote"] == "full_remote" else remote.capitalize()
    return f"{join_names(names)}, {_lower_first(DISTANCES[location['distance']])}, {remote}"


def _pay_text(pay):
    if pay["floor"] is None:
        return "Pas de plancher"
    return f"{format_amount(pay['floor'])} {_UNIT_TEXT[pay['unit']]} au moins"


def _keywords_text(keywords):
    parts = [f"{label} : {', '.join(words)}" for label, words in (("exigés", keywords["include"]),
                                                                  ("écartés", keywords["exclude"])) if words]
    text = " ; ".join(parts)
    return text[:1].upper() + text[1:] if text else "Aucun"


def describe(criteria):
    """Rows (key, label, text) for the offer kind and every criterion, in screen order."""
    kind = criteria.get("offer_kind")
    rows = [("offer_kind", "Type d’offres", _OFFER_KIND_TEXT[kind] if kind else NOT_ANSWERED)]
    texts = {
        "location": ("Où", _location_text),
        "contracts": ("Contrats", lambda c: ", ".join(CONTRACTS[v] for v in c["values"])),
        "keywords": ("Mots-clés du titre", _keywords_text),
        "pay": ("Rémunération", _pay_text),
        "hours": ("Temps de travail", lambda c: HOURS[c["value"]]),
        "experience": ("Expérience", lambda c: EXPERIENCE[c["value"]]),
    }
    for key, (label, text) in texts.items():
        if key not in criteria:
            rows.append((key, label, NOT_ANSWERED))
            continue
        value = text(criteria[key])
        if criteria[key].get("strict") is False:
            value += PREFERENCE_SUFFIX
        rows.append((key, label, value))
    return rows


def pay_consequence(pay):
    """The sentence under the pay floor: what the floor costs. Empty while nothing is saved."""
    if not pay:
        return ""
    if pay["floor"] is None:
        return "Aucune offre n’est écartée pour son salaire."
    threshold = f"Les offres sous {format_amount(pay['floor'])} {_UNIT_TEXT[pay['unit']]}"
    if pay["strict"]:
        return f"{threshold} ne vous seront pas montrées. Celles qui n’affichent pas de salaire restent visibles."
    return f"{threshold} seront moins bien notées."


def location_consequence(distance):
    """The sentence under the distance: how far is measured, and that offers without a known commune stay."""
    unknown = "Les offres dont la commune n’est pas reconnue restent visibles."
    return f"Distance à vol d’oiseau. {unknown}" if distance in ("30", "60") else unknown

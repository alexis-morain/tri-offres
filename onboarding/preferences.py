"""`Profile.weighted_preferences`: the answers of block 2 ("Ce que vous préférez").

Schema, version 1. A missing key means the screen was not answered ("Non renseigné").

    {"version": 1,
     "values": [value code, ...],                     # 1 to 3, see choices.VALUES
     "jobs": [{"custom": str} | job code, ...],       # the 1 to 3 postes typed on the search screen, then the job
                                                      # chips picked before them (5 at most, see choices.job_codes())
     "structures": [structure code, ...],             # 0 to 5, see choices.STRUCTURES; [] means "none in particular"
     "sectors": [sector code, ...]}                   # 0 to 3, see choices.SECTORS; [] means "none in particular"

A poste ({"custom": str}) is typed by the person: at most 150 characters, spaces collapsed, at least one letter.
None of these exclude an offer: they only weigh in the score.

`describe()` and `describe_words()` turn the answers into plain rows for the blue and yellow cards of the file,
and for part A of the summary (sub-step 3.6).
"""
import re

from django.utils.text import Truncator

from onboarding import choices, rome

VERSION = 1
MAX_VALUES = 3
MAX_JOBS = 5  # job chips, on the screen kept for the accounts from before the postes
MAX_POSTES = 3
MAX_STRUCTURES = 5
MAX_SECTORS = 3
CUSTOM_JOB_MAX = 150  # a ROME title suggested on the search screen holds up to 132 characters
WORDS_MAX = 1000  # each free text of the words screen
WORDS_PREVIEW = 120

NOT_ANSWERED = "Non renseigné"
NONE_CHOSEN = "Aucune en particulier"
NOT_FILLED = "Pas encore rempli."

_ALL_JOBS = {code for code, _ in rome.PROFESSIONAL_DOMAINS} | {code for items in choices.JOBS.values()
                                                                for code, _ in items}
_ALL_STRUCTURES = {code for code, _ in choices.STRUCTURES}
_ALL_SECTORS = {code for code, _ in choices.SECTORS}
_LISTS = {  # key: (allowed codes, minimum, maximum)
    "values": ({code for code, _ in choices.VALUES}, 1, MAX_VALUES),
    "jobs": (_ALL_JOBS, 1, MAX_POSTES + MAX_JOBS),
    "structures": (_ALL_STRUCTURES, 0, MAX_STRUCTURES),
    "sectors": (_ALL_SECTORS, 0, MAX_SECTORS),
}


def empty():
    return {"version": VERSION}


def is_empty(data):
    """Whether `data` holds no chosen preference; a value that is not an object counts as empty."""
    return not isinstance(data, dict) or not any(data.get(key) for key in _LISTS)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def is_custom(job):
    """A job typed by the person ({"custom": text}) rather than a code from the list."""
    return isinstance(job, dict)


def postes(data):
    """The postes typed on the search screen, in their order; none for preferences that are not an object."""
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return [job["custom"] for job in jobs if is_custom(job)] if isinstance(jobs, list) else []


def job_label(job):
    return job["custom"] if is_custom(job) else choices.LABELS[job]


def clean_custom_job(text):
    """The typed job with its spaces collapsed, or None when it has no letter."""
    text = " ".join(text.split())
    return text if re.search(r"[^\W\d_]", text) else None


def _item(key, item, allowed):
    if key == "jobs" and is_custom(item):
        _require(set(item) == {"custom"} and isinstance(item["custom"], str), "a custom job is {'custom': str}")
        text = clean_custom_job(item["custom"])
        _require(text is not None, "a custom job needs a letter")
        _require(len(text) <= CUSTOM_JOB_MAX, f"a custom job has at most {CUSTOM_JOB_MAX} characters")
        return {"custom": text}
    _require(isinstance(item, str) and item in allowed, f"{key}: unexpected value {item!r}")
    return item


def normalize(data):
    """Validated copy of `data` in the version 1 schema. Raises ValueError when anything does not fit."""
    data = data or {}
    _require(isinstance(data, dict), "preferences must be an object")
    _require(data.get("version", VERSION) == VERSION, "unsupported preferences version")
    unknown = set(data) - {"version", *_LISTS}
    _require(not unknown, f"unknown keys {sorted(unknown)}")
    result = empty()
    for key, (allowed, low, high) in _LISTS.items():
        if key not in data:
            continue
        items = data[key]
        _require(isinstance(items, list) and low <= len(items) <= high, f"{key}: {low} to {high} items")
        items = [_item(key, item, allowed) for item in items]
        _require(len({repr(item) for item in items}) == len(items), f"{key}: duplicates")
        _require(sum(map(is_custom, items)) <= MAX_POSTES, f"{key}: at most {MAX_POSTES} postes")
        result[key] = items
    return result


def with_postes(data, typed, domains):
    """The preferences once the search screen is saved with the postes `typed` (1 to 3) and these `domains`: the
    postes first, then the job chips the domains still offer. Values, structures and sectors hold for any domain."""
    result = normalize(data)
    offered = choices.job_codes(domains)
    chips = [job for job in result.get("jobs", []) if not is_custom(job) and job in offered]
    result["jobs"] = [{"custom": poste} for poste in typed] + chips
    return result


def _jobs_text(labels):
    # ROME labels hold commas: each job gets its capital and a semicolon between them, held to the word before.
    return "\u202f; ".join(choices.upper_first(label) for label in labels)


def _text(key, items):
    if not items:
        return NONE_CHOSEN
    if key == "jobs":
        return _jobs_text(job_label(item) for item in items)
    # Commas only: several labels already hold an "et".
    return choices.upper_first(", ".join(choices.LABELS[item] for item in items))


def describe_postes(preferences):
    """The postes of the search screen as one row's text, "Non renseigné" for an account from before them."""
    return _jobs_text(postes(preferences)) or NOT_ANSWERED


def describe(preferences):
    """Rows (key, label, text) of the blue card, in screen order. The job chips only for the accounts that picked
    some: the postes typed have their own row (describe_postes)."""
    rows = []
    for key, label in (("values", "Ce qui compte"), ("jobs", "Métiers"), ("structures", "Structures"),
                       ("sectors", "Secteurs")):
        items = preferences.get(key)
        if key == "jobs":
            items = [job for job in items or [] if not is_custom(job)]
            if not items:
                continue
        rows.append((key, label, _text(key, items) if key in preferences else NOT_ANSWERED))
    return rows


def describe_words(seek_text, refuse_text, empty=NOT_FILLED):
    """Rows (key, label, text) of the yellow card: each text cut to 120 characters, `empty` for a blank one
    (NOT_FILLED while the questionnaire goes on, NOT_ANSWERED on the summary)."""
    return [(key, label, Truncator(" ".join(text.split())).chars(WORDS_PREVIEW) if text.strip() else empty)
            for key, label, text in (("seek_text", "Ce que vous cherchez", seek_text),
                                     ("refuse_text", "Ce que vous refusez", refuse_text))]

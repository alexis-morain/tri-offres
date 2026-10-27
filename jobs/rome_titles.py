"""The ROME code of an offer, from its title compared to the ROME job titles (« appellations »).

jobs/data/rome-titles.json.gz maps each job title, in its masculine and feminine forms, marks dropped and folded, to
the ROME code of its fiche; scripts/build_rome_titles.py builds it from France Travail's open data. Titles leading to
two fiches, or too vague (« chef de projet »), map to None: sentinels.

An offer of a general source that gets a code only reaches the people of that domain, so a wrong code hides it from
the right people. Anything unclear gives None, and step 5 decides from the whole offer.
"""
import gzip
import json
import re
from functools import cache
from pathlib import Path

from jobs.text import fold, job_words, strip_marks

DATA = Path(__file__).resolve().parent / "data" / "rome-titles.json.gz"
# Parts of a title looked up one by one: « Lyon - Chef de projet - CDI », « Comptable, Paie (CDI) ».
_SEGMENTS = re.compile(r"\s[-/]\s|[–—|,()]")
# Words that leave a one-word match standing (« Comptable senior », « Infirmier de nuit »): level, contract, hours,
# and the small words around them. Any other word may change the job (« Comptable fournisseurs », « Électricien
# bâtiment »), so the one-word match is dropped.
MODIFIERS = {
    "senior", "junior", "confirme", "confirmee", "experimente", "experimentee", "debutant", "debutante",
    "cdi", "cdd", "interim", "alternance", "stage", "temps", "plein", "partiel", "nuit", "jour", "week", "end",
    "h", "f", "x", "de", "du", "des", "d", "en", "et", "a", "au", "aux", "l", "la", "le", "les",
}


@cache
def _load():
    with gzip.open(DATA, "rt", encoding="utf-8") as data:
        return json.load(data)


@cache
def _table():
    """(titles, number of words of the longest title)."""
    titles = _load()["titles"]
    return titles, max((len(title.split()) for title in titles), default=0)


def _segment_match(words):
    """(length, code or None) of the longest known run of words, the first in the segment when two are as long;
    None when no run is known, or when a one-word match has other words that may change the job."""
    titles, longest = _table()
    for length in range(min(longest, len(words)), 0, -1):
        for start in range(len(words) - length + 1):
            run = " ".join(words[start:start + length])
            if run not in titles:
                continue
            others = words[:start] + words[start + length:]
            if length == 1 and not all(word in MODIFIERS or word.isdigit() for word in others):
                return None
            return length, titles[run]
    return None


def rome_code_for(title):
    """ROME code of the title. Each segment is looked up, the segment with the longest match wins; None when that
    match is a sentinel, when two segments tie on different fiches, or when nothing matches."""
    matches = [match for segment in _SEGMENTS.split(strip_marks(title))
               if (match := _segment_match(fold(segment).split()))]
    if not matches:
        return None
    longest = max(length for length, _ in matches)
    codes = {code for length, code in matches if length == longest}
    return codes.pop() if len(codes) == 1 else None


def code_for_appellation(text):
    """ROME code of the job title Claude gives for an offer (step 5, decision Q26): an exact lookup of the whole
    folded title, never cut into segments. None when absent, a sentinel or blank."""
    titles, _ = _table()
    return titles.get(fold(strip_marks(text))) or None


# Folded job words found in the titles of hundreds of fiches (« responsable », « technicien »): sharing one of them
# with a fiche proves nothing, so they never vouch for Claude's code.
GENERIC_JOB_WORDS = frozenset("""
    responsable technicien technicienne agent assistant assistante charge chargee gestionnaire conseiller conseillere
    operateur operatrice employe employee ingenieur ingenieure chef cheffe manager directeur directrice adjoint
    adjointe consultant consultante specialiste expert experte animateur animatrice coordinateur coordinatrice
    superviseur superviseuse attache attachee aide collaborateur collaboratrice senior junior principal principale
""".split())


@cache
def fiche_words():
    """{ROME code: words of the titles of its fiche (jobs.text.job_words)}, sentinels left out."""
    words = {}
    for title, code in _load()["titles"].items():
        if code:
            words.setdefault(code, set()).update(job_words(title))
    return words


# What became of the fiche code Claude gives (the design plan, section 3): not read because the
# appellation is exactly a title, empty, not a fiche of ROME v61, refused for want of a shared word, accepted.
TITLE, EMPTY, UNKNOWN, REFUSED, ACCEPTED = "title", "empty", "unknown", "refused", "accepted"


def judge_claude_code(appellation, rome_code):
    """(outcome, ROME code or None) of an offer from the appellation and the fiche code Claude gives
    (the design plan, section 2). An appellation that is exactly a title decides alone,
    a sentinel giving None. Otherwise the fiche code holds only if the fiche exists and one of its titles shares a
    word with the appellation, a word of GENERIC_JOB_WORDS not counting."""
    titles, _ = _table()
    title = fold(strip_marks(appellation))
    if title in titles:
        return TITLE, titles[title]
    if rome_code is None or isinstance(rome_code, str) and not rome_code.strip():
        return EMPTY, None
    fiche = fiche_words().get(rome_code) if isinstance(rome_code, str) else None
    if not fiche:
        return UNKNOWN, None
    if fiche & job_words(strip_marks(appellation)) - GENERIC_JOB_WORDS:
        return ACCEPTED, rome_code
    return REFUSED, None


def code_for_claude(appellation, rome_code):
    """ROME code of an offer from the appellation and the fiche code Claude gives (judge_claude_code), None when
    doubtful."""
    return judge_claude_code(appellation, rome_code)[1]


# The search screen's suggestions: jobs/data/rome-labels.json.gz, the titles of a single fiche as people read them.
LABELS = Path(__file__).resolve().parent / "data" / "rome-labels.json.gz"
SUGGESTIONS = 8


@cache
def labels():
    """{label as shown: ROME code}, every label giving its code back through code_for_appellation."""
    with gzip.open(LABELS, "rt", encoding="utf-8") as data:
        return json.load(data)["labels"]


@cache
def _label_words():
    """(label, its folded words), shortest labels first, then in alphabetical order."""
    return [(label, fold(label).split()) for label in sorted(labels(), key=lambda label: (len(label), label))]


def suggest(query):
    """At most SUGGESTIONS labels, shortest first, each word of `query` starting one of their words (accents and
    capitals ignored). Nothing for a query of less than two letters."""
    words = fold(query).split()
    if len("".join(words)) < 2:
        return []
    found = []
    for label, label_words in _label_words():
        if all(any(word.startswith(typed) for word in label_words) for typed in words):
            found.append(label)
            if len(found) == SUGGESTIONS:
                break
    return found

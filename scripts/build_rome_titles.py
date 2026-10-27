#!/usr/bin/env python3
"""Builds jobs/data/rome-titles.json.gz, the ROME job titles (« appellations ») and the ROME code of their fiche, and
jobs/data/rome-labels.json.gz, the same titles as the search screen suggests them.

    python3 scripts/build_rome_titles.py <path to the xlsx>

Data: « Arborescence principale » of the ROME 4.0, France Travail, Licence Ouverte, listed on
https://www.data.gouv.fr/datasets/repertoire-operationnel-des-metiers-et-des-emplois-rome
(file rome-arborescence-principale-juin-2026.xlsx for v61 of 15/06/2026).

The ROME changes twice a year. At each version: download the new « Arborescence principale », update VERSION and
SHEET below (the sheet carries the date), run the script with the system Python (needs openpyxl, not Django), then
run the tests (`jobs/test_rome_titles.py`) and check the counts it prints against the previous ones.

jobs.rome_titles looks offer titles up in the file. Each form, marks dropped and folded as jobs.text does with
titles, maps to the ROME code of its fiche (letter + professional domain + fiche, « A1101 »). Forms leading to two
fiches, and forms too vague to place a job (« responsable », « chef de projet »), map to null: sentinels that make
the lookup give no code. The labels file keeps, written as people read them, the forms of a single fiche only: a
label picked on the search screen always gives its code back.
"""
import gzip
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs.text import fold, strip_marks  # noqa: E402  (jobs.text imports nothing from Django)

OUT = ROOT / "jobs" / "data" / "rome-titles.json.gz"
LABELS_OUT = ROOT / "jobs" / "data" / "rome-labels.json.gz"
SOURCE = ("ROME 4.0, Arborescence principale, France Travail, Licence Ouverte "
          "(https://www.data.gouv.fr/datasets/repertoire-operationnel-des-metiers-et-des-emplois-rome)")
VERSION = "v61, 15/06/2026"
SHEET = "Arbo Principale 15-06-2026"

# Forms too vague to say which job an offer is about, folded. They stay in the file as sentinels: a title whose
# longest match is one of them gets no code. The fiche a form has in v61, when the ROME has it, follows its line.
VAGUE = [
    "agent", "agente",
    "assistant", "assistante",
    "chef", "cheffe",
    "responsable",
    "directeur", "directrice",
    "conseiller", "conseillere",
    "technicien", "technicienne",
    "ingenieur", "ingenieure",
    "charge", "chargee",
    "gestionnaire",
    "consultant", "consultante",
    "operateur", "operatrice",
    "chef de projet", "cheffe de projet",  # M1828 (« Chef de projet digital » and longer forms stay known)
    "agent technique", "agente technique",  # A1203, from « Agent / Agente technique (espaces verts) »
    "designer",  # H1204 (industrial design), while offers mean UX, graphic or web design as often
    "electricien", "electricienne",  # I1309, while most offers are for buildings (F1602)
    "commercial", "commerciale",  # D1410
    "employe commercial", "employee commerciale",
    "ambassadeur", "ambassadrice",  # K1418 (diplomacy), while offers mean brand ambassadors
]


def _same_start(a, b):
    """Feminine of a masculine word: same folded start, three letters (the whole word when shorter)."""
    return fold(a)[:3] == fold(b)[:3]


def gender_forms(label):
    """Masculine and feminine forms of « A1 … An / B1 … Bm rest ». When B1 … Bn are the feminines of A1 … An
    (« Conducteur / Conductrice d’engins »): A1 … An + rest, and the right part. Otherwise, when B1 is the feminine
    of An alone (« Responsable régional / Régionale santé publique »): left + right without B1, and left without An
    + right. None when the rule cannot read the label."""
    if "/" not in label:
        return [label]
    if label.count("/") > 1:
        return None
    left, right = (part.split() for part in label.split("/"))
    if not left or not right:
        return None
    if [fold(word) for word in left[-len(right):]] == [fold(word) for word in right]:
        return [" ".join(left), " ".join(right)]  # « Homme sage-femme / Sage-femme »
    if len(right) >= len(left) and all(map(_same_start, left, right)):
        return [" ".join(left + right[len(left):]), " ".join(right)]
    if _same_start(left[-1], right[0]):
        return [" ".join(left + right[1:]), " ".join(left[:-1] + right)]
    return None


def variants(label):
    """(forms, note). note is None for a label read by the gender rule, "unreadable" for one it cannot read (kept
    whole), "parentheses" for one with parentheses: kept whole as well as through the rule, with and without the
    parentheses (« Accompagnant / Accompagnante des élèves en situation de handicap (AESH) » also gives
    « Accompagnant des élèves en situation de handicap »)."""
    forms = gender_forms(label)
    if "(" not in label:
        return (forms, None) if forms else ([label], "unreadable")
    bare = " ".join(re.sub(r"\([^)]*\)", " ", label).split())
    found = [label] + (forms or []) + (gender_forms(bare) or [])
    return list(dict.fromkeys(found)), "parentheses"


def key(form):
    """Folded form, marks dropped first, as jobs.rome_titles does with offer titles."""
    return fold(strip_marks(form))


def build(rows):
    """rows: (ROME code, label). Returns ({folded form: code, or None for an ambiguous or vague form}, report)."""
    codes = {}
    notes = {"parentheses": set(), "unreadable": set()}
    for code, label in rows:
        forms, note = variants(label)
        if note:
            notes[note].add(label)
        for form in forms:
            if key(form):
                codes.setdefault(key(form), set()).add(code)
    ambiguous = sorted(form for form, found in codes.items() if len(found) > 1)
    titles = {form: found.pop() if len(found) == 1 else None for form, found in codes.items()}
    titles.update(dict.fromkeys(VAGUE))
    report = {"ambiguous": ambiguous, "vague": sorted(form for form in VAGUE if form in codes)}
    report.update((note, sorted(labels)) for note, labels in notes.items())
    return dict(sorted(titles.items())), report


def display_labels(rows, titles):
    """{label as shown: ROME code} of every form whose folded key gives one code in `titles` (never a sentinel), with
    the typographic apostrophe. A whole label with its slash only when the gender rule reads nothing else of it."""
    labels = {}
    for code, label in rows:
        forms = [form for form in variants(label)[0] if titles.get(key(form)) == code]
        plain = [form for form in forms if "/" not in form]
        for form in plain or forms:
            labels.setdefault(form.replace("'", "’"), code)
    return dict(sorted(labels.items()))


def _write(path, data):
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    path.write_bytes(gzip.compress(raw, mtime=0))


def read_rows(path):
    """(ROME code, label) of every appellation: rows with the three codes and an OGR code."""
    import openpyxl

    sheet = openpyxl.load_workbook(path, read_only=True)[SHEET]
    for row in sheet.iter_rows(min_row=2, values_only=True):
        letter, domain, fiche, label, ogr = (str(cell).strip() if cell is not None else "" for cell in row[:5])
        if letter and domain and fiche and ogr:
            yield letter + domain + fiche, label


def main(path):
    rows = list(read_rows(path))
    titles, report = build(rows)
    header = {"source": SOURCE, "version": VERSION, "built_on": date.today().isoformat()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    _write(OUT, {**header, "titles": titles})
    labels = display_labels(rows, titles)
    _write(LABELS_OUT, {**header, "labels": labels})
    kept = sum(1 for code in titles.values() if code)
    print(f"{len(rows)} rows, {kept} forms with a code, {len(report['ambiguous'])} ambiguous, {len(VAGUE)} vague "
          f"({len(report['vague'])} of them in the ROME) -> {OUT} ({OUT.stat().st_size // 1024} Ko)")
    print(f"{len(labels)} labels to suggest -> {LABELS_OUT} ({LABELS_OUT.stat().st_size // 1024} Ko)")
    print("Vague forms in the ROME:", ", ".join(report["vague"]))
    print("Ambiguous:", ", ".join(report["ambiguous"]))
    print(f"{len(report['unreadable'])} labels the gender rule cannot read, kept whole:")
    for label in report["unreadable"]:
        print(f"  {label}")
    print(f"{len(report['parentheses'])} labels with parentheses, also kept whole:")
    for label in report["parentheses"]:
        print(f"  {label}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])

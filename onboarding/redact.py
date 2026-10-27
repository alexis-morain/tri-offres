"""Contact details removed from a CV's text before it is sent to Claude (the design plan, 4 and 8.2).

Minimisation, not anonymisation: names and employers stay (the instruction tells Claude never to repeat a name, and
the output schema has no field for one). Removed: e-mail addresses (masked "at" forms and spaces around the "@"
included), phone numbers (French and international), URLs and profiles, postal addresses (a 5-digit postal code
followed by a city or after it in brackets, the whole line when it also holds a street, and the line before it when
it holds a street), birth dates, French social security numbers (NIR).

Every pattern is bounded and none has a quantified group holding another quantifier: 60 000 hostile characters are
processed in well under half a second (tested).
"""
import re
import sys
import unicodedata

REMOVED = "[retiré]"
TAG_REMOVED = "[balise retirée]"

_SPACE = r"[ \t]"
_PHONE_SEP = r"[ .\-\u00a0\u202f]"

URL = re.compile(r"(?:https?://|www\.)[^\s<>\"]{1,2048}", re.IGNORECASE)
LINKEDIN = re.compile(r"[\w.\-]{0,63}linkedin\.com/[^\s<>\"]{0,512}", re.IGNORECASE)
EMAIL = re.compile(r"(?<![\w.%+\-])[\w.%+\-]{1,64}[ \t]{0,3}@[ \t]{0,3}[\w\-]{1,63}\.[\w.\-]{1,253}")
# "nora [at] gmail [dot] com", "nora (at) gmail.com", "nora (arobase) free (point) fr".
EMAIL_BRACKETS = re.compile(
    rf"(?<![\w.%+\-])[\w.%+\-]{{1,64}}{_SPACE}{{0,3}}[\[(]{_SPACE}{{0,2}}(?:at|arobase|chez){_SPACE}{{0,2}}[\])]"
    rf"{_SPACE}{{0,3}}[\w\-]{{1,63}}"
    rf"(?:{_SPACE}{{0,3}}[\[(]{_SPACE}{{0,2}}(?:dot|point){_SPACE}{{0,2}}[\])]{_SPACE}{{0,3}}|\.)[\w.\-]{{1,24}}",
    re.IGNORECASE)
# "nora at gmail dot com", "nora AT gmail.com": a domain ending is required, so "work at scale" stays.
EMAIL_WORDS = re.compile(
    rf"(?<![\w.%+\-])[\w.%+\-]{{1,64}}{_SPACE}{{1,3}}(?:at|arobase){_SPACE}{{1,3}}[\w\-]{{1,63}}"
    rf"(?:{_SPACE}{{1,3}}(?:dot|point){_SPACE}{{1,3}}|\.)[a-z]{{2,24}}\b",
    re.IGNORECASE)
_DATE = r"\d{1,2}(?:er)?[ ./\-]{1,3}(?:\d{1,2}|[^\W\d_]{3,9}\.?)[ ./\-]{1,3}\d{2,4}"
BIRTH_DATE = re.compile(
    rf"(?:\bn[ée]e?(?:\(e\))?{_SPACE}{{1,3}}le|\bdate{_SPACE}{{1,3}}de{_SPACE}{{1,3}}naissance|\bnaissance)"
    rf"[ \t:]{{0,5}}{_DATE}",
    re.IGNORECASE)
# French social security number: sex, year, month, department (2A and 2B included), commune, order, optional key.
NIR = re.compile(r"(?<!\w)[12][ .]?\d{2}[ .]?\d{2}[ .]?(?:\d{2}|2[ab])[ .]?\d{3}[ .]?\d{3}(?:[ .]\d\d|\d\d)?(?!\w)",
                 re.IGNORECASE)
# "+33 6 12 34 56 78", "+33 (0)6 12 34 56 78", "0033 6 ...", "+1 (555) 123-4567". After "00" comes a country code,
# which never starts with 0: "30 000 - 40 000 €" stays.
PHONE_INTERNATIONAL = re.compile(r"(?<![\w+])(?:\+|00(?=[1-9]))\d{1,3}[ .\-()\u00a0\u202f\d]{6,20}\d")
# "06 12 34 56 78", "06.12.34.56.78", "0612345678": unrolled, so no quantified group.
PHONE_FRENCH = re.compile(
    rf"(?<![\w+])0[1-9]{_PHONE_SEP}?\d\d{_PHONE_SEP}?\d\d{_PHONE_SEP}?\d\d{_PHONE_SEP}?\d\d(?!\d)")
# A postal code followed by a capitalised city, and the rest of the city's words on that line.
POSTAL_CODE_CITY = re.compile(
    r"(?<!\d)\d{5}[ \t\u00a0]{1,3}[A-Z\u00c0-\u00d6\u00d8-\u00de](?:[^\W\d_]|[ '\u2019\-]){0,60}")
# A city followed by its postal code in brackets: "Exempleville (99000)", "Saint-Étienne (42000)".
# One word, hyphens allowed.
CITY_POSTAL_CODE = re.compile(r"[A-Z\u00c0-\u00d6\u00d8-\u00de](?:[^\W\d_]|[\-'\u2019]){0,40}[ \t\u00a0]{0,3}\(\d{5}\)")
# A line starting with a street number: "12 rue des Essais", "3 bis, avenue Jean Jaurès".
STREET_LINE = re.compile(r"[ \t]{0,20}\d{1,4}[ \t,]{0,3}(?:bis|ter|b|t)?[ \t,]{1,3}[^\W\d_]", re.IGNORECASE)
# A street number and a street type anywhere in a line: "Appartement 4, 12 rue des Essais".
STREET_ANYWHERE = re.compile(
    r"(?<!\d)\d{1,4}[ \t,]{0,3}(?:bis|ter|b|t)?[ \t,]{1,3}(?:rue|avenue|av|boulevard|bd|chemin|all[ée]e|impasse|"
    r"place|quai|route|cours|square|passage|voie|chauss[ée]e|faubourg|sentier|villa|hameau|cit[ée]|esplanade|"
    r"promenade|mont[ée]e|traverse|r[ée]sidence|lotissement|lieu-dit|parvis|rond-point)\b",
    re.IGNORECASE)


def tag_pattern(*names):
    """A pattern for anything that could open or close a data block of a prompt named `names`, once the text is
    normalized: <cv>, </cv>, "< /CV >", "<cv lang=...>", "&lt;/cv&gt;". Possessive quantifiers: any amount of
    whitespace, no backtracking."""
    return re.compile(r"(?:<|&lt;|&#0*60;|&#x0*3c;)\s*+/?\s*+(?:" + "|".join(map(re.escape, names)) + r")\b",
                      re.IGNORECASE)


CV_TAG = tag_pattern("cv")
# Invisible characters that could split a tag or a contact detail, removed before anything is looked for: the whole
# Unicode category Cf (zero-width characters, soft hyphen, invisible separators, bidi marks, tags...) and U+034F, the
# combining grapheme joiner (category Mn, but shows nothing). About 170 code points, listed once at import.
INVISIBLE = dict.fromkeys([*(code for code in range(sys.maxunicode + 1) if unicodedata.category(chr(code)) == "Cf"),
                           0x034F])

# Order matters: URLs before e-mail addresses (a URL can hold an "@"), the NIR before phone numbers.
SUBSTITUTIONS = (URL, LINKEDIN, EMAIL, EMAIL_BRACKETS, EMAIL_WORDS, BIRTH_DATE, NIR, PHONE_INTERNATIONAL,
                 PHONE_FRENCH)
PATTERNS = (*SUBSTITUTIONS, POSTAL_CODE_CITY, CITY_POSTAL_CODE, STREET_LINE, STREET_ANYWHERE, CV_TAG)


def _has_street(line):
    return bool(STREET_LINE.match(line) or STREET_ANYWHERE.search(line))


def _postal_addresses(text):
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if not (POSTAL_CODE_CITY.search(line) or CITY_POSTAL_CODE.search(line)):
            continue
        if _has_street(line):
            lines[index] = REMOVED  # street, postal code and city on one line
        else:
            lines[index] = CITY_POSTAL_CODE.sub(REMOVED, POSTAL_CODE_CITY.sub(REMOVED, line))
        if index and _has_street(lines[index - 1]):
            lines[index - 1] = REMOVED
    return "\n".join(lines)


def strip_invisible(text):
    """`text` without the INVISIBLE characters, otherwise as written."""
    return text.translate(INVISIBLE)


def normalize(text):
    """`text` without invisible characters, in NFKC form: look-alikes such as a fullwidth "\uff1c" become plain ones."""
    return unicodedata.normalize("NFKC", strip_invisible(text))


def redact(text):
    """`text` with contact details replaced by [retiré]. Pure function."""
    text = _postal_addresses(text)
    for pattern in SUBSTITUTIONS:
        text = pattern.sub(REMOVED, text)
    return text


def neutralize_tags(text, pattern=CV_TAG):
    """`text`, normalized, with every look-alike of the tags of `pattern` (by default <cv> and </cv>) defused: the
    text cannot close its data block, nor open another."""
    return pattern.sub(TAG_REMOVED, normalize(text))

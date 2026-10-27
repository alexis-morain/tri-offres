"""Checks of a text written by Claude before it is stored and shown: the offer summary (5.2) and the reason of a score
(5.3). Contract of step 5, section 7, E14 (threats M2 and M3: an offer that makes Claude copy a link or contact
details into a text sent by email under our name).

A refused text is left blank, never repaired: removing a link would keep the rest of a text written under an
offer's instructions.
"""
import re
import unicodedata

from onboarding import redact

# Contact details, with the patterns used on CVs.
CONTACTS = (redact.URL, redact.LINKEDIN, redact.EMAIL, redact.EMAIL_BRACKETS, redact.EMAIL_WORDS,
            redact.PHONE_INTERNATIONAL, redact.PHONE_FRENCH)
# A bare domain, with or without a path: « exemple.fr/postuler », « site.com », « EXEMPLE.FR » (decision Q24).
# Looked for in the folded text, so « exemplė.fr » or « œuvre.fr » count too. The ending must be a real one: any two
# letters but js and ts (« Chart.js », « main.ts »), or a generic ending of this short list.
GENERIC_ENDINGS = (
    "com", "net", "org", "info", "biz", "io", "co", "app", "dev", "ai", "me", "tv", "eu", "xyz", "online", "site",
    "tech", "work", "jobs", "careers", "immo", "paris", "bzh", "alsace", "corsica", "store", "shop", "cloud",
    "digital", "agency", "studio", "page", "link", "club", "pro", "asso",
)
BARE_DOMAIN = re.compile(
    r"(?<![\w.\-])[a-z0-9\-]{1,63}(?:\.[a-z0-9\-]{1,63}){0,4}"
    r"\.(?:(?!js|ts)[a-z]{2}|" + "|".join(GENERIC_ENDINGS) + r")(?![\w\-])",
    re.IGNORECASE)
# A name shaped like a domain followed by a path, whatever its ending: « node.js/postuler » reads as a link.
NAME_WITH_PATH = re.compile(r"(?<![\w.\-])[a-z0-9\-]{1,63}(?:\.[a-z0-9\-]{1,63}){0,4}\.[a-z]{2,10}/", re.IGNORECASE)
# Names shaped like a domain and kept when no « / » follows, compared without case: technical names common in job
# offers, and a word followed by a suffix of inclusive writing (« technicien.ne », « conseiller.ère »). A few of
# these suffixes are also country endings (.es, .ne, .se, .ve): a bare « exemple.es » passes, an accepted residual
# risk.
TECHNICAL_NAMES = frozenset({
    "node.js", "vue.js", "next.js", "nuxt.js", "react.js", "d3.js", "three.js", "express.js", "socket.io",
    "asp.net", "vb.net", "ado.net",
})
INCLUSIVE_SUFFIXES = frozenset({
    "e", "es", "ne", "nes", "se", "ses", "rice", "rices", "trice", "trices", "ere", "eres", "le", "les", "te", "tes",
    "ve", "ves", "fe", "fes",
})
# A phone number whatever its separators: a French or international start (0, 33, +33, 0033), then nine digits each
# after at most three characters that are neither letters nor digits (« (06) 12 34 », « 06·12 », « 06×12 »).
# redact's patterns cover the plain forms.
PHONE = re.compile(r"(?<!\d)(?:\+?33|0033|0)[\W_]{0,3}[1-9](?:[\W_]{0,3}\d){8}(?!\d)")
# A date with a four-digit year, or a clock time, standing alone: masked before PHONE, so « 01/12/2026 - 31/05/2027 »
# and « 08:30-12:00 » are not read as ten digits. A phone written in pairs never holds four digits in a row, and a
# time glued to another « :dd » (« 06:12:34 ») is not masked.
DATE_OR_TIME = re.compile(r"(?<!\d)(?<!\d[./:])(?:[0-3]?\d[./][01]?\d[./](?:19|20)\d{2}|[0-2]?\d:[0-5]\d)(?![./:]?\d)")
# A tag, plain or disguised as an HTML entity (look-alikes are folded first).
TAG = re.compile(r"[<>]|&(?:lt|gt);|&#0*6[02];|&#x0*3[ce];", re.IGNORECASE)
ELLIPSIS = "…"
# The end of a sentence: a full stop, « ! » or « ? » before a space or the end (« 2.5 » goes on).
SENTENCE_END = re.compile(r"[.!?](?= |$)")
# With the letters below, ASCII digits and the space, the only characters allowed: an allowlist, not a list of
# look-alikes to refuse. Any other script, letter, emoji, filler, combining mark or control character refuses the
# text.
PUNCTUATION = frozenset(
    ".,;:!?'’‘\"“”«»()[]-‐‑–/&%€$£+=°*#…•·"
    # Numbers and units: m², 10³, 3 ans ½, ≈ 1 900 €, 50 ± 5, 24 × 36, § 3, 2 ‰, 9h~17h; « 1ᵉʳ », « 2ᵈ ». The
    # superscript letters fold to plain ones (NFKC) before the contact checks; × folds to nothing that a mail client
    # links.
    "²³½¼¾≈±×§‰~→✓ᵉʳᵈ"
    # Marks, comparisons, ordinals (nº, 1ª) and the bar: « Logiciel™ », « ≥ 3 ans ».
    "®™≥≤ºª|"
)
# Letters of French and its neighbours. The rest of extended Latin is refused: ǀ, ı, ƒ, ł... read as l, i or f.
LETTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "àâäáãåçéèêëíìîïñóòôöõúùûüýÿæœÀÂÄÁÃÅÇÉÈÊËÍÌÎÏÑÓÒÔÖÕÚÙÛÜÝŸÆŒß"
    "ŠšŽž"
)
ALLOWED = LETTERS | PUNCTUATION | set("0123456789 ")
# Ligatures spelt out in the folded text, so « œuvre.fr » is checked as « oeuvre.fr ».
LIGATURES = str.maketrans({"æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ß": "ss"})
LINE_BREAKS = "\t\n\v\f\r\u0085\u2028\u2029"


def no_em_dash(text):
    """`text` with each em dash, and the spaces around it, turned into « , »."""
    return re.sub(r"\s*\u2014\s*", ", ", text).strip()


def _folded(text):
    """NFKC, then accents dropped and ligatures spelt out, case kept: « exemplė.fr » is checked as « exemple.fr »."""
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text)).translate(LIGATURES)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _kept_domain(text, match):
    """A name shaped like a domain that is not one: a technical name or an inclusive suffix, no « / » after."""
    if text.startswith("/", match.end()):
        return False
    name = match.group().lower()
    suffix = name.partition(".")[2]
    return name in TECHNICAL_NAMES or ("." not in suffix and suffix in INCLUSIVE_SUFFIXES)


def _refused(text):
    if not re.search(r"\w", text) or not set(text) <= ALLOWED:
        return True
    text = _folded(text)
    if TAG.search(text) or PHONE.search(DATE_OR_TIME.sub("D", text)) or NAME_WITH_PATH.search(text):
        return True
    if any(pattern.search(text) for pattern in CONTACTS):
        return True
    return any(not _kept_domain(text, match) for match in BARE_DOMAIN.finditer(text))


def _spaces(text):
    """Every Unicode space and line break turned into one ASCII space, other characters left for the allowlist."""
    text = "".join(" " if char in LINE_BREAKS or unicodedata.category(char) == "Zs" else char for char in text)
    return re.sub(" +", " ", text).strip(" ")


def clean_text(text, limit, sentences=False):
    """`text` without invisible characters, its spaces normalized and em dashes replaced by commas, cut at its last
    space before `limit` characters (ellipsis included); with `sentences`, cut after its last whole sentence instead,
    and blank when not one fits. Blank when refused: a character outside the allowlist, no letter nor digit, or,
    once folded, a link, a bare domain, a LinkedIn profile, an email address, a phone number or a tag."""
    # NFC first: an accent sent as a combining mark becomes the allowed letter; a mark left over is refused.
    text = _spaces(no_em_dash(_spaces(unicodedata.normalize("NFC", redact.strip_invisible(text)))))
    if _refused(text):
        return ""
    if len(text) <= limit:
        return text
    if sentences:
        ends = [match.end() for match in SENTENCE_END.finditer(text) if match.end() <= limit]
        return text[:ends[-1]] if ends else ""
    cut = text[:limit - len(ELLIPSIS)]
    if " " in cut:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,;:") + ELLIPSIS

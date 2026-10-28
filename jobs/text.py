"""Text folding shared by the filters, the match keys and the reference lists, free of Django imports."""
import re
import unicodedata

# Words that tell a job apart (job_words): the closeness of matching/selection.py, the check of Claude's ROME code
# in jobs/rome_titles.py.
MIN_WORD_LETTERS = 4
# Folded words of 4 letters or more that say nothing of a job: grammar words, the words of any offer or search, and
# words of place (the place is filtered already).
STOP_WORDS = frozenset("""
    dans pour avec sans sous vers chez entre depuis pendant selon avant apres parmi contre
    plus moins tres aussi bien mais donc comme ainsi alors encore toujours jamais deja
    leur leurs notre votre nous vous elle elles cette ceux celle celles celui dont quel quelle quels quelles
    tout tous toute toutes autre autres meme memes chaque plusieurs certain certains
    etre avoir fait faire sont sera peut veux voudrais souhaite cherche recherche trouver
    poste postes emploi emplois travail travailler metier metiers offre offres mission missions
    equipe equipes entreprise entreprises structure structures temps plein partiel contrat
    lieu lieux ville villes region regions pres proche proches autour alentour alentours france
""".split())


def fold(text):
    """Lowercase, accents dropped, every run of punctuation or space turned into one space."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(char for char in text if not unicodedata.combining(char)).lower()
    return re.sub(r"[\W_]+", " ", text).strip()


# Feminine and plural endings written after a masculine word: « Chargé(e) », « conseiller(ère) », « vendeur/euse ».
_ENDING = r"(?:e|es|ère|ères|ere|eres|euse|euses|rice|rices|trice|trices|ne|nes)"
_MARKS = re.compile(
    # right after a letter: « (e) », « ·e », « .e », « -e », « /euse », and their repeats (« ·e·s »)
    rf"(?<=[^\W\d_])(?:\({_ENDING}\)|(?:[·.\-/]{_ENDING})+(?![^\W\d_]))"
    # « H/F », « (F/H) », « H/F/X », « HF » after some other text
    r"|(?<=\S)\s*[-–]?\s*\(?\b(?:[HF]\s*/\s*[HF](?:\s*/\s*X)?|HF|FH)\b\)?",
    re.IGNORECASE,
)


def strip_marks(text):
    """The text without its gender and inclusive writing marks. A mark must follow a letter (or, for « H/F », some
    text): « e-commerce » and a title made of « H » alone stay as they are."""
    return _MARKS.sub("", text or "")


# Feminine endings read as the masculine: « infirmière » meets « infirmier » (plan of 28/10, chantier 2).
_FEMININE = re.compile(r"(?:(ere)|(euse)|(trice)|(ienne))$")
_MASCULINE = {1: "er", 2: "eur", 3: "teur", 4: "ien"}


def job_words(text):
    """Folded words of `text` that may tell a job apart: gender marks dropped, 4 letters at least, not in
    STOP_WORDS, a final s or x dropped so a plural meets its singular, a feminine ending read as the masculine."""
    words = set()
    for word in fold(strip_marks(text)).split():
        if len(word) >= MIN_WORD_LETTERS and word.isalpha() and word not in STOP_WORDS:
            word = word[:-1] if word[-1] in "sx" else word
            words.add(_FEMININE.sub(lambda match: _MASCULINE[match.lastindex], word))
    return words


def fold_title(text):
    """A title as the keywords filter reads it (JobOffer.title_folded): gender marks dropped, then folded, so
    « Chargé·e (H/F) » reads « charge »."""
    return fold(strip_marks(text))

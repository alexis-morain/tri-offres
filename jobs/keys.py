"""Match key between sources: the same offer posted on two boards gets the same key (no merge before step 5)."""
from jobs.models import JobOffer
from jobs.text import fold


def match_key(title, employer, place):
    """Folded title, employer and place joined by `|`; `place` is the INSEE code when known, else the city.
    Empty when the title folds to nothing."""
    folded_title = fold(title)
    if not folded_title:
        return ""
    return "|".join((folded_title, fold(employer), fold(place)))[:JobOffer._meta.get_field("match_key").max_length]

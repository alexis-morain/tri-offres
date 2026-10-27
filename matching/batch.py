"""Shared pieces of the batch prompts of step 5: the pre-pass (jobs/summaries.py) and the scoring
(matching/scoring.py). Contract: the design plan, section 7, E12 and E13.

An offer is a third party's data: every field is defused against the tags of its prompt. A batch names its offers
by local ids from 1 to n, never a primary key; the schema bounds them and answered() checks them again.
"""
import itertools

from jobs.models import JobOffer
from onboarding.redact import neutralize_tags


def batches(items, size):
    """`items` (a queryset or a list) in lists of `size`."""
    iterator = iter(items)
    while batch := list(itertools.islice(iterator, size)):
        yield batch


def defused_line(text, pattern):
    """A one-line field (title, employer...), its spaces normalized, defused against `pattern`."""
    return neutralize_tags(" ".join((text or "").split()), pattern)


def offer_header(offer, pattern):
    """Title, employer, place and contract of `offer`, one line each, defused; no line for a missing field."""
    place = [offer.city, offer.department]
    if offer.remote_type == JobOffer.Remote.FULL:
        place.append("télétravail complet")
    fields = (("Titre", offer.title), ("Employeur", offer.employer),
              ("Lieu", ", ".join(part for part in place if part)),
              ("Contrat", offer.contract or offer.get_contract_type_display()))
    lines = []
    for label, text in fields:
        if value := defused_line(text, pattern):
            lines.append(f"{label} : {value}")
    return "\n".join(lines)


def batch_schema(key, size, item_properties):
    """{key: [items]}: at most `size` items, each with an `id` in [1, size] and `item_properties`, all required."""
    return {
        "type": "object",
        "properties": {
            key: {
                "type": "array",
                "maxItems": size,
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "integer", "minimum": 1, "maximum": size}, **item_properties},
                    "required": ["id", *item_properties],
                    "additionalProperties": False,
                },
            },
        },
        "required": [key],
        "additionalProperties": False,
    }


def answered(items, size):
    """(entries, missing): the entries whose id is an integer in [1, size], the first one only per id, in their
    order; and the ids from 1 to size that no entry holds."""
    entries = []
    seen = set()
    for item in items:
        index = item["id"]
        # type() and not isinstance(): True is an int to Python.
        if type(index) is not int or not 1 <= index <= size or index in seen:
            continue
        seen.add(index)
        entries.append(item)
    return entries, [index for index in range(1, size + 1) if index not in seen]

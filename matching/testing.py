"""Test helpers of the night (matching/night.py): fake answers sized on the batch, and a response that fails on
demand. Texts are fictitious."""
import re
import threading

import llm

SUMMARY = "Production d’un festival de théâtre. CDI à Nantes, temps plein."
REASON = "Vous retrouvez la régie de festival que vous cherchez, en CDI à Nantes."
USAGE = llm.Usage(model=llm.HAIKU, input_tokens=100, output_tokens=10, cache_read_input_tokens=1,
                  cache_creation_input_tokens=2)


def size(call):
    """Number of offers in a batch call."""
    return len(re.findall(r'<offre id="\d+">', call.user_content))


def summaries_answer(call):
    return {"offres": [{"id": index, "summary": SUMMARY, "appellation": "", "rome_code": ""}
                       for index in range(1, size(call) + 1)]}


def scores_answer(call):
    return {"notes": [{"id": index, "score": 7, "reason": REASON, "exploration": False}
                      for index in range(1, size(call) + 1)]}


class Failing:
    """A fake response: raises `error` for the calls `when(call, attempt)` picks, answers with `answer` otherwise.
    `attempt` counts the calls seen so far for the same batch size and user (1 for the first)."""

    def __init__(self, answer, error, when):
        self.answer, self.error, self.when = answer, error, when
        self.seen = {}
        self.lock = threading.Lock()

    def __call__(self, call):
        key = (size(call), getattr(call.user, "email", None))
        with self.lock:
            self.seen[key] = self.seen.get(key, 0) + 1
            attempt = self.seen[key]
        if self.when(call, attempt):
            raise self.error
        return self.answer(call)

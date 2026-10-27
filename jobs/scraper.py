"""The actor run locally, in a subprocess of the collection (the design plan, section 3).

Off the platform, the actor's SDK reads its input and writes its dataset, its registry of offers seen and RUN_STATS
in a local storage folder (APIFY_LOCAL_STORAGE_DIR, the SDK's own variable): SCRAPER_STORAGE, kept between nights
since the registry of the "new-only" mode lives there. Each run starts from a cleared default dataset and default
key-value store; the named stores (the registry) stay. The subprocess gets a filtered environment: never the Max
token nor a key of the .env. Its output goes to a log file of the storage, cut, never to the report nor to a Django
log (data of third-party sites). No database write in this module.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

from django.conf import settings

from jobs.actor_stats import (RUN_STATS_COUNTS, RUN_STATS_MAX_COUNT, RUN_STATS_MAX_SECONDS,
                               RUN_STATS_VERSION)
from supervision.redaction import redact_text

REGISTRY_KEY = "tri-offres"  # fixed: derived from the input, cutting a source would make every offer new again
MODES = ("new-only", "all")
# The statistics by source the actor writes at the end of its run (spec 4.7, section 1); format in actor_stats.
RUN_STATS_KEY = "RUN_STATS"
MAX_STATS_BYTES = 1024 * 1024
MAX_ITEM_BYTES = 5 * 1024 * 1024
MAX_LOG_BYTES = 1024 * 1024  # the end of the log is kept
LOG_NAME = "actor.log"
# Variables of the collection's environment the actor may see; the storage and the proxy are set apart.
ENVIRONMENT = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
_SOURCE_KEY = re.compile(r"[a-z0-9_]{1,50}")


class ScraperError(Exception):
    """The actor could not be run or its output read. Messages are in French: they go to the operator, in the
    report of the collection command."""


class Run(NamedTuple):
    status: str  # SUCCEEDED, FAILED or TIMED-OUT
    exit_code: int | None  # None when the run was killed at its timeout
    duration_s: float


def build_input(sources, posted_within_days=None, max_results=None, mode="new-only"):
    """The actor's input for these source keys. Night runs keep the defaults; the measurement run of 4.7 passes
    posted_within_days, max_results and mode="all".

    No contractTypes: the actor would then drop offers without a contract type or with AUTRE, which must pass
    (plan of step 4, decision 13). The import of 4.4 drops VIE and VIA itself.
    """
    sources = list(dict.fromkeys(sources))
    # Without sources the actor reads all of them, African and Canadian ones included.
    if not sources or not all(isinstance(key, str) and key for key in sources):
        raise ValueError("At least one actor source key is needed.")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}.")
    actor_input = {
        "sources": sources, "countries": ["FR"], "languages": ["fr"], "mode": mode, "registryKey": REGISTRY_KEY,
        "includeDescription": True,
        "maxResultsPerSource": settings.SCRAPER_MAX_RESULTS_PER_SOURCE if max_results is None else max_results,
        "maxMinutesPerSource": settings.SCRAPER_MAX_MINUTES_PER_SOURCE,
    }
    if posted_within_days is not None:
        actor_input["postedWithinDays"] = posted_within_days
    return actor_input


def storage():
    return Path(settings.SCRAPER_STORAGE)


def dataset_dir():
    return storage() / "datasets" / "default"


def _default_store():
    return storage() / "key_value_stores" / "default"


def environment():
    """The actor's environment: the variables of ENVIRONMENT, the storage, the proxy when SCRAPER_PROXY_URL is set."""
    env = {name: os.environ[name] for name in ENVIRONMENT if name in os.environ}
    env["APIFY_LOCAL_STORAGE_DIR"] = str(storage())
    proxy = settings.SCRAPER_PROXY_URL.strip()
    if proxy:
        env["HTTPS_PROXY"] = env["HTTP_PROXY"] = proxy  # httpx follows them, the actor has nothing to change
    return env


def require_installed():
    """ScraperError unless the actor's code and its interpreter are there: the image is built without them when
    vendor/actor is missing (scripts/fetch_actor.sh has not been run)."""
    script = Path(settings.SCRAPER_DIR) / "src" / "__main__.py"
    if not script.is_file() or not Path(settings.SCRAPER_PYTHON).exists():
        raise ScraperError("Le collecteur d’offres (actor) n’est pas installé : lancez scripts/fetch_actor.sh puis "
                           "reconstruisez l’image.")


def run(actor_input, timeout_s=None, clock=time.monotonic):
    """Run the actor on this input and wait for it, SCRAPER_TIMEOUT_S at most (then it is killed: TIMED-OUT).
    ScraperError when it cannot be started."""
    timeout_s = settings.SCRAPER_TIMEOUT_S if timeout_s is None else timeout_s
    # The previous night's dataset and RUN_STATS go first: a run that dies early must not be read as this one.
    shutil.rmtree(dataset_dir(), ignore_errors=True)
    shutil.rmtree(_default_store(), ignore_errors=True)
    _default_store().mkdir(parents=True)
    (_default_store() / "INPUT.json").write_text(json.dumps(actor_input))
    log_path = storage() / LOG_NAME
    started = clock()
    run, failure = None, None
    # The failure is raised after its except block, so no exception stays chained to it.
    with open(log_path, "wb") as log:
        try:
            code = subprocess.run([settings.SCRAPER_PYTHON, "-m", "src"], cwd=settings.SCRAPER_DIR,
                                  env=environment(), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=timeout_s).returncode
            run = Run("SUCCEEDED" if code == 0 else "FAILED", code, clock() - started)
        except subprocess.TimeoutExpired:  # subprocess.run has killed it
            run = Run("TIMED-OUT", None, clock() - started)
        except OSError as error:  # interpreter or folder missing
            failure = (f"Actor impossible à lancer ({error.__class__.__name__}) : voir SCRAPER_PYTHON et SCRAPER_DIR, "
                       "puis scripts/fetch_actor.sh.")
    _cut_log(log_path)
    if failure:
        raise ScraperError(failure)
    return run


def _cut_log(path):
    """Keep the last MAX_LOG_BYTES of the log, with the proxy credentials and other secrets masked (S18): the actor
    prints what it likes, and the file stays on the volume."""
    try:
        with open(path, "rb") as log:
            log.seek(0, os.SEEK_END)
            cut = log.tell() > MAX_LOG_BYTES
            log.seek(max(0, log.tell() - MAX_LOG_BYTES))
            tail = log.read()
        text = tail.decode("utf-8", errors="replace")
        if cut:
            # The cut may fall inside a secret, whose remainder no pattern would know: drop the partial first line.
            text = text.partition("\n")[2]
        path.write_text(redact_text(text), encoding="utf-8")
    except OSError:
        pass


def iter_items():
    """The items of the default dataset, in their order. Files that do not hold an object are skipped and counted
    in `.skipped`, unreadable ones (not JSON, over MAX_ITEM_BYTES) in `.unreadable`."""
    return DatasetItems(dataset_dir())


class DatasetItems:
    """Iterator over the dicts of the dataset; `skipped` and `unreadable` are complete once it is exhausted."""

    def __init__(self, folder):
        self.skipped = 0
        self.unreadable = 0
        self._items = self._read(folder)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._items)

    def _read(self, folder):
        # The SDK names items 000000001.json and up; its __metadata__.json is not one.
        paths = sorted(path for path in folder.glob("*.json") if path.stem.isdigit()) if folder.is_dir() else []
        for path in paths:
            try:
                with open(path, "rb") as file:
                    raw = file.read(MAX_ITEM_BYTES + 1)
                if len(raw) > MAX_ITEM_BYTES:
                    raise ValueError
                item = json.loads(raw)
            except (OSError, ValueError):  # UnicodeDecodeError included
                self.unreadable += 1
                continue
            if isinstance(item, dict):
                yield item
            else:
                self.skipped += 1


def run_stats():
    """The actor's statistics by source, from the RUN_STATS record of the default key-value store:
    {"version": 1, "sources": {key: {pushed, filtered, dupes, cross_dupes, already_seen, invalid: int,
    failed: str | None, timed_out: bool, seconds: float | None}}}, only these fields kept. None when the record
    does not exist or has not exactly this shape, numbers within bounds; ScraperError when it cannot be read (not
    JSON, over MAX_STATS_BYTES). The record is written by the actor: checked field by field, without any
    exception."""
    path = _default_store() / RUN_STATS_KEY
    try:
        with open(path, "rb") as file:
            raw = file.read(MAX_STATS_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ScraperError(f"{RUN_STATS_KEY} illisible ({error.__class__.__name__}).") from None
    if len(raw) > MAX_STATS_BYTES:
        raise ScraperError(f"{RUN_STATS_KEY} trop long (plus de {MAX_STATS_BYTES} octets).")
    # Raised after the except block: a JSON error keeps the whole document, third-party text, in its context.
    record = failure = None
    try:
        record = json.loads(raw)
    except ValueError:  # UnicodeDecodeError included
        failure = f"{RUN_STATS_KEY} n’est pas du JSON."
    if failure:
        raise ScraperError(failure)
    return _run_stats(record)


def _run_stats(record):
    """The record checked and copied field by field, None at the first value of an unexpected type or out of its
    bounds. Types are checked before any comparison, bounds before any conversion: nothing here can raise."""
    if not isinstance(record, dict) or type(record.get("version")) is not int \
            or record["version"] != RUN_STATS_VERSION or not isinstance(record.get("sources"), dict):
        return None
    sources = {}
    for key, values in record["sources"].items():
        if not isinstance(key, str) or not _SOURCE_KEY.fullmatch(key) or not isinstance(values, dict):
            return None
        line = {name: values.get(name) for name in RUN_STATS_COUNTS}
        if not all(type(value) is int and 0 <= value < RUN_STATS_MAX_COUNT for value in line.values()):
            return None
        # A missing "failed" or "seconds" gets a default of a refused type: the field must be there, null or not.
        failed, timed_out, seconds = values.get("failed", 0), values.get("timed_out"), values.get("seconds", "")
        if not (failed is None or type(failed) is str) or type(timed_out) is not bool:
            return None
        if seconds is not None:
            # NaN and infinities fail the comparison; an int is bounded before float() sees it.
            if type(seconds) not in (int, float) or not 0 <= seconds < RUN_STATS_MAX_SECONDS:
                return None
            seconds = float(seconds)
        sources[key] = {**line, "failed": failed, "timed_out": timed_out, "seconds": seconds}
    return {"version": RUN_STATS_VERSION, "sources": sources}

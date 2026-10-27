"""The account journal (arbitrage B of the security review of 21/10, the design plan).

A backup brings back what was deleted or unsubscribed after it was taken. So every deletion, unsubscription and
re-subscription also leaves one line in a text file outside the database, which Litestream does not replicate: it
must survive a restore of the database, not the loss of the disk. `manage.py rejouer_journal` applies again the lines
that follow a restored backup.

A line is `<UTC stamp> <kind> <fingerprint>`. The fingerprint is a keyed HMAC-SHA256 of the lowercase address, never
the address: the file alone does not give the addresses back by a dictionary. The key derives from SECRET_KEY, so a
rotation of the key makes the older lines unusable (SECRET_KEY_FALLBACKS is not tried); replay before rotating, or
keep the lines unusable on purpose. Lines are kept BACKUP_DAYS + 1 days, as long as a deleted account can live on in
a backup, and purged by `manage.py purge_journal` in the night.
"""
import fcntl
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.crypto import salted_hmac

from accounts.models import normalize_email
from config.backups import BACKUP_DAYS

logger = logging.getLogger(__name__)

DELETION = "suppression"
UNSUBSCRIPTION = "desinscription"
RESUBSCRIPTION = "reabonnement"
KINDS = (DELETION, UNSUBSCRIPTION, RESUBSCRIPTION)

KEEP = timedelta(days=BACKUP_DAYS + 1)
SALT = "tri-offres.account.journal"
STAMP = "%Y-%m-%dT%H:%M:%SZ"
LINE = re.compile(rf"^(\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}}:\d{{2}}Z) ({'|'.join(KINDS)}) ([0-9a-f]{{64}})$")
PRIVATE = 0o600


@dataclass(frozen=True)
class Entry:
    at: datetime  # aware, UTC
    kind: str
    fingerprint: str


def fingerprint(address):
    """Keyed hash of the normalized address, the only trace of it in the journal."""
    return salted_hmac(SALT, normalize_email(address), algorithm="sha256").hexdigest()


def _path():
    return Path(settings.ACCOUNT_JOURNAL_PATH)


@contextmanager
def _locked():
    """Appends and the purge's rewrite never overlap. The lock is a file of its own: the purge replaces the journal,
    and a lock on the replaced file would guard nothing. Closing the descriptor releases it."""
    path = _path()
    descriptor = os.open(path.with_name(path.name + ".lock"), os.O_WRONLY | os.O_CREAT, PRIVATE)
    try:
        os.fchmod(descriptor, PRIVATE)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def record(kind, key, address, now=None):
    """Append one line for `address`. Never raises: a journal that cannot be written must not undo a deletion or an
    unsubscription (the person's request is honored first). The failure is logged by its class and the account key,
    never by the address, the fingerprint or the path."""
    try:
        line = f"{(now or timezone.now()).astimezone(dt_timezone.utc).strftime(STAMP)} {kind} {fingerprint(address)}\n"
        with _locked():
            descriptor = os.open(_path(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, PRIVATE)
            try:
                os.fchmod(descriptor, PRIVATE)
                os.write(descriptor, line.encode("ascii"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except Exception as error:
        logger.error("Account journal: the %s line of account %s was not written (%s).", kind, key,
                     type(error).__name__)


def _parse(line):
    match = LINE.match(line)
    if match is None:
        return None
    return Entry(datetime.strptime(match.group(1), STAMP).replace(tzinfo=dt_timezone.utc), match.group(2),
                 match.group(3))


def _read():
    try:
        return _path().read_text(encoding="ascii", errors="replace").splitlines()
    except FileNotFoundError:
        return []


def entries():
    """The readable lines, in file order; the others are skipped."""
    with _locked():
        lines = _read()
    return [entry for entry in map(_parse, lines) if entry is not None]


def purge(now=None, dry_run=False):
    """Drop the lines older than KEEP, and those that cannot be read (their age is unknown). Returns how many went
    (how many would, in a dry run). The file is rewritten beside itself, synced, then put in place: a failure leaves
    the old file whole."""
    limit = (now or timezone.now()) - KEEP
    with _locked():
        lines = _read()
        kept = [line for line in lines if (entry := _parse(line)) is not None and entry.at >= limit]
        removed = len(lines) - len(kept)
        if removed == 0 or dry_run:
            return removed
        path = _path()
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                os.fchmod(handle.fileno(), PRIVATE)
                handle.write("".join(f"{line}\n" for line in kept))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    return removed

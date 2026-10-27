"""One lock file per slot: two passes of the same slot never overlap. The kernel drops the lock with the process,
so a killed run leaves nothing to clean up."""
import fcntl
from contextlib import contextmanager

from django.conf import settings


def _path(slot):
    directory = settings.TASK_LOCK_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{slot}.lock"


@contextmanager
def hold(slot):
    """Yields True with the lock held, False when another process holds it."""
    with open(_path(slot), "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def is_locked(slot):
    """True when a pass of this slot is running (used by the scheduler, 10.1)."""
    with hold(slot) as free:
        return not free

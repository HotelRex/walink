"""One lock for everything that uses the phone (relink, last, keepalive, local or remote), so a
scheduled keepalive can't collide with a relink or a message read. Works on Linux and Windows;
the OS releases it when the process exits, even after a crash.

The file lives in state/, never in logs/: deleting a held lock file lets a second run take a lock
on a new file, so two runs would drive the phone at once."""
import os
import sys

from . import paths

LOCK_PATH = paths.STATE / "phone.lock"


class Busy(Exception):
    pass


def _try_lock(f) -> bool:
    if sys.platform == "win32":
        import msvcrt
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)   # byte 0; beyond EOF is allowed on Windows
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def acquire():
    """Non-blocking exclusive lock; returns the open file (keep it referenced). Raises Busy.
    The child of a process that already holds it sets WALINK_LOCK_HELD=1 and skips locking."""
    if os.environ.get("WALINK_LOCK_HELD") == "1":
        return None
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK_PATH, "a+")          # never truncates a file another process holds
    if not _try_lock(f):
        f.close()
        raise Busy("the phone is in use (another walink run)")
    return f

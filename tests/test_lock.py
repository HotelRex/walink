import os, subprocess, sys
from walink import lock

# The child uses the same temporary lock file, so a real walink run on this machine can't interfere.
CHILD = ("import sys; from pathlib import Path; from walink import lock\n"
         "lock.LOCK_PATH = Path(sys.argv[1])\n"
         "try:\n    lock.acquire(); print('got')\nexcept lock.Busy:\n    print('busy')")

def _other_process(path):
    env = {k: v for k, v in os.environ.items() if k != "WALINK_LOCK_HELD"}
    return subprocess.run([sys.executable, "-c", CHILD, str(path)], capture_output=True, text=True, env=env).stdout.strip()

def test_lock_excludes_other_process_and_releases(tmp_path, monkeypatch):
    path = tmp_path / "phone.lock"
    monkeypatch.setattr(lock, "LOCK_PATH", path)
    held = lock.acquire()
    assert _other_process(path) == "busy"
    held.close()
    assert _other_process(path) == "got"

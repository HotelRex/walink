"""Which WhatsApp screen walink left the phone on, so the next action can continue from there instead of
reopening WhatsApp. A record older than REUSE_S is ignored, and the caller always checks that the phone still
shows that screen before using it. Stored in state/screen.json (shared by the CLI, the TUI and SSH runs)."""
from __future__ import annotations

import json
import os
import time

from . import paths

REUSE_S = 300                       # 5 minutes
LINKED_DEVICES = "linked_devices"   # the Linked devices list
CHAT = "chat"                       # an open chat (with its name)


def _file():
    return paths.STATE / "screen.json"


def remember(screen: str, chat: str | None = None, now: float | None = None) -> None:
    record = {"screen": screen, "chat": chat, "at": time.time() if now is None else now}
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    os.replace(tmp, f)


def recall(now: float | None = None) -> dict | None:
    """The last screen if recorded less than REUSE_S ago; None otherwise (missing, old or unreadable)."""
    try:
        record = json.loads(_file().read_text(encoding="utf-8"))
        age = (time.time() if now is None else now) - float(record["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return record if 0 <= age < REUSE_S else None


def forget() -> None:
    try:
        _file().unlink()
    except FileNotFoundError:
        pass

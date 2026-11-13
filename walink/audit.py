"""Audit log and notifications, for every run (local CLI, TUI, SSH). Never message content."""
from __future__ import annotations

import os
import subprocess
import time

from . import paths

NOTIFY_TIMEOUT_S = 20


def _log_path():
    return paths.LOGS / "audit.log"


def _client_ip() -> str:
    parts = os.environ.get("SSH_CLIENT", "").split()
    return parts[0] if parts else "?"


def _append(line: str) -> None:
    p = _log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def audit(label: str, event: str, detail: str = "") -> str:
    """Append one line to logs/audit.log and return it."""
    line = f"{time.strftime('%F %T')} label={label} ip={_client_ip()} {event} {detail}".strip()
    _append(line)
    return line


def notify(cfg: dict, message: str) -> None:
    """Best effort: a broken or slow notifier must never stop or fail a run."""
    cmd = (cfg.get("remote") or {}).get("notify_cmd")
    if not cmd:
        return
    try:
        subprocess.run(cmd, shell=True, input=message, text=True, encoding="utf-8", timeout=NOTIFY_TIMEOUT_S,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        _append(f"{time.strftime('%F %T')} NOTIFY_FAILED {type(e).__name__}")

"""Policy for remote (SSH) triggering. Pure functions so they can be unit-tested."""
from __future__ import annotations

import json
import os
import re
import shlex
import time
from pathlib import Path

from .code import normalize_code, CodeError

MAX_REQUEST_LEN = 200
NAME_RE = re.compile(r"^[A-Za-z0-9 _().-]{1,50}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
CHAT_RE = re.compile(r"^[\w +.,'&()-]{1,60}$")   # \w is Unicode: accented names are fine


class Rejected(Exception):
    pass


def _split(original: str | None) -> list[str]:
    if not original:
        raise Rejected("no command")
    if len(original) > MAX_REQUEST_LEN:
        raise Rejected("request too long")
    try:
        return shlex.split(original)
    except ValueError:
        raise Rejected("bad quoting")


def parse_command(original: str | None, allowed_names: list[str]) -> tuple[str, str]:
    """Accepts exactly:  relink <NAME> <CODE>  -> (name, normalized code). Nothing else."""
    parts = _split(original)
    if len(parts) != 3 or parts[0] != "relink":
        raise Rejected("usage: relink <NAME> <CODE>")
    name = parts[1]
    if not NAME_RE.fullmatch(name) or name not in allowed_names:
        raise Rejected("device name not allowed")
    try:
        return name, normalize_code(parts[2])
    except CodeError as e:
        raise Rejected(str(e))


def parse_request(original: str | None, allowed_names: list[str], readable_chats: list[str]) -> tuple:
    """('relink', name, code) | ('last', chat). Anything else is rejected."""
    parts = _split(original)
    if parts[0] == "last":
        if len(parts) != 2:
            raise Rejected('usage: last "<CHAT NAME>"')
        if not CHAT_RE.fullmatch(parts[1]) or parts[1] not in readable_chats:
            raise Rejected("chat not allowed")
        return ("last", parts[1])
    name, code = parse_command(original, allowed_names)
    return ("relink", name, code)


def _load_rate_state(state_file: Path) -> dict[str, list[float]]:
    """Missing file = no attempts yet. Anything unreadable or malformed fails CLOSED: resetting
    to empty would silently lift every limit (e.g. after a crash mid-write or a tampered file)."""
    try:
        raw = state_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise Rejected(f"rate-limit state unreadable ({e.__class__.__name__}); ask the admin")
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    ok = isinstance(data, dict) and all(
        isinstance(v, list) and all(isinstance(t, (int, float)) and not isinstance(t, bool) for t in v)
        for v in data.values())
    if not ok:
        raise Rejected(f"rate-limit state is corrupt; ask the admin to check {state_file.name}")
    return data


def check_rate(state_file: Path, label: str, limit: int, now: float | None = None) -> None:
    """Record an attempt for `label`; raise Rejected when over `limit` in the last hour.
    The caller must hold the phone lock, so read-modify-write cannot race."""
    now = now if now is not None else time.time()
    data = _load_rate_state(state_file)
    recent = [t for t in data.get(label, []) if now - t < 3600]
    if len(recent) >= limit:
        raise Rejected("rate limit reached, try later")
    data[label] = recent + [now]
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_name(state_file.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, state_file)          # atomic: a crash leaves the old file or the new one, never half

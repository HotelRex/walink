"""Policy for remote (SSH) triggering. Pure functions so they can be unit-tested."""
from __future__ import annotations

import re
import shlex

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

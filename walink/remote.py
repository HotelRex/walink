"""Turning an SSH request (SSH_ORIGINAL_COMMAND) into walink CLI arguments. Pure, so it is unit-testable.
The arguments go to argparse and then straight to Python functions; they never reach a shell."""
from __future__ import annotations

import re
import shlex

MAX_REQUEST_LEN = 200
LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


class Rejected(Exception):
    pass


def to_argv(original: str) -> list[str]:
    """CLI arguments for a request. `relink NAME CODE` (the form older client scripts send) becomes
    `link CODE --replace NAME --yes`; everything else is passed through for argparse to validate."""
    if not original or not original.strip():
        raise Rejected("no command")
    if len(original) > MAX_REQUEST_LEN:
        raise Rejected("request too long")
    try:
        parts = shlex.split(original)
    except ValueError:
        raise Rejected("bad quoting")
    if parts[0] == "relink":
        if len(parts) != 3:
            raise Rejected('usage: relink "<NAME>" <CODE>')
        return ["link", parts[2], "--replace", parts[1], "--yes"]
    return parts

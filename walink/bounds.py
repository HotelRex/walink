"""The one parser for uiautomator `bounds="[left,top][right,bottom]"` attributes."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def parse_bounds(s: str | None) -> tuple[int, int, int, int]:
    """(left, top, right, bottom). Raises ValueError on a malformed string."""
    m = _BOUNDS.fullmatch(s or "")
    if not m:
        raise ValueError(f"bad bounds: {s!r}")
    left, top, right, bottom = (int(g) for g in m.groups())
    return left, top, right, bottom


def top_of(node: ET.Element) -> int:
    """Top y of a hierarchy node; 0 when it has no bounds."""
    b = node.get("bounds")
    return parse_bounds(b)[1] if b else 0


def left_of(node: ET.Element) -> int:
    """Left x of a hierarchy node; 0 when it has no bounds."""
    b = node.get("bounds")
    return parse_bounds(b)[0] if b else 0

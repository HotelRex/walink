"""Pure parsing of an open chat's UI hierarchy (no phone access), so it is unit-testable."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from . import selectors as S
from .bounds import top_of as _top


def _descendants(row: ET.Element):
    """Nodes under `row`, skipping quoted-reply subtrees."""
    for c in row:
        if c.get("resource-id") == S.MSG_QUOTE_ID:
            continue
        yield c
        yield from _descendants(c)


def last_message(root: ET.Element) -> dict:
    """The bottom-most message row on screen: {'text', 'day', 'time', 'direction', 'status', 'sender', 'kind'}.
    A row counts as a message if it has a timestamp or a body (skips date dividers / system notices;
    a row cut off at the screen edge may lack its timestamp). A non-text last message (photo without
    caption, voice note...) gives kind='non-text', text=None."""
    lst = next((n for n in root.iter("node") if n.get("resource-id") == S.MSG_LIST_ID), None)
    if lst is None:
        raise ValueError("message list not found")
    rows = []
    for row in lst:
        nodes = list(_descendants(row))
        dates = sorted((n for n in nodes if n.get("resource-id") == S.MSG_DATE_ID and n.get("text")), key=_top)
        bodies = sorted((n for n in nodes if n.get("resource-id") in S.MSG_BODY_IDS and n.get("text")), key=_top)
        if dates or bodies:
            rows.append((_top(row), nodes, dates, bodies))
    if not rows:
        raise ValueError("no messages visible")
    row_top, nodes, dates, bodies = max(rows, key=lambda r: r[0])
    msg_top = min(_top(n) for n in (bodies or dates))
    days = [n for n in lst.iter("node") if n.get("resource-id") == S.MSG_DAY_ID and n.get("text")
            and _top(n) <= msg_top]
    day = max(days, key=_top).get("text") if days else None    # None: divider scrolled off screen
    sender = next((n.get("text") for n in nodes if n.get("resource-id") == S.MSG_SENDER_ID and n.get("text")), None)
    status = next((n.get("content-desc") for n in nodes if n.get("resource-id") == S.MSG_STATUS_ID), None)
    text = bodies[-1].get("text") if bodies else None
    return {"text": text, "day": day,
            "time": dates[-1].get("text").replace("\u202f", " ") if dates else None,
            "direction": "outgoing" if status is not None else "incoming",
            "status": status, "sender": sender,
            "kind": "text" if text is not None else "non-text"}


def format_message(m: dict) -> str:
    """One line: [day time] who: text"""
    who = "me" if m["direction"] == "outgoing" else (m.get("sender") or m["chat"])
    when = " ".join(x for x in (m.get("day"), m.get("time")) if x) or "?"
    return f"[{when}] {who}: " + (m["text"] if m["text"] is not None else "(non-text message)")

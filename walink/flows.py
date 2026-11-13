"""Business flows. Only uses walink.device.Device, never uiautomator2/adb directly."""
from __future__ import annotations

import logging
import re
from typing import Callable

from . import selectors as S
from .device import Device, DeviceError
from .messages import last_message as parse_last_message

log = logging.getLogger("walink")

# progress(step_text, fraction 0..1 of this flow); the caller scales it into its own progress bar
Progress = Callable[[str, float], None]


def _no_progress(text: str, fraction: float) -> None:
    pass


HEADER_RE = re.compile(r"(\d+) of (\d+)")
DEFAULT_MAX_DEVICES = 4


class FlowError(DeviceError):
    pass


def open_linked_devices(dev: Device) -> None:
    dev.open_app_home()
    dev.tap(S.OVERFLOW)
    dev.tap(S.MENU_LINKED_DEVICES)
    dev.wait_activity(S.ACT_LINKED)
    dev.text(S.HEADER_COUNT)


def resume_linked_devices(dev: Device) -> bool:
    """Continue on the Linked devices list a previous action left open, if the phone still shows it
    (scrolled back to the top, where the header is). False: the caller opens it from scratch."""
    dev.wake_and_unlock()
    if dev.activity() != S.ACT_LINKED:
        return False
    for _ in range(3):
        if dev.exists(S.HEADER_COUNT, timeout=0.5):
            return True
        dev.scroll_up()
    return dev.exists(S.HEADER_COUNT, timeout=0.5)


def read_devices(dev: Device, max_scrolls: int = 6) -> dict:
    """Returns {'count': n, 'max': m, 'devices': [{'name','status'}]}. Scrolls (at most `max_scrolls`
    times) until it has read as many rows as the header announces."""
    m = HEADER_RE.search(dev.text(S.HEADER_COUNT))
    expected = int(m.group(1)) if m else None
    seen: dict[str, str] = {}
    order: list[str] = []
    for attempt in range(max_scrolls + 1):
        if attempt:
            dev.scroll_down()
        pending = None
        for rid, text, _y in dev.texts_by_id(S.ROW_NAME_ID, S.ROW_STATUS_ID):
            if rid == S.ROW_NAME_ID:
                pending = text
            elif pending is not None:
                if pending not in seen:
                    order.append(pending)
                seen[pending] = text
                pending = None
        if expected is not None and len(seen) >= expected:
            break
    count = expected if expected is not None else len(seen)
    if len(seen) != count:
        raise FlowError(f"header says {count} devices but read {len(seen)}: {order}")
    mx = int(m.group(2)) if m else DEFAULT_MAX_DEVICES
    return {"count": count, "max": mx, "devices": [{"name": n, "status": seen[n]} for n in order]}


def remove_device(dev: Device, name: str, dry_run: bool = False) -> None:
    """Remove ONE device by exact name. Caller is responsible for authorization."""
    dev.tap_row(name)
    dev.wait_activity(S.ACT_EDIT)
    shown = dev.text(S.EDIT_DEVICE_NAME)
    if shown != name:
        raise FlowError(f"opened '{shown}' instead of '{name}'; refusing to remove")
    if dry_run:
        log.info("[dry-run] would remove '%s'", name)
        dev.press_back()
        dev.wait_activity(S.ACT_LINKED)
        return
    dev.tap(S.OVERFLOW)
    dev.tap(S.EDIT_MENU_REMOVE)
    if dev.text(S.DIALOG_TITLE) != S.TXT_REMOVE_TITLE:
        raise FlowError("unexpected dialog instead of remove confirmation")
    dev.tap(S.DIALOG_BUTTON_OK)
    dev.wait_activity(S.ACT_LINKED)
    dev.wait_gone({"resourceId": S.ROW_NAME_ID, "text": name}, timeout=15)
    log.info("removed '%s'", name)


def link_with_code(dev: Device, code: str, new_name: str, progress: Progress = _no_progress) -> None:
    """From Linked devices: link a new device and name it. Needs a free slot.
    `code` must already be normalized (walink.code.normalize_code)."""
    progress("Opening the code screen", 0.0)
    dev.tap(S.LINK_BUTTON)
    seen = dev.wait_any({"scanner": {"resourceId": S.SCANNER_PHONE_NUMBER["resourceId"]},
                         "full": {"resourceId": S.DIALOG_MESSAGE["resourceId"]}}, timeout=15)
    if seen == "full":
        raise FlowError("list is full (Meta One dialog); remove a device first")
    dev.tap(S.SCANNER_PHONE_NUMBER)
    dev.wait_activity(S.ACT_CODE)
    progress("Entering the code", 0.25)
    boxes = [S.code_box(i) for i in range(1, 9)]
    dev.type_into_boxes(boxes, code)               # boxes 1-7, verified by readback
    dev.type_last_box(boxes, code[-1], code[-2])   # 8th box submits
    log.info("code submitted, waiting for result")
    progress("Waiting for WhatsApp to accept the code", 0.45)
    result = dev.wait_any({"name": S.NAME_FIELD,
                           "dialog": S.DIALOG_TITLE}, timeout=45)
    if result == "dialog":
        title = dev.text(S.DIALOG_TITLE)
        msg = dev.text(S.DIALOG_MESSAGE)
        raise FlowError(f"code rejected: {title}: {msg}")
    dev.wait_activity(S.ACT_NAME)
    progress(f"Naming the device '{new_name}'", 0.8)
    dev.type_into_field(S.NAME_FIELD, new_name)
    dev.tap(S.NAME_SAVE)
    dev.wait_activity(S.ACT_LINKED, timeout=30)


def verify_linked(dev: Device, name: str) -> dict:
    """Success = the name is in the list AND it is not 'Logged out'. Anything else is a failure."""
    info = read_devices(dev)
    match = [d for d in info["devices"] if d["name"] == name]
    if len(match) != 1 or match[0]["status"].lower().startswith("logged out"):
        raise FlowError(f"link NOT confirmed for '{name}': {info['devices']}")
    return match[0]


def _same_chat(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def open_chat(dev: Device, name: str, progress: Progress = _no_progress) -> str:
    """From the chat list: search the chat by its whole name (case does not matter), open it, verify the title,
    jump to the newest message. Returns the name as WhatsApp shows it. Opening a chat marks it as read."""
    progress("Opening WhatsApp", 0.0)
    dev.open_app_home()
    progress(f"Searching for the chat '{name}'", 0.4)
    dev.tap(S.SEARCH_BAR)
    dev.type_into_field(S.SEARCH_INPUT, name, tap_first=False)   # tapping the bar focuses the input
    row = {"resourceId": S.CHAT_ROW_NAME_ID, **S.text_ignoring_case(name)}
    if not dev.exists(row, timeout=8):
        raise FlowError(f"no chat named '{name}'")
    progress("Opening the chat", 0.7)
    dev.tap(row)
    dev.wait_activity(S.ACT_CHAT)
    shown = dev.text(S.CHAT_HEADER_NAME)
    if not _same_chat(shown, name):
        raise FlowError(f"opened '{shown}' instead of '{name}'")
    _go_to_newest(dev)
    return shown


def _go_to_newest(dev: Device) -> None:
    if dev.exists(S.CHAT_SCROLL_BOTTOM, timeout=1.5):     # not at the bottom (e.g. opened at the first unread)
        dev.tap(S.CHAT_SCROLL_BOTTOM)
        dev.wait_gone(S.CHAT_SCROLL_BOTTOM, timeout=10)


def resume_chat(dev: Device, name: str) -> str | None:
    """Continue in chat `name` (case does not matter) if a previous action left it open and the phone still
    shows it. Returns the name as WhatsApp shows it, or None."""
    dev.wake_and_unlock()
    if dev.activity() != S.ACT_CHAT:
        return None
    try:
        shown = dev.text(S.CHAT_HEADER_NAME, timeout=1)
    except DeviceError:
        return None
    if not _same_chat(shown, name):
        return None
    _go_to_newest(dev)
    return shown


def last_message(dev: Device, name: str, progress: Progress = _no_progress, resume: bool = False) -> dict:
    """Open the chat `name` (or, with `resume`, continue in it if it is still open) and return its newest
    message (see walink.messages.last_message)."""
    shown = resume_chat(dev, name) if resume else None
    if shown:
        progress(f"Continuing in the open chat '{shown}'", 0.7)
    else:
        shown = open_chat(dev, name, progress)
    progress("Reading the newest message", 0.9)
    try:
        msg = parse_last_message(dev.hierarchy())
    except ValueError as e:
        raise FlowError(f"could not read the last message of '{name}': {e}")
    return {"chat": shown, **msg}

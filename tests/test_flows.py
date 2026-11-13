"""flows.read_devices against a fake phone whose list needs scrolling."""
import pytest

from walink import flows
from walink import selectors as S

N, ST = S.ROW_NAME_ID, S.ROW_STATUS_ID


class ScrollingList:
    def __init__(self, header, pages):
        self.header, self.pages, self.page, self.scrolls = header, pages, 0, 0

    def text(self, sel):
        assert sel == S.HEADER_COUNT
        return self.header

    def texts_by_id(self, *ids):
        return self.pages[min(self.page, len(self.pages) - 1)]

    def scroll_down(self):
        self.scrolls += 1
        self.page += 1


def rows(*pairs, top=100):
    out = []
    for i, (name, status) in enumerate(pairs):
        out += [(N, name, top + i * 100), (ST, status, top + i * 100 + 40)]
    return out


def test_reads_across_a_scroll_and_stops():
    dev = ScrollingList("4 of 4 devices linked.", [
        rows(("A", "Active now"), ("B", "Active now"), ("C", "Logged out")),
        rows(("C", "Logged out"), ("D", "Active now"))])
    info = flows.read_devices(dev)
    assert [d["name"] for d in info["devices"]] == ["A", "B", "C", "D"]
    assert (info["count"], info["max"], dev.scrolls) == (4, 4, 1)


def test_no_scroll_when_everything_is_visible():
    dev = ScrollingList("2 of 4 devices linked.", [rows(("A", "x"), ("B", "y"))])
    assert flows.read_devices(dev)["count"] == 2 and dev.scrolls == 0


def test_never_scrolls_past_the_last_read():
    dev = ScrollingList("3 of 4 devices linked.", [rows(("A", "x"))])
    with pytest.raises(flows.FlowError):
        flows.read_devices(dev, max_scrolls=2)
    assert dev.scrolls == 2


def test_unparsable_header_uses_what_was_read():
    dev = ScrollingList("something new", [rows(("A", "x"))])
    info = flows.read_devices(dev)
    assert (info["count"], info["max"]) == (1, flows.DEFAULT_MAX_DEVICES)


def test_landscape_needs_more_scrolls_and_still_reads_everything():
    # landscape: one device per screen
    dev = ScrollingList("4 of 4 devices linked.", [rows(("A", "x")), rows(("B", "x")), rows(("C", "x")),
                                                   rows(("D", "x"))])
    assert [d["name"] for d in flows.read_devices(dev)["devices"]] == ["A", "B", "C", "D"]
    assert dev.scrolls == 3


class Phone:
    """A phone left on some screen by the previous action."""

    def __init__(self, activity, header_after_scrolls=0, chat_title=None, scroll_bottom=False):
        self.act, self.scrolls_needed, self.title, self.bottom = activity, header_after_scrolls, chat_title, scroll_bottom
        self.calls = []

    def wake_and_unlock(self):
        self.calls.append("wake")

    def activity(self):
        return self.act

    def exists(self, sel, timeout=0):
        if sel == S.HEADER_COUNT:
            return self.calls.count("scroll_up") >= self.scrolls_needed
        if sel == S.CHAT_SCROLL_BOTTOM:
            return self.bottom
        return False

    def scroll_up(self):
        self.calls.append("scroll_up")

    def text(self, sel, timeout=5):
        return self.title

    def tap(self, sel):
        self.calls.append("tap")

    def wait_gone(self, sel, timeout=10):
        self.bottom = False


def test_resume_linked_devices_scrolls_back_to_the_header():
    phone = Phone(S.ACT_LINKED, header_after_scrolls=1)
    assert flows.resume_linked_devices(phone)
    assert phone.calls == ["wake", "scroll_up"]


def test_resume_linked_devices_refuses_other_screens():
    assert not flows.resume_linked_devices(Phone(S.ACT_HOME))


def test_resume_chat_only_the_same_chat_and_jumps_to_newest():
    assert not flows.resume_chat(Phone(S.ACT_CHAT, chat_title="Bank"), "PayPal")
    assert not flows.resume_chat(Phone(S.ACT_HOME), "PayPal")
    phone = Phone(S.ACT_CHAT, chat_title="PayPal", scroll_bottom=True)
    assert flows.resume_chat(phone, "PayPal") and "tap" in phone.calls

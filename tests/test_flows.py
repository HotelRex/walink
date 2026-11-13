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

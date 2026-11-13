"""Device.tap waits for an element to stop moving (e.g. the overflow menu unfolding) before tapping it."""
import random

import pytest

from walink import device as device_mod
from walink.device import Device, DeviceError
from walink.humanize import Pacer


def box(top):
    return {"left": 300, "right": 700, "top": top, "bottom": top + 96}


class FakeU2:
    """Each lookup of the element returns the next bounds in `frames` (the last one repeats)."""

    def __init__(self, frames):
        self.frames, self.reads, self.clicks = frames, 0, []

    def __call__(self, **sel):
        return self

    @property
    def info(self):
        b = self.frames[min(self.reads, len(self.frames) - 1)]
        self.reads += 1
        if b is None:
            raise device_mod.UiObjectNotFoundError({"code": -32002, "message": "not found"}, "")
        return {"bounds": b}

    def click(self, x, y):
        self.clicks.append((x, y))


def make(frames, monkeypatch):
    monkeypatch.setattr(device_mod.time, "sleep", lambda s: None)
    dev = object.__new__(Device)
    dev.pacer = Pacer(rng=random.Random(1), sleep=lambda s: None)
    dev._d = FakeU2(frames)
    return dev


def test_menu_item_moving_while_menu_unfolds_is_tapped_where_it_settles(monkeypatch):
    # "Linked devices" first seen one row higher (where "Lists" ends up), then the menu finishes unfolding
    dev = make([box(562), box(610), box(658), box(658)], monkeypatch)
    dev.tap({"text": "Linked devices"})
    (x, y), = dev._d.clicks
    assert 658 <= y <= 754


def test_still_element_costs_one_extra_lookup(monkeypatch):
    dev = make([box(658)], monkeypatch)
    dev.tap({"text": "x"})
    assert dev._d.reads == 2 and len(dev._d.clicks) == 1


def test_element_that_never_settles_is_not_tapped(monkeypatch):
    tops = iter(range(0, 100000, 10))
    dev = make([], monkeypatch)
    type(dev._d).info = property(lambda self: {"bounds": box(next(tops))})
    clock = iter(range(0, 1000))
    monkeypatch.setattr(device_mod.time, "time", lambda: next(clock))
    with pytest.raises(DeviceError):
        dev.tap({"text": "x"})
    assert dev._d.clicks == []

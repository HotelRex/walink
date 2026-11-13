import random
from walink.device import Device
from walink.humanize import Pacer

def test_tap_points_stay_in_middle_of_element_and_vary():
    dev = object.__new__(Device)           # no phone needed for _jitter
    dev.pacer = Pacer(rng=random.Random(1))
    b = {"left": 100, "right": 300, "top": 500, "bottom": 560}
    pts = [dev._jitter(b) for _ in range(2000)]
    assert all(140 <= x <= 260 and 512 <= y <= 548 for x, y in pts)
    assert len(set(pts)) > 200            # not always the same pixel

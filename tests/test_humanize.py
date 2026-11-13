import random, statistics
from walink.humanize import (Pacer, Range, SAME_TYPE, SWITCH_TYPE, CLICK,
                             sample_ms, typing_delay_ms)

def test_ranges_never_exceeded():
    rng = random.Random(1)
    for r in (SAME_TYPE, SWITCH_TYPE, CLICK):
        xs = [sample_ms(r, rng) for _ in range(20000)]
        assert min(xs) >= r.lo_ms and max(xs) <= r.hi_ms

def test_is_bell_shaped_around_mean():
    rng = random.Random(2)
    xs = [sample_ms(CLICK, rng) for _ in range(20000)]
    assert abs(statistics.mean(xs) - 475) < 5
    centre = sum(1 for x in xs if 475-58.3 <= x <= 475+58.3) / len(xs)   # +-1 sigma
    assert 0.62 < centre < 0.75                                 # ~68%, not uniform (33%)

def test_same_vs_switch_ranges():
    rng = random.Random(3)
    assert all(70 <= typing_delay_ms("A", "B", rng=rng) <= 200 for _ in range(500))
    assert all(70 <= typing_delay_ms("4", "7", rng=rng) <= 200 for _ in range(500))
    assert all(130 <= typing_delay_ms("4", "N", rng=rng) <= 320 for _ in range(500))
    assert all(130 <= typing_delay_ms("N", "4", rng=rng) <= 320 for _ in range(500))
    assert typing_delay_ms(None, "A") == 0.0

def test_type_text_sequence_and_sleep():
    slept, typed = [], []
    p = Pacer(rng=random.Random(4), sleep=slept.append)
    delays = p.type_text("47NH1G3T", lambda i, c: typed.append((i, c)))
    assert "".join(c for _, c in typed) == "47NH1G3T"
    assert delays[0] == 0 and len(slept) == 7
    # 4->7 same, 7->N switch, N->H same, H->1 switch, ...
    for (a, b), d in zip(zip("47NH1G3T", "7NH1G3T"), delays[1:]):
        lo, hi = (70, 200) if a.isdigit() == b.isdigit() else (130, 320)
        assert lo <= d <= hi

def test_click_pause_range():
    slept = []
    p = Pacer(rng=random.Random(5), sleep=slept.append)
    for _ in range(300): p.click_pause()
    assert all(0.3 <= s <= 0.65 for s in slept)

def test_latency_aware_click():
    slept = []
    p = Pacer(rng=random.Random(6), sleep=slept.append)
    ms = p.click_pause(already_ms=10_000)   # lookup already took longer than any sample
    assert slept[-1] == 0.0 and 300 <= ms <= 650
    ms = p.click_pause(already_ms=100)
    assert abs(slept[-1] - (ms - 100) / 1000) < 1e-9

def test_type_text_counts_call_time():
    slept, clock_t = [], [0.0]
    clock = lambda: clock_t[0]
    def put(i, ch): clock_t[0] += 0.5          # each call "takes" 500 ms > any sampled gap
    p = Pacer(rng=random.Random(7), sleep=slept.append)
    p.type_text("ABCD", put, clock=clock)
    assert all(s == 0.0 for s in slept)        # gaps fully covered by call time

def test_char_pause_matches_typing_ranges():
    slept = []
    p = Pacer(rng=random.Random(8), sleep=slept.append)
    assert p.char_pause(None, "A") == 0 and slept == []
    ms = p.char_pause("4", "N")
    assert 130 <= ms <= 320 and slept == [ms / 1000]

"""Human-like input timing. Every tap and every typed character in walink
must go through this module, so pacing is defined in exactly one place.

Delays are drawn from a normal distribution centred in the allowed range
(sigma = range/6, so ~99.7% of raw draws are already in range); the rare
out-of-range draw is re-sampled, never clamped, so there is no spike at the edges.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable

Sleep = Callable[[float], None]


@dataclass(frozen=True)
class Range:
    lo_ms: float
    hi_ms: float

    def __post_init__(self):
        for v in (self.lo_ms, self.hi_ms):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"timing values must be numbers, got {v!r}")
        if not 0 <= self.lo_ms <= self.hi_ms:
            raise ValueError(f"timing range must satisfy 0 <= min <= max, got [{self.lo_ms}, {self.hi_ms}]")

    @property
    def mean(self) -> float:
        return (self.lo_ms + self.hi_ms) / 2

    @property
    def sigma(self) -> float:
        return (self.hi_ms - self.lo_ms) / 6


# Defaults requested by the user; overridable from config.yaml ("timing:").
SAME_TYPE = Range(70, 200)    # char->char, digit->digit
SWITCH_TYPE = Range(130, 320)  # letter<->digit
CLICK = Range(300, 650)       # tapping layout elements


def sample_ms(r: Range, rng: random.Random | None = None) -> float:
    if r.sigma == 0:
        return r.mean
    rng = rng or random
    while True:
        v = rng.gauss(r.mean, r.sigma)
        if r.lo_ms <= v <= r.hi_ms:
            return v


def char_class(ch: str) -> str:
    return "digit" if ch.isdigit() else "letter"


def typing_delay_ms(prev: str | None, cur: str, same: Range = SAME_TYPE,
                    switch: Range = SWITCH_TYPE, rng: random.Random | None = None) -> float:
    """Pause before typing `cur` after `prev`. First char of a field has no typing
    pause (the click on the field already carried a CLICK delay)."""
    if prev is None:
        return 0.0
    return sample_ms(same if char_class(prev) == char_class(cur) else switch, rng)


class Pacer:
    """Use pacer.click_pause() before every tap, pacer.type_text(...) for text input and
    pacer.char_pause(...) for a single character typed outside type_text. `sleep` is injectable for tests."""

    def __init__(self, same: Range = SAME_TYPE, switch: Range = SWITCH_TYPE,
                 click: Range = CLICK, rng: random.Random | None = None,
                 sleep: Sleep = time.sleep):
        self.same, self.switch, self.click = same, switch, click
        self.rng, self.sleep = rng or random.Random(), sleep

    def click_pause(self, already_ms: float = 0.0) -> float:
        """Pause before a tap. `already_ms` is time the caller already spent (e.g. element
        lookup); only the remainder of the sampled delay is slept, so the gap is
        max(sampled, already)."""
        ms = sample_ms(self.click, self.rng)
        self.sleep(max(0.0, ms - already_ms) / 1000)
        return ms

    def char_pause(self, prev: str | None, cur: str, already_ms: float = 0.0) -> float:
        """Pause before typing `cur` after `prev` (none for the first char); returns the sampled gap."""
        ms = typing_delay_ms(prev, cur, self.same, self.switch, self.rng)
        if ms:
            self.sleep(max(0.0, ms - already_ms) / 1000)
        return ms

    def type_text(self, text: str, put_char: Callable[[int, str], None],
                  clock: Callable[[], float] = time.perf_counter) -> list[float]:
        """Call put_char(index, char) per character with human pacing; returns the sampled
        gaps. Time spent inside the previous put_char counts toward the next gap, so the
        effective key-to-key interval is max(sampled gap, call time)."""
        delays, prev, last_call_ms = [], None, 0.0
        for i, ch in enumerate(text):
            delays.append(self.char_pause(prev, ch, already_ms=last_call_ms))
            t0 = clock()
            put_char(i, ch)
            last_call_ms = (clock() - t0) * 1000
            prev = ch
        return delays

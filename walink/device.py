"""The ONLY module allowed to touch the phone (uiautomator2 / adb).
Everything above this layer (flows, CLI) uses these methods, so pacing
(walink.humanize.Pacer) is applied to every tap and every typed character."""
from __future__ import annotations

import logging
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

import uiautomator2 as _u2
from uiautomator2.exceptions import UiObjectNotFoundError

from . import selectors as S
from .bounds import left_of, top_of
from .humanize import Pacer

log = logging.getLogger("walink")

ADB_TIMEOUT_S = 60
# Before tapping, an element must be at the same place in two lookups in a row (each lookup takes ~250 ms).
SETTLE_STEP_S = 0.05
SETTLE_TIMEOUT_S = 3

# Swipes as fractions of the screen size (x, y_from, y_to), so they fit any resolution.
# On the 720x1600 Huawei: scroll 1280->720, unlock 1280->400.
SCROLL_SWIPE = (0.5, 0.80, 0.45)
UNLOCK_SWIPE = (0.5, 0.80, 0.25)


class DeviceError(RuntimeError):
    pass


class Device:
    def __init__(self, serial: str | None = None, pacer: Pacer | None = None):
        self.pacer = pacer or Pacer()
        self._serial = serial or self._only_serial()
        self._d = _u2.connect(self._serial)

    # ---------- raw adb (private) ----------
    def _adb(self, *args: str, binary: bool = False, check: bool = True):
        try:
            r = subprocess.run(["adb", "-s", self._serial, *args], capture_output=True, timeout=ADB_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise DeviceError(f"adb {' '.join(args)} timed out after {ADB_TIMEOUT_S}s")
        if check and r.returncode != 0:
            raise DeviceError(f"adb {' '.join(args)} failed: {r.stderr.decode(errors='replace')}")
        return r.stdout if binary else r.stdout.decode(errors="replace")

    @staticmethod
    def _only_serial() -> str:
        try:
            out = subprocess.run(["adb", "devices"], capture_output=True, text=True,
                                 timeout=ADB_TIMEOUT_S).stdout.splitlines()[1:]
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DeviceError(f"cannot run 'adb devices': {e}")
        devs = [line.split()[0] for line in out if line.strip().endswith("device")]
        if len(devs) != 1:
            raise DeviceError(f"expected exactly one adb device, found {devs or 'none'}; pass --serial")
        return devs[0]

    # ---------- read-only inspection (no pacing needed) ----------
    def info(self) -> dict:
        def prop(name: str) -> str:
            return self._adb("shell", "getprop", name).strip()
        pm = self._adb("shell", "pm", "list", "packages", S.PKG)
        return {"serial": self._serial, "model": prop("ro.product.model"),
                "android": prop("ro.build.version.release"), "screen_on": self.screen_on(),
                "whatsapp_business_installed": f"package:{S.PKG}" in pm}

    def screen_on(self) -> bool:
        return "mWakefulness=Awake" in self._adb("shell", "dumpsys", "power")

    def keyguard_showing(self) -> bool:
        w = self._adb("shell", "dumpsys", "window")
        return "mShowingLockscreen=true" in w or "isKeyguardShowing=true" in w or "mDreamingLockscreen=true" in w

    def activity(self) -> str:
        return self._d.app_current().get("activity", "")

    def package(self) -> str:
        return self._d.app_current().get("package", "")

    def hierarchy(self) -> ET.Element:
        """Read-only UI tree of the current screen (also parsed by pure modules like walink.messages)."""
        return ET.fromstring(self._d.dump_hierarchy())

    def exists(self, sel: dict, timeout: float = 0) -> bool:
        return self._d(**sel).wait(timeout=timeout) if timeout else self._d(**sel).exists

    def text(self, sel: dict, timeout: float = 5) -> str:
        try:
            return self._d(**sel).get_text(timeout=timeout) or ""
        except UiObjectNotFoundError:
            raise DeviceError(f"element not found: {sel}")

    def _find(self, sel: dict, timeout: float) -> dict | None:
        """Element info (incl. bounds) in ONE lookup when present; polls until `timeout`."""
        end = time.time() + timeout
        while True:
            try:
                return self._d(**sel).info
            except UiObjectNotFoundError:
                if time.time() >= end:
                    return None
                time.sleep(0.2)

    def texts_by_id(self, *res_ids: str) -> list[tuple[str, str, int]]:
        """(resource-id, text, top-y) of visible nodes, in screen order."""
        out = [(n.get("resource-id"), n.get("text"), top_of(n)) for n in self.hierarchy().iter("node")
               if n.get("resource-id") in res_ids and n.get("text")]
        return sorted(out, key=lambda t: t[2])

    # ---------- waits (explicit waits, never fixed sleeps) ----------
    def wait_activity(self, activity: str, timeout: float = 15) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.activity() == activity:
                return
            time.sleep(0.3)
        raise DeviceError(f"timed out waiting for {activity.rsplit('.', 1)[-1]} (now {self.activity()})")

    def wait_gone(self, sel: dict, timeout: float = 30) -> None:
        if not self._d(**sel).wait_gone(timeout=timeout):
            raise DeviceError(f"still visible after {timeout}s: {sel}")

    def wait_any(self, sels: dict[str, dict], timeout: float = 30) -> str:
        """Return the key of the first selector that appears."""
        end = time.time() + timeout
        while time.time() < end:
            for k, s in sels.items():
                if self._d(**s).exists:
                    return k
            time.sleep(0.4)
        raise DeviceError(f"none of {list(sels)} appeared in {timeout}s")

    # ---------- paced inputs (the only way to act) ----------
    @staticmethod
    def _keycode(ch: str) -> tuple[int, int] | None:
        """Android (keycode, meta) for a character, or None if not in the table."""
        shift = 1
        if "a" <= ch.lower() <= "z" and ch.isascii():
            return 29 + ord(ch.lower()) - 97, (shift if ch.isupper() else 0)
        if ch.isascii() and ch.isdigit():
            return 7 + int(ch), 0
        table = {" ": (62, 0), "-": (69, 0), ".": (56, 0), ",": (55, 0),
                 "_": (69, shift), "(": (16, shift), ")": (7, shift)}
        return table.get(ch)

    def _press_char(self, ch: str) -> None:
        kc, meta = self._keycode(ch)
        if meta:
            self._d.press(kc, meta)
        else:
            self._d.press(kc)

    def tap(self, sel: dict, timeout: float = 10) -> None:
        """Find the element, wait until it stops moving, pause like a human, then tap a slightly random point
        inside it (a finger never hits the exact centre). The lookups count toward the pause."""
        t0 = time.perf_counter()
        x, y = self._jitter(self._settled_bounds(sel, timeout))
        self.pacer.click_pause(already_ms=(time.perf_counter() - t0) * 1000)
        log.info("tap %s", sel)
        self._d.click(x, y)

    def _settled_bounds(self, sel: dict, timeout: float) -> dict:
        """Bounds once two lookups in a row agree. Menus animate in: the overflow menu unfolds from the bottom
        and items appear above "Linked devices" for ~300 ms, so a position read too early is one row off."""
        def bounds_now(wait: float) -> dict:
            info = self._find(sel, wait)
            if info is None:
                raise DeviceError(f"cannot tap, not found: {sel}")
            return info.get("visibleBounds") or info["bounds"]
        prev = bounds_now(timeout)
        end = time.time() + SETTLE_TIMEOUT_S
        while True:
            time.sleep(SETTLE_STEP_S)
            cur = bounds_now(SETTLE_TIMEOUT_S)
            if cur == prev:
                return cur
            if time.time() > end:
                raise DeviceError(f"cannot tap, still moving after {SETTLE_TIMEOUT_S}s: {sel}")
            prev = cur

    def _jitter(self, b: dict) -> tuple[int, int]:
        """Point near the centre, normal spread, kept inside the middle 60% of the element."""
        def axis(lo, hi):
            c, half = (lo + hi) / 2, (hi - lo) * 0.3
            return int(round(max(c - half, min(c + half, self.pacer.rng.gauss(c, half / 2)))))
        return axis(b["left"], b["right"]), axis(b["top"], b["bottom"])

    def tap_row(self, name: str) -> None:
        self.tap({"resourceId": S.ROW_NAME_ID, "text": name})

    def _swipe(self, fx: float, fy_from: float, fy_to: float, duration: float) -> None:
        w, h = self._d.window_size()
        self._d.swipe(int(w * fx), int(h * fy_from), int(w * fx), int(h * fy_to), duration=duration)

    def scroll_down(self) -> None:
        self.pacer.click_pause()
        log.info("scroll down")
        self._swipe(*SCROLL_SWIPE, duration=0.45)

    def scroll_up(self) -> None:
        x, y_from, y_to = SCROLL_SWIPE
        self.pacer.click_pause()
        log.info("scroll up")
        self._swipe(x, y_to, y_from, duration=0.45)

    def press_back(self) -> None:
        self.pacer.click_pause()
        self._d.press("back")

    def _box_texts(self) -> list[str]:
        """Text of the 8 code boxes from ONE hierarchy dump (cheaper than 8 lookups)."""
        boxes = [n for n in self.hierarchy().iter("node")
                 if n.get("class") == "android.widget.EditText"
                 and (n.get("content-desc") or "").startswith("Enter 8-character code")]
        boxes.sort(key=left_of)
        return [n.get("text") or "" for n in boxes]

    def type_into_boxes(self, boxes: list[dict], text: str) -> None:
        """Characters 1..N-1 into the code boxes with per-box set_text. set_text bypasses the
        keyboard: raw key presses were unreliable here because SwiftKey hides and re-attaches
        each time focus moves to the next box. The last box is NOT filled here: in this app
        the 8th box submits, so the caller verifies first (single-dump readback happens here)."""
        if len(text) != len(boxes):
            raise DeviceError("text/boxes length mismatch")
        if any(self._box_texts()):                  # one dump; boxes must start empty
            raise DeviceError("code boxes are not empty; refusing to type")
        self.pacer.click_pause()
        self.pacer.type_text(text[:-1], lambda i, ch: self._d(**boxes[i]).set_text(ch))
        got = "".join(self._box_texts()[: len(boxes) - 1])
        if got != text[:-1]:
            raise DeviceError(f"readback mismatch, refusing to submit (got {got!r})")

    def type_last_box(self, boxes: list[dict], ch: str, prev: str) -> None:
        """Fill the last code box (which submits), paced as the char after `prev`."""
        self.pacer.char_pause(prev, ch)
        self._d(**boxes[-1]).set_text(ch)

    def type_into_field(self, sel: dict, text: str, tap_first: bool = True) -> None:
        """Tap the field once (skip with tap_first=False when it is already focused), then raw key
        presses per char. Falls back to a single set_text if a char has no keycode or the readback differs."""
        if tap_first:
            self.tap(sel)
        elif self._find(sel, 10) is None:
            raise DeviceError(f"field not found: {sel}")
        else:
            self.pacer.click_pause()
        if all(self._keycode(c) for c in text):
            self.pacer.type_text(text, lambda i, ch: self._press_char(ch))
            if self._field_text(sel) == text:
                return
            log.warning("press-typing readback differed; falling back to set_text")
            self._d(**sel).clear_text()
        self._d(**sel).set_text(text)
        if self._field_text(sel) != text:
            raise DeviceError("field readback mismatch")

    def _field_text(self, sel: dict) -> str:
        """Field text without zero-width chars (the search box prefixes U+200B)."""
        return "".join(c for c in (self._d(**sel).get_text() or "") if c not in "​‌‍﻿")

    # ---------- phone state ----------
    def _poll(self, cond: Callable[[], bool], timeout: float, step: float = 0.1) -> bool:
        end = time.time() + timeout
        while not cond():
            if time.time() > end:
                return False
            time.sleep(step)
        return True

    def wake_and_unlock(self) -> None:
        """Wake, then dismiss the (PIN-less) lock screen. Polls instead of fixed sleeps.
        `dismiss-keyguard` cannot bypass a PIN/pattern: with one set, walink stops as before."""
        if not self.screen_on():
            self._adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")   # ~0.9 s (JVM); no faster wake here
            if not self._poll(self.screen_on, 5):
                raise DeviceError("screen did not turn on")
        if not self.keyguard_showing():
            return
        self._adb("shell", "cmd", "window", "dismiss-keyguard")         # ~0.06 s, gone in ~0.5 s
        if self._poll(lambda: not self.keyguard_showing(), 2.5):
            return
        log.info("dismiss-keyguard did not unlock; swiping")
        self.pacer.click_pause()
        self._swipe(*UNLOCK_SWIPE, duration=0.4)
        if not self._poll(lambda: not self.keyguard_showing(), 2.5):
            raise DeviceError("phone is locked (PIN/biometric not supported); stopping")

    # `input keyevent` and `monkey` start a JVM on the phone (~0.9 s each); `am start` is ~0.1 s.
    def _launch_app(self) -> None:
        self._adb("shell", "am", "start", "-n", f"{S.PKG}/{S.ACT_LAUNCHER}")

    def _app_running(self) -> bool:
        return bool(self._adb("shell", "pidof", S.PKG, check=False).strip())

    def go_home_screen(self) -> None:
        self._adb("shell", "am", "start", "-a", "android.intent.action.MAIN",
                  "-c", "android.intent.category.HOME")

    def _on_chat_list(self, timeout: float = 0) -> bool:
        return self.activity() == S.ACT_HOME and self.exists(S.SEARCH_BAR, timeout=timeout)

    def go_home_and_reset_app(self) -> None:
        """Cold 'step 0': force-stop and relaunch to the chat list. Used after failures and by keepalive."""
        self.wake_and_unlock()
        self.go_home_screen()
        self._adb("shell", "am", "force-stop", S.PKG)       # does not clear data
        if not self._poll(lambda: not self._app_running(), 5, step=0.2):
            log.warning("%s still running 5 s after force-stop; relaunching anyway", S.PKG)
        self._launch_app()
        self.wait_activity(S.ACT_HOME, timeout=20)

    def open_app_home(self) -> None:
        """Warm 'step 0', like a person reopening the app: bring WhatsApp to the front, back out to the
        chat list (max 3 backs). Anything unexpected falls back to the cold go_home_and_reset_app()."""
        self.wake_and_unlock()
        self._launch_app()
        try:
            self.wait_activity(S.ACT_HOME, timeout=6)
        except DeviceError:
            pass
        for _ in range(3):
            if self._on_chat_list(timeout=1.0):
                return
            if self.package() != S.PKG:
                break
            self.press_back()
        if self._on_chat_list(timeout=1.0):
            return
        log.info("warm open did not reach the chat list; cold restart")
        self.go_home_and_reset_app()

    def leave_app(self) -> None:
        """End of a successful run: back out to the chat list (max 4 backs), then the launcher home."""
        for _ in range(4):
            if self.package() != S.PKG or self._on_chat_list():
                break
            self.press_back()
        self.go_home_screen()

    def lock_screen(self) -> None:
        """Screen off, which locks the phone. KEYCODE_SLEEP (unlike POWER) never turns it back on."""
        self._adb("shell", "input", "keyevent", "KEYCODE_SLEEP")
        if not self._poll(lambda: not self.screen_on(), 5):
            raise DeviceError("screen did not turn off")

    # ---------- failure evidence ----------
    def save_evidence(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            (directory / "screen.png").write_bytes(self._adb("exec-out", "screencap", "-p", binary=True))
            (directory / "hierarchy.xml").write_text(self._d.dump_hierarchy(), encoding="utf-8")
        except Exception as e:  # never mask the original error
            log.error("could not save evidence: %s", e)

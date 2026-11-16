"""Keep WhatsApp Business on the phone up to date from Google Play, through goopdl (anonymous, no Google account).
Meant for a daily cron job: `walink.py update`.

One run:
1. ask Google Play (goopdl) for the newest versionCode: the "version check";
2. read the installed versionCode over adb (phone lock held); nothing to do when it is not older;
3. download the base APK + split APKs for the phone's ABI (no lock: the phone stays usable meanwhile);
4. `adb install-multiple -r` and read the version back (phone lock held again). App data is kept.

Silence, for cron (which mails any output):
- goopdl fails (Play, the token dispenser, the network): nothing on stdout/stderr, exit 0. Play and the dispenser fail
  now and then; tomorrow's run tries again. The failure goes to logs/update.log only.
- the phone is busy (another walink run): nothing printed, exit 5. Tomorrow's run tries again.
- up to date: nothing printed, exit 0.
- installed: one line on stdout. adb/install failures: stderr, exit 2 (a person should look).

Staleness: state/update.json remembers the last successful version check. When there has been none for
`update.stale_after_days` (default 7) days, every run calls notify_maintainer(), so silent goopdl failures cannot
go unnoticed for long. Counting starts at the first run when there has never been a successful check.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import device, lock, paths
from .audit import audit, notify
from .selectors import PKG

log = logging.getLogger("walink")

STATE_PATH = paths.STATE / "update.json"
LOG_PATH = paths.LOGS / "update.log"
GOOPDL_DIR = paths.STATE / "goopdl"       # goopdl's token cache
DEFAULT_STALE_AFTER_DAYS = 7
DAY_S = 24 * 3600

EXIT_OK, EXIT_FAILED, EXIT_BUSY = 0, 2, 5

# Android ABI (ro.product.cpu.abi) -> goopdl --arch.
ABI_TO_ARCH = {"arm64-v8a": "arm64", "armeabi-v7a": "armv7", "armeabi": "armv7", "x86": "x86", "x86_64": "x86_64"}


class PlayError(Exception):
    """goopdl failed. Always handled silently."""


@dataclass(frozen=True)
class Release:
    code: int
    name: str


class GooglePlay:
    """The only place that uses goopdl. Every goopdl failure comes out as PlayError."""

    def __init__(self, package: str = PKG, config_dir: Path | None = None):
        self.package = package
        self.config_dir = config_dir or GOOPDL_DIR

    def _client(self, arch: str = "arm64"):
        """One fixed device profile per arch (the one goopdl ranks first for new rollouts), so the version check
        and the download share one token pool. Unpinned, each picks its own profile and pool, and the download asks
        Aurora's token dispenser (~20 requests/hour per IP) for fresh tokens and often gets HTTP 429."""
        from goopdl import Client, Config      # imported here: only `update` needs goopdl and its dependencies
        from goopdl.profiles import get_latest_probe_profiles
        self.config_dir.mkdir(parents=True, exist_ok=True)
        ranked = get_latest_probe_profiles(arch, n=1)
        return Client(Config(config_dir=self.config_dir.resolve(), credentials=None, arch=arch,
                             profile=ranked[0][0] if ranked else None))

    def _call(self, fn: Callable):
        """Anything goopdl raises is "goopdl failed" (its GoopdlError family, but also a disk-full OSError or a bug
        in goopdl): handled silently, and the stale-check alert catches it if it keeps happening."""
        try:
            return fn()
        except Exception as e:
            raise PlayError(f"{type(e).__name__}: {e}") from None

    def latest(self) -> Release:
        d = self._call(lambda: self._client().details(self.package))
        if not d.version_code:
            raise PlayError("Google Play returned no versionCode")
        return Release(d.version_code, d.version_string)

    def download(self, arch: str, dest: Path) -> list[Path]:
        """Base APK first, then the split APKs."""
        r = self._call(lambda: self._client(arch).download(self.package, dest, splits=True))
        files = [Path(f) for f in r.files if Path(f).suffix == ".apk"]
        if not files:
            raise PlayError("goopdl downloaded no APK")
        return files


# ---------- state and staleness ----------
def load_state(path: Path | None = None) -> dict:
    path = path or STATE_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def stale_after_s(cfg: dict) -> float:
    days = (cfg.get("update") or {}).get("stale_after_days", DEFAULT_STALE_AFTER_DAYS)
    if isinstance(days, bool) or not isinstance(days, (int, float)) or days <= 0:
        days = DEFAULT_STALE_AFTER_DAYS
    return float(days) * DAY_S


def stale_for(state: dict, now: float) -> float:
    """Seconds since the last successful version check (or since the first run, if none ever succeeded)."""
    since = state.get("last_check_ok") or state.get("first_run") or now
    return max(0.0, now - since)


def notify_maintainer(cfg: dict, subject: str, body: str) -> None:
    """Alert the system maintainer. SCAFFOLDING: the notification system does not exist yet.

    For now the alert only goes to logs/update.log and logs/audit.log, and never to stdout/stderr (cron stays
    quiet). Called once per run while the version check is stale, so with a daily cron the reminder repeats daily.
    """
    # TODO(notifications): deliver `subject` and `body` to the maintainer (mail, ntfy, ...) once the notification
    #  system exists. Must stay best effort: never raise, never block the run for long, never print.
    _log_line(f"MAINTAINER {subject}: {body}")
    audit("update", "UPDATE_STALE", subject)


# ---------- the run ----------
def _log_line(text: str) -> None:
    log.info(text)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%F %T')} {text}\n")
    except OSError:
        pass


def _check_stale(cfg: dict, state: dict, now: float) -> None:
    age = stale_for(state, now)
    if age < stale_after_s(cfg):
        return
    last = state.get("last_check_ok")
    when = time.strftime("%F %T", time.localtime(last)) if last else "never"
    notify_maintainer(cfg, "WhatsApp Business version check is stale",
                      f"no successful Google Play version check for {age / DAY_S:.1f} days (last: {when}); "
                      f"goopdl keeps failing, see logs/update.log")


def run(cfg: dict, serial: str | None = None, dry_run: bool = False, play: GooglePlay | None = None,
        now: Callable[[], float] = time.time, out: Callable[[str], None] = print,
        err: Callable[[str], None] = lambda s: print(s, file=sys.stderr)) -> int:
    play = play or GooglePlay()
    serial = serial or cfg.get("serial")
    state = load_state()
    state.setdefault("first_run", now())

    # 1. version check
    try:
        latest = play.latest()
    except PlayError as e:
        _log_line(f"goopdl failed (version check): {e}")
        state["last_error"] = f"{time.strftime('%F %T')} {e}"
        save_state(state)
        _check_stale(cfg, state, now())
        return EXIT_OK
    state["last_check_ok"] = now()
    state["play_version"] = {"code": latest.code, "name": latest.name}
    state.pop("last_error", None)
    save_state(state)

    try:
        # 2. installed version
        with _phone():
            serial = device.ensure_adb(serial)
            installed = device.installed_version(serial, PKG)
            abi = device.primary_abi(serial)
        if installed is None:
            raise device.DeviceError(f"{PKG} is not installed on the phone; install it once by hand")
        code, name = installed
        if code >= latest.code:
            _log_line(f"up to date: installed {name} ({code}), Google Play {latest.name} ({latest.code})")
            return EXIT_OK
        arch = ABI_TO_ARCH.get(abi)
        if arch is None:
            raise device.DeviceError(f"unsupported phone ABI {abi!r}")
        if dry_run:
            out(f"[dry-run] would update WhatsApp Business {name} ({code}) -> {latest.name} ({latest.code})")
            return EXIT_OK

        # 3. download (no lock)
        with tempfile.TemporaryDirectory(prefix="walink-update-") as tmp:
            try:
                files = play.download(arch, Path(tmp))
            except PlayError as e:
                _log_line(f"goopdl failed (download): {e}")
                return EXIT_OK
            _log_line(f"downloaded {latest.name} for {arch}: {', '.join(f.name for f in files)}")

            # 4. install + verify
            with _phone():
                serial = device.ensure_adb(serial)
                device.install_apks(serial, files)
                after = device.installed_version(serial, PKG)
        if after is None or after[0] <= code:
            raise device.DeviceError(f"install reported Success but the version is still "
                                     f"{after[1] if after else 'missing'} ({after[0] if after else '-'})")
    except lock.Busy:
        _log_line("phone busy (another walink run): skipped")
        return EXIT_BUSY
    except device.DeviceError as e:
        _log_line(f"FAILED: {e}")
        notify(cfg, audit("update", "UPDATE_FAILED", str(e)))
        err(f"error: WhatsApp Business update failed: {e}")
        return EXIT_FAILED

    msg = f"updated WhatsApp Business {name} ({code}) -> {after[1]} ({after[0]})"
    _log_line(msg)
    notify(cfg, audit("update", "UPDATE_INSTALLED", f"{name} -> {after[1]}"))
    out(msg)
    return EXIT_OK


@contextmanager
def _phone():
    """The shared phone lock (walink.lock) for one step; raises lock.Busy."""
    held = lock.acquire()
    try:
        yield
    finally:
        if held is not None:
            held.close()

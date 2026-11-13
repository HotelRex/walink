"""Every operation walink offers, with ALL of its rules in one place. The CLI, the TUI and the SSH entry point
are thin front ends over these functions, so they cannot behave differently.

Rules applied here:
- protected devices (config `protected_devices`) are never removed or replaced;
- any removal asks `ctx.confirm("Are you sure you want to delete <NAME>?")` first (not in dry-run);
- policy checks and the confirmation run BEFORE the phone is touched (no lock, no device);
- the phone lock is held for one action only;
- every action is audited; link, remove, last message and failures are also notified; never message content;
- on failure: evidence in logs/<timestamp>/, app reset; the screen is locked at the end (except `status`);
- WhatsApp is left on the screen the action ended on, which is remembered (walink.screen_state): the next action
  within 5 minutes continues from it when the phone still shows it, instead of reopening WhatsApp.
"""
from __future__ import annotations

import getpass
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from . import flows, lock, paths, screen_state
from .audit import audit, notify
from .code import normalize_code
from .config import ConfigError, make_pacer
from .device import Device, DeviceError

log = logging.getLogger("walink")

CONFIRM_TEXT = "Are you sure you want to delete {name}?"
# Fixed on purpose: keepalive keeps the app open for a moment so it reconnects to WhatsApp's servers.
KEEPALIVE_ONLINE_S = 5
# Failures explained in one line; anything else is logged with a traceback.
EXPECTED_ERRORS = (DeviceError, ConfigError)


class Refused(Exception):
    """The action was not allowed or not confirmed; nothing (more) was changed."""


class ActionFailed(Exception):
    def __init__(self, message: str, evidence: Path | None = None):
        super().__init__(message)
        self.evidence = evidence


@dataclass
class Context:
    cfg: dict
    label: str                              # who: SSH key label or "local:<user>" (audit only)
    confirm: Callable[[str], bool]          # asked before any removal
    serial: str | None = None
    dry_run: bool = False
    device_factory: Callable = Device       # (serial, pacer) -> Device; replaced in tests
    progress: Callable[[str, float], None] = lambda text, fraction: None   # plain-words step, 0..1 overall


def _part(ctx: Context, lo: float, hi: float) -> Callable[[str, float], None]:
    """A flow's own 0..1 progress mapped into [lo, hi] of the action's bar."""
    return lambda text, fraction: ctx.progress(text, lo + (hi - lo) * fraction)


def default_label() -> str:
    try:
        return f"local:{getpass.getuser()}"
    except Exception:
        return "local:?"


def quick_chats(cfg: dict) -> list[str]:
    """Chats offered as quick picks for `last` (a helper, not a restriction)."""
    chats = cfg.get("quick_chats")
    if chats is None:
        chats = (cfg.get("remote") or {}).get("readable_chats")
    return list(chats or [])


def _protected(cfg: dict) -> list[str]:
    return list(cfg.get("protected_devices") or [])


def _check_removal(ctx: Context, name: str) -> None:
    if name in _protected(ctx.cfg):
        raise Refused(f"'{name}' is protected")
    if not ctx.dry_run and not ctx.confirm(CONFIRM_TEXT.format(name=name)):
        raise Refused("cancelled: nothing was removed")


@contextmanager
def _session(ctx: Context, action: str, detail: str = "", notify_ok: bool = False,
             phone_lock: bool = True) -> Iterator:
    """Lock, device, per-run log, audit, recovery and screen lock around one action."""
    held = lock.acquire() if phone_lock else None
    run_dir = paths.LOGS / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(run_dir / "walink.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    if log.level == logging.NOTSET:
        log.setLevel(logging.INFO)
    dry = " dry-run" if ctx.dry_run else ""

    def event(name: str, send: bool) -> None:
        line = audit(ctx.label, f"{action}_{name}", detail + dry)
        if send:
            notify(ctx.cfg, line)

    dev = None
    try:
        event("START", notify_ok)
        ctx.progress("Connecting to the phone", 0.02)
        dev = ctx.device_factory(ctx.serial or ctx.cfg.get("serial"), make_pacer(ctx.cfg))
        yield dev
        event("OK", notify_ok)
    except Refused as e:
        event("REFUSED", notify_ok)
        log.error("REFUSED: %s", e)
        raise
    except Exception as e:
        if isinstance(e, EXPECTED_ERRORS):
            log.error("FAILED: %s", e)
        else:
            log.exception("FAILED (unexpected %s): %s", type(e).__name__, e)
        evidence = None
        screen_state.forget()
        if dev:
            dev.save_evidence(run_dir)
            evidence = run_dir
            try:
                dev.go_home_and_reset_app()
            except Exception as reset_error:
                log.error("could not reset the app: %s", reset_error)
        event("FAILED", True)
        raise ActionFailed(str(e), evidence) from e
    finally:
        try:
            if dev and phone_lock and ctx.cfg.get("lock_screen_when_done", True):
                try:
                    ctx.progress("Locking the phone", 0.97)
                    dev.lock_screen()
                except Exception as lock_error:
                    log.error("could not lock the screen: %s", lock_error)
        finally:
            log.removeHandler(handler)
            handler.close()
            if held is not None:
                held.close()


def _to_linked_devices(ctx: Context, dev) -> None:
    """The Linked devices list: continue on it if the previous action left it open, else open it."""
    last = screen_state.recall()
    if last and last["screen"] == screen_state.LINKED_DEVICES and flows.resume_linked_devices(dev):
        log.info("continuing on the Linked devices screen left by the previous action")
        ctx.progress("Continuing on the Linked devices screen", 0.08)
    else:
        ctx.progress("Opening WhatsApp", 0.05)
        flows.open_linked_devices(dev)
    screen_state.remember(screen_state.LINKED_DEVICES)


def status(ctx: Context) -> dict:
    with _session(ctx, "STATUS", phone_lock=False) as dev:
        ctx.progress("Reading the phone status", 0.5)
        return dev.info()


def list_devices(ctx: Context) -> dict:
    """{'count', 'max', 'devices': [{'name', 'status', 'protected'}]}"""
    with _session(ctx, "LIST") as dev:
        _to_linked_devices(ctx, dev)
        ctx.progress("Reading the linked devices", 0.6)
        info = flows.read_devices(dev)
    protected = _protected(ctx.cfg)
    return {**info, "devices": [{**d, "protected": d["name"] in protected} for d in info["devices"]]}


def link(ctx: Context, code: str, *, replace: str | None = None, name: str | None = None) -> dict:
    """Replace device `replace` (removed first) or use a free slot; the new device is called `name`
    (default: the replaced name). Returns the verified row {'name', 'status'}."""
    code = normalize_code(code)
    new_name = name or replace
    if not new_name:
        raise Refused("a name for the new device is needed")
    if replace:
        _check_removal(ctx, replace)
    detail = f"replace='{replace}' name='{new_name}'" if replace else f"name='{new_name}'"
    with _session(ctx, "LINK", detail, notify_ok=True) as dev:
        _to_linked_devices(ctx, dev)
        ctx.progress("Reading the linked devices", 0.15)
        info = flows.read_devices(dev)
        if replace:
            if replace not in [d["name"] for d in info["devices"]]:
                raise Refused(f"no such device: {replace}")
            ctx.progress(f"Removing '{replace}'", 0.25)
            flows.remove_device(dev, replace, ctx.dry_run)
        elif info["count"] >= info["max"]:
            raise Refused("the list is full: replace a device instead")
        if ctx.dry_run:
            log.info("[dry-run] would link code %s as '%s'", code[:2] + "******", new_name)
            return {"name": new_name, "status": "dry-run"}
        flows.link_with_code(dev, code, new_name, _part(ctx, 0.35, 0.85))
        ctx.progress("Checking that the new device is linked", 0.88)
        res = flows.verify_linked(dev, new_name)
        log.info("CONFIRMED: %s", res)
        screen_state.remember(screen_state.LINKED_DEVICES)      # back on the list
        return res


def remove(ctx: Context, name: str) -> None:
    _check_removal(ctx, name)
    with _session(ctx, "REMOVE", f"name='{name}'", notify_ok=True) as dev:
        _to_linked_devices(ctx, dev)
        ctx.progress("Reading the linked devices", 0.4)
        if name not in [d["name"] for d in flows.read_devices(dev)["devices"]]:
            raise Refused(f"no such device: {name}")
        ctx.progress(f"Removing '{name}'", 0.6)
        flows.remove_device(dev, name, ctx.dry_run)
        screen_state.remember(screen_state.LINKED_DEVICES)


def last_message(ctx: Context, chat: str) -> dict:
    """Open `chat` (marks it read) and return its newest message. The content is returned, never logged."""
    with _session(ctx, "LAST", f"chat='{chat}'", notify_ok=True) as dev:
        last = screen_state.recall()
        resume = bool(last and last["screen"] == screen_state.CHAT and last["chat"] == chat)
        msg = flows.last_message(dev, chat, _part(ctx, 0.05, 0.9), resume=resume)
        screen_state.remember(screen_state.CHAT, chat)
        log.info("read last message of '%s' (%s, %s)", chat, msg["direction"], msg["kind"])
        return msg


def keepalive(ctx: Context) -> None:
    screen_state.forget()
    with _session(ctx, "KEEPALIVE") as dev:
        ctx.progress("Restarting WhatsApp", 0.1)
        dev.go_home_and_reset_app()
        ctx.progress("Keeping WhatsApp online for a moment", 0.6)
        time.sleep(KEEPALIVE_ONLINE_S)
        dev.go_home_screen()


def reset(ctx: Context) -> None:
    """Force-stop + relaunch WhatsApp, then the launcher (recovery)."""
    screen_state.forget()
    with _session(ctx, "RESET") as dev:
        ctx.progress("Restarting WhatsApp", 0.2)
        dev.go_home_and_reset_app()
        dev.go_home_screen()

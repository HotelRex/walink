#!/usr/bin/env python3
"""Forced-command entry point for SSH. authorized_keys line (one per workstation):

  restrict,command="/path/.venv/bin/python /path/walink_remote.py alice-pc" ssh-ed25519 AAAA... alice-pc
  (Windows controller: command="C:\\walink\\.venv\\Scripts\\python.exe C:\\walink\\walink_remote.py alice-pc"; see README)

The key can run only this program. Accepted requests (SSH_ORIGINAL_COMMAND):
  relink "<NAME>" <CODE>   replace an allowed device (remote.allowed_names) with a new link
  last "<CHAT>"            print the newest message of an allowed chat (remote.readable_chats);
                           opening the chat marks it as read on the phone
"""
import json
import os
import subprocess
import sys
import time

from walink import lock, paths
from walink.config import ConfigError, load_config
from walink.messages import format_message
from walink.remote import parse_request, Rejected, LABEL_RE

AUDIT_LOG = paths.LOGS / "remote-audit.log"
NOTIFY_TIMEOUT_S = 20
RESET_TIMEOUT_S = 90
EXIT_FAILED, EXIT_MISCONFIGURED, EXIT_REJECTED, EXIT_BUSY = 2, 3, 4, 5


def _client_ip() -> str:
    parts = os.environ.get("SSH_CLIENT", "").split()
    return parts[0] if parts else "?"


def audit(label: str, event: str, detail: str = "") -> str:
    line = f"{time.strftime('%F %T')} label={label} ip={_client_ip()} {event} {detail}".strip()
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return line


def notify(cfg: dict, message: str) -> None:
    """Best effort: a broken or slow notifier must never stop or fail a request."""
    cmd = cfg.get("notify_cmd")
    if not cmd:
        return
    try:
        subprocess.run(cmd, shell=True, input=message, text=True, encoding="utf-8", timeout=NOTIFY_TIMEOUT_S,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:   # not via audit(): keep it out of notify loops
            f.write(f"{time.strftime('%F %T')} NOTIFY_FAILED {type(e).__name__}\n")


def run_cli(cfg: dict, *args: str, timeout: float | None = None):
    """Run walink.py under the lock this process already holds. Returns CompletedProcess or None on timeout."""
    env = dict(os.environ, WALINK_LOCK_HELD="1", PYTHONIOENCODING="utf-8")
    try:
        return subprocess.run([sys.executable, str(paths.ROOT / "walink.py"), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout or cfg.get("timeout_s", 240), cwd=paths.ROOT, env=env)
    except subprocess.TimeoutExpired:
        return None


def _recover_after_timeout(cfg: dict, label: str) -> None:
    """The killed run may have left WhatsApp mid-flow and the phone unlocked: reset the app and lock it."""
    r = run_cli(cfg, "reset", timeout=RESET_TIMEOUT_S)
    audit(label, "TIMEOUT_RESET_OK" if r is not None and r.returncode == 0 else "TIMEOUT_RESET_FAILED")


def _lock_then_rate(cfg: dict, label: str):
    return lock.acquire()          # raises lock.Busy


def do_relink(cfg_all: dict, cfg: dict, label: str, name: str, code: str) -> int:
    if name in cfg_all.get("protected_devices", []):
        raise Rejected("device is protected")
    held = _lock_then_rate(cfg, label)  # noqa: F841 (keep reference)
    notify(cfg, audit(label, "START", f"replace='{name}'"))
    r = run_cli(cfg, "link", code, "--replace", name, "--confirm-remove", name)
    if r is None:
        _recover_after_timeout(cfg, label)
    ok = r is not None and r.returncode == 0
    event = "OK" if ok else ("TIMEOUT" if r is None else "FAILED")
    notify(cfg, audit(label, event, f"replace='{name}'"))
    print(f"OK: '{name}' re-linked and confirmed on the phone" if ok else
          "FAILED: not confirmed. Check the workstation; the old entry may already be removed.")
    return 0 if ok else EXIT_FAILED


def do_last(cfg: dict, label: str, chat: str) -> int:
    held = _lock_then_rate(cfg, label)  # noqa: F841 (keep reference)
    r = run_cli(cfg, "last", chat, "--json")
    if r is None:
        _recover_after_timeout(cfg, label)
    msg = None
    if r is not None and r.returncode == 0 and r.stdout.strip():
        try:
            msg = json.loads(r.stdout)       # stdout carries only the result; logs go to stderr
        except ValueError:
            pass
    # never put message content in the audit log or the notification
    notify(cfg, audit(label, "LAST_OK" if msg else "LAST_FAILED", f"chat='{chat}'"))
    if not msg:
        print("FAILED: could not read the last message")
        return EXIT_FAILED
    print(format_message(msg))
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):   # the SSH channel is a pipe: cp1252 on Windows otherwise
        stream.reconfigure(encoding="utf-8", errors="replace")
    label = sys.argv[1] if len(sys.argv) > 1 else ""
    if not LABEL_RE.fullmatch(label):
        print("misconfigured key (missing label)")
        return EXIT_MISCONFIGURED
    try:
        cfg_all = load_config()
    except ConfigError as e:
        audit(label, "CONFIG_ERROR", str(e).splitlines()[0])
        print("server misconfigured")
        return EXIT_MISCONFIGURED
    cfg = cfg_all.get("remote") or {}
    try:
        req = parse_request(os.environ.get("SSH_ORIGINAL_COMMAND"),
                            cfg.get("allowed_names", []), cfg.get("readable_chats", []))
        if req[0] == "relink":
            return do_relink(cfg_all, cfg, label, req[1], req[2])
        return do_last(cfg, label, req[1])
    except lock.Busy:
        print("busy: the phone is in use")
        return EXIT_BUSY
    except Rejected as e:
        notify(cfg, audit(label, "REJECTED", str(e)))
        print(f"rejected: {e}")
        return EXIT_REJECTED


if __name__ == "__main__":
    sys.exit(main())

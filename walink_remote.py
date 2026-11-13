#!/usr/bin/env python3
"""Forced-command entry point for SSH. authorized_keys line (one per workstation; `pty` is needed for the TUI):

  restrict,pty,command="/path/.venv/bin/python /path/walink_remote.py alice-pc" ssh-ed25519 AAAA... alice-pc
  (Windows controller: command="C:\\walink\\.venv\\Scripts\\python.exe C:\\walink\\walink_remote.py alice-pc"; see README)

- `ssh walink@controller`                       opens the TUI (same as running walink.py with no arguments)
- `ssh walink@controller "list --json"`         runs any walink command once (same syntax as walink.py)
- `ssh walink@controller "relink 'Hotel Rex' CODE"`   older form, = link CODE --replace "Hotel Rex" --yes

The label names the key in the audit log. All rules (protected devices, confirmation, lock) live in walink.actions.
"""
import os
import subprocess
import sys

from walink import cli, paths
from walink.audit import audit, notify
from walink.config import ConfigError, load_config
from walink.remote import LABEL_RE, Rejected, to_argv

RESET_TIMEOUT_S = 90
DEFAULT_TIMEOUT_S = 240
EXIT_FAILED, EXIT_MISCONFIGURED, EXIT_REJECTED = 2, 3, 4


def _run_child(argv: list[str], label: str, timeout: float) -> int | None:
    """walink.py <argv> with output streaming straight to the SSH client. None on timeout (the child is killed)."""
    env = dict(os.environ, WALINK_AUDIT_LABEL=label, PYTHONIOENCODING="utf-8")
    try:
        return subprocess.run([sys.executable, str(paths.ROOT / "walink.py"), *argv],
                              timeout=timeout, cwd=paths.ROOT, env=env).returncode
    except subprocess.TimeoutExpired:
        return None


def main() -> int:
    for stream in (sys.stdout, sys.stderr):   # the SSH channel is a pipe: cp1252 on Windows otherwise
        stream.reconfigure(encoding="utf-8", errors="replace")
    label = sys.argv[1] if len(sys.argv) > 1 else ""
    if not LABEL_RE.fullmatch(label):
        print("misconfigured key (missing label)", file=sys.stderr)
        return EXIT_MISCONFIGURED

    original = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if not original.strip():
        return cli.main([], label=label)      # the TUI (or help, without a pty)

    try:
        argv = to_argv(original)
    except Rejected as e:
        audit(label, "REJECTED", str(e))
        print(f"rejected: {e}", file=sys.stderr)
        return EXIT_REJECTED
    try:
        cfg = load_config()
    except ConfigError as e:
        audit(label, "CONFIG_ERROR", str(e).splitlines()[0])
        print("server misconfigured", file=sys.stderr)
        return EXIT_MISCONFIGURED

    timeout = (cfg.get("remote") or {}).get("timeout_s", DEFAULT_TIMEOUT_S)
    code = _run_child(argv, label, timeout)
    if code is not None:
        return code
    # The killed run may have left WhatsApp mid-flow and the phone unlocked: reset the app and lock it.
    notify(cfg, audit(label, "TIMEOUT", f"after {timeout}s: {argv[0]}"))
    reset_code = _run_child(["reset"], label, RESET_TIMEOUT_S)
    audit(label, "TIMEOUT_RESET_OK" if reset_code == 0 else "TIMEOUT_RESET_FAILED")
    print(f"FAILED: timed out after {timeout}s; the phone was reset. Check the device list.", file=sys.stderr)
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())

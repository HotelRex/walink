"""Command line: a thin front end over walink.actions (all rules live there).

No command (and an interactive terminal) opens the TUI. stdout carries ONLY the command's result (plain text or
--json); logs go to stderr and to logs/<timestamp>/walink.log, so `walink.py list --json | jq` works.
Exit codes: 0 ok, 2 failed or usage error, 4 refused (protected, not confirmed, ...), 5 phone busy."""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
from typing import Callable

from . import actions, lock
from .code import CodeError
from .config import ConfigError, load_config
from .messages import format_message

log = logging.getLogger("walink")

EXIT_OK, EXIT_FAILED, EXIT_REFUSED, EXIT_BUSY = 0, 2, 4, 5


class _RemovedFlag(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        parser.error("--confirm-remove was removed; the command now asks for confirmation (use --yes in scripts)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="walink", description="Run without a command to open the TUI.")
    parser.add_argument("--serial")
    parser.add_argument("--dry-run", action="store_true", help="go as far as possible, change nothing")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status")
    p_list = sub.add_parser("list")
    p_list.add_argument("--json", action="store_true")
    p_link = sub.add_parser("link", help="link CODE: replace --replace NAME (removed first) or use a free slot")
    p_link.add_argument("code")
    p_link.add_argument("--replace", metavar="NAME", help="existing device to delete first")
    p_link.add_argument("--name", help="name for the new device (default: the replaced name)")
    p_unlink = sub.add_parser("unlink", help="delete a linked device")
    p_unlink.add_argument("name")
    for p in (p_link, p_unlink):
        p.add_argument("--yes", action="store_true", help="do not ask 'Are you sure?' (for scripts)")
        p.add_argument("--confirm-remove", nargs="?", action=_RemovedFlag, help=argparse.SUPPRESS)
    p_last = sub.add_parser("last", help="open the chat NAME (marks it read) and print its newest message")
    p_last.add_argument("name")
    p_last.add_argument("--json", action="store_true")
    sub.add_parser("keepalive")
    sub.add_parser("reset", help="force-stop + relaunch WhatsApp, then go to the launcher (recovery)")
    return parser


def _utf8_stdio() -> None:
    """Emoji and accented chat names must not crash print() when stdout is a pipe on Windows (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def setup_logging() -> None:
    """Log lines to stderr (each action adds its own per-run file log)."""
    for old in list(log.handlers):
        if getattr(old, "_walink_cli", False):
            log.removeHandler(old)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler._walink_cli = True
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def tty_confirm(question: str) -> bool:
    print(f"{question} [y/N] ", end="", flush=True)
    answer = sys.stdin.readline().strip().lower()
    return answer in ("y", "yes")


def _confirm_for(args) -> Callable[[str], bool]:
    if getattr(args, "yes", False):
        return lambda question: True
    if sys.stdin.isatty():
        return tty_confirm

    def refuse(question: str) -> bool:
        print(f"refusing: not an interactive terminal; pass --yes ({question})", file=sys.stderr)
        return False
    return refuse


def _render_list(info: dict) -> str:
    rows = [f"- {'[locked] ' if d.get('protected') else ''}{d['name']}: {d['status']}" for d in info["devices"]]
    return "\n".join([f"{info['count']} of {info['max']}"] + rows)


def _run(args, ctx: actions.Context) -> None:
    if args.cmd == "status":
        print(json.dumps(actions.status(ctx), indent=2))
    elif args.cmd == "list":
        info = actions.list_devices(ctx)
        print(json.dumps(info, indent=2) if args.json else _render_list(info))
    elif args.cmd == "link":
        print(json.dumps(actions.link(ctx, args.code, replace=args.replace, name=args.name)))
    elif args.cmd == "unlink":
        actions.remove(ctx, args.name)
        print(f"{'[dry-run] would remove' if ctx.dry_run else 'removed'} '{args.name}'")
    elif args.cmd == "last":
        msg = actions.last_message(ctx, args.name)
        print(json.dumps(msg, ensure_ascii=False) if args.json else format_message(msg))
    elif args.cmd == "keepalive":
        actions.keepalive(ctx)
    elif args.cmd == "reset":
        actions.reset(ctx)


def main(argv: list[str] | None = None, label: str | None = None) -> int:
    _utf8_stdio()
    parser = build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as e:                     # usage errors: return, never exit (used in-process over SSH)
        return EXIT_FAILED if e.code else EXIT_OK
    label = label or os.environ.get("WALINK_AUDIT_LABEL") or actions.default_label()
    setup_logging()
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_FAILED

    if args.cmd is None:
        if not _interactive():
            parser.print_help(sys.stderr)
            return EXIT_FAILED
        tui = importlib.import_module("walink.tui")
        return tui.run(cfg, label, serial=args.serial or cfg.get("serial"), dry_run=args.dry_run)

    ctx = actions.Context(cfg=cfg, label=label, confirm=_confirm_for(args),
                          serial=args.serial or cfg.get("serial"), dry_run=args.dry_run)
    try:
        _run(args, ctx)
        return EXIT_OK
    except actions.Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except lock.Busy:
        print("busy: the phone is in use, try again", file=sys.stderr)
        return EXIT_BUSY
    except actions.ActionFailed as e:
        where = f" (evidence in {e.evidence})" if e.evidence else ""
        print(f"error: {e}{where}", file=sys.stderr)
        return EXIT_FAILED
    except (CodeError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())

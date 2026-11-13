"""Command line. stdout carries ONLY the command's result (plain text or --json); logs go to stderr
and to logs/<timestamp>/walink.log, so `walink.py list --json | jq` works."""
import argparse
import json
import logging
import sys
import time

from . import flows, lock, paths
from .code import normalize_code, CodeError
from .config import ConfigError, load_config, make_pacer
from .device import Device, DeviceError
from .messages import format_message

log = logging.getLogger("walink")

# Fixed on purpose: keepalive keeps the app open for a moment so it reconnects to WhatsApp's servers.
KEEPALIVE_ONLINE_S = 5

# Failures we expect and explain in one line; anything else is logged with a traceback.
EXPECTED_ERRORS = (DeviceError, CodeError, PermissionError, ConfigError, lock.Busy)


def _utf8_stdio() -> None:
    """Emoji and accented chat names must not crash print() when stdout is a pipe on Windows (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def setup_logging(run_dir) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for old in list(log.handlers):          # main() may run more than once in one process (tests)
        log.removeHandler(old)
        old.close()
    for handler in (logging.StreamHandler(sys.stderr),
                    logging.FileHandler(run_dir / "walink.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="walink")
    parser.add_argument("--serial")
    parser.add_argument("--dry-run", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_list = sub.add_parser("list")
    p_list.add_argument("--json", action="store_true")
    p_link = sub.add_parser("link", help="remove --replace NAME, then link CODE as a new device with that name")
    p_link.add_argument("code")
    p_link.add_argument("--replace", metavar="NAME", help="existing device to delete")
    p_link.add_argument("--name", help="name for the new device (default: the replaced name)")
    p_link.add_argument("--confirm-remove", metavar="NAME", help="must equal --replace to allow the real removal")
    p_unlink = sub.add_parser("unlink")
    p_unlink.add_argument("name")
    p_unlink.add_argument("--confirm-remove", metavar="NAME")
    p_last = sub.add_parser("last", help="open the chat NAME (marks it read) and print its newest message")
    p_last.add_argument("name")
    p_last.add_argument("--json", action="store_true")
    sub.add_parser("keepalive")
    sub.add_parser("reset", help="force-stop + relaunch WhatsApp, then go to the launcher (recovery)")
    return parser


def _guard_remove(cfg: dict, name: str, confirm: str | None, dry_run: bool) -> None:
    if name in cfg.get("protected_devices", []):
        raise PermissionError(f"'{name}' is protected")
    if not dry_run and confirm != name:
        raise PermissionError("real removal requires --confirm-remove with the exact same name")


def _run(args, cfg: dict, dev: Device) -> int:
    if args.cmd == "status":
        print(json.dumps(dev.info(), indent=2))
        return 0
    if args.cmd == "reset":
        dev.go_home_and_reset_app()
        dev.go_home_screen()
        return 0
    if args.cmd == "keepalive":
        dev.go_home_and_reset_app()
        time.sleep(KEEPALIVE_ONLINE_S)
        dev.go_home_screen()
        return 0
    if args.cmd == "last":
        msg = flows.last_message(dev, args.name)
        log.info("read last message of '%s' (%s, %s)", args.name, msg["direction"], msg["kind"])  # no content in logs
        dev.leave_app()
        print(json.dumps(msg, ensure_ascii=False) if args.json else format_message(msg))
        return 0

    flows.open_linked_devices(dev)
    if args.cmd == "list":
        info = flows.read_devices(dev)
        lines = [f"{info['count']} of {info['max']}"] + [f"- {d['name']}: {d['status']}" for d in info["devices"]]
        print(json.dumps(info, indent=2) if args.json else "\n".join(lines))
    elif args.cmd == "unlink":
        _guard_remove(cfg, args.name, args.confirm_remove, args.dry_run)
        flows.remove_device(dev, args.name, args.dry_run)
    elif args.cmd == "link":
        code = normalize_code(args.code)
        info = flows.read_devices(dev)
        names = [d["name"] for d in info["devices"]]
        name = args.name or args.replace
        if not name:
            raise flows.FlowError("need --name or --replace")
        if args.replace:
            if args.replace not in names:
                raise flows.FlowError(f"no such device: {args.replace}")
            _guard_remove(cfg, args.replace, args.confirm_remove, args.dry_run)
            flows.remove_device(dev, args.replace, args.dry_run)
        elif info["count"] >= info["max"]:
            raise flows.FlowError("list is full: use --replace NAME")
        if args.dry_run:
            log.info("[dry-run] would link code %s as '%s'", code[:2] + "******", name)
            return 0
        flows.link_with_code(dev, code, name)
        res = flows.verify_linked(dev, name)
        log.info("CONFIRMED: %s", res)
        print(json.dumps(res))
    dev.leave_app()
    return 0


def main(argv=None) -> int:
    _utf8_stdio()
    args = build_parser().parse_args(argv)
    run_dir = paths.LOGS / time.strftime("%Y%m%d-%H%M%S")
    setup_logging(run_dir)
    dev = None
    cfg: dict = {}
    try:
        cfg = load_config()
        pacer = make_pacer(cfg)
        _held = lock.acquire() if args.cmd != "status" else None   # noqa: F841 (keep reference)
        dev = Device(args.serial or cfg.get("serial"), pacer)
        return _run(args, cfg, dev)
    except Exception as e:
        if isinstance(e, EXPECTED_ERRORS):
            log.error("FAILED: %s", e)
        else:
            log.exception("FAILED (unexpected %s): %s", type(e).__name__, e)
        where = ""
        if dev:
            dev.save_evidence(run_dir)
            where = f" (evidence in {run_dir})"
            try:
                dev.go_home_and_reset_app()
            except Exception as reset_error:
                log.error("could not reset the app: %s", reset_error)
        print(f"error: {e}{where}", file=sys.stderr)
        return 2
    finally:
        if dev and args.cmd != "status" and cfg.get("lock_screen_when_done", True):
            try:
                dev.lock_screen()
            except Exception as e:
                log.error("could not lock the screen: %s", e)


if __name__ == "__main__":
    sys.exit(main())

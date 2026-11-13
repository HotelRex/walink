"""The CLI is a thin front end over walink.actions: parsing, confirmation, rendering, exit codes."""
import io
import json
import sys

import pytest

from walink import actions, cli, lock

DEVICES = {"count": 2, "max": 4, "devices": [{"name": "Hotel Rex", "status": "Active now", "protected": False},
                                             {"name": "Test", "status": "x", "protected": True}]}


class Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def calls(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "load_config", lambda: {"protected_devices": ["Test"]})

    def fake(name, result=None):
        def f(ctx, *a, **k):
            calls.append((name, ctx, a, k))
            if name in ("remove", "link") and not ctx.dry_run:
                if not ctx.confirm(actions.CONFIRM_TEXT.format(name=a[0] if name == "remove" else k["replace"])):
                    raise actions.Refused("cancelled: nothing was removed")
            return result
        return f
    monkeypatch.setattr(actions, "list_devices", fake("list", DEVICES))
    monkeypatch.setattr(actions, "remove", fake("remove"))
    monkeypatch.setattr(actions, "link", fake("link", {"name": "Hotel Rex", "status": "Active now"}))
    monkeypatch.setattr(actions, "status", fake("status", {"model": "x"}))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))          # not a tty unless a test says so
    return calls


def test_unlink_yes_skips_prompt(calls):
    assert cli.main(["unlink", "Sara", "--yes"]) == 0
    assert calls[0][0] == "remove" and calls[0][2] == ("Sara",)


def test_unlink_non_tty_without_yes_refused(calls, capsys):
    assert cli.main(["unlink", "Sara"]) == 4
    assert "pass --yes" in capsys.readouterr().err


def test_unlink_tty_asks(calls, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", Tty("y\n"))
    assert cli.main(["unlink", "Sara"]) == 0
    assert "Are you sure you want to delete Sara? [y/N]" in capsys.readouterr().out
    monkeypatch.setattr(sys, "stdin", Tty("\n"))
    assert cli.main(["unlink", "Sara"]) == 4


def test_link_replace_and_name_passed_through(calls):
    assert cli.main(["link", "abcd-efgh", "--replace", "Hotel Rex", "--yes"]) == 0
    assert calls[0][2] == ("abcd-efgh",) and calls[0][3] == {"replace": "Hotel Rex", "name": None}


def test_confirm_remove_flag_errors(calls, capsys):
    assert cli.main(["link", "ABCDEFGH", "--replace", "A", "--confirm-remove", "A"]) == 2
    assert "--yes" in capsys.readouterr().err and calls == []


@pytest.mark.parametrize("exc,code", [(actions.Refused("no"), 4), (lock.Busy("in use"), 5),
                                      (actions.ActionFailed("boom", None), 2)])
def test_exit_codes(calls, monkeypatch, exc, code):
    def raise_(ctx):
        raise exc
    monkeypatch.setattr(actions, "list_devices", raise_)
    assert cli.main(["list"]) == code


def test_failure_mentions_evidence(calls, monkeypatch, capsys, tmp_path):
    def raise_(ctx):
        raise actions.ActionFailed("boom", tmp_path)
    monkeypatch.setattr(actions, "list_devices", raise_)
    assert cli.main(["list"]) == 2
    assert str(tmp_path) in capsys.readouterr().err


def test_list_json_stdout_is_pure_json(calls, capsys):
    assert cli.main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == DEVICES


def test_list_text_marks_locked(calls, capsys):
    cli.main(["list"])
    assert "- [locked] Test: x" in capsys.readouterr().out


def test_dry_run_and_label_reach_context(calls, monkeypatch):
    monkeypatch.setenv("WALINK_AUDIT_LABEL", "pc")
    cli.main(["--dry-run", "list"])
    assert calls[0][1].dry_run and calls[0][1].label == "pc"
    cli.main(["list"], label="other")
    assert calls[1][1].label == "other"


def test_no_args_without_tty_prints_help(calls, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "walink.tui", None)        # importing it would fail
    assert cli.main([]) == 2
    assert "usage" in capsys.readouterr().err


def test_no_args_with_tty_starts_tui(calls, monkeypatch):
    import types
    seen = {}
    fake_tui = types.SimpleNamespace(run=lambda cfg, label, serial=None, dry_run=False, debug=False: seen.update(
        label=label, dry_run=dry_run, debug=debug) or 0)
    monkeypatch.setitem(sys.modules, "walink.tui", fake_tui)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    assert cli.main(["--dry-run", "--debug"], label="pc") == 0
    assert seen == {"label": "pc", "dry_run": True, "debug": True}


def test_steps_on_stderr_logs_only_with_debug(calls, monkeypatch, capsys):
    import logging

    def list_devices(ctx):
        ctx.progress("Opening WhatsApp", 0.05)
        logging.getLogger("walink").info("tap {'resourceId': 'x'}")
        return DEVICES
    monkeypatch.setattr(actions, "list_devices", list_devices)
    cli.main(["list"])
    err = capsys.readouterr().err
    assert "  5% Opening WhatsApp" in err and "tap {" not in err
    cli.main(["--debug", "list"])
    assert "tap {" in capsys.readouterr().err

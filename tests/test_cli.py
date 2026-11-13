"""CLI orchestration with a fake phone: guards, remove-then-link order, failure recovery, clean stdout."""
import json
import logging

import pytest

from walink import cli, flows, lock, paths


class FakeDevice:
    def __init__(self, serial=None, pacer=None):
        self.calls = []

    def __getattr__(self, name):            # every phone action is recorded and does nothing
        def record(*args, **kwargs):
            self.calls.append(name)
        return record


DEVICES = {"count": 4, "max": 4, "devices": [{"name": n, "status": "Active now"}
                                             for n in ("Hotel Rex", "Test", "A", "B")]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fake phone + recorded flow calls; logs in tmp; no real lock or config."""
    monkeypatch.setattr(paths, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(lock, "acquire", lambda: None)
    monkeypatch.setattr(cli, "load_config", lambda: {"protected_devices": ["Test"]})
    devs = []

    def make_device(*a, **k):
        devs.append(FakeDevice())
        return devs[-1]
    monkeypatch.setattr(cli, "Device", make_device)
    order = []

    def rec(name, result=None):
        def f(*a, **k):
            order.append((name, a[1:]))
            return result
        return f
    monkeypatch.setattr(flows, "open_linked_devices", rec("open"))
    monkeypatch.setattr(flows, "read_devices", rec("read", DEVICES))
    monkeypatch.setattr(flows, "remove_device", rec("remove"))
    monkeypatch.setattr(flows, "link_with_code", rec("link"))
    monkeypatch.setattr(flows, "verify_linked", rec("verify", {"name": "Hotel Rex", "status": "Active now"}))
    yield order, devs
    for h in list(logging.getLogger("walink").handlers):
        h.close()


def test_link_removes_first_then_links_normalized_code(env):
    order, devs = env
    assert cli.main(["link", "abcd-efgh", "--replace", "Hotel Rex", "--confirm-remove", "Hotel Rex"]) == 0
    assert [o[0] for o in order] == ["open", "read", "remove", "link", "verify"]
    assert order[3][1] == ("ABCDEFGH", "Hotel Rex")
    assert devs[0].calls[-2:] == ["leave_app", "lock_screen"]


@pytest.mark.parametrize("argv", [
    ["link", "ABCDEFGH", "--replace", "Test", "--confirm-remove", "Test"],      # protected
    ["link", "ABCDEFGH", "--replace", "Hotel Rex"],                             # no confirmation
    ["link", "ABCDEFGH", "--replace", "Hotel Rex", "--confirm-remove", "Hotel"],  # wrong confirmation
    ["unlink", "Test", "--confirm-remove", "Test"],
    ["link", "ABCDEFGH", "--replace", "Nope", "--confirm-remove", "Nope"],      # not in the list
    ["link", "ABCDEFGH", "--name", "New"],                                      # list full, no replace
])
def test_refusals_never_remove(env, argv):
    order, devs = env
    assert cli.main(argv) == 2
    assert "remove" not in [o[0] for o in order]
    assert "go_home_and_reset_app" in devs[0].calls and devs[0].calls[-1] == "lock_screen"


def test_dry_run_removes_nothing_for_real(env):
    order, _ = env
    assert cli.main(["--dry-run", "link", "ABCDEFGH", "--replace", "Hotel Rex"]) == 0
    assert ("remove", ("Hotel Rex", True)) in order
    assert "link" not in [o[0] for o in order]


def test_unexpected_error_still_saves_evidence_resets_and_locks(env, monkeypatch, capsys):
    _, devs = env

    def boom(*a, **k):
        raise KeyError("surprise")
    monkeypatch.setattr(flows, "read_devices", boom)
    assert cli.main(["list"]) == 2
    assert devs[0].calls[-3:] == ["save_evidence", "go_home_and_reset_app", "lock_screen"]
    assert "surprise" in capsys.readouterr().err


def test_list_json_stdout_is_pure_json(env, capsys):
    assert cli.main(["list", "--json"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out) == DEVICES          # logs did not leak into stdout
    assert "INFO" not in out.out


def test_busy_reports_no_evidence(env, monkeypatch, capsys):
    def busy():
        raise lock.Busy("the phone is in use")
    monkeypatch.setattr(lock, "acquire", busy)
    assert cli.main(["list"]) == 2
    err = capsys.readouterr().err
    assert "in use" in err and "evidence" not in err


def test_bad_timing_config_fails_cleanly(env, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: {"timing": {"click_ms": [650, 300]}})
    assert cli.main(["list"]) == 2
    assert "click_ms" in capsys.readouterr().err

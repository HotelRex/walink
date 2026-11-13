"""The SSH wrapper with the CLI subprocess faked."""
import json
import subprocess
from types import SimpleNamespace

import pytest

import walink_remote as wr
from walink import lock


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "AUDIT_LOG", tmp_path / "logs" / "remote-audit.log")
    monkeypatch.setattr(lock, "LOCK_PATH", tmp_path / "state" / "phone.lock")
    monkeypatch.delenv("WALINK_LOCK_HELD", raising=False)
    calls = []

    def fake_cli(cfg, *args, timeout=None):
        calls.append(args)
        return env.result(args)
    env = SimpleNamespace(calls=calls, result=lambda args: SimpleNamespace(returncode=0, stdout="", stderr=""),
                          audit=lambda: (tmp_path / "logs" / "remote-audit.log").read_text(encoding="utf-8"),
                          rate=tmp_path / "state" / "remote-rate.json")
    monkeypatch.setattr(wr, "run_cli", fake_cli)
    return env


CFG = {"max_attempts_per_hour": 2}


def test_relink_ok(env, capsys):
    assert wr.do_relink({}, CFG, "pc", "Hotel Rex", "ABCDEFGH") == 0
    assert env.calls == [("link", "ABCDEFGH", "--replace", "Hotel Rex", "--confirm-remove", "Hotel Rex")]
    assert "OK" in capsys.readouterr().out


def test_relink_protected_never_runs(env):
    with pytest.raises(wr.Rejected):
        wr.do_relink({"protected_devices": ["Test"]}, CFG, "pc", "Test", "ABCDEFGH")
    assert env.calls == []


def test_timeout_triggers_reset(env, capsys):
    env.result = lambda args: None if args[0] == "link" else SimpleNamespace(returncode=0, stdout="", stderr="")
    assert wr.do_relink({}, CFG, "pc", "Hotel Rex", "ABCDEFGH") == wr.EXIT_FAILED
    assert env.calls[1] == ("reset",)
    assert "TIMEOUT_RESET_OK" in env.audit() and "FAILED" in capsys.readouterr().out


def test_broken_notifier_does_not_stop_relink(env, monkeypatch):
    def hang(*a, **k):
        raise subprocess.TimeoutExpired("ntfy", 20)
    monkeypatch.setattr(wr.subprocess, "run", hang)
    assert wr.do_relink({}, dict(CFG, notify_cmd="ntfy"), "pc", "Hotel Rex", "ABCDEFGH") == 0
    assert "NOTIFY_FAILED" in env.audit()


def test_last_reads_json_stdout_and_keeps_content_out_of_audit(env, capsys):
    msg = {"chat": "PayPal", "text": "Code 123456 🔐", "day": "Today", "time": "1:00 PM",
           "direction": "incoming", "status": None, "sender": None, "kind": "text"}
    env.result = lambda args: SimpleNamespace(returncode=0, stdout=json.dumps(msg, ensure_ascii=False) + "\n", stderr="")
    assert wr.do_last(CFG, "pc", "PayPal") == 0
    assert "123456 🔐" in capsys.readouterr().out
    assert "LAST_OK" in env.audit() and "123456" not in env.audit()


def test_audit_survives_missing_ssh_client(env, monkeypatch):
    monkeypatch.setenv("SSH_CLIENT", "")
    assert "ip=?" in wr.audit("pc", "X")

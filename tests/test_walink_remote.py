"""The SSH forced command: TUI on a plain login, any CLI command one-shot, timeout + reset."""
import subprocess
import sys
from types import SimpleNamespace

import pytest

import walink_remote as wr
from walink import audit, cli, paths


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(sys, "argv", ["walink_remote.py", "pc"])
    monkeypatch.setattr(wr, "load_config", lambda: {"remote": {"timeout_s": 7}})
    runs = []
    state = SimpleNamespace(runs=runs, returncode=0, timeout_on=None,
                            audit=lambda: (tmp_path / "logs" / "audit.log").read_text(encoding="utf-8"))

    def fake_run(cmd, **kw):
        runs.append((cmd, kw))
        if state.timeout_on and cmd[2] == state.timeout_on:
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        return SimpleNamespace(returncode=state.returncode)
    monkeypatch.setattr(wr.subprocess, "run", fake_run)
    return state


def test_empty_command_runs_cli_in_process(env, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv, label=None: seen.append((argv, label)) or 0)
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    assert wr.main() == 0
    assert seen == [([], "pc")] and env.runs == []


def test_one_shot_passes_exit_code_and_label(env, monkeypatch):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "unlink Sara --yes")
    env.returncode = 4
    assert wr.main() == 4
    cmd, kw = env.runs[0]
    assert cmd[2:] == ["unlink", "Sara", "--yes"]
    assert kw["env"]["WALINK_AUDIT_LABEL"] == "pc" and kw["timeout"] == 7
    assert "capture_output" not in kw          # output streams live to the SSH client


def test_relink_alias_runs_link(env, monkeypatch):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "relink 'Hotel Rex' ABCDEFGH")
    wr.main()
    assert env.runs[0][0][2:] == ["link", "ABCDEFGH", "--replace", "Hotel Rex", "--yes"]


def test_timeout_runs_reset(env, monkeypatch, capsys):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "link ABCDEFGH --name X")
    env.timeout_on = "link"
    assert wr.main() == 2
    assert env.runs[1][0][2:] == ["reset"]
    assert "TIMEOUT" in env.audit() and "TIMEOUT_RESET_OK" in env.audit()
    assert "timed out" in capsys.readouterr().err


def test_rejected_request_is_audited(env, monkeypatch):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "last 'unterminated")
    assert wr.main() == 4
    assert "REJECTED" in env.audit() and env.runs == []


def test_bad_label(env, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["walink_remote.py", "bad label!"])
    assert wr.main() == 3


def test_cli_uses_env_label(monkeypatch):
    from walink import actions
    seen = []
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(actions, "status", lambda ctx: seen.append(ctx.label) or {})
    monkeypatch.setenv("WALINK_AUDIT_LABEL", "pc")
    cli.main(["status"])
    assert seen == ["pc"]

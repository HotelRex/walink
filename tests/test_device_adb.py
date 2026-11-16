"""adb health check before an action, and the one-time recovery (reconnect, kill-server + start-server)."""
import subprocess

import pytest

from walink import device as device_mod
from walink.device import DeviceError, ensure_adb


class FakeAdb:
    """Stands in for subprocess.run(["adb", ...]). `alive_after` = how many recovery steps it takes to come back
    (None: never). Records every adb command."""

    def __init__(self, alive_after=0, hang=False, devices=("SERIAL1",)):
        self.cmds, self.alive_after, self.hang, self.devices, self.steps = [], alive_after, hang, devices, 0

    @property
    def alive(self):
        return self.alive_after is not None and self.steps >= self.alive_after

    def __call__(self, argv, **kw):
        args = argv[1:]
        self.cmds.append(" ".join(args))
        if args[0] in ("kill-server", "connect"):
            self.steps += 1
        if args[0] in ("kill-server", "start-server", "connect"):
            return subprocess.CompletedProcess(argv, 0, "", "")
        if not self.alive:
            if self.hang:
                raise subprocess.TimeoutExpired(argv, kw["timeout"])
            return subprocess.CompletedProcess(argv, 1, "", "error: device offline")
        if args[0] == "devices":
            rows = "".join(f"{d}\tdevice\n" for d in self.devices)
            return subprocess.CompletedProcess(argv, 0, "List of devices attached\n" + rows, "")
        return subprocess.CompletedProcess(argv, 0, "device\n", "")          # get-state


@pytest.fixture
def adb(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(device_mod.time, "time", lambda: clock["t"])
    monkeypatch.setattr(device_mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))

    def install(**kw):
        fake = FakeAdb(**kw)
        monkeypatch.setattr(device_mod.subprocess, "run", fake)
        return fake
    return install


def test_healthy_adb_costs_one_quick_check(adb):
    fake = adb()
    assert ensure_adb("SERIAL1") == "SERIAL1"
    assert fake.cmds == ["-s SERIAL1 get-state"]


def test_hung_adb_server_is_restarted_then_the_action_goes_on(adb):
    fake = adb(alive_after=1, hang=True)
    assert ensure_adb("SERIAL1") == "SERIAL1"
    assert fake.cmds[1:3] == ["kill-server", "start-server"] and "connect SERIAL1" not in fake.cmds


def test_wifi_adb_tries_a_plain_reconnect_before_restarting_the_server(adb):
    fake = adb(alive_after=1)
    assert ensure_adb("192.168.1.50:5555") == "192.168.1.50:5555"
    assert "connect 192.168.1.50:5555" in fake.cmds and "kill-server" not in fake.cmds


def test_wifi_adb_reconnects_again_after_the_restart(adb):
    fake = adb(alive_after=2)
    ensure_adb("192.168.1.50:5555")
    i = fake.cmds.index("kill-server")
    assert fake.cmds[i:i + 3] == ["kill-server", "start-server", "connect 192.168.1.50:5555"]


def test_still_dead_after_one_restart_fails_with_a_clear_error(adb):
    fake = adb(alive_after=None, hang=True)
    with pytest.raises(DeviceError, match="restarted the adb server, still no phone at SERIAL1"):
        ensure_adb("SERIAL1")
    assert fake.cmds.count("kill-server") == 1


def test_no_serial_and_no_phone_restarts_once_then_finds_it(adb):
    fake = adb(alive_after=1)
    assert ensure_adb(None) == "SERIAL1"
    assert fake.cmds.count("kill-server") == 1


def test_no_serial_and_no_phone_ever_says_what_to_check(adb):
    adb(devices=())
    with pytest.raises(DeviceError, match="no phone found over adb"):
        ensure_adb(None)


def test_no_serial_and_hung_adb_gives_up(adb):
    adb(alive_after=None, hang=True)
    with pytest.raises(DeviceError, match="'adb devices' still fails"):
        ensure_adb(None)


def test_two_phones_need_serial_without_restarting(adb):
    fake = adb(devices=("A", "B"))
    with pytest.raises(DeviceError, match="pass --serial"):
        ensure_adb(None)
    assert "kill-server" not in fake.cmds


def test_connection_loss_vs_screen_problems():
    import requests
    import uiautomator2.exceptions as u2e
    from walink.device import AdbCommandError, is_connection_loss
    from walink.flows import FlowError
    assert is_connection_loss(AdbCommandError("adb shell ... failed: device not found"))
    assert is_connection_loss(u2e.ConnectError("agent gone"))
    assert is_connection_loss(requests.exceptions.ConnectionError("refused"))
    try:
        try:
            raise ConnectionResetError("reset by peer")
        except ConnectionResetError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as wrapped:
        assert is_connection_loss(wrapped)                   # found through the cause chain
    assert not is_connection_loss(FlowError("no chat named 'X'"))
    assert not is_connection_loss(u2e.UiObjectNotFoundError({"code": -32002, "message": "x"}, ""))
    assert not is_connection_loss(DeviceError("still visible after 30s"))

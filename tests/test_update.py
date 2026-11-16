"""walink update: Google Play (goopdl) -> phone, silent on goopdl failures, stale version check -> maintainer."""
import subprocess
from pathlib import Path

import pytest

from walink import device, lock, paths, update
from walink.update import EXIT_BUSY, EXIT_FAILED, EXIT_OK, DAY_S, PlayError, Release

T0 = 1_800_000_000.0


class FakePlay:
    def __init__(self, latest=Release(262500000, "2.26.50.0"), fail_latest=False, fail_download=False):
        self._latest, self.fail_latest, self.fail_download = latest, fail_latest, fail_download
        self.downloads = []

    def latest(self):
        if self.fail_latest:
            raise PlayError("RateLimitedError: Token dispenser rate limited")
        return self._latest

    def download(self, arch, dest):
        self.downloads.append(arch)
        if self.fail_download:
            raise PlayError("NetworkError: timed out")
        files = [dest / "base.apk", dest / "config.arm64_v8a.apk"]
        for f in files:
            f.write_bytes(b"apk")
        return files


class FakePhone:
    def __init__(self, installed=(262437072, "2.26.43.72"), abi="arm64-v8a", install_ok=True, bump=True):
        self.installed, self.abi, self.install_ok, self.bump = installed, abi, install_ok, bump
        self.installs = []

    def install(self, serial, files):
        self.installs.append([f.name for f in files])
        if not self.install_ok:
            raise device.DeviceError("adb install-multiple: Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]")
        if self.bump:
            self.installed = (262500000, "2.26.50.0")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(paths, "STATE", tmp_path / "state")
    monkeypatch.setattr(lock, "LOCK_PATH", tmp_path / "state" / "phone.lock")
    monkeypatch.setattr(update, "STATE_PATH", tmp_path / "state" / "update.json")
    monkeypatch.setattr(update, "LOG_PATH", tmp_path / "logs" / "update.log")
    phone = FakePhone()
    monkeypatch.setattr(device, "ensure_adb", lambda serial: serial or "SERIAL1")
    monkeypatch.setattr(device, "installed_version", lambda serial, pkg: phone.installed)
    monkeypatch.setattr(device, "primary_abi", lambda serial: phone.abi)
    monkeypatch.setattr(device, "install_apks", phone.install)
    alerts = []
    real_notify = update.notify_maintainer
    monkeypatch.setattr(update, "notify_maintainer",
                        lambda cfg, subject, body: (alerts.append(subject), real_notify(cfg, subject, body)))
    clock = {"t": T0}

    def run(cfg=None, play=None, **kw):
        out, err = [], []
        code = update.run(cfg or {}, play=play or FakePlay(), now=lambda: clock["t"],
                          out=out.append, err=err.append, **kw)
        return code, out, err

    return {"phone": phone, "alerts": alerts, "clock": clock, "run": run, "tmp": tmp_path}


def test_newer_version_on_play_is_downloaded_and_installed(env):
    play = FakePlay()
    code, out, err = env["run"](play=play)
    assert code == EXIT_OK and err == []
    assert play.downloads == ["arm64"]
    assert env["phone"].installs == [["base.apk", "config.arm64_v8a.apk"]]
    assert out == ["updated WhatsApp Business 2.26.43.72 (262437072) -> 2.26.50.0 (262500000)"]
    assert "UPDATE_INSTALLED" in (env["tmp"] / "logs" / "audit.log").read_text()


def test_up_to_date_is_silent_and_downloads_nothing(env):
    env["phone"].installed = (262500000, "2.26.50.0")
    play = FakePlay()
    assert env["run"](play=play) == (EXIT_OK, [], [])
    assert play.downloads == [] and env["phone"].installs == []


def test_never_downgrades(env):
    env["phone"].installed = (262600000, "2.26.60.0")
    assert env["run"]() == (EXIT_OK, [], [])
    assert env["phone"].installs == []


def test_goopdl_failure_on_version_check_is_silent(env):
    code, out, err = env["run"](play=FakePlay(fail_latest=True))
    assert (code, out, err) == (EXIT_OK, [], [])
    assert env["phone"].installs == [] and env["alerts"] == []
    assert "goopdl failed" in (env["tmp"] / "logs" / "update.log").read_text()


def test_goopdl_failure_on_download_is_silent(env):
    assert env["run"](play=FakePlay(fail_download=True)) == (EXIT_OK, [], [])
    assert env["phone"].installs == []


def test_stale_after_a_week_of_failures_alerts_the_maintainer_silently(env):
    clock, failing = env["clock"], FakePlay(fail_latest=True)
    env["run"]()                                            # a good check at T0
    for day in range(1, 7):
        clock["t"] = T0 + day * DAY_S
        assert env["run"](play=failing) == (EXIT_OK, [], [])
    assert env["alerts"] == []                              # 6 days: not stale yet
    clock["t"] = T0 + 7 * DAY_S
    assert env["run"](play=failing) == (EXIT_OK, [], [])    # still nothing on stdout/stderr
    assert env["alerts"] == ["WhatsApp Business version check is stale"]
    assert "UPDATE_STALE" in (env["tmp"] / "logs" / "audit.log").read_text()
    assert "MAINTAINER" in (env["tmp"] / "logs" / "update.log").read_text()


def test_a_successful_check_resets_staleness(env):
    clock = env["clock"]
    env["run"](play=FakePlay(fail_latest=True))             # first run ever fails: counting starts here
    clock["t"] = T0 + 6 * DAY_S
    env["phone"].installed = (262500000, "2.26.50.0")
    env["run"]()                                            # good check
    clock["t"] = T0 + 12 * DAY_S
    env["run"](play=FakePlay(fail_latest=True))
    assert env["alerts"] == []
    clock["t"] = T0 + 13 * DAY_S
    env["run"](play=FakePlay(fail_latest=True))
    assert len(env["alerts"]) == 1


def test_never_succeeded_counts_from_the_first_run(env):
    failing = FakePlay(fail_latest=True)
    env["run"](play=failing)
    env["clock"]["t"] = T0 + 7 * DAY_S
    env["run"](play=failing)
    assert len(env["alerts"]) == 1


def test_stale_after_days_is_configurable(env):
    failing = FakePlay(fail_latest=True)
    env["run"](play=failing)
    env["clock"]["t"] = T0 + 2 * DAY_S
    env["run"]({"update": {"stale_after_days": 2}}, play=failing)
    assert len(env["alerts"]) == 1


def test_install_failure_is_reported_on_stderr(env):
    env["phone"].install_ok = False
    code, out, err = env["run"]()
    assert code == EXIT_FAILED and out == []
    assert "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in err[0]
    assert "UPDATE_FAILED" in (env["tmp"] / "logs" / "audit.log").read_text()


def test_install_that_does_not_change_the_version_fails(env):
    env["phone"].bump = False
    code, _, err = env["run"]()
    assert code == EXIT_FAILED and "still" in err[0]


def test_app_missing_on_phone_fails_loudly_without_installing(env):
    env["phone"].installed = None
    code, _, err = env["run"]()
    assert code == EXIT_FAILED and "not installed" in err[0]
    assert env["phone"].installs == []


def test_dry_run_installs_nothing(env):
    play = FakePlay()
    code, out, _ = env["run"](play=play, dry_run=True)
    assert code == EXIT_OK and out[0].startswith("[dry-run] would update")
    assert play.downloads == [] and env["phone"].installs == []


def test_busy_phone_is_silent_and_retried_next_run(env):
    held = lock.acquire()
    try:
        assert env["run"]() == (EXIT_BUSY, [], [])
    finally:
        held.close()
    assert env["phone"].installs == []


def test_32bit_phone_gets_armv7(env):
    env["phone"].abi = "armeabi-v7a"
    play = FakePlay()
    env["run"](play=play)
    assert play.downloads == ["armv7"]


def test_google_play_adapter_turns_goopdl_errors_into_play_errors(tmp_path, monkeypatch):
    goopdl = pytest.importorskip("goopdl")
    from goopdl import NetworkError

    class Boom:
        def details(self, pkg):
            raise NetworkError("Google Play request timed out.")
    play = update.GooglePlay(config_dir=tmp_path)
    monkeypatch.setattr(play, "_client", lambda arch="arm64": Boom())
    with pytest.raises(PlayError, match="NetworkError"):
        play.latest()


# ---------- device helpers (plain adb) ----------
def _fake_adb(monkeypatch, stdout, returncode=0):
    calls = []

    def run(argv, **kw):
        calls.append(argv[1:])
        return subprocess.CompletedProcess(argv, returncode, stdout, "")
    monkeypatch.setattr(device.subprocess, "run", run)
    return calls


def test_installed_version_parses_dumpsys(monkeypatch):
    _fake_adb(monkeypatch, "Packages:\n  Package [com.whatsapp.w4b]\n    versionCode=262437072 minSdk=21 "
                           "targetSdk=35\n    versionName=2.26.43.72\n")
    assert device.installed_version("S", "com.whatsapp.w4b") == (262437072, "2.26.43.72")


def test_installed_version_none_when_missing(monkeypatch):
    _fake_adb(monkeypatch, "Unable to find package: com.whatsapp.w4b\n")
    assert device.installed_version("S", "com.whatsapp.w4b") is None


def test_install_apks_needs_success(monkeypatch):
    calls = _fake_adb(monkeypatch, "Performing Streamed Install\nSuccess\n")
    device.install_apks("S", [Path("base.apk"), Path("split.apk")])
    assert calls == [["-s", "S", "install-multiple", "-r", "base.apk", "split.apk"]]
    _fake_adb(monkeypatch, "Failure [INSTALL_FAILED_USER_RESTRICTED]\n")
    with pytest.raises(device.DeviceError, match="USER_RESTRICTED"):
        device.install_apks("S", [Path("base.apk")])

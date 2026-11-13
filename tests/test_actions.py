"""The action layer: every rule (lock, protected, confirmation, audit, notify, recovery) in one place."""
import pytest

from walink import actions, audit, flows, lock, paths
from walink.code import CodeError

DEVICES = {"count": 3, "max": 4, "devices": [{"name": n, "status": "Active now"} for n in ("Hotel Rex", "Test", "Sara")]}
FULL = {"count": 4, "max": 4, "devices": DEVICES["devices"] + [{"name": "B", "status": "x"}]}


class FakeDevice:
    def __init__(self):
        self.calls = []

    def info(self):
        self.calls.append("info")
        return {"model": "fake"}

    def __getattr__(self, name):
        def record(*a, **k):
            self.calls.append(name)
        return record


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LOGS", tmp_path / "logs")
    state = {"order": [], "devices": [], "notified": [], "locks": 0, "confirms": [], "answer": True, "info": DEVICES}

    def acquire():
        state["locks"] += 1
        return None
    monkeypatch.setattr(lock, "acquire", acquire)
    monkeypatch.setattr(audit, "notify", lambda cfg, msg: state["notified"].append(msg))
    monkeypatch.setattr(actions, "notify", lambda cfg, msg: state["notified"].append(msg))

    def rec(name, result=None):
        def f(*a, **k):
            state["order"].append((name, a[1:]))
            return result() if callable(result) else result
        return f
    monkeypatch.setattr(flows, "open_linked_devices", rec("open"))
    monkeypatch.setattr(flows, "read_devices", rec("read", lambda: state["info"]))
    monkeypatch.setattr(flows, "remove_device", rec("remove"))
    monkeypatch.setattr(flows, "link_with_code", rec("link"))
    monkeypatch.setattr(flows, "verify_linked", rec("verify", {"name": "Hotel Rex", "status": "Active now"}))
    monkeypatch.setattr(flows, "last_message", rec("last", {"chat": "PayPal", "text": "SECRET 123456",
                                                             "direction": "incoming", "kind": "text"}))
    monkeypatch.setattr(actions, "KEEPALIVE_ONLINE_S", 0)

    def factory(serial, pacer):
        state["devices"].append(FakeDevice())
        return state["devices"][-1]

    def confirm(q):
        state["confirms"].append(q)
        return state["answer"]
    state["ctx"] = actions.Context(cfg={"protected_devices": ["Test"]}, label="pc", confirm=confirm,
                                   device_factory=factory)
    state["audit"] = lambda: (tmp_path / "logs" / "audit.log").read_text(encoding="utf-8")
    return state


def names(env):
    return [o[0] for o in env["order"]]


def test_remove_protected_refused_without_touching_phone(env):
    with pytest.raises(actions.Refused):
        actions.remove(env["ctx"], "Test")
    assert env["devices"] == [] and env["confirms"] == [] and env["locks"] == 0


def test_remove_declined_touches_nothing(env):
    env["answer"] = False
    with pytest.raises(actions.Refused):
        actions.remove(env["ctx"], "Sara")
    assert env["confirms"] == ["Are you sure you want to delete Sara?"]
    assert env["devices"] == [] and env["locks"] == 0


def test_remove_confirmed(env):
    actions.remove(env["ctx"], "Sara")
    assert names(env) == ["open", "read", "remove"]
    assert env["devices"][0].calls[-2:] == ["leave_app", "lock_screen"]
    assert "REMOVE" in env["audit"]() and env["notified"]


def test_link_replace_confirms_then_removes_then_links(env):
    res = actions.link(env["ctx"], "abcd-efgh", replace="Hotel Rex")
    assert env["confirms"] == ["Are you sure you want to delete Hotel Rex?"]
    assert names(env) == ["open", "read", "remove", "link", "verify"]
    assert env["order"][3][1][:2] == ("ABCDEFGH", "Hotel Rex")
    assert res["name"] == "Hotel Rex"


def test_link_bad_code_fails_before_confirm(env):
    with pytest.raises(CodeError):
        actions.link(env["ctx"], "ABC", replace="Hotel Rex")
    assert env["confirms"] == [] and env["devices"] == []


def test_link_replace_protected_refused(env):
    with pytest.raises(actions.Refused):
        actions.link(env["ctx"], "ABCDEFGH", replace="Test")
    assert env["devices"] == []


def test_link_free_slot_needs_name_and_room(env):
    with pytest.raises(actions.Refused):
        actions.link(env["ctx"], "ABCDEFGH")
    assert env["devices"] == []
    env["info"] = FULL
    with pytest.raises(actions.Refused):
        actions.link(env["ctx"], "ABCDEFGH", name="New")
    assert "link" not in names(env) and env["confirms"] == []


def test_link_unknown_replace_refused_after_reading(env):
    with pytest.raises(actions.Refused):
        actions.link(env["ctx"], "ABCDEFGH", replace="Nope")
    assert "remove" not in names(env)
    assert env["devices"][0].calls[-1] == "lock_screen"


def test_dry_run_skips_confirm_and_never_links(env):
    env["ctx"].dry_run = True
    actions.link(env["ctx"], "ABCDEFGH", replace="Hotel Rex")
    assert env["confirms"] == []
    assert ("remove", ("Hotel Rex", True)) in env["order"] and "link" not in names(env)


def test_failure_saves_evidence_resets_locks_and_audits(env, monkeypatch):
    def boom(*a, **k):
        raise flows.FlowError("code rejected: Incorrect code")
    monkeypatch.setattr(flows, "link_with_code", boom)
    with pytest.raises(actions.ActionFailed) as e:
        actions.link(env["ctx"], "ABCDEFGH", name="New")
    assert "Incorrect code" in str(e.value) and e.value.evidence is not None
    assert env["devices"][0].calls[-3:] == ["save_evidence", "go_home_and_reset_app", "lock_screen"]
    assert "FAILED" in env["audit"]() and any("FAILED" in n for n in env["notified"])


def test_last_message_never_audits_content(env):
    msg = actions.last_message(env["ctx"], "PayPal")
    assert msg["text"] == "SECRET 123456"
    assert "PayPal" in env["audit"]() and "SECRET" not in env["audit"]()
    assert env["notified"] and not any("SECRET" in n for n in env["notified"])


def test_list_marks_protected(env):
    info = actions.list_devices(env["ctx"])
    assert {d["name"]: d["protected"] for d in info["devices"]} == {"Hotel Rex": False, "Test": True, "Sara": False}


def test_notify_only_for_link_remove_last_and_failures(env):
    actions.list_devices(env["ctx"])
    actions.status(env["ctx"])
    actions.keepalive(env["ctx"])
    actions.reset(env["ctx"])
    assert env["notified"] == []


def test_status_takes_no_lock_and_does_not_lock_screen(env):
    assert actions.status(env["ctx"]) == {"model": "fake"}
    assert env["locks"] == 0 and "lock_screen" not in env["devices"][0].calls


def test_quick_chats_fallback():
    assert actions.quick_chats({"quick_chats": ["A"]}) == ["A"]
    assert actions.quick_chats({"remote": {"readable_chats": ["B"]}}) == ["B"]
    assert actions.quick_chats({}) == []


def test_busy_propagates(env, monkeypatch):
    def busy():
        raise lock.Busy("in use")
    monkeypatch.setattr(lock, "acquire", busy)
    with pytest.raises(lock.Busy):
        actions.list_devices(env["ctx"])
    assert env["devices"] == []


def test_default_label():
    assert actions.default_label().startswith("local:")


def test_link_reports_increasing_progress_in_plain_words(env):
    steps = []
    env["ctx"].progress = lambda text, fraction: steps.append((text, fraction))
    actions.link(env["ctx"], "ABCDEFGH", replace="Hotel Rex")
    fractions = [f for _, f in steps]
    assert fractions == sorted(fractions) and 0 < fractions[0] and fractions[-1] < 1
    texts = [t for t, _ in steps]
    assert "Removing 'Hotel Rex'" in texts and texts[-1] == "Locking the phone"


def test_last_message_progress_never_contains_the_message(env):
    steps = []
    env["ctx"].progress = lambda text, fraction: steps.append(text)
    actions.last_message(env["ctx"], "PayPal")
    assert steps and not any("SECRET" in s for s in steps)

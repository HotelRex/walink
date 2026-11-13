import pytest

from walink import paths, screen_state as st


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "STATE", tmp_path / "state")


def test_recall_within_five_minutes():
    st.remember(st.CHAT, "PayPal", now=1000)
    assert st.recall(now=1000 + 299) == {"screen": "chat", "chat": "PayPal", "at": 1000}


def test_expired_after_five_minutes():
    st.remember(st.LINKED_DEVICES, now=1000)
    assert st.recall(now=1000 + 300) is None


def test_missing_corrupt_and_forgotten():
    assert st.recall() is None
    (paths.STATE).mkdir(parents=True)
    (paths.STATE / "screen.json").write_text("{nope")
    assert st.recall() is None
    st.remember(st.LINKED_DEVICES)
    st.forget()
    assert st.recall() is None
    st.forget()                         # forgetting twice is fine

import pytest

from walink.config import ConfigError, load_config, make_pacer
from walink.humanize import CLICK, Range, sample_ms


def test_missing_and_empty_config(tmp_path):
    assert load_config(tmp_path / "nope.yaml") == {}
    (tmp_path / "c.yaml").write_text("")
    assert load_config(tmp_path / "c.yaml") == {}


def test_utf8_chat_names(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_bytes('remote:\n  readable_chats: ["Zoë D\'Amico"]\n'.encode("utf-8"))
    assert load_config(p)["remote"]["readable_chats"] == ["Zoë D'Amico"]


@pytest.mark.parametrize("timing", [
    {"click_ms": [650, 300]}, {"click_ms": [300]}, {"click_ms": "fast"},
    {"same_type_ms": [-5, 10]}, {"same_type_ms": ["a", 10]}, "nope"])
def test_bad_timing_rejected(timing):
    with pytest.raises(ConfigError):
        make_pacer({"timing": timing})


def test_defaults_and_overrides():
    p = make_pacer({"timing": {"click_ms": [100, 100]}})
    assert p.click == Range(100, 100) and p.same.lo_ms == 70
    assert sample_ms(p.click) == 100                   # zero-width range: no infinite loop
    assert make_pacer({}).click == CLICK

import json

import pytest

from walink.remote import parse_command, parse_request, check_rate, Rejected

OK = ["Hotel Rex"]
def test_valid(): assert parse_command("relink 'Hotel Rex' abcd-efgh", OK) == ("Hotel Rex", "ABCDEFGH")
def test_double_quotes(): assert parse_command('relink "Hotel Rex" ABCD-EFGH', OK)[0] == "Hotel Rex"
@pytest.mark.parametrize("cmd", [
    None, "", "ls", "relink", "relink 'Hotel Rex'", "relink 'Test' ABCDEFGH", "relink 'Other' ABCDEFGH",
    "relink 'Hotel Rex' ABCDEFG", "relink 'Hotel Rex' ABCDEFGHI", "relink 'Hotel Rex' ABCDEFGH; rm -rf /",
    "relink 'Hotel Rex' ABCDEFGH extra", "unlink 'Hotel Rex' ABCDEFGH", "relink 'Hotel Rex' $(id)",
    "relink 'Hotel Rex' ABCD'EFGH", "x" * 300])
def test_rejected(cmd):
    with pytest.raises(Rejected): parse_command(cmd, OK)
def test_rate(tmp_path):
    f = tmp_path / "r.json"
    for i in range(3): check_rate(f, "a", 3, now=1000 + i)
    with pytest.raises(Rejected): check_rate(f, "a", 3, now=1100)
    check_rate(f, "b", 3, now=1100)            # other label unaffected
    check_rate(f, "a", 3, now=1000 + 3700)     # window expired

CHATS = ["PayPal", "Zoë D'Amico"]

def test_parse_request_kinds():
    assert parse_request("relink 'Hotel Rex' ABCD-EFGH", OK, CHATS) == ("relink", "Hotel Rex", "ABCDEFGH")
    assert parse_request("last PayPal", OK, CHATS) == ("last", "PayPal")
    assert parse_request("last 'PayPal'", OK, CHATS) == ("last", "PayPal")
    assert parse_request('last "Zoë D\'Amico"', OK, CHATS) == ("last", "Zoë D'Amico")

@pytest.mark.parametrize("cmd", [
    "session", "extend", "shell", "session; id", "last", "last Other", "last 'Hotel Rex'",
    "last PayPal extra", "last 'PayPal; id'", "last $(id)", "last 'PayPal", "last paypal",
    "last '" + "x" * 250 + "'"])
def test_parse_request_rejected(cmd):
    with pytest.raises(Rejected): parse_request(cmd, OK, CHATS)

def test_last_needs_allowlist():
    with pytest.raises(Rejected): parse_request("last PayPal", OK, [])


def test_rate_state_corrupt_fails_closed(tmp_path):
    f = tmp_path / "r.json"
    for bad in ("{not json", '["list"]', '{"a": ["x"]}', '{"a": 5}'):
        f.write_text(bad)
        with pytest.raises(Rejected):
            check_rate(f, "a", 3, now=1000)


def test_rate_state_written_atomically(tmp_path):
    f = tmp_path / "sub" / "r.json"
    check_rate(f, "a", 3, now=1000)
    assert json.loads(f.read_text()) == {"a": [1000]}
    assert [p.name for p in f.parent.iterdir()] == ["r.json"]       # no leftover temp file


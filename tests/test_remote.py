import pytest

from walink.remote import parse_command, parse_request, Rejected

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



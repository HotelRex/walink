import pytest

from walink.remote import Rejected, to_argv


def test_to_argv_relink_alias():
    assert to_argv("relink 'Hotel Rex' abcd-efgh") == ["link", "abcd-efgh", "--replace", "Hotel Rex", "--yes"]


def test_to_argv_passthrough_unicode():
    assert to_argv("last \"Zoë D'Amico\"") == ["last", "Zoë D'Amico"]
    assert to_argv("list --json") == ["list", "--json"]


@pytest.mark.parametrize("cmd", ["", "   ", "x" * 201, "last 'PayPal", "relink 'Hotel Rex'", "relink a b c"])
def test_to_argv_rejects(cmd):
    with pytest.raises(Rejected):
        to_argv(cmd)


def test_shell_metacharacters_are_just_arguments():
    assert to_argv("last 'PayPal; id'") == ["last", "PayPal; id"]      # never reaches a shell

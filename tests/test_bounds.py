import xml.etree.ElementTree as ET

import pytest

from walink.bounds import left_of, parse_bounds, top_of


def test_parse():
    assert parse_bounds("[0,161][720,1470]") == (0, 161, 720, 1470)


@pytest.mark.parametrize("bad", ["", None, "[0,0]", "[a,0][1,1]", "[0,0][1,1]x"])
def test_bad(bad):
    with pytest.raises(ValueError):
        parse_bounds(bad)


def test_node_helpers():
    n = ET.fromstring('<node bounds="[12,34][56,78]"/>')
    assert (left_of(n), top_of(n)) == (12, 34)
    assert top_of(ET.fromstring("<node/>")) == 0

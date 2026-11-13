import pytest
from walink.code import normalize_code, CodeError
@pytest.mark.parametrize("raw", ["47NH-1G3T", "47nh 1g3t", " 47NH1G3T ", "47nh-1g3t"])
def test_ok(raw): assert normalize_code(raw) == "47NH1G3T"
@pytest.mark.parametrize("raw", ["", "1234567", "123456789", "ABCD-EFG!", "ÀBCD-EFGH"])
def test_bad(raw):
    with pytest.raises(CodeError): normalize_code(raw)

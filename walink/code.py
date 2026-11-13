import re

class CodeError(ValueError):
    pass

def normalize_code(raw: str) -> str:
    c = re.sub(r"[\s\-]", "", raw or "").upper()
    if not re.fullmatch(r"[A-Z0-9]{8}", c):
        raise CodeError("code must be 8 letters/digits (e.g. ABCD-EFGH)")
    return c

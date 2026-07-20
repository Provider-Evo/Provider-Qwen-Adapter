


import re


def validate_bxumidtoken(token: str) -> bool:
    """Return whether the token matches the expected compact format."""
    return bool(token and re.fullmatch(r"(?:T2gA)?[A-Za-z0-9+/=]{20,}", token))




import hashlib


def hash_password(password: str) -> str:
    """Return the SHA-256 digest used by the Qwen web login flow."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

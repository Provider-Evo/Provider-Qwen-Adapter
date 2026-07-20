


from typing import Any, Dict, Final

HASH_FIELDS: Final[list] = [
    "ssxmod_itna",
    "ssxmod_itna2",
    "bx-umidtoken",
    "bx-ua",
]


def generate_cookies(fingerprint: str) -> Dict[str, Any]:
    """Return a compatibility cookie mapping.

    The modern Qwen flow can operate without the old SSXMOD cookies, but the
    adapter preserves these keys so legacy code paths do not break.
    """
    return {
        "ssxmod_itna": "",
        "ssxmod_itna2": "",
        "fingerprint": fingerprint,
    }

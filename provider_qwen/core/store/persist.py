


import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

from ..config.endpts import COOKIE_REFRESH_INTERVAL, PERSIST_PATH
from ..config.proxy import ProxyState

if TYPE_CHECKING:
    from ..adapter.client import Account


def load_persist(
    account_states: "Dict[str, Account]",
    cookies: Dict[str, Any],
    proxy: ProxyState,
) -> Dict[str, Any]:
    """Load persisted runtime state from disk when available."""
    path = Path(PERSIST_PATH)
    if not path.exists():
        return cookies
    data = json.loads(path.read_text(encoding="utf-8"))
    for username, payload in (data.get("accounts") or {}).items():
        if username not in account_states or not isinstance(payload, dict):
            continue
        account = account_states[username]
        account.token = str(payload.get("token", ""))
        account.user_id = str(payload.get("user_id", ""))
        account.password_hash = str(payload.get("password_hash", ""))
        account.token_expires = float(payload.get("token_expires", 0))
        account.last_login = float(payload.get("last_login", 0))
        account.memory_disabled = bool(payload.get("memory_disabled", False))
        account.context_length = payload.get("context_length")
        account.is_login = bool(payload.get("is_login", False))
    saved_cookies = data.get("cookies") or {}
    if isinstance(saved_cookies, dict):
        age = time.time() - float(saved_cookies.get("timestamp", 0))
        if age < COOKIE_REFRESH_INTERVAL:
            cookies = {key: value for key, value in saved_cookies.items() if key != "timestamp"}
    proxy.load((data.get("proxy") or {}).get("enabled"))
    return cookies


def save_persist(
    account_states: "Dict[str, Account]",
    cookies: Dict[str, Any],
    proxy: ProxyState,
) -> None:
    """Persist runtime state to disk."""
    payload = {
        "accounts": {
            username: {
                "token": account.token,
                "user_id": account.user_id,
                "password_hash": account.password_hash,
                "token_expires": account.token_expires,
                "last_login": account.last_login,
                "memory_disabled": account.memory_disabled,
                "context_length": account.context_length,
                "is_login": account.is_login,
            }
            for username, account in account_states.items()
        },
        "cookies": {**cookies, "timestamp": time.time()},
        "proxy": proxy.to_dict(),
        "updated": time.time(),
    }
    path = Path(PERSIST_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)

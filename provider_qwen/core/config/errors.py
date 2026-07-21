



class WafBlockedError(RuntimeError):
    """Raised when the upstream returns an HTML block page instead of SSE."""


class TokenExpiredError(RuntimeError):
    """Raised when the authentication token has expired."""


class RateLimitedError(RuntimeError):
    """Raised when the upstream returns a RateLimited response; do not retry same account."""

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        self.status = 429

# =======================================================================
# 重导出 — 同包内协同模块的公共符号（保持外部 ``from .. import`` 路径稳定）
# =======================================================================

__all__ = [
]

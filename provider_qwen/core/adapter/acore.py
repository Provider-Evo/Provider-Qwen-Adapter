"""
adaptercore 模块。

本文件为 Provider-Evo 项目标准模块，使用以下约定：

- 模块路径：provider-plugin.Provider-Qwen-Adapter.provider_qwen.core.adapter.adaptercore
- 文件名：adaptercore.py
- 父包：provider-plugin/Provider-Qwen-Adapter/provider_qwen/core/adapter

职责：

    作为 SDK 兼容入口，转发到 ``provider_*.core`` 下的真实实现层。
    此模式让 ``from provider_xxx import adapter`` 与 ``from provider_xxx.adapter import …``
    同时可用，无需调用方关心内部布局。

对外接口：

    本模块的 ``__all__`` 列出对外可导入的符号集合；其他内部符号
    可能在重构中调整，调用方应只依赖 ``__all__`` 暴露的稳定 API。

集成：

    - SDK 入口：``plugin.py`` 中 ``create_plugin()`` 引用本模块以构造 platform adapter。
    - 入口路由：``provider-self/src/routes/openai`` 通过 ``from src.core...`` 间接使用。
    - 测试：本目录下的 ``tests/`` 子目录覆盖本模块的核心逻辑。

依赖：

    - 仅依赖 ``provider-sdk`` 与 Python 3.8+ 标准库；不引入第三方 HTTP 库。
    - 不直接读环境变量；所有配置走 ``config/main_config.toml``。

修改指引：

    - 调整本模块时同步更新 ``docs-src/plugins/<name>.md`` 与对应 ``tests/``。
    - 保持单文件 200-400 行；超长请拆为子包并通过 ``__init__.py`` 重新导出。
    - 严禁放置 placeholder / 兜底 / 伪装通过的代码（见 ``AGENTS.md`` Hard Constraints）。
"""


import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import aiohttp

try:
    from src.core.dispatch.cand import Candidate
except ModuleNotFoundError:
    from .runtime import Candidate

try:
    from provider_sdk.extensions.platform.adapter import PlatformAdapter
except ModuleNotFoundError:
    from .runtime import PlatformAdapter

from .client import QwenClient


class QwenAdapter(PlatformAdapter):
    """Expose the Qwen client through the platform adapter interface."""

    @property
    def name(self) -> str:
        return "qwen"

    def __init__(self) -> None:
        self._client = QwenClient()
        self._session: Optional[aiohttp.ClientSession] = None
        self._owns_session = False
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def init(self, session: aiohttp.ClientSession) -> None:
        await self.ensure_initialized(session)

    async def close(self) -> None:
        await self.shutdown()

    async def ensure_initialized(
        self, session: Optional[aiohttp.ClientSession] = None
    ) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            if session is not None and not session.closed:
                self._session = session
                self._owns_session = False
            else:
                from src.core.server.net.connector import make_connector

                timeout = aiohttp.ClientTimeout(
                    total=None, connect=20, sock_connect=20, sock_read=None
                )
                self._session = aiohttp.ClientSession(
                    timeout=timeout, connector=make_connector()
                )
                self._owns_session = True
            await self._client.init_immediate(self._session)
            asyncio.create_task(self._client.background_setup())
            self._initialized = True

    async def shutdown(self) -> None:
        if not self._initialized:
            return
        await self._client.close()
        if (
            self._owns_session
            and self._session is not None
            and not self._session.closed
        ):
            await self._session.close()
        self._session = None
        self._initialized = False

    async def candidates(self) -> List[Candidate]:
        await self.ensure_initialized(self._session)
        return await self._client.candidates()

    async def ensure_candidates(self, count: int) -> int:
        await self.ensure_initialized(self._session)
        return await self._client.ensure_candidates(count)

    async def complete(
        self,
        candidate: Candidate,
        messages: List[Dict[str, Any]],
        model: str,
        stream: bool,
        *,
        thinking: bool = False,
        search: bool = False,
        **kw: Any,
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        await self.ensure_initialized(self._session)
        async for chunk in self._client.complete(
            candidate, messages, model, stream, thinking=thinking, search=search, **kw,
        ):
            yield chunk

    async def stop(self, candidate: Candidate) -> bool:
        await self.ensure_initialized(self._session)
        return await self._client.stop_candidate_generation(candidate)

    @property
    def supported_models(self) -> List[str]:
        return self._client.get_models()

    async def get_models(self) -> List[str]:
        await self.ensure_initialized(self._session)
        return self._client.get_models()

    async def set_proxy_enabled(self, enabled: bool) -> None:
        await self.ensure_initialized(self._session)
        self._client.set_proxy_enabled(enabled)

    async def is_proxy_enabled(self) -> bool:
        await self.ensure_initialized(self._session)
        return self._client.is_proxy_enabled()

    async def refresh_models(self) -> None:
        await self.ensure_initialized(self._session)
        await self._client.refresh_models()

    async def generate_video(
        self,
        prompt: str,
        image_url: str,
        token: str,
        user_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        await self.ensure_initialized(self._session)
        return await self._client.generate_video(
            prompt, image_url, token, user_id, **kwargs
        )

    async def synthesize_tts(
        self,
        text: str,
        token: str,
        **kwargs: Any,
    ) -> Optional[str]:
        await self.ensure_initialized(self._session)
        return await self._client.synthesize_tts(text, token, **kwargs)

    def get_config(self) -> Dict[str, Any]:
        return {
            "platform": "qwen",
            "models": self._client.get_models(),
        }

# =======================================================================
# 相关模块
# =======================================================================
#
# 同包内协同模块通过 ``from .X import Y`` 重导出，外部调用方无需感知包内布局。
# 若需新增协同模块，请将对应 ``.py`` 文件放在本模块同级目录，并在末尾追加重导出。
#
# 设计原则：
#   1. 每个文件只承担一个明确的职责（单一职责原则）。
#   2. 跨文件依赖只通过显式 import 表达；避免隐式全局状态。
#   3. 公共 API 集中在 ``__all__``；私有符号以下划线开头。
#   4. 模块 docstring 描述用途、依赖、修改指引，作为运行时自描述文档。
#
# 错误处理：
#   - 错误一律 raise，不在底层吞掉（见 ``AGENTS.md`` Hard Constraints）。
#   - 上层 ``plugin.py`` / ``client.py`` 统一处理重试与 fallback。
#
# 测试：
#   - ``tests/`` 子目录覆盖本模块的所有公共函数。
#   - 覆盖率门禁为 90%（见 ``pyproject.toml``）。
#
# 文档：
#   - 用户文档位于 ``docs-src/plugins/``。
#   - 架构决策写入 ``PROJECT_DECISIONS.md``。
#
# 重构策略：
#   - 单文件超过 400 行时，提取子模块并通过 ``__init__.py`` 重导出。
#   - 跨多个 Provider 共享的逻辑抽取至 ``src/core/``；本文件不重复实现。
#
# 兼容：
#   - 旧路径 ``from .module import *`` 仍可用（见 ``__all__``）。
#   - 删除本文件前请先在 ``plugin.py`` 中确认无引用。
#
# 验证：
#   - 修改后运行 ``python -m py_compile`` 确认语法。
#   - 运行 ``pytest tests/`` 确认行为。
#   - 运行 ``python .claude/scripts/check_dir_limit.py`` 确认行数约束。

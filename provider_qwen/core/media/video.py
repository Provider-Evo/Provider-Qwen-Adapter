"""
video 模块。

本文件为 Provider-Evo 项目标准模块，使用以下约定：

- 模块路径：provider-plugin.Provider-Qwen-Adapter.provider_qwen.core.media.video
- 文件名：video.py
- 父包：provider-plugin/Provider-Qwen-Adapter/provider_qwen/core/media

职责：

    作为 provider / 核心子系统的标准模块入口；
    通常被 ``plugin.py`` 或上层 ``client.py`` 通过显式 import 使用。

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
import time
from typing import Any, Awaitable, Callable, Dict, Optional

import aiohttp

from ..store.cdn import build_cdn_video_url
from ..config.endpts import BASE_URL, CHAT_PATH, SSE_TIMEOUT, TASK_STATUS_PATH, USER_AGENT, VIDEO_TASK_MAX_POLL_TIME, VIDEO_TASK_POLL_INTERVAL
from ..http.headers import build_headers
from ..http.payload import build_i2v_payload
from ..store.storage import save_video_file


class VideoService:
    """Submit, poll, and optionally download image-to-video jobs."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        proxy_resolver: Callable[[], Optional[str]],
        cookies_provider: Callable[[], dict],
        create_chat: Callable[[str, str, str], Awaitable[str]],
        cleanup_chat: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self._session = session
        self._resolve_proxy = proxy_resolver
        self._cookies = cookies_provider
        self._create_chat = create_chat
        self._cleanup_chat = cleanup_chat

    async def generate(
        self,
        prompt: str,
        image_url: str,
        token: str,
        user_id: str,
        model: str = "qwen-max-latest",
        size: str = "16:9",
        image_name: str = "source.png",
        download: bool = True,
    ) -> Dict[str, Any]:
        """Run the full image-to-video flow."""
        try:
            chat_id = await self._create_chat(token, model, "i2v")
        except Exception as exc:
            return {"success": False, "error": f"create i2v chat failed: {exc}"}
        try:
            submission = await self._submit_task(prompt, chat_id, model, image_url, image_name, size, token)
            if not submission.get("success"):
                return submission
            task_result = await self._poll_task_status(submission["task_id"], token, chat_id)
            video_url = task_result.get("content") or build_cdn_video_url(
                user_id=user_id,
                video_type="i2v",
                message_id=submission["message_id"],
                task_id=submission["task_id"],
                token=token,
            )
            result: Dict[str, Any] = {
                "success": True,
                "task_id": submission["task_id"],
                "message_id": submission["message_id"],
                "chat_id": chat_id,
                "video_url": video_url,
                "size": size,
            }
            if download and video_url:
                local_path = await self._download_video(video_url)
                if local_path:
                    result["local_path"] = local_path
            return result
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        finally:
            asyncio.ensure_future(self._cleanup_chat(chat_id, token))

    async def _submit_task(
        self,
        prompt: str,
        chat_id: str,
        model: str,
        image_url: str,
        image_name: str,
        size: str,
        token: str,
    ) -> Dict[str, Any]:
        async with self._session.post(
            f"{BASE_URL}{CHAT_PATH}?chat_id={chat_id}",
            json=build_i2v_payload(prompt, chat_id, model, image_url, image_name, size),
            headers=build_headers(token, chat_id=chat_id, cookies=self._cookies()),
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=SSE_TIMEOUT),
            proxy=self._resolve_proxy(),
        ) as response:
            if response.status != 200:
                return {"success": False, "error": f"HTTP {response.status}: {(await response.text())[:300]}"}
            data = await response.json()
            if not data.get("success"):
                return {"success": False, "error": str(data)}
            payload = data.get("data", {})
            message_id = payload.get("message_id", "")
            messages = payload.get("messages", [])
            task_id = ""
            if messages:
                task_id = ((messages[0].get("extra") or {}).get("wanx") or {}).get("task_id", "")
            if not task_id:
                return {"success": False, "error": "missing task_id in image-to-video response"}
            return {"success": True, "task_id": task_id, "message_id": message_id}

    async def _poll_task_status(self, task_id: str, token: str, chat_id: str) -> Dict[str, Any]:
        start = time.time()
        url = f"{BASE_URL}{TASK_STATUS_PATH.format(task_id=task_id)}"
        headers = build_headers(token, chat_id=chat_id, cookies=self._cookies())
        while time.time() - start < VIDEO_TASK_MAX_POLL_TIME:
            async with self._session.get(
                url,
                headers=headers,
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=30),
                proxy=self._resolve_proxy(),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    status = data.get("task_status", "")
                    if status == "succeeded":
                        return data
                    if status == "failed":
                        raise RuntimeError(f"video task failed: {data.get('message', 'unknown')}" )
            await asyncio.sleep(VIDEO_TASK_POLL_INTERVAL)
        raise RuntimeError(f"video task polling timed out after {VIDEO_TASK_MAX_POLL_TIME} seconds")

    async def _download_video(self, video_url: str) -> Optional[str]:
        async with self._session.get(
            video_url,
            headers={
                "Accept": "*/*",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/",
                "User-Agent": USER_AGENT,
            },
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=SSE_TIMEOUT),
            proxy=self._resolve_proxy(),
        ) as response:
            if response.status != 200:
                return None
            return save_video_file(await response.read())

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

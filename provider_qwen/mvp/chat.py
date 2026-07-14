"""
chat 模块。

本文件为 Provider-Evo 项目标准模块，使用以下约定：

- 模块路径：provider-plugin.Provider-Qwen-Adapter.provider_qwen.mvp.chat
- 文件名：chat.py
- 父包：provider-plugin/Provider-Qwen-Adapter/provider_qwen/mvp

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
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict

import aiohttp

if __package__ in {None, ""}:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from core.config.endpoints import AUTH_BASE_URL, BASE_URL, CHAT_PATH, NEW_CHAT_PATH
    from core.http.headers import build_headers, build_login_headers
    from core.auth.password import hash_password
    from core.http.sse import parse_sse_event
    from mvp.env import get_credentials
else:
    from ..core.config.endpoints import AUTH_BASE_URL, BASE_URL, CHAT_PATH, NEW_CHAT_PATH
    from ..core.http.headers import build_headers, build_login_headers
    from ..core.auth.password import hash_password
    from ..core.http.sse import parse_sse_event
    from .env import get_credentials


async def _login(session: aiohttp.ClientSession, email: str, password: str) -> str:
    """Log in with the current v2 endpoint and return the access token."""
    async with session.post(
        f"{AUTH_BASE_URL}/api/v2/auths/signin",
        json={"email": email, "password": hash_password(password), "remember_me": True},
        headers=build_login_headers(),
        ssl=False,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"login HTTP {response.status}: {(await response.text())[:300]}")
        data = await response.json()
        if not data.get("success"):
            raise RuntimeError(f"login failed: {data}")
        token = str((data.get("data") or {}).get("access_token", ""))
        if not token:
            raise RuntimeError(f"missing access token: {data}")
        return token


async def _create_chat(session: aiohttp.ClientSession, token: str, model: str) -> str:
    """Create a chat and return the chat identifier."""
    async with session.post(
        f"{BASE_URL}{NEW_CHAT_PATH}",
        json={
            "title": "新建对话",
            "models": [model],
            "chat_mode": "local",
            "chat_type": "t2t",
            "timestamp": int(time.time() * 1000),
            "project_id": "",
        },
        headers=build_headers(token, include_version=False),
        ssl=False,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"create chat HTTP {response.status}: {(await response.text())[:300]}")
        data = await response.json()
        chat_id = str((data.get("data") or {}).get("id", ""))
        if not data.get("success") or not chat_id:
            raise RuntimeError(f"invalid create chat payload: {data}")
        return chat_id


async def get_qwen_stream(message: str, model: str = "qwen3.7-max") -> AsyncGenerator[Dict[str, Any], None]:
    """Log in directly and stream one response without host-project dependencies."""
    email, password = get_credentials()
    async with aiohttp.ClientSession() as session:
        try:
            token = await _login(session, email, password)
            chat_id = await _create_chat(session, token, model)
            yield {"type": "chat_id", "content": chat_id}
            payload = {
                "stream": True,
                "version": "2.1",
                "incremental_output": True,
                "chat_id": chat_id,
                "chat_mode": "local",
                "model": model,
                "parent_id": None,
                "messages": [
                    {
                        "fid": str(uuid.uuid4()),
                        "parentId": None,
                        "childrenIds": [str(uuid.uuid4())],
                        "role": "user",
                        "content": message,
                        "user_action": "chat",
                        "files": [],
                        "timestamp": int(time.time() * 1000),
                        "models": [model],
                        "chat_type": "t2t",
                        "feature_config": {
                            "thinking_enabled": True,
                            "output_schema": "phase",
                            "research_mode": "normal",
                            "auto_thinking": False,
                            "thinking_mode": "Thinking",
                            "thinking_format": "raw",
                            "auto_search": False,
                        },
                        "extra": {"meta": {"subChatType": "t2t"}},
                        "sub_chat_type": "t2t",
                    }
                ],
                "timestamp": int(time.time() * 1000),
            }
            async with session.post(
                f"{BASE_URL}{CHAT_PATH}?chat_id={chat_id}",
                json=payload,
                headers=build_headers(token, chat_id=chat_id, include_sse=True),
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"chat HTTP {response.status}: {(await response.text())[:300]}")
                async for raw in response.content:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        payload = json.loads(data_str)
                    except Exception:
                        continue
                    choices = payload.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    phase = delta.get("phase", "")
                    content = delta.get("content", "")
                    if phase == "think" and content:
                        yield {"type": "thinking", "content": content}
                    elif phase == "answer" and content:
                        yield {"type": "answer", "content": content}
            yield {"type": "done"}
        except Exception as exc:
            yield {"type": "error", "content": str(exc)}


async def main() -> None:
    """Run a single smoke-test chat request."""
    async for event in get_qwen_stream("你好"):
        print(event)


if __name__ == "__main__":
    asyncio.run(main())

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

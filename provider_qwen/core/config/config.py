


from typing import Any, Dict, Final

DEFAULT_FULL_SETTINGS: Final[Dict[str, Any]] = {
    "ui": {
        "notificationEnabled": False,
        "theme": "dark",
        "language": "",
        "chatBubble": True,
        "showUsername": False,
        "widescreenMode": False,
        "title": {"auto": False},
        "autoTags": True,
        "largeTextAsFile": False,
        "splitLargeChunks": False,
        "scrollOnBranchChange": True,
        "responseAutoCopy": False,
        "models": [],
        "richTextInput": False,
    },
    "mcp_remind": False,
    "mcp": {
        "code-interpreter": False,
        "fire-crawl": False,
        "amap": False,
        "image-generation": False,
    },
    "memory": {
        "enable_memory": False,
        "enable_history_memory": False,
        "memory_version_reminder": False,
    },
    "reminder": {"project_version_reminder": False},
    "tts_speaker": {
        "speaker": "Cherry",
        "description": "一位阳光、积极、友好且自然的年轻女士",
        "url": "",
        "gender": "female",
    },
    "tts_speaker_v2": {
        "speaker": "Nini",
        "description": "像糯米糍一样软糯黏腻的嗓音",
        "url": "",
        "gender": "female",
        "is_personal": False,
        "speaker_id": "",
        "spk_name": "邻家妹妹",
    },
    "aipodcast": {"host": "", "guest": ""},
    "code_settings": {
        "custom_prompt": "",
        "diff_display": "split",
        "branch_format": "",
        "last_repo_choice": "",
        "last_branch_choice": "",
    },
    "manage_cookies": None,
    "personalization": {
        "name": "",
        "description": "",
        "style": None,
        "instruction": "",
        "enable_for_new_chat": False,
    },
    "tools_enabled": {
        "web_search": False,
        "web_extractor": False,
        "web_search_image": False,
        "image_gen_tool": True,
        "image_edit_tool": True,
        "code_interpreter": False,
        "bio": False,
        "history_retriever": False,
        "image_zoom_in_tool": False,
    },
}

# =======================================================================
# 重导出 — 同包内协同模块的公共符号（保持外部 ``from .. import`` 路径稳定）
# =======================================================================

__all__ = [
]

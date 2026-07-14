from __future__ import annotations

"""Upload mixin for OSS-backed Qwen file inputs."""

import asyncio
import base64
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

from ..config.endpoints import BASE_URL, GENERATED_IMAGE_DIR, STS_TOKEN_PATHS, USER_AGENT
from .files import build_file_object
from .mimes import DATA_URI_EXT_MAP, get_file_category, get_mime_type
from .oss import build_oss_authorization
from ..store.storage import save_image_file

_MAX_FILE_SIZES: Dict[str, int] = {
    "video": 500 * 1024 * 1024,
    "audio": 100 * 1024 * 1024,
    "image": 20 * 1024 * 1024,
    "file": 20 * 1024 * 1024,
}


class UploadMixin:
    """Provide file upload and image-download capabilities."""

    async def _get_sts_credentials(
        self,
        token: str,
        filename: str,
        filesize: int,
        filetype: str,
    ) -> Dict[str, Any]:
        """Request temporary STS credentials for OSS upload."""
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json;charset=UTF-8",
            "source": "web",
            "user-agent": USER_AGENT,
            "origin": BASE_URL,
            "referer": f"{BASE_URL}/",
            "accept": "application/json",
        }
        payload = {"filename": filename, "filesize": filesize, "filetype": filetype}
        last_error: Optional[Exception] = None
        for path in STS_TOKEN_PATHS:
            try:
                async with self._session.post(
                    f"{BASE_URL}{path}",
                    json=payload,
                    headers=headers,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=15),
                    proxy=self._get_proxy_kwarg(),
                ) as response:
                    if response.status != 200:
                        last_error = RuntimeError(f"STS HTTP {response.status}: {(await response.text())[:200]}")
                        continue
                    data = await response.json()
                    creds = data.get("data", data)
                    if all(key in creds for key in {"access_key_id", "access_key_secret", "security_token"}):
                        return creds
                    last_error = RuntimeError(f"invalid STS payload: {data}")
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"all STS endpoints failed: {last_error}")

    async def _upload_to_oss(
        self,
        file_data: bytes,
        content_type: str,
        creds: Dict[str, Any],
    ) -> str:
        """Upload bytes to OSS and return the final file URL."""
        file_url = str(creds.get("file_url", ""))
        object_key = str(creds.get("file_path", ""))
        parsed = urlparse(file_url)
        bucket_host = parsed.netloc
        bucket_name = bucket_host.split(".")[0]
        resource = f"/{bucket_name}/{object_key}"
        date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        oss_headers = {"x-oss-security-token": str(creds.get("security_token", ""))}
        authorization = build_oss_authorization(
            "PUT",
            content_type,
            date,
            oss_headers,
            resource,
            str(creds.get("access_key_id", "")),
            str(creds.get("access_key_secret", "")),
        )
        headers = {
            "Host": bucket_host,
            "Date": date,
            "Content-Type": content_type,
            "Content-Length": str(len(file_data)),
            "Authorization": authorization,
            "x-oss-security-token": str(creds.get("security_token", "")),
            "User-Agent": USER_AGENT,
        }
        async with self._session.put(
            f"https://{bucket_host}/{object_key}",
            data=file_data,
            headers=headers,
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as response:
            if response.status not in {200, 201}:
                raise RuntimeError(f"OSS PUT failed: HTTP {response.status}: {(await response.text())[:300]}")
        return file_url

    async def upload_file(
        self,
        file_data: bytes,
        filename: str,
        token: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Upload one file and return the corresponding Qwen file object."""
        content_type = get_mime_type(filename)
        file_type, _ = get_file_category(content_type)
        file_size = len(file_data)
        if file_size <= 0:
            raise RuntimeError(f"empty file: {filename}")
        limit = _MAX_FILE_SIZES.get(file_type, 20 * 1024 * 1024)
        if file_size > limit:
            raise RuntimeError(f"file too large: {filename} ({file_size} > {limit})")
        creds = await self._get_sts_credentials(token, filename, file_size, file_type)
        file_url = await self._upload_to_oss(file_data, content_type, creds)
        return build_file_object(
            file_id=str(creds.get("file_id", uuid.uuid4())),
            file_url=file_url,
            filename=filename,
            size=file_size,
            content_type=content_type,
            user_id=user_id,
        )

    async def upload_file_from_path(self, file_path: str, token: str, user_id: str) -> Dict[str, Any]:
        """Upload a local file by path."""
        if not os.path.exists(file_path):
            raise RuntimeError(f"file not found: {file_path}")
        return await self.upload_file(Path(file_path).read_bytes(), os.path.basename(file_path), token, user_id)

    async def upload_file_from_base64(self, data_uri: str, token: str, user_id: str) -> Dict[str, Any]:
        """Upload a base64 data URI as a file object."""
        if not data_uri.startswith("data:") or ";base64," not in data_uri:
            raise RuntimeError("invalid base64 data URI")
        header, encoded = data_uri.split(";base64,", 1)
        mime_type = header.split("data:", 1)[1]
        padding = (-len(encoded)) % 4
        if padding:
            encoded += "=" * padding
        filename = f"upload_{uuid.uuid4().hex[:8]}{DATA_URI_EXT_MAP.get(mime_type, '.bin')}"
        return await self.upload_file(base64.b64decode(encoded), filename, token, user_id)

    def _extract_base64_images(self, messages: List[Dict[str, Any]]) -> List[str]:
        """Extract inline base64 images from OpenAI-style messages."""
        results: List[str] = []
        for message in messages:
            content = message.get("content", "")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "image_url":
                    continue
                image_url = part.get("image_url", {})
                if isinstance(image_url, dict):
                    candidate = str(image_url.get("url", ""))
                else:
                    candidate = str(image_url)
                if candidate.startswith("data:"):
                    results.append(candidate)
        return results

    async def download_image(self, image_url: str, save_dir: str = GENERATED_IMAGE_DIR) -> Optional[str]:
        """Download an image and return the saved local path."""
        async with self._session.get(
            image_url,
            headers={
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Connection": "keep-alive",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/",
                "User-Agent": USER_AGENT,
            },
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=60),
            proxy=self._get_proxy_kwarg(),
        ) as response:
            if response.status != 200:
                return None
            return save_image_file(await response.read(), response.headers.get("Content-Type", "image/png"), save_dir)

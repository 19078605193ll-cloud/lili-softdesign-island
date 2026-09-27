from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import socket
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import httpx
from PIL import Image

from app.imports.storage import LocalImportStorage, ImportStorageError


def unpack(storage: LocalImportStorage, batch_id: uuid.UUID, filename: str, data: bytes) -> Path:
    if not data or len(data) > storage.max_bytes:
        raise ImportStorageError("文件为空或超过上传大小限制")
    root = storage.resolve(f"{batch_id}/markdown")
    root.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower()
    if suffix == ".md":
        target = root / "source.md"
        target.write_bytes(data)
        return target
    if suffix != ".zip":
        raise ImportStorageError("新导入只支持 Markdown 或 ZIP，PDF/DOCX 导入已停用")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 2000 or sum(m.file_size for m in members) > storage.max_bytes * 4:
                raise ImportStorageError("ZIP 解压后过大或文件过多")
            seen = set()
            for member in members:
                name = member.filename.replace("\\", "/")
                parts = PurePosixPath(name).parts
                if not parts or name.startswith("/") or ":" in name or ".." in parts or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ImportStorageError("ZIP 包含不安全路径或符号链接")
                if name.casefold() in seen:
                    raise ImportStorageError("ZIP 包含重复路径")
                seen.add(name.casefold())
                target = root.joinpath(*parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise ImportStorageError("无法读取 ZIP") from exc
    documents = list(root.rglob("*.md"))
    if len(documents) != 1:
        raise ImportStorageError("每个 ZIP 必须包含一份 Markdown 文档")
    return documents[0]


def local_image(reference: str, document: Path, root: Path) -> Path:
    ref = unquote(reference).replace("\\", "/")
    root = root.resolve()
    candidates = []
    direct = (document.parent / ref).resolve()
    if direct.is_relative_to(root) and direct.is_file():
        return direct
    # Remap the original machine's path by the longest matching package suffix.
    parts = ref.split("/")
    for count in range(len(parts), 0, -1):
        tail = "/".join(parts[-count:])
        candidates = [p for p in root.rglob("*") if p.is_file() and p.as_posix().endswith("/" + tail)]
        if candidates:
            break
    if len(candidates) != 1 or not candidates[0].resolve().is_relative_to(root):
        raise ImportStorageError(f"图片不存在或匹配不唯一：{reference}")
    return candidates[0]


async def download_image(url: str, max_bytes: int) -> bytes:
    async with httpx.AsyncClient(timeout=25, trust_env=False) as client:
        for _ in range(5):
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ImportStorageError("图片地址必须是公网 HTTP/HTTPS 地址")
            addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            # Some Windows proxy/TUN setups map public hosts to 198.18/15.
            # Permit that reserved proxy range only for the known source CDN.
            proxy_cdn = parsed.hostname == "cdn-mineru.openxlab.org.cn"
            proxy_range = ipaddress.ip_network("198.18.0.0/15")
            if not addresses or any(not (ipaddress.ip_address(a[4][0]).is_global or
                (proxy_cdn and ipaddress.ip_address(a[4][0]) in proxy_range)) for a in addresses):
                raise ImportStorageError("不能下载内网地址图片")
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    from urllib.parse import urljoin
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise ImportStorageError("图片超过大小限制")
                return bytes(body)
    raise ImportStorageError("图片重定向次数过多")


def store_image(storage: LocalImportStorage, batch_id: uuid.UUID, data: bytes) -> dict:
    if len(data) > min(storage.max_bytes, 20 * 1024 * 1024):
        raise ImportStorageError("图片超过大小限制")
    try:
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
            if width * height > 40_000_000:
                raise ValueError("图片像素过大")
            fmt = im.format
            im.verify()
        suffix, mime = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
                        "WEBP": ("webp", "image/webp"), "GIF": ("gif", "image/gif")}[fmt]
    except Exception as exc:
        raise ImportStorageError("不是有效的 PNG/JPEG/WebP/GIF 图片") from exc
    digest = hashlib.sha256(data).hexdigest()
    relative = f"{batch_id}/assets/{digest}.{suffix}"
    path = storage.resolve(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    return dict(storage_path=relative, sha256=digest, width=width, height=height, mime_type=mime)

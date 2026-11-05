"""Local-filesystem DocumentStorage (ADR-0016): tests, fixtures and the corpus build; same key layout as S3."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path


class LocalFsDocumentStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root.resolve() not in path.parents:
            raise ValueError("storage key escapes the root")
        return path

    async def put_file(self, key: str, path: Path, *, content_type: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, path, target)

    async def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)

    async def get_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def download_to(self, key: str, path: Path) -> None:
        await asyncio.to_thread(shutil.copyfile, self._path(key), path)

    async def stream(self, key: str) -> AsyncIterator[bytes]:
        with self._path(key).open("rb") as handle:
            while block := handle.read(1 << 16):
                yield block

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()

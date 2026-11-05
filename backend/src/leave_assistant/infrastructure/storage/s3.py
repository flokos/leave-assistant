"""S3-compatible object storage client (SeaweedFS) and the DocumentStorage adapter (ADR-0016)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from leave_assistant.config import Settings


def make_s3_client(settings: Settings, timeout: float = 2.0) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        region_name=settings.s3_region,
        config=Config(
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": 1},
            s3={"addressing_style": "path"},
        ),
    )


def bucket_exists(client: Any, bucket: str) -> bool:
    try:
        client.head_bucket(Bucket=bucket)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchBucket", "NotFound"}:
            return False
        raise


def ensure_bucket(client: Any, bucket: str) -> bool:
    """Create the bucket if missing. Returns True when it was created."""
    if bucket_exists(client, bucket):
        return False
    client.create_bucket(Bucket=bucket)
    return True


class S3DocumentStorage:
    """DocumentStorage over boto3 (sync client wrapped in threads). One bucket, path-style addressing."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put_file(self, key: str, path: Path, *, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.upload_file, str(path), self._bucket, key, ExtraArgs={"ContentType": content_type}
        )

    async def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object, Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
        )

    async def get_bytes(self, key: str) -> bytes:
        def read() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return bytes(response["Body"].read())

        return await asyncio.to_thread(read)

    async def download_to(self, key: str, path: Path) -> None:
        await asyncio.to_thread(self._client.download_file, self._bucket, key, str(path))

    async def stream(self, key: str) -> AsyncIterator[bytes]:
        response = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        body = response["Body"]
        try:
            while True:
                block = await asyncio.to_thread(body.read, 1 << 16)
                if not block:
                    break
                yield block
        finally:
            body.close()

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(self._client.head_object, Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

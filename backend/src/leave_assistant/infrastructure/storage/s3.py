"""S3-compatible object storage client (SeaweedFS). The DocumentStorage port adapter arrives in M4 (ADR-0016)."""

from __future__ import annotations

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

#!/usr/bin/env python3
"""Fill empty keys in deploy/.env with fresh random values (`make env`): the M5 encryption keys, the M8 n8n
encryption key and owner password, and the M9 Langfuse/ClickHouse/Valkey secrets. Never prints the values."""

from __future__ import annotations

import base64
import re
import secrets
import sys
import uuid
from collections.abc import Callable
from pathlib import Path


def _b64() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def _password(prefix: str = "") -> Callable[[], str]:
    # URL-safe characters only: the values end up in shell commands, JSON and connection strings
    return lambda: prefix + secrets.token_urlsafe(18)


GENERATORS: dict[str, Callable[[], str]] = {
    # M5 — content security (doc 08)
    "APP_KEK": _b64,
    "VAULT_HMAC_KEY": _b64,
    "VAULT_KEK": _b64,
    # M8 — n8n (doc 06); n8n requires 8+ characters with a digit and an upper-case letter
    "N8N_ENCRYPTION_KEY": _b64,
    "N8N_OWNER_PASSWORD": _password("N8n-"),
    # M9 — Langfuse (ADR-0022): ENCRYPTION_KEY must be 64 hex characters; project keys follow Langfuse's format
    "LANGFUSE_NEXTAUTH_SECRET": _b64,
    "LANGFUSE_SALT": _b64,
    "LANGFUSE_ENCRYPTION_KEY": lambda: secrets.token_hex(32),
    "LANGFUSE_PUBLIC_KEY": lambda: f"pk-lf-{uuid.uuid4()}",
    "LANGFUSE_SECRET_KEY": lambda: f"sk-lf-{uuid.uuid4()}",
    "LANGFUSE_INIT_USER_PASSWORD": _password("Lf-"),
    "LANGFUSE_S3_ACCESS_KEY": lambda: "langfuse-" + secrets.token_hex(6),
    "LANGFUSE_S3_SECRET_KEY": _password(),
    "CLICKHOUSE_PASSWORD": _password(),
    "VALKEY_PASSWORD": _password(),
}


def main(path: str) -> int:
    env = Path(path)
    if not env.exists():
        print(f"{path} not found", file=sys.stderr)
        return 1
    lines = env.read_text(encoding="utf-8").splitlines()
    generated: list[str] = []
    for key, make in GENERATORS.items():
        pattern = re.compile(rf"^{key}=\s*(#.*)?$")
        for index, line in enumerate(lines):
            if pattern.match(line):
                lines[index] = f"{key}={make()}"
                generated.append(key)
                break
        else:
            if not any(line.startswith(f"{key}=") for line in lines):
                lines.append(f"{key}={make()}")
                generated.append(key)
    if generated:
        env.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("generated " + ", ".join(generated) + f" in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "deploy/.env"))

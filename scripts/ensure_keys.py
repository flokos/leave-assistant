#!/usr/bin/env python3
"""Fill empty keys in deploy/.env with fresh random values (`make env`): the M5 encryption keys and the M8 n8n
encryption key and owner password. Never prints the values."""

from __future__ import annotations

import base64
import re
import secrets
import sys
from pathlib import Path

KEYS = ("APP_KEK", "VAULT_HMAC_KEY", "VAULT_KEK", "N8N_ENCRYPTION_KEY")
PASSWORDS = ("N8N_OWNER_PASSWORD",)  # n8n requires 8+ characters with a digit and an upper-case letter


def main(path: str) -> int:
    env = Path(path)
    if not env.exists():
        print(f"{path} not found", file=sys.stderr)
        return 1
    lines = env.read_text(encoding="utf-8").splitlines()
    generated: list[str] = []
    def value(key: str) -> str:
        if key in PASSWORDS:
            return "N8n-" + secrets.token_urlsafe(18)
        return base64.b64encode(secrets.token_bytes(32)).decode()

    for key in (*KEYS, *PASSWORDS):
        pattern = re.compile(rf"^{key}=\s*(#.*)?$")
        for index, line in enumerate(lines):
            if pattern.match(line):
                lines[index] = f"{key}={value(key)}"
                generated.append(key)
                break
        else:
            if not any(line.startswith(f"{key}=") for line in lines):
                lines.append(f"{key}={value(key)}")
                generated.append(key)
    if generated:
        env.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("generated " + ", ".join(generated) + f" in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "deploy/.env"))

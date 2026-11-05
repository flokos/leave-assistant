#!/usr/bin/env python3
"""Fill empty M5 encryption keys in deploy/.env with fresh random values (`make env`). Never prints the keys."""

from __future__ import annotations

import base64
import re
import secrets
import sys
from pathlib import Path

KEYS = ("APP_KEK", "VAULT_HMAC_KEY", "VAULT_KEK")


def main(path: str) -> int:
    env = Path(path)
    if not env.exists():
        print(f"{path} not found", file=sys.stderr)
        return 1
    lines = env.read_text(encoding="utf-8").splitlines()
    generated: list[str] = []
    for key in KEYS:
        pattern = re.compile(rf"^{key}=\s*(#.*)?$")
        for index, line in enumerate(lines):
            if pattern.match(line):
                lines[index] = f"{key}={base64.b64encode(secrets.token_bytes(32)).decode()}"
                generated.append(key)
                break
        else:
            if not any(line.startswith(f"{key}=") for line in lines):
                lines.append(f"{key}={base64.b64encode(secrets.token_bytes(32)).decode()}")
                generated.append(key)
    if generated:
        env.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("generated " + ", ".join(generated) + f" in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "deploy/.env"))

"""Print the OpenAPI document — the executable contract snapshotted in docs/api/openapi.json (doc 09)."""

from __future__ import annotations

import json
import sys

from leave_assistant.config import Settings
from leave_assistant.main import create_app


def render() -> str:
    app = create_app(Settings(app_env="test"), bootstrap_infrastructure=False)
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render())

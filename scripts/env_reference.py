"""Generate docs/operations/configuration.md from deploy/.env.example (M10; `make docs-env`, checked by `make contract`).

The example file is the single source of the environment variables: each line `NAME=value  # comment`, grouped by
`# --- Title [Mx] ---` headers, with free comment lines above a group. The generated page lists every variable
with its example value and comment; secret-like variables never show a value (the example has none anyway).

    python3 scripts/env_reference.py            # write the page
    python3 scripts/env_reference.py --check    # exit 1 when the page is stale
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "deploy" / ".env.example"
TARGET = ROOT / "docs" / "operations" / "configuration.md"
HEADER = re.compile(r"^#\s*---\s*(?P<title>.+?)\s*-*\s*$")
VARIABLE = re.compile(r"^(?P<name>[A-Z][A-Z0-9_]*)=(?P<value>[^#]*?)\s*(?:#\s?(?P<comment>.*))?$")
SECRET = re.compile(r"(PASSWORD|SECRET|_KEY$|KEK|SALT|TOKEN)")

INTRO = """# Configuration reference

Generated from [`deploy/.env.example`](../../deploy/.env.example) by `scripts/env_reference.py` (`make docs-env`);
`make contract` fails when this page is out of date. Copy the example with `make env`, which also generates every
empty key and password into the git-ignored `deploy/.env`. The backend reads the variables through
`backend/src/leave_assistant/config.py` (pydantic-settings: an empty value means "use the default", unknown values of
enumerated flags fail at start-up); Compose passes them to the containers (`deploy/compose.yaml`).

Secrets are never shown here: *(your key)* is a key from an external account you add yourself; *(generated)* means
`make env` fills an empty value with a random one; *(dev value)*
means the example carries a development value (for example `postgres-dev`) that must be replaced on anything but a
local, non-exposed machine. Neither belongs in git. The milestone in each section title says when the variables were
introduced.
"""


def _cell(text: str) -> str:
    return text.replace("|", "\\|").strip()


def render(source: str) -> str:
    out = [INTRO]
    notes: list[str] = []
    rows: list[str] = []

    def flush() -> None:
        if notes:
            out.append("\n" + " ".join(notes) + "\n")
            notes.clear()
        if rows:
            out.append("\n| Variable | Example | Purpose |\n|---|---|---|")
            out.extend(rows)
            rows.clear()

    started = False
    pending: dict[str, str] = {}
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not started and not HEADER.match(stripped):
            continue  # the file's own preamble ("copy to deploy/.env …") is said in the intro
        started = True
        header = HEADER.match(stripped)
        if header:
            flush()
            out.append(f"\n## {header.group('title').rstrip('- ').strip()}")
            continue
        if stripped.startswith("#"):
            text = stripped.lstrip("#").strip()
            own = re.match(r"^([A-Z][A-Z0-9_]*):\s*(.*)$", text)
            if own:  # "# NAME: text" above an empty variable (an inline comment would become its value)
                pending[own.group(1)] = own.group(2)
            elif text and not rows:
                notes.append(text)
            elif text:  # a comment between variables of the same group: keep it as a row note
                rows.append(f"| | | {_cell(text)} |")
            continue
        variable = VARIABLE.match(stripped)
        if variable is None:
            continue
        name = variable.group("name")
        value = variable.group("value").strip()
        if SECRET.search(name) and not name.endswith("_PORT"):
            if name.endswith("_API_KEY") or name.startswith("GOOGLE_OAUTH"):
                shown = "*(your key)*"  # an external account; never generated
            else:
                shown = "*(generated)*" if not value else "*(dev value)*"
        else:
            shown = f"`{value}`" if value else "—"
        comment = variable.group("comment") or pending.pop(name, "")
        rows.append(f"| `{name}` | {shown} | {_cell(comment)} |")
    flush()
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str]) -> int:
    page = render(SOURCE.read_text(encoding="utf-8"))
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != page:
            sys.stderr.write(f"{TARGET.relative_to(ROOT)} is stale: run `make docs-env`\n")
            return 1
        return 0
    TARGET.write_text(page, encoding="utf-8")
    sys.stdout.write(f"wrote {TARGET.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

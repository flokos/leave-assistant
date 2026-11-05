"""`python -m leave_assistant.security.check [fixtures_dir]`: score the EL/EN injection fixtures with the live
heuristics + similarity classifier (`make security-check`). Each fixture is a Markdown file whose first line is
`<!-- expect: low|medium|high -->`; the exit code is 1 when any expectation is missed."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from leave_assistant.application.security.scan import InjectionScanService
from leave_assistant.config import get_settings
from leave_assistant.domain.security.scoring import RiskThresholds
from leave_assistant.infrastructure.ml.embedder import E5Embedder
from leave_assistant.infrastructure.security.similarity_classifier import SimilarityClassifier

FIXTURES = Path(__file__).with_name("fixtures") / "injection"
EXPECT = re.compile(r"<!--\s*expect:\s*(low|medium|high)\s*-->")


def blocks_of(text: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip() and not b.strip().startswith("<!--")]


def load_fixtures(root: Path) -> list[tuple[str, str, str]]:
    """`(relative name, expected tier, text)` for every `*.md` under `root`."""
    out: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = EXPECT.search(text)
        expected = match.group(1) if match else "low"
        out.append((str(path.relative_to(root)), expected, text))
    return out


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else FIXTURES
    settings = get_settings()
    embedder = E5Embedder(
        settings.embedding_model, settings.embedding_model_revision, batch_size=settings.embedding_batch_size
    )
    service = InjectionScanService(SimilarityClassifier(embedder))
    thresholds = RiskThresholds()
    misses = 0
    sys.stdout.write(
        f"{'fixture':<40} {'expected':<9} {'tier':<7} {'score':>6} {'heur':>6} {'cls':>6}  families\n"
    )
    for name, expected, text in load_fixtures(root):
        result = service.assess_blocks(blocks_of(text), thresholds=thresholds)
        families = sorted({str(f["family"]) for f in result.findings})
        ok = result.tier.value == expected
        misses += 0 if ok else 1
        sys.stdout.write(
            f"{name:<40} {expected:<9} {result.tier.value:<7} {result.score:>6.3f} {result.heuristic_score:>6.3f} "
            f"{(result.classifier_score or 0.0):>6.3f}  {','.join(families)}{'' if ok else '   <-- MISS'}\n"
        )
    sys.stdout.write(f"misses: {misses}\n")
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

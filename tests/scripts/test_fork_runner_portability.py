from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
UNAVAILABLE_LARGER_RUNNER = re.compile(
    r"\b(?:ubuntu|windows|macos)-latest-\d+(?:-[a-z0-9]+)*-core\b"
)


def test_fork_ci_uses_public_github_hosted_runner_labels() -> None:
    unavailable: list[str] = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for line_number, line in enumerate(workflow.read_text().splitlines(), 1):
            match = UNAVAILABLE_LARGER_RUNNER.search(line)
            if match:
                unavailable.append(
                    f"{workflow.relative_to(ROOT)}:{line_number}: {match.group(0)}"
                )

    assert unavailable == [], (
        "Fork CI must use public GitHub-hosted runner labels; configured "
        "larger-runner labels queue forever in batumilove/hermes-agent:\n"
        + "\n".join(unavailable)
    )

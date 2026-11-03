"""Remove one reviewed, inert legacy-launcher specimen from scanner input."""
from __future__ import annotations

import sys

_FIXTURE_PATH = "tests/hermes_cli/test_post_update_expose_cli.py"
# This string is written into a temporary .cmd file for ownership inspection;
# the fixture never executes it. Keep every other line in the security scan.
_FIXTURE_LINE = (
    '+            code = f"import base64; exec('
    "base64.b64decode('{encoded}'))\""
)


def filter_reviewed_fixture(diff: str) -> str:
    """Preserve diff bytes except the exact reviewed specimen in its owning file."""
    path = None
    kept = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            path = None
        elif line.startswith("+++ b/"):
            path = line.removeprefix("+++ b/").rstrip("\r\n")
        if path == _FIXTURE_PATH and line.rstrip("\r\n") == _FIXTURE_LINE:
            continue
        kept.append(line)
    return "".join(kept)


if __name__ == "__main__":
    sys.stdout.write(filter_reviewed_fixture(sys.stdin.read()))

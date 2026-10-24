"""Native Windows marker metadata behavior, isolated within the existing per-file deadline."""

from __future__ import annotations

from pathlib import Path
import subprocess
import pytest
from tests.scripts.desktop_update.windows_marker_support import _assert_marker_body

pytest_plugins = ["tests.scripts.desktop_update.windows_marker_support"]


@pytest.mark.platforms("windows")
@pytest.mark.parametrize(
    "case",
    [
        "crlf-bom-v2",
        "delegate-without-ct-is-ignored",
        "garbled-ct-is-v1-fresh",
        "garbled-ct-is-v1-past-20min",
    ],
)
def test_marker_bodies_are_parsed_positionally_like_every_other_reader(
    tmp_path: Path, sleeper: subprocess.Popen, case: str
) -> None:
    _assert_marker_body(tmp_path, sleeper, case)

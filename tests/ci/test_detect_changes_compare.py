"""Execute the CI detector against complete and capped GitHub compare responses."""

import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("file_count", [299, 300])
def test_compare_cap_cannot_hide_required_ci_lanes(tmp_path, file_count):
    root = Path(__file__).resolve().parents[2]
    action = yaml.safe_load((root / ".github/actions/detect-changes/action.yml").read_text())
    script = action["runs"]["steps"][0]["run"]
    response = tmp_path / "response.json"
    response.write_text(json.dumps({
        "files": [{"filename": f"website/docs/page-{i}.md"} for i in range(file_count)],
    }))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    # Run the action's actual jq selection over a controlled API response.
    gh.write_text('#!/bin/sh\nfor arg do query="$arg"; done\nexec jq -r "$query" "$DETECT_RESPONSE"\n')
    gh.chmod(0o755)
    output = tmp_path / "outputs"
    result = subprocess.run(
        ["bash", "-c", script], cwd=root, capture_output=True, text=True,
        timeout=15, check=True,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
             "DETECT_RESPONSE": str(response), "GITHUB_OUTPUT": str(output),
             "EVENT_NAME": "pull_request", "REPO": "example/repo",
             "BASE_SHA": "base", "HEAD_SHA": "head"},
    )
    lanes = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert lanes["site"] == "true"
    if file_count == 300:
        assert set(lanes.values()) == {"true"}, result.stdout
    else:
        assert lanes["frontend"] == "false"

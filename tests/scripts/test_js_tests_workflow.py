from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "js-tests.yml"


def test_js_workflow_installs_only_the_exact_cooldown_protected_npm() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    install_lines = [
        line.strip()
        for line in text.splitlines()
        if "npm install -g npm@" in line or "npm i -g npm@" in line
    ]

    assert install_lines == [
        "npm install -g npm@12.0.1 --ignore-scripts --min-release-age=14",
        "npm install -g npm@12.0.1 --ignore-scripts --min-release-age=14",
    ]
    assert "npm i -g npm@12" not in text
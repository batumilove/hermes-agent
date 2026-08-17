import shlex
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "js-tests.yml"


def _global_npm_install_tokens(text: str) -> list[list[str]]:
    workflow = yaml.safe_load(text)
    installs: list[list[str]] = []
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            run = step.get("run") if isinstance(step, dict) else None
            if not isinstance(run, str):
                continue
            for line in run.splitlines():
                if not line.lstrip().startswith(("npm ", "npm\t")):
                    continue
                tokens = shlex.split(line, comments=True, posix=True)
                if (
                    len(tokens) >= 3
                    and tokens[0] == "npm"
                    and tokens[1] in {"install", "i"}
                    and any(token in {"-g", "--global"} for token in tokens[2:])
                ):
                    installs.append(tokens)
    return installs


def _assert_approved_global_npm_installs(text: str) -> None:
    installs = _global_npm_install_tokens(text)
    assert len(installs) == 2
    for tokens in installs:
        arguments = tokens[2:]
        packages = [token for token in arguments if not token.startswith("-")]
        assert packages == ["npm@12.0.1"]
        assert "--ignore-scripts" in arguments
        assert "--min-release-age=14" in arguments


def test_js_workflow_installs_only_the_exact_cooldown_protected_npm() -> None:
    _assert_approved_global_npm_installs(WORKFLOW.read_text(encoding="utf-8"))


def _run_contract_against(tmp_path: Path, monkeypatch, commands: list[str]) -> None:
    fixture = tmp_path / "js-tests.yml"
    fixture.write_text(
        yaml.safe_dump(
            {
                "jobs": {
                    f"job-{index}": {"steps": [{"run": command}]}
                    for index, command in enumerate(commands)
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "WORKFLOW", fixture)
    test_js_workflow_installs_only_the_exact_cooldown_protected_npm()


def test_contract_accepts_global_flag_and_reordered_approved_arguments(
    tmp_path: Path, monkeypatch
) -> None:
    approved = (
        "npm install --ignore-scripts npm@12.0.1 "
        "--min-release-age=14 --global"
    )
    _run_contract_against(tmp_path, monkeypatch, [approved, approved])


@pytest.mark.parametrize(
    "unapproved",
    [
        "npm install --global npm@12",
        "npm install --global npm@12.0.2 --ignore-scripts --min-release-age=14",
        "npm install --global npm@latest --ignore-scripts --min-release-age=14",
        "npm i npm@12.0.1 --global --ignore-scripts",
        "npm install left-pad --ignore-scripts --min-release-age=14 --global",
    ],
)
def test_contract_rejects_equivalent_unapproved_global_bootstraps(
    tmp_path: Path, monkeypatch, unapproved: str
) -> None:
    approved = "npm install -g npm@12.0.1 --ignore-scripts --min-release-age=14"
    with pytest.raises(AssertionError):
        _run_contract_against(
            tmp_path,
            monkeypatch,
            [approved, approved, unapproved],
        )

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
                # Strip shell control prefixes (assignments, subshells,
                # command separators) so an unsafe install hidden after
                # `true;`, `X=1`, `(`, or similar cannot slip past.
                cleaned = line.strip().lstrip("()")
                # Split on shell separators and check each segment.
                for sep in ["&&", "||", ";", " | "]:
                    cleaned = cleaned.replace(sep, "\n")
                for segment in cleaned.splitlines():
                    segment = segment.strip().lstrip("()")
                    # Strip leading env-var assignments: VAR=val cmd ...
                    while segment.split() and "=" in segment.split()[0]:
                        parts = segment.split(None, 1)
                        if len(parts) < 2:
                            break
                        segment = parts[1].strip()
                    try:
                        tokens = shlex.split(segment, comments=True, posix=True)
                    except ValueError:
                        # Unbalanced quotes or other shlex error — skip.
                        continue
                    if len(tokens) < 3 or tokens[0] != "npm":
                        continue
                    # Find the install/i subcommand (may be preceded by
                    # global flags like `npm --global install npm@12`).
                    sub_idx = None
                    for idx in range(1, len(tokens)):
                        if tokens[idx] in {"install", "i"}:
                            sub_idx = idx
                            break
                    if sub_idx is None:
                        continue
                    remaining = tokens[sub_idx + 1:]
                    # Check -g/--global anywhere in tokens after "npm".
                    if not any(
                        token in {"-g", "--global"} or token.startswith("--global=")
                        for token in tokens[1:]
                    ):
                        continue
                    installs.append(tokens)
    return installs


def _assert_approved_global_npm_installs(text: str) -> None:
    installs = _global_npm_install_tokens(text)
    assert len(installs) == 2
    for tokens in installs:
        # Collect all non-flag arguments (packages) and all flags.
        sub_idx = next(i for i, t in enumerate(tokens) if t in {"install", "i"})
        arguments = tokens[sub_idx + 1:]
        # Also check flags before install subcommand.
        pre_flags = tokens[1:sub_idx]
        all_args = pre_flags + arguments
        packages = [token for token in arguments if not token.startswith("-")]
        assert packages == ["npm@12.0.1"]
        assert "--ignore-scripts" in all_args
        assert "--min-release-age=14" in all_args


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
        # Shell-prefixed forms that must also be caught:
        "true; npm install --global npm@12",
        'X=1 npm install --global npm@12',
        "(npm install --global npm@12)",
        "npm --global install npm@12",
        "npm install --global=true npm@12",
        "echo hi && npm i -g npm@12",
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

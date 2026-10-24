from pathlib import Path

import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_dcf_skill_uses_locked_project_extra() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    assert project["optional-dependencies"]["dcf"] == ["openpyxl==3.1.5"]
    assert not (ROOT / "optional-skills/finance/dcf-model/requirements.txt").exists()

    skill = (ROOT / "optional-skills/finance/dcf-model/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "hermes-agent[dcf]" in skill

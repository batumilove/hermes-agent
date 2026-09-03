from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from scripts.run_batumi_contracts import ContractError, load_contracts, main


def test_repository_contract_manifest_is_valid():
    contracts = load_contracts(
        Path(__file__).resolve().parents[2] / ".github" / "batumi-contracts.yaml"
    )
    assert {contract["id"] for contract in contracts} >= {
        "cli-and-config",
        "state-and-sqlite",
        "plugins",
        "telegram",
        "cron",
        "gateway-lifecycle",
        "terminal-backends",
        "update-and-rollback",
    }
    update_contract = next(
        contract for contract in contracts if contract["id"] == "update-and-rollback"
    )
    assert update_contract["serial"] is True


def test_contract_manifest_rejects_duplicate_ids(tmp_path, monkeypatch):
    test_file = tmp_path / "test_ok.py"
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")
    manifest = tmp_path / "contracts.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "contracts": [
                    {"id": "same", "rationale": "one", "tests": [str(test_file)]},
                    {"id": "same", "rationale": "two", "tests": [str(test_file)]},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.run_batumi_contracts.ROOT", Path("/"))

    with pytest.raises(ContractError, match="duplicate contract id"):
        load_contracts(manifest)


def test_list_can_select_one_contract(capsys):
    assert main(["--contract", "cron", "--list"]) == 0
    assert capsys.readouterr().out == "cron\n"


def test_serial_contract_uses_single_worker(tmp_path, monkeypatch):
    test_file = tmp_path / "test_ok.py"
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")
    manifest = tmp_path / "contracts.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "contracts": [
                    {
                        "id": "docker",
                        "rationale": "avoid competing image builds",
                        "serial": True,
                        "tests": [str(test_file)],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("scripts.run_batumi_contracts.ROOT", Path("/"))
    monkeypatch.setattr("scripts.run_batumi_contracts.subprocess.run", run)

    assert main(["--manifest", str(manifest)]) == 0
    command = run.call_args.args[0]
    assert command[1:3] == ["-j", "1"]

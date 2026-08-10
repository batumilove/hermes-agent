#!/usr/bin/env python3
"""Run the fork's behavioral compatibility contracts hermetically."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / ".github" / "batumi-contracts.yaml"


class ContractError(RuntimeError):
    pass


def load_contracts(path: Path) -> list[dict[str, Any]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"could not read {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ContractError("contract manifest version must be 1")
    contracts = raw.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise ContractError("contracts must be a non-empty list")

    seen: set[str] = set()
    for index, contract in enumerate(contracts):
        if not isinstance(contract, dict):
            raise ContractError(f"contracts[{index}] must be a mapping")
        contract_id = contract.get("id")
        if not isinstance(contract_id, str) or not contract_id.strip():
            raise ContractError(f"contracts[{index}].id must be a non-empty string")
        if contract_id in seen:
            raise ContractError(f"duplicate contract id: {contract_id}")
        seen.add(contract_id)
        if not isinstance(contract.get("rationale"), str) or not contract["rationale"].strip():
            raise ContractError(f"{contract_id}.rationale must be a non-empty string")
        if not isinstance(contract.get("serial", False), bool):
            raise ContractError(f"{contract_id}.serial must be a boolean")
        tests = contract.get("tests")
        if not isinstance(tests, list) or not tests:
            raise ContractError(f"{contract_id}.tests must be a non-empty list")
        for selector in tests:
            if not isinstance(selector, str) or not selector.strip():
                raise ContractError(f"{contract_id}.tests contains an invalid selector")
            test_path = ROOT / selector.split("::", 1)[0]
            if not test_path.is_file():
                raise ContractError(f"{contract_id} references missing test: {selector}")
    return contracts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--contract", action="append", default=[])
    parser.add_argument("--list", action="store_true")
    args, pytest_args = parser.parse_known_args(argv)

    try:
        contracts = load_contracts(args.manifest.resolve())
    except ContractError as exc:
        print(f"contract validation failed: {exc}", file=sys.stderr)
        return 2

    requested = set(args.contract)
    known = {contract["id"] for contract in contracts}
    unknown = requested - known
    if unknown:
        print(f"unknown contracts: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    selected = [
        contract
        for contract in contracts
        if not requested or contract["id"] in requested
    ]
    if args.list:
        for contract in selected:
            print(contract["id"])
        return 0

    serial_selectors = list(
        dict.fromkeys(
            selector
            for contract in selected
            if contract.get("serial", False)
            for selector in contract["tests"]
        )
    )
    serial_set = set(serial_selectors)
    parallel_selectors = list(
        dict.fromkeys(
            selector
            for contract in selected
            if not contract.get("serial", False)
            for selector in contract["tests"]
            if selector not in serial_set
        )
    )
    with tempfile.TemporaryDirectory(prefix="hermes-batumi-contracts-") as home:
        env = os.environ.copy()
        env["HERMES_HOME"] = home
        lanes = (
            (parallel_selectors, []),
            (serial_selectors, ["-j", "1"]),
        )
        for selectors, runner_args in lanes:
            if not selectors:
                continue
            command = [
                str(ROOT / "scripts" / "run_tests.sh"),
                *runner_args,
                *selectors,
                "-q",
                *pytest_args,
            ]
            result = subprocess.run(command, cwd=ROOT, env=env)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

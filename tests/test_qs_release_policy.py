"""The approved qs fix must use normal fourteen-day policy, not an exclusion."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_qs_eligible_fixed_release_in_both_graphs():
    for scope in ("website", "scripts/whatsapp-bridge"):
        directory = ROOT / scope
        manifest = json.loads((directory / "package.json").read_text())
        assert manifest["overrides"].get("qs") == "6.16.1", scope
        lock = json.loads((directory / "package-lock.json").read_text())
        versions = {row["version"] for name, row in lock["packages"].items()
                    if name.endswith("node_modules/qs")}
        assert versions == {"6.16.1"}, (scope, versions)


def test_website_qs_has_no_release_age_bypass():
    config = (ROOT / "website/.npmrc").read_text().splitlines()
    assert "min-release-age=14" in config
    assert not any(line.strip() == "min-release-age-exclude[]=qs" for line in config)

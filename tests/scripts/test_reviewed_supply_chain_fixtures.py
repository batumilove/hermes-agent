"""An inert launcher specimen must not hide executable supply-chain indicators."""
from scripts.ci.reviewed_supply_chain_fixtures import filter_reviewed_fixture


def test_reviewed_launcher_specimen_does_not_hide_other_payload_lines():
    specimen = (
        '+            code = f"import base64; exec('
        "base64.b64decode('{encoded}'))\"\n"
    )
    harmful = (
        '+exec('
        'base64.b64decode(payload))\n'
    )
    header = 'diff --git a/tests/hermes_cli/test_post_update_expose_cli.py b/tests/hermes_cli/test_post_update_expose_cli.py\n+++ b/tests/hermes_cli/test_post_update_expose_cli.py\n'
    assert filter_reviewed_fixture(header + specimen + harmful) == header + harmful
    # The same bytes outside the reviewed fixture, or changed payload bytes,
    # stay visible to the scanner.
    other = 'diff --git a/production.py b/production.py\n+++ b/production.py\n'
    assert filter_reviewed_fixture(other + specimen) == other + specimen
    changed = specimen.replace('{encoded}', '{unreviewed}')
    assert filter_reviewed_fixture(header + changed) == header + changed
    # A subsequent file cannot inherit the previous file's exception.
    assert filter_reviewed_fixture(header + specimen + other + specimen) == header + other + specimen

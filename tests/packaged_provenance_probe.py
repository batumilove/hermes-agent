"""Run with an installed wheel's Python -I, outside the source checkout.

This deliberately creates the provenance marker that enters the lazy helper path.
It never starts a server, gateway, provider, or application database.
"""
from pathlib import Path
import importlib.util
import sys
import tempfile

from hermes_cli.version_provenance import imported_release_version

assert importlib.util.find_spec("scripts") is None, "source scripts leaked into installed test"
from hermes_cli import version_provenance
assert Path(version_provenance.__file__).is_relative_to(Path(sys.prefix))
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / ".github").mkdir()
    (root / ".github/upstream-sync-provenance.json").write_text("{}")
    # An unverified repository must be rejected, not fail to import a helper.
    assert imported_release_version(root) is None
print("installed provenance rejects invalid proof without missing-module failure")

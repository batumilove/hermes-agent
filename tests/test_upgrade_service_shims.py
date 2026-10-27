"""Offline contract for the shared upgrade/desktop service-manager fixture.

Run directly with python3; no updater, provider or state database is imported.
"""
import ast
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "upgrade_helpers", ROOT / "tests/e2e/core/upgrade/_helpers.py"
)
assert spec is not None and spec.loader is not None
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def discovery_args():
    tree = ast.parse((ROOT / "hermes_cli/update_cmd_fleet.py").read_text())
    return next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "_LIST_GATEWAY_UNITS"
                        for target in node.targets))


class ServiceShimContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = Path(self.tmp.name) / "shims"
        self.log = helpers.write_shims(self.bin)

    def invoke(self, name, args):
        # Absolute fixture path: never fall back to a real manager on PATH.
        result = subprocess.run([str(self.bin / name), *args], capture_output=True,
                                text=True, timeout=5, check=False, env={"PATH": "/nonexistent"})
        self.assertEqual(self.log.read_text().splitlines()[-1], f"{name} {' '.join(args)}")
        return result

    def test_exact_gateway_listing_models_empty_user_and_system_scopes(self):
        for prefix in ([], ["--user"]):
            with self.subTest(prefix=prefix):
                result = self.invoke("systemctl", prefix + discovery_args())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def test_unknown_mutating_and_near_match_commands_remain_denied(self):
        query = discovery_args()
        denied = [[], ["--version"], ["restart", "hermes-gateway.service"],
                  ["stop", "hermes-gateway.service"], ["kill", "hermes-gateway.service"],
                  ["reset-failed"], query[:-1], query + ["--all"],
                  ["--system"] + query, ["--user", "--user"] + query,
                  [" ".join(query)], ["list-units", "*", *query[5:]],
                  [*query[:2], " ".join(query[2:4]), *query[4:]]]
        for name in helpers._SHIMMED:
            for args in denied + [query, ["--user"] + query] if name != "systemctl" else denied:
                with self.subTest(name=name, args=args):
                    result = self.invoke(name, args)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("sandboxed test shim", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Independent publication floor for the October 2026 undici advisories.

Keep the expected fixed version literal: deriving it from production policy
would let a vulnerable policy and its tests agree. No CLI bootstrap or install.
"""
import json
from pathlib import Path
import unittest

from pm.npm_security_overlay import UNDICI_FLOOR, UNDICI_OVERLAY_URL


class UndiciPublicationFloor(unittest.TestCase):
    def test_policy_and_locked_artifact_clear_all_three_advisories(self):
        self.assertEqual(UNDICI_FLOOR, "6.29.1")
        expected = "https://registry.npmjs.org/undici/-/undici-6.29.1.tgz"
        self.assertEqual(UNDICI_OVERLAY_URL, expected)
        root = Path(__file__).resolve().parents[2]
        lock = json.loads((root / "pm/lock.json").read_text(encoding="utf-8-sig"))
        artifacts = lock["packages"]["npm"]["artifacts"]["any"]
        overlays = [row for row in artifacts if "/undici/-/" in row["url"]]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0]["url"], expected)
        self.assertEqual(overlays[0]["sha256"],
                         "29c4b8e0b2d86cb8795571c1959012ce057a14ecfd34858335f47c31e770a04f")


if __name__ == "__main__":
    unittest.main()

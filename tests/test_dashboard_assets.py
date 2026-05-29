from __future__ import annotations

import json
import unittest
from pathlib import Path


class DashboardAssetTests(unittest.TestCase):
    def test_manifest_references_shipped_dashboard_files(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dashboard = root / "dashboard"
        manifest_path = dashboard / "manifest.json"

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "agenttalk")
        for key in ("entry", "css", "api"):
            target = dashboard / manifest[key]
            self.assertTrue(target.is_file(), f"{key} points to missing file: {target}")

        bundle = (dashboard / manifest["entry"]).read_text(encoding="utf-8")
        self.assertIn('REGISTRY.register("agenttalk"', bundle)


if __name__ == "__main__":
    unittest.main()

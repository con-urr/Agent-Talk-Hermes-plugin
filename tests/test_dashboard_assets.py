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
        self.assertIn("AgentTalk dashboard backend is not mounted", bundle)
        self.assertIn("/cli/install", bundle)
        self.assertNotIn("SDK.fetchJSON(API_ROOT", bundle)

    def test_versions_match_across_plugin_manifests(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dashboard_manifest = json.loads((root / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
        plugin_yaml = (root / "plugin.yaml").read_text(encoding="utf-8")
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

        version = dashboard_manifest["version"]
        self.assertIn(f"version: {version}", plugin_yaml)
        self.assertIn(f'version = "{version}"', pyproject)

    def test_plugin_skill_is_shipped(self) -> None:
        root = Path(__file__).resolve().parents[1]
        skill = root / "agenttalk_hermes_plugin" / "skills" / "agenttalk" / "SKILL.md"

        self.assertTrue(skill.is_file())
        text = skill.read_text(encoding="utf-8")
        self.assertIn("name: agenttalk", text)
        self.assertIn("hermes agenttalk status --json", text)
        self.assertIn("Do not silently enable open wake", text)
        self.assertIn("hermes agenttalk setup --json", text)


if __name__ == "__main__":
    unittest.main()

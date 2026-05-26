from __future__ import annotations

import os
import tempfile
import unittest
import importlib.util
import sys
from pathlib import Path

from agenttalk_hermes_plugin import control


class FakeContext:
    def __init__(self) -> None:
        self.commands = {}

    def register_cli_command(self, **kwargs):
        self.commands[kwargs["name"]] = kwargs


class ControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("AGENTTALK_SUPERVISOR_HOME")
        os.environ["AGENTTALK_SUPERVISOR_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("AGENTTALK_SUPERVISOR_HOME", None)
        else:
            os.environ["AGENTTALK_SUPERVISOR_HOME"] = self.old_home
        self.tmp.cleanup()

    def test_registers_hermes_cli_command(self) -> None:
        ctx = FakeContext()
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "agenttalk_plugin_test",
            root / "__init__.py",
            submodule_search_locations=[str(root)],
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        plugin_entry = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = plugin_entry
        spec.loader.exec_module(plugin_entry)
        plugin_entry.register(ctx)
        self.assertIn("agenttalk", ctx.commands)
        self.assertTrue(callable(ctx.commands["agenttalk"]["setup_fn"]))
        self.assertTrue(callable(ctx.commands["agenttalk"]["handler_fn"]))

    def test_setup_defaults_agent_and_wake_off(self) -> None:
        repo = Path(self.tmp.name) / "hermes-agent"
        repo.mkdir()
        (repo / "hermes").write_text("# stub\n", encoding="utf-8")

        result = control.ensure_agent_config(repo=str(repo))

        self.assertTrue(result["configured"])
        self.assertFalse(result["agentEnabled"])
        self.assertFalse(result["wakeEnabled"])

        config = control.load_config()
        agent = config["agents"][0]
        self.assertEqual(agent["kind"], "hermes")
        self.assertFalse(agent["enabled"])
        self.assertFalse(agent["wake"]["enabled"])
        self.assertFalse(config["defaultWakePolicy"]["wakeOnDirectMessage"])

    def test_agent_and_wake_are_separate_toggles(self) -> None:
        control.ensure_agent_config()

        on = control.set_agent_enabled(True)
        self.assertTrue(on["agentEnabled"])
        self.assertFalse(on["wakeEnabled"])
        self.assertFalse(on["wakeActive"])

        wake_on = control.set_wake_enabled(True)
        self.assertTrue(wake_on["agentEnabled"])
        self.assertTrue(wake_on["wakeEnabled"])
        self.assertTrue(wake_on["wakeActive"])

        wake_off = control.set_wake_enabled(False)
        self.assertTrue(wake_off["agentEnabled"])
        self.assertFalse(wake_off["wakeEnabled"])

        off = control.set_agent_enabled(False)
        self.assertFalse(off["agentEnabled"])
        self.assertFalse(off["wakeEnabled"])
        self.assertFalse(off["wakeActive"])


if __name__ == "__main__":
    unittest.main()

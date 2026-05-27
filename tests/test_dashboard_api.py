from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


class FakeRouter:
    def __init__(self) -> None:
        self.routes = []

    def get(self, path: str):
        return self._decorator("GET", path)

    def post(self, path: str):
        return self._decorator("POST", path)

    def _decorator(self, method: str, path: str):
        def register(func):
            self.routes.append((method, path, func.__name__))
            return func

        return register


def load_plugin_api():
    root = Path(__file__).resolve().parents[1]
    sys.modules["fastapi"] = types.SimpleNamespace(APIRouter=FakeRouter)
    spec = importlib.util.spec_from_file_location(
        "agenttalk_dashboard_api_test",
        root / "dashboard" / "plugin_api.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DashboardApiTests(unittest.TestCase):
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

    def test_routes_cover_status_and_toggles(self) -> None:
        api = load_plugin_api()
        route_keys = {(method, path) for method, path, _name in api.router.routes}
        self.assertIn(("GET", "/status"), route_keys)
        self.assertIn(("POST", "/agent"), route_keys)
        self.assertIn(("POST", "/wake"), route_keys)
        self.assertIn(("POST", "/wake-access"), route_keys)

    def test_setup_and_wake_default_off(self) -> None:
        api = load_plugin_api()
        result = asyncio.run(api.setup({}))

        self.assertTrue(result["configured"])
        self.assertFalse(result["agentEnabled"])
        self.assertFalse(result["wakeEnabled"])
        self.assertEqual(result["wakeAccess"]["mode"], "allow_list")

    def test_wake_access_route_updates_lists(self) -> None:
        api = load_plugin_api()
        result = asyncio.run(
            api.set_wake_access(
                {
                    "allowedWakeSenderAgentIds": "agent-a,agent-b",
                    "blockedWakeSenderAgentIds": "agent-c",
                }
            )
        )

        self.assertEqual(result["wakeAccess"]["mode"], "allow_list")
        self.assertEqual(result["wakeAccess"]["allowedWakeSenderAgentIds"], ["agent-a", "agent-b"])
        self.assertEqual(result["wakeAccess"]["blockedWakeSenderAgentIds"], ["agent-c"])

    def test_wake_access_route_requires_open_confirmation(self) -> None:
        api = load_plugin_api()
        with self.assertRaises(ValueError):
            asyncio.run(api.set_wake_access({"wakeAccessMode": "open"}))

        result = asyncio.run(
            api.set_wake_access({"wakeAccessMode": "open", "openWakeRiskAccepted": True})
        )
        self.assertEqual(result["wakeAccess"]["mode"], "open")


if __name__ == "__main__":
    unittest.main()

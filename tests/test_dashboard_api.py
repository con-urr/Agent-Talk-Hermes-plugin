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
        self.old_state_home = os.environ.get("AGENTTALK_AGENT_STATE_HOME")
        self.old_handle = os.environ.get("AGENTTALK_HERMES_AGENT_HANDLE")
        os.environ["AGENTTALK_SUPERVISOR_HOME"] = self.tmp.name
        os.environ["AGENTTALK_AGENT_STATE_HOME"] = os.path.join(self.tmp.name, "agents")
        os.environ.pop("AGENTTALK_HERMES_AGENT_HANDLE", None)

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("AGENTTALK_SUPERVISOR_HOME", None)
        else:
            os.environ["AGENTTALK_SUPERVISOR_HOME"] = self.old_home
        if self.old_state_home is None:
            os.environ.pop("AGENTTALK_AGENT_STATE_HOME", None)
        else:
            os.environ["AGENTTALK_AGENT_STATE_HOME"] = self.old_state_home
        if self.old_handle is None:
            os.environ.pop("AGENTTALK_HERMES_AGENT_HANDLE", None)
        else:
            os.environ["AGENTTALK_HERMES_AGENT_HANDLE"] = self.old_handle
        self.tmp.cleanup()

    def configure_open_wake_passphrase(self, api, passphrase: str) -> None:
        config = api.control.load_config()
        salt = "00112233445566778899aabbccddeeff"
        config["openWakeApproval"] = {
            "mode": "passphrase",
            "salt": salt,
            "hash": api.control._hash_open_wake_approval_passphrase(
                passphrase=passphrase,
                salt=salt,
                iterations=api.control.OPEN_WAKE_APPROVAL_ITERATIONS,
                key_length=api.control.OPEN_WAKE_APPROVAL_KEY_LENGTH,
                digest=api.control.OPEN_WAKE_APPROVAL_DIGEST,
            ),
            "iterations": api.control.OPEN_WAKE_APPROVAL_ITERATIONS,
            "keyLength": api.control.OPEN_WAKE_APPROVAL_KEY_LENGTH,
            "digest": api.control.OPEN_WAKE_APPROVAL_DIGEST,
        }
        api.control.save_config(config)

    def test_routes_cover_status_and_toggles(self) -> None:
        api = load_plugin_api()
        route_keys = {(method, path) for method, path, _name in api.router.routes}
        self.assertIn(("GET", "/status"), route_keys)
        self.assertIn(("POST", "/agent"), route_keys)
        self.assertIn(("POST", "/wake"), route_keys)
        self.assertIn(("POST", "/wake-access"), route_keys)
        self.assertIn(("GET", "/wake-requests"), route_keys)
        self.assertIn(("POST", "/wake-requests/{request_id}/approve"), route_keys)
        self.assertIn(("POST", "/wake-requests/{request_id}/deny"), route_keys)
        self.assertIn(("POST", "/cli/install"), route_keys)

    def test_setup_and_wake_default_off(self) -> None:
        api = load_plugin_api()
        result = asyncio.run(api.setup({"handle": "hermes-api-test"}))

        self.assertTrue(result["configured"])
        self.assertFalse(result["agentEnabled"])
        self.assertFalse(result["wakeEnabled"])
        self.assertEqual(result["wakeAccess"]["mode"], "allow_list")
        self.assertEqual(result["credentialScope"], "plugin_runtime")
        self.assertEqual(result["registrationState"], "not_registered")
        self.assertEqual(result["agentTalkHandle"], "hermes-api-test")
        self.assertEqual(result["openWakeApproval"]["mode"], "passphrase")
        self.assertFalse(result["openWakeApproval"]["configured"])

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

        with self.assertRaises(ValueError):
            asyncio.run(api.set_wake_access({"wakeAccessMode": "open", "openWakeRiskAccepted": True}))

        self.configure_open_wake_passphrase(api, "correct horse battery staple")
        result = asyncio.run(
            api.set_wake_access(
                {
                    "wakeAccessMode": "open",
                    "openWakeRiskAccepted": True,
                    "openWakeApprovalPassphrase": "correct horse battery staple",
                }
            )
        )
        self.assertEqual(result["wakeAccess"]["mode"], "open")


if __name__ == "__main__":
    unittest.main()

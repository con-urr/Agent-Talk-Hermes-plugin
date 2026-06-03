from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

from agenttalk_hermes_plugin import control


class FakeContext:
    def __init__(self) -> None:
        self.commands = {}
        self.skills = {}

    def register_cli_command(self, **kwargs):
        self.commands[kwargs["name"]] = kwargs

    def register_skill(self, name, path, description=""):
        self.skills[name] = {
            "path": Path(path),
            "description": description,
        }


class ControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("AGENTTALK_SUPERVISOR_HOME")
        self.old_state_home = os.environ.get("AGENTTALK_AGENT_STATE_HOME")
        self.old_handle = os.environ.get("AGENTTALK_HERMES_AGENT_HANDLE")
        self.old_busy = os.environ.get("AGENTTALK_HERMES_BUSY_COMMAND")
        self.old_busy_timeout = os.environ.get("AGENTTALK_HERMES_BUSY_COMMAND_TIMEOUT_MS")
        self.old_cli = os.environ.get("AGENTTALK_CLI")
        self.old_cli_home = os.environ.get("AGENTTALK_CLI_HOME")
        self.old_cli_spec = os.environ.get("AGENTTALK_CLI_NPM_SPEC")
        self.old_npm_command = os.environ.get("AGENTTALK_NPM_COMMAND")
        os.environ["AGENTTALK_SUPERVISOR_HOME"] = self.tmp.name
        os.environ["AGENTTALK_AGENT_STATE_HOME"] = os.path.join(self.tmp.name, "agents")
        os.environ["AGENTTALK_CLI_HOME"] = os.path.join(self.tmp.name, "cli")
        os.environ.pop("AGENTTALK_HERMES_AGENT_HANDLE", None)
        os.environ.pop("AGENTTALK_HERMES_BUSY_COMMAND", None)
        os.environ.pop("AGENTTALK_HERMES_BUSY_COMMAND_TIMEOUT_MS", None)
        os.environ.pop("AGENTTALK_CLI", None)
        os.environ.pop("AGENTTALK_CLI_NPM_SPEC", None)
        os.environ.pop("AGENTTALK_NPM_COMMAND", None)

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
        if self.old_busy is None:
            os.environ.pop("AGENTTALK_HERMES_BUSY_COMMAND", None)
        else:
            os.environ["AGENTTALK_HERMES_BUSY_COMMAND"] = self.old_busy
        if self.old_busy_timeout is None:
            os.environ.pop("AGENTTALK_HERMES_BUSY_COMMAND_TIMEOUT_MS", None)
        else:
            os.environ["AGENTTALK_HERMES_BUSY_COMMAND_TIMEOUT_MS"] = self.old_busy_timeout
        if self.old_cli is None:
            os.environ.pop("AGENTTALK_CLI", None)
        else:
            os.environ["AGENTTALK_CLI"] = self.old_cli
        if self.old_cli_home is None:
            os.environ.pop("AGENTTALK_CLI_HOME", None)
        else:
            os.environ["AGENTTALK_CLI_HOME"] = self.old_cli_home
        if self.old_cli_spec is None:
            os.environ.pop("AGENTTALK_CLI_NPM_SPEC", None)
        else:
            os.environ["AGENTTALK_CLI_NPM_SPEC"] = self.old_cli_spec
        if self.old_npm_command is None:
            os.environ.pop("AGENTTALK_NPM_COMMAND", None)
        else:
            os.environ["AGENTTALK_NPM_COMMAND"] = self.old_npm_command
        self.tmp.cleanup()

    def configure_open_wake_passphrase(self, passphrase: str) -> None:
        config = control.load_config()
        salt = "00112233445566778899aabbccddeeff"
        config["openWakeApproval"] = {
            "mode": "passphrase",
            "salt": salt,
            "hash": control._hash_open_wake_approval_passphrase(
                passphrase=passphrase,
                salt=salt,
                iterations=control.OPEN_WAKE_APPROVAL_ITERATIONS,
                key_length=control.OPEN_WAKE_APPROVAL_KEY_LENGTH,
                digest=control.OPEN_WAKE_APPROVAL_DIGEST,
            ),
            "iterations": control.OPEN_WAKE_APPROVAL_ITERATIONS,
            "keyLength": control.OPEN_WAKE_APPROVAL_KEY_LENGTH,
            "digest": control.OPEN_WAKE_APPROVAL_DIGEST,
        }
        control.save_config(config)

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
        self.assertIn("agenttalk", ctx.skills)
        self.assertTrue(ctx.skills["agenttalk"]["path"].is_file())
        self.assertIn("AgentTalk", ctx.skills["agenttalk"]["path"].read_text(encoding="utf-8"))

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
        self.assertEqual(agent["controlProfile"], "plugin_managed")
        self.assertFalse(agent["enabled"])
        self.assertFalse(agent["wake"]["enabled"])
        self.assertEqual(agent["wake"]["accessMode"], "allow_list")
        self.assertEqual(agent["wake"]["allowedWakeSenderAgentIds"], [])
        self.assertEqual(agent["wake"]["blockedWakeSenderAgentIds"], [])
        self.assertEqual(agent["connector"]["sendReplyText"], True)
        self.assertEqual(agent["connector"]["hermesSkills"], ["agenttalk:agenttalk"])
        self.assertEqual(agent["connector"]["hermesToolsets"], ["terminal"])
        self.assertEqual(agent["connector"]["reuseHermesSession"], False)
        self.assertEqual(agent["connector"]["liveChat"], True)
        self.assertEqual(agent["connector"]["liveChatIdleTimeoutMs"], 600000)
        self.assertEqual(agent["connector"]["liveChatMaxSessionMs"], 3600000)
        self.assertEqual(agent["connector"]["startupTimeoutMs"], 60000)
        self.assertEqual(agent["connector"]["wakePromptTemplate"], control.STANDARD_WAKE_PROMPT_TEMPLATE)
        self.assertFalse(config["defaultWakePolicy"]["wakeOnDirectMessage"])
        self.assertEqual(result["credentialScope"], "plugin_runtime")
        self.assertEqual(result["registrationState"], "not_registered")
        self.assertFalse(result["busyCheck"]["configured"])
        self.assertIn("wakePrompt", result)
        self.assertEqual(result["wakePrompt"]["template"], control.STANDARD_WAKE_PROMPT_TEMPLATE)

    def test_setup_accepts_unique_agenttalk_handle(self) -> None:
        result = control.ensure_agent_config(handle="Hermes-Gui-Test")

        self.assertEqual(result["agentTalkHandle"], "hermes-gui-test")
        config = control.load_config()
        self.assertEqual(config["agents"][0]["handle"], "hermes-gui-test")

        os.environ["AGENTTALK_HERMES_AGENT_HANDLE"] = "env-hermes-handle"
        forced = control.ensure_agent_config(force=True)
        self.assertEqual(forced["agentTalkHandle"], "env-hermes-handle")

    def test_runtime_busy_check_is_configurable(self) -> None:
        result = control.ensure_agent_config(
            busy_command="python busy_check.py",
            busy_command_timeout_ms=2500,
        )

        self.assertTrue(result["busyCheck"]["configured"])
        self.assertEqual(result["busyCheck"]["timeoutMs"], 2500)

        config = control.load_config()
        agent = config["agents"][0]
        self.assertEqual(agent["connector"]["busyCommand"], "python busy_check.py")
        self.assertEqual(agent["connector"]["busyCommandTimeoutMs"], 2500)

        cleared = control.ensure_agent_config(busy_command="")
        self.assertFalse(cleared["busyCheck"]["configured"])
        config = control.load_config()
        self.assertNotIn("busyCommand", config["agents"][0]["connector"])

    def test_runtime_busy_check_can_default_from_environment(self) -> None:
        os.environ["AGENTTALK_HERMES_BUSY_COMMAND"] = "python host_busy.py"
        os.environ["AGENTTALK_HERMES_BUSY_COMMAND_TIMEOUT_MS"] = "3000"

        result = control.ensure_agent_config()

        self.assertTrue(result["busyCheck"]["configured"])
        self.assertEqual(result["busyCheck"]["timeoutMs"], 3000)

    def test_agenttalk_command_uses_managed_cli(self) -> None:
        managed = control.managed_agenttalk_bin()
        managed.parent.mkdir(parents=True, exist_ok=True)
        managed.write_text("# managed agenttalk\n", encoding="utf-8")

        with patch("agenttalk_hermes_plugin.control.shutil.which", return_value=None):
            self.assertEqual(control.agenttalk_command(), str(managed))

    def test_ensure_agenttalk_cli_installs_private_npm_copy(self) -> None:
        os.environ["AGENTTALK_CLI_NPM_SPEC"] = "pistils-chat-cli@0.1.2"

        def fake_run(args, **kwargs):
            managed = control.managed_agenttalk_bin()
            managed.parent.mkdir(parents=True, exist_ok=True)
            managed.write_text("# managed agenttalk\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="installed", stderr="")

        def fake_which(name):
            if name in {"npm", "npm.cmd"}:
                return "npm"
            return None

        with patch("agenttalk_hermes_plugin.control.shutil.which", side_effect=fake_which), patch(
            "agenttalk_hermes_plugin.control.subprocess.run",
            side_effect=fake_run,
        ) as run:
            result = control.ensure_agenttalk_cli()

        self.assertTrue(result["ok"])
        self.assertTrue(result["installed"])
        self.assertTrue(result["managed"])
        self.assertEqual(result["npmSpec"], "pistils-chat-cli@0.1.2")
        self.assertIn("--prefix", run.call_args.args[0])

    def test_ensure_agenttalk_cli_uses_default_github_spec(self) -> None:
        def fake_run(args, **kwargs):
            managed = control.managed_agenttalk_bin()
            managed.parent.mkdir(parents=True, exist_ok=True)
            managed.write_text("# managed agenttalk\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="installed", stderr="")

        def fake_which(name):
            if name in {"npm", "npm.cmd"}:
                return "npm"
            return None

        with patch("agenttalk_hermes_plugin.control.shutil.which", side_effect=fake_which), patch(
            "agenttalk_hermes_plugin.control.subprocess.run",
            side_effect=fake_run,
        ) as run:
            result = control.ensure_agenttalk_cli()

        self.assertTrue(result["ok"])
        self.assertEqual(result["npmSpec"], "github:con-urr/pistils_chat_cli#main")
        self.assertEqual(run.call_args.args[0][-1], "github:con-urr/pistils_chat_cli#main")

    def test_status_projects_agenttalk_state(self) -> None:
        control.ensure_agent_config()
        state_dir = control.default_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "state.json").write_text(
            json.dumps(
                {
                    "agentId": "agt_test_123",
                    "handle": "research-agent",
                    "registrationState": "registered",
                    "lastProfileSyncAt": "2026-05-27T00:00:00.000Z",
                }
            ),
            encoding="utf-8",
        )

        result = control.status()

        self.assertEqual(result["agentTalkAgentId"], "agt_test_123")
        self.assertEqual(result["agentTalkHandle"], "research-agent")
        self.assertEqual(result["registrationState"], "registered")
        self.assertEqual(result["controlProfile"], "plugin_managed")
        self.assertEqual(result["credentialScope"], "plugin_runtime")
        self.assertEqual(result["openWakeApproval"]["mode"], "passphrase")
        self.assertFalse(result["openWakeApproval"]["configured"])

    def test_wake_access_lists_are_configurable(self) -> None:
        control.ensure_agent_config()

        result = control.set_wake_access(
            allowed_wake_sender_agent_ids="agent-a, agent-b\nagent-a",
            blocked_wake_sender_agent_ids=["agent-c"],
        )

        self.assertEqual(result["wakeAccess"]["mode"], "allow_list")
        self.assertEqual(result["wakeAccess"]["allowedWakeSenderAgentIds"], ["agent-a", "agent-b"])
        self.assertEqual(result["wakeAccess"]["blockedWakeSenderAgentIds"], ["agent-c"])

        cleared = control.set_wake_access(allowed_wake_sender_agent_ids="")
        self.assertEqual(cleared["wakeAccess"]["mode"], "allow_list")
        self.assertEqual(cleared["wakeAccess"]["allowedWakeSenderAgentIds"], [])
        self.assertEqual(cleared["wakeAccess"]["blockedWakeSenderAgentIds"], ["agent-c"])

    def test_wake_behavior_updates_prompt_and_toolsets(self) -> None:
        control.ensure_agent_config()

        result = control.set_wake_behavior(
            wake_prompt_template="Custom {{conversationId}}",
            hermes_toolsets="terminal,agenttalk,memory",
            max_concurrent_sessions=5,
        )

        self.assertEqual(result["wakePrompt"]["template"], "Custom {{conversationId}}\n")
        self.assertEqual(result["hermesToolsets"], ["terminal", "agenttalk", "memory"])
        self.assertEqual(result["maxConcurrentSessions"], 5)
        config = control.load_config()
        self.assertEqual(config["agents"][0]["connector"]["wakePromptTemplate"], "Custom {{conversationId}}\n")
        self.assertEqual(config["agents"][0]["connector"]["hermesToolsets"], ["terminal", "agenttalk", "memory"])
        self.assertEqual(config["agents"][0]["maxConcurrentWakeJobs"], 5)

    def test_chat_sessions_split_wake_runs_for_same_conversation(self) -> None:
        control.ensure_agent_config()
        run_root = control.supervisor_home() / "runs"
        first_run = run_root / "local-direct_4097_4100_10" / "local-direct_4097_4100_10_attempt-1"
        second_run = run_root / "local-direct_4097_4200_20" / "local-direct_4097_4200_20_attempt-1"
        first_run.mkdir(parents=True)
        second_run.mkdir(parents=True)

        def write_run(path: Path, wake_id: str, attempt_id: str, sequence: str, last: str) -> None:
            (path / "input.json").write_text(
                json.dumps(
                    {
                        "attemptId": attempt_id,
                        "wake": {
                            "wakeId": wake_id,
                            "conversationId": "4097",
                            "senderAgentId": "agt_support",
                            "minSequence": sequence,
                            "maxSequence": sequence,
                            "reason": "direct_message",
                            "nextAttemptAt": f"2026-06-02T00:00:{sequence}.000Z",
                        },
                        "contextMessages": [
                            {
                                "conversationId": "4097",
                                "sequence": sequence,
                                "authorLabel": "support",
                                "text": f"wake {sequence}",
                                "sent": f"2026-06-02T00:00:{sequence}.000Z",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (path / "result.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "handled": True,
                        "replySent": True,
                        "metadata": {"connectorMetadata": {"lastHandledSequence": last}},
                    }
                ),
                encoding="utf-8",
            )

        write_run(first_run, "local-direct:4097:4100:10", "local-direct:4097:4100:10:attempt-1", "10", "11")
        write_run(second_run, "local-direct:4097:4200:20", "local-direct:4097:4200:20:attempt-1", "20", "21")

        def fake_run(args, **kwargs):
            if args[:2] == ["conversation", "list"]:
                return {
                    "ok": True,
                    "json": {
                        "ok": True,
                        "conversations": [
                            {
                                "id": "4097",
                                "kind": "direct",
                                "title": "support <-> research",
                                "createdAt": "2026-06-02T00:00:00.000Z",
                                "lastActivity": "2026-06-02T00:00:21.000Z",
                                "memberCount": 2,
                            }
                        ],
                    },
                }
            if args[:2] == ["conversation", "messages"]:
                return {
                    "ok": True,
                    "json": {
                        "ok": True,
                        "messages": [
                            {"id": "10", "conversationId": "4097", "sequence": "10", "authorLabel": "support", "text": "wake 10"},
                            {"id": "11", "conversationId": "4097", "sequence": "11", "authorLabel": "research", "text": "reply 11"},
                            {"id": "20", "conversationId": "4097", "sequence": "20", "authorLabel": "support", "text": "wake 20"},
                            {"id": "21", "conversationId": "4097", "sequence": "21", "authorLabel": "research", "text": "reply 21"},
                        ],
                    },
                }
            return {"ok": False, "error": "unexpected command"}

        with patch("agenttalk_hermes_plugin.control._run_agenttalk", side_effect=fake_run):
            sessions = control.chat_sessions(limit=10)
            self.assertTrue(sessions["ok"])
            self.assertEqual(len(sessions["sessions"]), 2)
            self.assertEqual({row["conversationId"] for row in sessions["sessions"]}, {"4097"})
            self.assertEqual(len({row["sessionId"] for row in sessions["sessions"]}), 2)
            second = next(row for row in sessions["sessions"] if row["startSequence"] == "20")
            messages = control.chat_messages("4097", session_id=second["sessionId"])

        self.assertTrue(messages["ok"])
        self.assertEqual([row["sequence"] for row in messages["messages"]], ["20", "21"])

    def test_wake_prompt_preview_renders_placeholders(self) -> None:
        preview = control.render_wake_prompt_preview("Wake {{conversationId}} from {{senderAgentId}}")

        self.assertEqual(preview, "Wake 4097 from agt_example_peer\n")

    def test_chat_helpers_use_configured_agenttalk_environment(self) -> None:
        control.ensure_agent_config()
        custom_state = Path(self.tmp.name) / "custom-state"
        config = control.load_config()
        config["host"] = "https://agenttalk.example.test"
        config["databaseName"] = "agenttalk-test-db"
        config["agents"][0]["stateDir"] = str(custom_state)
        control.save_config(config)
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs.get("env")
            return subprocess.CompletedProcess(args, 0, stdout='{"ok":true,"conversations":[]}', stderr="")

        with patch("agenttalk_hermes_plugin.control.agenttalk_command", return_value="agenttalk"), patch(
            "agenttalk_hermes_plugin.control.subprocess.run",
            side_effect=fake_run,
        ):
            result = control.chat_sessions(limit=1)

        self.assertTrue(result["ok"])
        self.assertEqual(captured["args"], ["agenttalk", "conversation", "list", "--json"])
        self.assertEqual(captured["env"]["AGENTTALK_STATE_DIR"], str(custom_state.resolve()))
        self.assertEqual(captured["env"]["SPACETIMEDB_HOST"], "https://agenttalk.example.test")
        self.assertEqual(captured["env"]["SPACETIMEDB_DB_NAME"], "agenttalk-test-db")
        mcp = control.agenttalk_mcp_server_config()
        self.assertEqual(mcp["env"]["AGENTTALK_STATE_DIR"], str(custom_state.resolve()))
        self.assertEqual(mcp["env"]["SPACETIMEDB_HOST"], "https://agenttalk.example.test")
        self.assertEqual(mcp["env"]["SPACETIMEDB_DB_NAME"], "agenttalk-test-db")

    def test_open_wake_requires_confirmation(self) -> None:
        control.ensure_agent_config()

        with self.assertRaises(ValueError):
            control.set_wake_access(wake_access_mode="open")

        with self.assertRaises(ValueError):
            control.set_wake_access(
                wake_access_mode="open",
                open_wake_risk_accepted=True,
            )

        self.configure_open_wake_passphrase("correct horse battery staple")
        with self.assertRaises(ValueError):
            control.set_wake_access(
                wake_access_mode="open",
                open_wake_risk_accepted=True,
                open_wake_approval_passphrase="wrong passphrase",
            )

        opened = control.set_wake_access(
            wake_access_mode="open",
            open_wake_risk_accepted=True,
            open_wake_approval_passphrase="correct horse battery staple",
            allowed_wake_sender_agent_ids="",
        )
        self.assertEqual(opened["wakeAccess"]["mode"], "open")

    def test_pending_wake_change_requests_can_be_approved_or_denied(self) -> None:
        control.ensure_agent_config()
        store = {
            "version": 1,
            "requests": [
                {
                    "id": "wcr_test_open",
                    "createdAt": "2026-05-27T00:00:00.000Z",
                    "updatedAt": "2026-05-27T00:00:00.000Z",
                    "status": "pending",
                    "agentName": control.AGENT_NAME,
                    "requestedBy": "mcp-runtime",
                    "desired": {"wakeAccessMode": "open"},
                    "warning": control.OPEN_WAKE_WARNING,
                },
                {
                    "id": "wcr_test_allow",
                    "createdAt": "2026-05-27T00:00:01.000Z",
                    "updatedAt": "2026-05-27T00:00:01.000Z",
                    "status": "pending",
                    "agentName": control.AGENT_NAME,
                    "requestedBy": "mcp-runtime",
                    "desired": {"allowedWakeSenderAgentIds": ["agent-a"]},
                },
            ],
        }
        control.save_wake_change_requests(store)

        self.assertEqual(control.status()["pendingWakeChangeRequestCount"], 2)
        with self.assertRaises(ValueError):
            control.approve_wake_change_request("wcr_test_open")

        with self.assertRaises(ValueError):
            control.approve_wake_change_request(
                "wcr_test_open",
                open_wake_risk_accepted=True,
            )

        self.configure_open_wake_passphrase("correct horse battery staple")
        approved = control.approve_wake_change_request(
            "wcr_test_open",
            open_wake_risk_accepted=True,
            open_wake_approval_passphrase="correct horse battery staple",
        )
        self.assertEqual(approved["wakeAccess"]["mode"], "open")
        self.assertEqual(approved["request"]["status"], "approved")

        denied = control.deny_wake_change_request("wcr_test_allow")
        self.assertEqual(denied["request"]["status"], "denied")
        self.assertEqual(control.status()["pendingWakeChangeRequestCount"], 0)

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
        self.assertEqual(wake_on["wakeAccess"]["mode"], "allow_list")

        wake_off = control.set_wake_enabled(False)
        self.assertTrue(wake_off["agentEnabled"])
        self.assertFalse(wake_off["wakeEnabled"])

        off = control.set_agent_enabled(False)
        self.assertFalse(off["agentEnabled"])
        self.assertFalse(off["wakeEnabled"])
        self.assertFalse(off["wakeActive"])

    def test_wake_toggle_restarts_running_supervisor(self) -> None:
        control.ensure_agent_config()
        control.set_agent_enabled(True)

        with patch("agenttalk_hermes_plugin.control.supervisor_running", return_value=True), patch(
            "agenttalk_hermes_plugin.control.restart_supervisor",
            return_value={"ok": True, "restarted": True},
        ) as restart:
            wake_on = control.set_wake_enabled(True)

        restart.assert_called_once()
        self.assertTrue(wake_on["wakeEnabled"])
        self.assertEqual(wake_on["supervisorRestart"], {"ok": True, "restarted": True})

    def test_live_status_autostarts_missing_wake_supervisor(self) -> None:
        control.ensure_agent_config(enabled=True, wake_enabled=True)

        with patch("agenttalk_hermes_plugin.control.supervisor_running", return_value=False), patch(
            "agenttalk_hermes_plugin.control.supervisor_manually_stopped",
            return_value=False,
        ), patch("agenttalk_hermes_plugin.control.live_supervisor_agent_status", return_value=None), patch(
            "agenttalk_hermes_plugin.control.start_supervisor",
            return_value={"ok": True, "started": True, "pid": 123},
        ) as start:
            result = control.status(live=True)

        start.assert_called_once()
        self.assertEqual(result["supervisorAutoStart"]["reason"], "wake-autostart")
        self.assertTrue(result["supervisorAutoStart"]["started"])

    def test_live_status_respects_manual_supervisor_stop(self) -> None:
        control.ensure_agent_config(enabled=True, wake_enabled=True)

        with patch("agenttalk_hermes_plugin.control.supervisor_running", return_value=False), patch(
            "agenttalk_hermes_plugin.control.supervisor_manually_stopped",
            return_value=True,
        ), patch("agenttalk_hermes_plugin.control.live_supervisor_agent_status", return_value=None), patch(
            "agenttalk_hermes_plugin.control.start_supervisor"
        ) as start:
            result = control.status(live=True)

        start.assert_not_called()
        self.assertEqual(result["supervisorAutoStart"]["reason"], "manual-stop")
        self.assertTrue(result["supervisorManualStop"])

    def test_pid_running_treats_runtime_probe_errors_as_not_running(self) -> None:
        with (
            patch.object(control.sys, "platform", "linux"),
            patch.object(control.os, "kill", side_effect=SystemError("invalid handle")),
        ):
            self.assertFalse(control._pid_running(12345))


if __name__ == "__main__":
    unittest.main()

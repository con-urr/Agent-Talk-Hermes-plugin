from __future__ import annotations

import argparse
import json
from typing import Any

from . import control


def _print(payload: dict[str, Any], *, as_json: bool = False) -> int:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"AgentTalk Hermes: {'ok' if payload.get('ok') else 'error'}")
        for key in (
            "configured",
            "agentTalkAgentId",
            "registrationState",
            "credentialScope",
            "agentEnabled",
            "wakeEnabled",
            "wakeActive",
            "supervisorRunning",
            "supervisorPid",
            "agenttalkCliInstalled",
        ):
            if key in payload:
                print(f"  {key}: {payload[key]}")
        if payload.get("error"):
            print(f"  error: {payload['error']}")
    return 0 if payload.get("ok") else 1


def register_cli(subparser: argparse.ArgumentParser) -> None:
    subcommands = subparser.add_subparsers(dest="agenttalk_command")

    setup = subcommands.add_parser("setup", help="Configure Hermes for AgentTalk; defaults to off")
    setup.add_argument("--repo", default=None, help="Path to the Hermes repo/runtime")
    setup.add_argument("--handle", default=None, help="Unique AgentTalk handle for this Hermes agent")
    setup.add_argument("--enable", action="store_true", help="Enable the Hermes connector after setup")
    setup.add_argument("--wake", action="store_true", help="Enable wake after setup")
    setup.add_argument("--force", action="store_true", help="Replace existing Hermes AgentTalk config")
    setup.add_argument("--no-install-cli", action="store_true", help="Skip plugin-managed AgentTalk CLI install")
    setup.add_argument("--json", action="store_true", help="Print JSON")

    for name, help_text in (
        ("status", "Show AgentTalk supervisor status"),
        ("on", "Turn on the Hermes AgentTalk connector and start the local supervisor"),
        ("off", "Turn off the Hermes AgentTalk connector and stop the local supervisor"),
        ("test", "Run local readiness checks"),
        ("logs", "Show AgentTalk supervisor logs"),
    ):
        parser = subcommands.add_parser(name, help=help_text)
        parser.add_argument("--json", action="store_true", help="Print JSON")
        if name == "on":
            parser.add_argument("--repo", default=None, help="Path to the Hermes repo/runtime")
            parser.add_argument("--handle", default=None, help="Unique AgentTalk handle for this Hermes agent")

    wake = subcommands.add_parser("wake", help="Control only wake dispatch")
    wake_subcommands = wake.add_subparsers(dest="wake_command")
    for name in ("on", "off", "status"):
        parser = wake_subcommands.add_parser(name, help=f"Turn wake {name}")
        parser.add_argument("--json", action="store_true", help="Print JSON")

    subparser.set_defaults(func=agenttalk_command)


def agenttalk_command(args: argparse.Namespace) -> int:
    command = getattr(args, "agenttalk_command", None) or "status"
    as_json = bool(getattr(args, "json", False))

    if command == "setup":
        payload = control.ensure_agent_config(
            repo=getattr(args, "repo", None),
            handle=getattr(args, "handle", None),
            enabled=bool(getattr(args, "enable", False)),
            wake_enabled=bool(getattr(args, "wake", False)),
            force=bool(getattr(args, "force", False)),
        )
        if not bool(getattr(args, "no_install_cli", False)):
            payload["cliInstall"] = control.ensure_agenttalk_cli()
            if not payload["cliInstall"].get("ok"):
                payload["ok"] = False
                payload["error"] = payload["cliInstall"].get("error", "AgentTalk CLI install failed")
        return _print(payload, as_json=as_json)

    if command == "status":
        return _print(control.status(), as_json=as_json)

    if command == "on":
        control.ensure_agent_config(
            repo=getattr(args, "repo", None),
            handle=getattr(args, "handle", None),
            enabled=True,
            wake_enabled=False,
        )
        cli_install = control.ensure_agenttalk_cli()
        payload = control.set_agent_enabled(True)
        payload["supervisorStart"] = control.start_supervisor()
        payload.update(control.status())
        payload["cliInstall"] = cli_install
        if not cli_install.get("ok"):
            payload["ok"] = False
            payload["error"] = cli_install.get("error", "AgentTalk CLI install failed")
        return _print(payload, as_json=as_json)

    if command == "off":
        payload = control.set_agent_enabled(False)
        payload["supervisorStop"] = control.stop_supervisor()
        payload.update(control.status())
        return _print(payload, as_json=as_json)

    if command == "wake":
        wake_command = getattr(args, "wake_command", None) or "status"
        if wake_command == "on":
            return _print(control.set_wake_enabled(True), as_json=as_json)
        if wake_command == "off":
            return _print(control.set_wake_enabled(False), as_json=as_json)
        return _print(control.status(), as_json=as_json)

    if command == "test":
        return _print(control.doctor(), as_json=as_json)

    if command == "logs":
        payload = control._run_agenttalk(["supervisor", "logs", "--agent", control.AGENT_NAME, "--tail", "80", "--json"])
        return _print(payload, as_json=as_json)

    print("usage: hermes agenttalk {setup,status,on,off,wake,test,logs}")
    return 2

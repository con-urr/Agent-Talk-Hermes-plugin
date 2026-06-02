from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from agenttalk_hermes_plugin import control  # noqa: E402

router = APIRouter()


def _body_value(body: dict[str, Any] | None, key: str, default: Any = None) -> Any:
    if not isinstance(body, dict):
        return default
    return body.get(key, default)


def _sync_supervisor_after_config_change(payload: dict[str, Any]) -> dict[str, Any]:
    prior_ok = payload.get("ok")
    prior_error = payload.get("error")
    if not payload.get("agentEnabled"):
        return payload
    if control.supervisor_running():
        payload["supervisorRestart"] = control.restart_supervisor()
    else:
        payload["supervisorStart"] = control.start_supervisor()
    payload.update(control.status())
    if prior_ok is False:
        payload["ok"] = False
        payload["error"] = prior_error or "AgentTalk plugin action failed"
    return payload


@router.get("/status")
async def get_status(live: bool = False) -> dict[str, Any]:
    return control.status(live=live)


@router.post("/setup")
async def setup(body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = control.ensure_agent_config(
        repo=_body_value(body, "repo"),
        handle=_body_value(body, "handle"),
        enabled=bool(_body_value(body, "enabled", False)),
        wake_enabled=bool(_body_value(body, "wakeEnabled", False)),
        wake_access_mode=_body_value(body, "wakeAccessMode"),
        open_wake_risk_accepted=bool(_body_value(body, "openWakeRiskAccepted", False)),
        open_wake_approval_passphrase=_body_value(body, "openWakeApprovalPassphrase"),
        allowed_wake_sender_agent_ids=_body_value(body, "allowedWakeSenderAgentIds"),
        blocked_wake_sender_agent_ids=_body_value(body, "blockedWakeSenderAgentIds"),
        force=bool(_body_value(body, "force", False)),
    )
    if bool(_body_value(body, "installCli", True)):
        payload["cliInstall"] = control.ensure_agenttalk_cli()
        if not payload["cliInstall"].get("ok"):
            payload["ok"] = False
            payload["error"] = payload["cliInstall"].get("error", "AgentTalk CLI install failed")
    return _sync_supervisor_after_config_change(payload)


@router.post("/agent")
async def set_agent(body: dict[str, Any] | None = None) -> dict[str, Any]:
    enabled = bool(_body_value(body, "enabled", False))
    if enabled:
        control.ensure_agent_config(
            handle=_body_value(body, "handle"),
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
        return payload
    payload = control.set_agent_enabled(False)
    payload["supervisorStop"] = control.stop_supervisor()
    payload.update(control.status())
    return payload


@router.post("/wake")
async def set_wake(body: dict[str, Any] | None = None) -> dict[str, Any]:
    return _sync_supervisor_after_config_change(
        control.set_wake_enabled(bool(_body_value(body, "enabled", False)))
    )


@router.post("/wake-access")
async def set_wake_access(body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = control.set_wake_access(
        wake_access_mode=_body_value(body, "wakeAccessMode"),
        open_wake_risk_accepted=bool(_body_value(body, "openWakeRiskAccepted", False)),
        open_wake_approval_passphrase=_body_value(body, "openWakeApprovalPassphrase"),
        allowed_wake_sender_agent_ids=_body_value(body, "allowedWakeSenderAgentIds"),
        blocked_wake_sender_agent_ids=_body_value(body, "blockedWakeSenderAgentIds"),
    )
    if isinstance(body, dict) and ("wakePromptTemplate" in body or "hermesToolsets" in body):
        payload = control.set_wake_behavior(
            wake_prompt_template=_body_value(body, "wakePromptTemplate"),
            hermes_toolsets=_body_value(body, "hermesToolsets"),
        )
    return _sync_supervisor_after_config_change(payload)


@router.post("/wake-prompt/preview")
async def preview_wake_prompt(body: dict[str, Any] | None = None) -> dict[str, Any]:
    template = _body_value(body, "wakePromptTemplate")
    return {
        "ok": True,
        "preview": control.render_wake_prompt_preview(template),
        "warning": control.WAKE_PROMPT_WARNING,
    }


@router.post("/test-wake")
async def test_wake() -> dict[str, Any]:
    result = control._run_agenttalk(["supervisor", "test-wake", control.AGENT_NAME, "--json"])
    payload = control.status(live=True)
    payload["testWake"] = result
    if not result.get("ok"):
        payload["ok"] = False
        payload["error"] = result.get("error") or result.get("stderr") or "AgentTalk test wake failed"
    return payload


@router.get("/mcp")
async def get_mcp() -> dict[str, Any]:
    return {"ok": True, "agenttalkMcp": control.agenttalk_mcp_status()}


@router.post("/mcp")
async def set_mcp(body: dict[str, Any] | None = None) -> dict[str, Any]:
    enabled = bool(_body_value(body, "enabled", True))
    return _sync_supervisor_after_config_change(control.configure_agenttalk_mcp(enabled=enabled))


@router.get("/chats")
async def get_chats(limit: int = 25) -> dict[str, Any]:
    return control.chat_sessions(limit=limit)


@router.get("/chats/{conversation_id}")
async def get_chat(conversation_id: str, limit: int = 100) -> dict[str, Any]:
    return control.chat_messages(conversation_id, limit=limit)


@router.get("/wake-requests")
async def get_wake_requests() -> dict[str, Any]:
    return {"ok": True, "requests": control.list_wake_change_requests("pending")}


@router.post("/wake-requests/{request_id}/approve")
async def approve_wake_request(request_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return _sync_supervisor_after_config_change(control.approve_wake_change_request(
        request_id,
        open_wake_risk_accepted=bool(_body_value(body, "openWakeRiskAccepted", False)),
        open_wake_approval_passphrase=_body_value(body, "openWakeApprovalPassphrase"),
        note=_body_value(body, "note"),
    ))


@router.post("/wake-requests/{request_id}/deny")
async def deny_wake_request(request_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return control.deny_wake_change_request(request_id, note=_body_value(body, "note"))


@router.get("/doctor")
async def get_doctor() -> dict[str, Any]:
    return control.doctor()


@router.post("/cli/install")
async def install_cli(body: dict[str, Any] | None = None) -> dict[str, Any]:
    install = control.ensure_agenttalk_cli(force=bool(_body_value(body, "force", False)))
    payload = control.status()
    payload["cliInstall"] = install
    if not install.get("ok"):
        payload["ok"] = False
        payload["error"] = install.get("error", "AgentTalk CLI install failed")
    return payload


@router.get("/logs")
async def get_logs() -> dict[str, Any]:
    return control._run_agenttalk(
        ["supervisor", "logs", "--agent", control.AGENT_NAME, "--tail", "80", "--json"]
    )

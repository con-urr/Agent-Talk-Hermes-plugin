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
    if not payload.get("agentEnabled"):
        return payload
    if control.supervisor_running():
        payload["supervisorRestart"] = control.restart_supervisor()
    else:
        payload["supervisorStart"] = control.start_supervisor()
    payload.update(control.status())
    return payload


@router.get("/status")
async def get_status(live: bool = False) -> dict[str, Any]:
    return control.status(live=live)


@router.post("/setup")
async def setup(body: dict[str, Any] | None = None) -> dict[str, Any]:
    return _sync_supervisor_after_config_change(control.ensure_agent_config(
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
    ))


@router.post("/agent")
async def set_agent(body: dict[str, Any] | None = None) -> dict[str, Any]:
    enabled = bool(_body_value(body, "enabled", False))
    if enabled:
        control.ensure_agent_config(
            handle=_body_value(body, "handle"),
            enabled=True,
            wake_enabled=False,
        )
        payload = control.set_agent_enabled(True)
        payload["supervisorStart"] = control.start_supervisor()
        payload.update(control.status())
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
    return _sync_supervisor_after_config_change(control.set_wake_access(
        wake_access_mode=_body_value(body, "wakeAccessMode"),
        open_wake_risk_accepted=bool(_body_value(body, "openWakeRiskAccepted", False)),
        open_wake_approval_passphrase=_body_value(body, "openWakeApprovalPassphrase"),
        allowed_wake_sender_agent_ids=_body_value(body, "allowedWakeSenderAgentIds"),
        blocked_wake_sender_agent_ids=_body_value(body, "blockedWakeSenderAgentIds"),
    ))


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


@router.get("/logs")
async def get_logs() -> dict[str, Any]:
    return control._run_agenttalk(
        ["supervisor", "logs", "--agent", control.AGENT_NAME, "--tail", "80", "--json"]
    )

from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLUGIN_ID = "agenttalk-hermes"
AGENT_NAME = "research"
AGENT_HANDLE = "research-agent"
AGENT_KIND = "hermes"
CONTROL_PROFILE = "plugin_managed"
OPEN_WAKE_WARNING = (
    "Careful: you are about to expose this agent to open wake requests from any AgentTalk sender who can deliver "
    "a message. This is generally inadvisable unless you have hardened the runtime and limited the blast radius "
    "of malicious actors attempting to influence or control your agents."
)
OPEN_WAKE_APPROVAL_PASSPHRASE_REQUIRED = (
    "Open wake approval passphrase is required before enabling open wake mode."
)
OPEN_WAKE_APPROVAL_DIGEST = "sha256"
OPEN_WAKE_APPROVAL_KEY_LENGTH = 32
OPEN_WAKE_APPROVAL_ITERATIONS = 210_000
AGENTTALK_CLI_NPM_SPEC = "github:con-urr/pistils_chat_cli#main"
DEFAULT_CONNECTOR_TIMEOUT_MS = 300_000
DEFAULT_LIVE_CHAT_IDLE_TIMEOUT_MS = 600_000
DEFAULT_LIVE_CHAT_MAX_SESSION_MS = 3_600_000
DEFAULT_STARTUP_TIMEOUT_MS = 60_000


def _home() -> Path:
    return Path.home()


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def default_agent_handle() -> str:
    return (
        os.environ.get("AGENTTALK_HERMES_AGENT_HANDLE")
        or os.environ.get("AGENTTALK_AGENT_HANDLE")
        or AGENT_HANDLE
    )


def normalize_agent_handle(handle: Any | None = None) -> str:
    normalized = str(handle or default_agent_handle()).strip().lstrip("@").lower()
    if not re.match(r"^[a-z0-9][a-z0-9_-]{2,39}$", normalized):
        raise ValueError("AgentTalk handle must be 3-40 lowercase letters, numbers, dashes, or underscores")
    return normalized


def default_busy_command() -> str | None:
    return os.environ.get("AGENTTALK_HERMES_BUSY_COMMAND") or os.environ.get("AGENTTALK_BUSY_COMMAND")


def default_busy_command_timeout_ms() -> int:
    raw = os.environ.get("AGENTTALK_HERMES_BUSY_COMMAND_TIMEOUT_MS") or os.environ.get(
        "AGENTTALK_BUSY_COMMAND_TIMEOUT_MS"
    )
    return normalize_busy_command_timeout_ms(raw)


def normalize_busy_command(command: Any | None = None) -> str | None:
    if command is None:
        return None
    normalized = str(command).strip()
    if not normalized:
        return None
    if "\r" in normalized or "\n" in normalized or len(normalized) > 2000:
        raise ValueError("Busy check command must be a single-line command up to 2000 characters.")
    return normalized


def normalize_busy_command_timeout_ms(value: Any | None = None) -> int:
    if value in (None, ""):
        return 5000
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Busy check timeout must be an integer from 250 to 60000 ms.") from exc
    if parsed < 250 or parsed > 60000:
        raise ValueError("Busy check timeout must be an integer from 250 to 60000 ms.")
    return parsed


def supervisor_home() -> Path:
    configured = os.environ.get("AGENTTALK_SUPERVISOR_HOME")
    if configured:
        return _expand(configured)
    return _home() / ".agenttalk" / "supervisor"


def supervisor_config_path() -> Path:
    configured = os.environ.get("AGENTTALK_SUPERVISOR_CONFIG")
    if configured:
        return _expand(configured)
    return supervisor_home() / "config.json"


def pid_path() -> Path:
    return supervisor_home() / f"{PLUGIN_ID}.pid"


def default_state_dir() -> Path:
    configured_root = os.environ.get("AGENTTALK_AGENT_STATE_HOME")
    if configured_root:
        return _expand(configured_root) / AGENT_NAME
    return _home() / ".agenttalk" / "agents" / AGENT_NAME


def state_path(agent: dict[str, Any] | None = None) -> Path:
    state_dir = agent.get("stateDir") if agent else None
    return _expand(state_dir or default_state_dir()) / "state.json"


def wake_change_requests_path() -> Path:
    return supervisor_home() / "wake-change-requests.json"


def managed_cli_home() -> Path:
    configured = os.environ.get("AGENTTALK_CLI_HOME")
    if configured:
        return _expand(configured)
    return supervisor_home() / "cli"


def managed_agenttalk_bin() -> Path:
    bin_name = "agenttalk.cmd" if sys.platform == "win32" else "agenttalk"
    return managed_cli_home() / "node_modules" / ".bin" / bin_name


def npm_command() -> str | None:
    configured = os.environ.get("AGENTTALK_NPM_COMMAND")
    if configured:
        return configured
    return shutil.which("npm") or shutil.which("npm.cmd")


def load_agent_state(agent: dict[str, Any] | None = None) -> dict[str, Any]:
    path = state_path(agent)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            parsed = json.load(handle)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def load_wake_change_requests() -> dict[str, Any]:
    path = wake_change_requests_path()
    if not path.exists():
        return {"version": 1, "requests": []}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if parsed.get("version") != 1 or not isinstance(parsed.get("requests"), list):
            return {"version": 1, "requests": []}
        return parsed
    except Exception:
        return {"version": 1, "requests": []}


def save_wake_change_requests(store: dict[str, Any]) -> None:
    path = wake_change_requests_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def list_wake_change_requests(status: str = "pending") -> list[dict[str, Any]]:
    store = load_wake_change_requests()
    rows = [row for row in store.get("requests", []) if row.get("agentName") == AGENT_NAME]
    if status != "all":
        rows = [row for row in rows if row.get("status") == status]
    return sorted(rows, key=lambda row: str(row.get("createdAt") or ""))


def control_profile(agent: dict[str, Any] | None) -> str:
    raw = (agent or {}).get("controlProfile") or CONTROL_PROFILE
    normalized = str(raw).strip().lower().replace("-", "_")
    if normalized in ("", "plugin", "plugin_managed"):
        return "plugin_managed"
    if normalized in ("autonomous", "admin", "full"):
        return "autonomous"
    return "plugin_managed"


def normalize_wake_sender_agent_ids(value: Any, field: str = "wake sender agent IDs") -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return []
        if trimmed.startswith("["):
            parsed = json.loads(trimmed)
            if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
                raise ValueError(f"{field} must be a comma/newline-separated list or JSON array of strings")
            items = parsed
        else:
            items = re.split(r"[\s,]+", trimmed)
    elif isinstance(value, list):
        if any(not isinstance(item, str) for item in value):
            raise ValueError(f"{field} must contain only strings")
        items = value
    else:
        raise ValueError(f"{field} must be a list or string")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        agent_id = item.strip()
        if not agent_id:
            continue
        if len(agent_id) > 256 or re.search(r"[\s,]", agent_id):
            raise ValueError(f"{field} must contain AgentTalk agent IDs without whitespace or commas")
        if agent_id not in seen:
            normalized.append(agent_id)
            seen.add(agent_id)
    if len(normalized) > 100:
        raise ValueError(f"{field} must contain 100 or fewer agent IDs")
    return normalized


def normalize_wake_access_mode(value: Any = None) -> str:
    if value is None:
        return "allow_list"
    if not isinstance(value, str):
        raise ValueError("wake access mode must be allow-list or open")
    normalized = value.strip().lower().replace("-", "_")
    if normalized in ("", "allow_list", "allowlist"):
        return "allow_list"
    if normalized in ("open", "open_wake", "any_sender"):
        return "open"
    raise ValueError("wake access mode must be allow-list or open")


def normalize_open_wake_approval_config(config: dict[str, Any]) -> dict[str, Any]:
    approval = config.get("openWakeApproval")
    if not isinstance(approval, dict):
        approval = {"mode": "passphrase"}
    raw_mode = str(approval.get("mode") or "passphrase").strip().lower().replace("-", "_")
    if raw_mode in ("", "off", "none"):
        mode = "none"
    elif raw_mode in ("passphrase", "password"):
        mode = "passphrase"
    else:
        raise ValueError("open wake approval mode must be none or passphrase")
    return {
        **approval,
        "mode": mode,
        "digest": approval.get("digest") or OPEN_WAKE_APPROVAL_DIGEST,
        "keyLength": int(approval.get("keyLength") or OPEN_WAKE_APPROVAL_KEY_LENGTH),
        "iterations": int(approval.get("iterations") or OPEN_WAKE_APPROVAL_ITERATIONS),
    }


def _hash_open_wake_approval_passphrase(
    *,
    passphrase: str,
    salt: str,
    iterations: int,
    key_length: int,
    digest: str,
) -> str:
    return hashlib.pbkdf2_hmac(
        digest,
        passphrase.encode("utf-8"),
        bytes.fromhex(salt),
        iterations,
        dklen=key_length,
    ).hex()


def open_wake_approval_status(config: dict[str, Any]) -> dict[str, Any]:
    approval = normalize_open_wake_approval_config(config)
    configured = (
        approval["mode"] == "passphrase"
        and isinstance(approval.get("salt"), str)
        and isinstance(approval.get("hash"), str)
    )
    return {
        "mode": approval["mode"],
        "configured": configured,
        "iterations": approval["iterations"] if approval["mode"] == "passphrase" else None,
        "digest": approval["digest"] if approval["mode"] == "passphrase" else None,
    }


def require_open_wake_local_approval(config: dict[str, Any], passphrase: str | None = None) -> None:
    approval = normalize_open_wake_approval_config(config)
    if approval["mode"] != "passphrase":
        return
    if not isinstance(approval.get("salt"), str) or not isinstance(approval.get("hash"), str):
        raise ValueError(OPEN_WAKE_APPROVAL_PASSPHRASE_REQUIRED)
    normalized = passphrase.strip() if isinstance(passphrase, str) else ""
    if not normalized:
        raise ValueError(OPEN_WAKE_APPROVAL_PASSPHRASE_REQUIRED)
    candidate = _hash_open_wake_approval_passphrase(
        passphrase=normalized,
        salt=approval["salt"],
        iterations=approval["iterations"],
        key_length=approval["keyLength"],
        digest=approval["digest"],
    )
    if not hmac.compare_digest(candidate, str(approval["hash"])):
        raise ValueError("Open wake approval passphrase did not match.")


def require_open_wake_confirmation(
    accepted: bool | None,
    *,
    config: dict[str, Any] | None = None,
    open_wake_approval_passphrase: str | None = None,
) -> None:
    if not accepted:
        raise ValueError(f"{OPEN_WAKE_WARNING} Confirm open wake mode before saving.")
    if config is not None:
        require_open_wake_local_approval(config, open_wake_approval_passphrase)


def _default_config() -> dict[str, Any]:
    root = supervisor_home()
    return {
        "version": 1,
        "host": os.environ.get("SPACETIMEDB_HOST", "https://maincloud.spacetimedb.com"),
        "databaseName": os.environ.get("SPACETIMEDB_DB_NAME", "crimsonconfidentialgibbon"),
        "logDir": str(root / "logs"),
        "runDir": str(root / "runs"),
        "defaultWakePolicy": {
            "wakeOnDirectMessage": False,
            "wakeOnMention": False,
            "wakeOnGroupMessage": False,
            "acceptsNewConversations": False,
            "coalesceWindowMs": 15000,
            "minWakeIntervalMs": 5000,
            "maxWakesPerMinute": 30,
        },
        "openWakeApproval": {"mode": "passphrase"},
        "agents": [],
    }


def load_config() -> dict[str, Any]:
    path = supervisor_config_path()
    if not path.exists():
        return _default_config()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("version") != 1 or not isinstance(data.get("agents"), list):
        raise RuntimeError(f"Unsupported AgentTalk supervisor config: {path}")
    return data


def save_config(config: dict[str, Any]) -> None:
    path = supervisor_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    Path(config["logDir"]).mkdir(parents=True, exist_ok=True)
    Path(config["runDir"]).mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    tmp.replace(path)


def _candidate_repos(explicit_repo: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    for raw in (
        explicit_repo,
        os.environ.get("HERMES_REPO"),
        os.environ.get("HERMES_AGENT_REPO"),
        str(Path.cwd()),
        str(_home() / "Documents" / "GitHub" / "hermes-agent"),
        str(_home() / "GitHub" / "hermes-agent"),
        str(_home() / "github" / "hermes-agent"),
    ):
        if raw:
            candidates.append(_expand(raw))
    return candidates


def find_hermes_repo(explicit_repo: str | None = None) -> str | None:
    for candidate in _candidate_repos(explicit_repo):
        if (candidate / "hermes").exists():
            return str(candidate)
    return str(_expand(explicit_repo)) if explicit_repo else None


def _new_agent(
    repo: str | None,
    *,
    handle: str,
    enabled: bool,
    wake_enabled: bool,
    wake_access_mode: Any = None,
    allowed_wake_sender_agent_ids: Any = None,
    blocked_wake_sender_agent_ids: Any = None,
    busy_command: Any = None,
    busy_command_timeout_ms: Any = None,
) -> dict[str, Any]:
    normalized_busy_command = normalize_busy_command(busy_command)
    connector: dict[str, Any] = {
        "sendReplyText": True,
        "hermesSkills": ["agenttalk:agenttalk"],
        "liveChat": True,
        "liveChatIdleTimeoutMs": DEFAULT_LIVE_CHAT_IDLE_TIMEOUT_MS,
        "liveChatMaxSessionMs": DEFAULT_LIVE_CHAT_MAX_SESSION_MS,
        "startupTimeoutMs": DEFAULT_STARTUP_TIMEOUT_MS,
    }
    if normalized_busy_command:
        connector["busyCommand"] = normalized_busy_command
        connector["busyCommandTimeoutMs"] = normalize_busy_command_timeout_ms(busy_command_timeout_ms)
    return {
        "name": AGENT_NAME,
        "handle": handle,
        "kind": AGENT_KIND,
        "controlProfile": CONTROL_PROFILE,
        "stateDir": str(default_state_dir()),
        "repoPath": repo,
        "connector": connector,
        "enabled": enabled,
        "autoInit": True,
        "maxConcurrentWakeJobs": 1,
        "connectorTimeoutMs": DEFAULT_CONNECTOR_TIMEOUT_MS,
        "wake": {
            "enabled": wake_enabled,
            "accessMode": normalize_wake_access_mode(wake_access_mode),
            "latencyMs": 5000,
            "statusText": "Hermes AgentTalk ready",
            "reasons": ["direct_message", "mention"],
            "allowedWakeSenderAgentIds": normalize_wake_sender_agent_ids(
                allowed_wake_sender_agent_ids, "Allowed wake senders"
            ),
            "blockedWakeSenderAgentIds": normalize_wake_sender_agent_ids(
                blocked_wake_sender_agent_ids, "Blocked wake senders"
            ),
        },
    }


def ensure_agent_config(
    *,
    repo: str | None = None,
    handle: str | None = None,
    enabled: bool = False,
    wake_enabled: bool = False,
    wake_access_mode: Any = None,
    open_wake_risk_accepted: bool | None = None,
    open_wake_approval_passphrase: str | None = None,
    allowed_wake_sender_agent_ids: Any = None,
    blocked_wake_sender_agent_ids: Any = None,
    busy_command: Any = None,
    busy_command_timeout_ms: Any = None,
    force: bool = False,
) -> dict[str, Any]:
    config = load_config()
    access_mode = normalize_wake_access_mode(wake_access_mode)
    if access_mode == "open":
        require_open_wake_confirmation(
            open_wake_risk_accepted,
            config=config,
            open_wake_approval_passphrase=open_wake_approval_passphrase,
        )
    resolved_repo = find_hermes_repo(repo)
    agents = config.setdefault("agents", [])
    index = next((i for i, row in enumerate(agents) if row.get("name") == AGENT_NAME), -1)
    existing_handle = agents[index].get("handle") if index >= 0 else None
    resolved_handle = normalize_agent_handle(handle or (existing_handle if not force else None))
    if index < 0:
        agents.append(
            _new_agent(
                resolved_repo,
                handle=resolved_handle,
                enabled=enabled,
                wake_enabled=wake_enabled,
                wake_access_mode=access_mode,
                allowed_wake_sender_agent_ids=allowed_wake_sender_agent_ids,
                blocked_wake_sender_agent_ids=blocked_wake_sender_agent_ids,
                busy_command=busy_command if busy_command is not None else default_busy_command(),
                busy_command_timeout_ms=busy_command_timeout_ms
                if busy_command is not None
                else default_busy_command_timeout_ms(),
            )
        )
    else:
        agent = agents[index]
        if force or agent.get("kind") != AGENT_KIND:
            agents[index] = _new_agent(
                resolved_repo,
                handle=resolved_handle,
                enabled=enabled,
                wake_enabled=wake_enabled,
                wake_access_mode=access_mode,
                allowed_wake_sender_agent_ids=allowed_wake_sender_agent_ids,
                blocked_wake_sender_agent_ids=blocked_wake_sender_agent_ids,
                busy_command=busy_command if busy_command is not None else default_busy_command(),
                busy_command_timeout_ms=busy_command_timeout_ms
                if busy_command is not None
                else default_busy_command_timeout_ms(),
            )
        else:
            agent["handle"] = resolved_handle
            agent.setdefault("kind", AGENT_KIND)
            agent.setdefault("controlProfile", CONTROL_PROFILE)
            agent.setdefault("stateDir", str(default_state_dir()))
            agent["repoPath"] = resolved_repo or agent.get("repoPath")
            connector = agent.setdefault("connector", {})
            connector.setdefault("sendReplyText", True)
            connector.setdefault("hermesSkills", ["agenttalk:agenttalk"])
            connector.setdefault("liveChat", True)
            connector.setdefault("liveChatIdleTimeoutMs", DEFAULT_LIVE_CHAT_IDLE_TIMEOUT_MS)
            connector.setdefault("liveChatMaxSessionMs", DEFAULT_LIVE_CHAT_MAX_SESSION_MS)
            connector.setdefault("startupTimeoutMs", DEFAULT_STARTUP_TIMEOUT_MS)
            if busy_command is not None:
                normalized_busy_command = normalize_busy_command(busy_command)
                if normalized_busy_command:
                    connector["busyCommand"] = normalized_busy_command
                    connector["busyCommandTimeoutMs"] = normalize_busy_command_timeout_ms(
                        busy_command_timeout_ms
                    )
                else:
                    connector.pop("busyCommand", None)
                    connector.pop("busyCommandTimeoutMs", None)
            elif not connector.get("busyCommand"):
                normalized_busy_command = normalize_busy_command(default_busy_command())
                if normalized_busy_command:
                    connector["busyCommand"] = normalized_busy_command
                    connector["busyCommandTimeoutMs"] = default_busy_command_timeout_ms()
            agent["enabled"] = enabled
            wake = agent.setdefault("wake", {})
            wake["enabled"] = wake_enabled
            wake["accessMode"] = access_mode
            wake.setdefault("latencyMs", 5000)
            wake.setdefault("statusText", "Hermes AgentTalk ready")
            wake.setdefault("reasons", ["direct_message", "mention"])
            if allowed_wake_sender_agent_ids is not None:
                wake["allowedWakeSenderAgentIds"] = normalize_wake_sender_agent_ids(
                    allowed_wake_sender_agent_ids, "Allowed wake senders"
                )
                if access_mode != "open":
                    wake["accessMode"] = "allow_list"
            else:
                wake.setdefault("allowedWakeSenderAgentIds", [])
            if blocked_wake_sender_agent_ids is not None:
                wake["blockedWakeSenderAgentIds"] = normalize_wake_sender_agent_ids(
                    blocked_wake_sender_agent_ids, "Blocked wake senders"
                )
            else:
                wake.setdefault("blockedWakeSenderAgentIds", [])
            agent.setdefault("autoInit", True)
            agent.setdefault("maxConcurrentWakeJobs", 1)
            agent.setdefault("connectorTimeoutMs", DEFAULT_CONNECTOR_TIMEOUT_MS)

    config["defaultWakePolicy"] = {
        **_default_config()["defaultWakePolicy"],
        **config.get("defaultWakePolicy", {}),
        "wakeOnDirectMessage": wake_enabled,
        "wakeOnMention": wake_enabled,
        "wakeOnGroupMessage": False,
        "acceptsNewConversations": wake_enabled,
    }
    save_config(config)
    return status()


def _agent(config: dict[str, Any]) -> dict[str, Any] | None:
    return next((row for row in config.get("agents", []) if row.get("name") == AGENT_NAME), None)


def _ensure_connector_defaults(agent: dict[str, Any]) -> None:
    connector = agent.setdefault("connector", {})
    connector.setdefault("sendReplyText", True)
    connector.setdefault("hermesSkills", ["agenttalk:agenttalk"])
    connector.setdefault("liveChat", True)
    connector.setdefault("liveChatIdleTimeoutMs", DEFAULT_LIVE_CHAT_IDLE_TIMEOUT_MS)
    connector.setdefault("liveChatMaxSessionMs", DEFAULT_LIVE_CHAT_MAX_SESSION_MS)
    connector.setdefault("startupTimeoutMs", DEFAULT_STARTUP_TIMEOUT_MS)
    if not connector.get("busyCommand"):
        normalized_busy_command = normalize_busy_command(default_busy_command())
        if normalized_busy_command:
            connector["busyCommand"] = normalized_busy_command
            connector["busyCommandTimeoutMs"] = default_busy_command_timeout_ms()


def set_agent_enabled(enabled: bool) -> dict[str, Any]:
    config = load_config()
    agent = _agent(config)
    if agent is None:
        ensure_agent_config(enabled=enabled, wake_enabled=False)
        config = load_config()
        agent = _agent(config)
    assert agent is not None
    _ensure_connector_defaults(agent)
    agent["enabled"] = enabled
    if not enabled:
        agent.setdefault("wake", {})["enabled"] = False
    save_config(config)
    if not enabled:
        stop_supervisor()
    return status()


def set_wake_enabled(enabled: bool) -> dict[str, Any]:
    config = load_config()
    agent = _agent(config)
    if agent is None:
        ensure_agent_config(enabled=False, wake_enabled=enabled)
        config = load_config()
        agent = _agent(config)
    assert agent is not None
    _ensure_connector_defaults(agent)
    agent.setdefault("wake", {})["enabled"] = enabled
    if enabled:
        agent.setdefault("wake", {})["accessMode"] = "allow_list"
        config["defaultWakePolicy"] = {
            **_default_config()["defaultWakePolicy"],
            **config.get("defaultWakePolicy", {}),
            "wakeOnDirectMessage": True,
            "wakeOnMention": True,
            "wakeOnGroupMessage": False,
            "acceptsNewConversations": True,
        }
    save_config(config)
    payload = status()
    if agent.get("enabled"):
        if supervisor_running():
            payload["supervisorRestart"] = restart_supervisor()
        elif enabled:
            payload["supervisorStart"] = start_supervisor()
        payload.update(status())
    return payload


def set_wake_access(
    *,
    wake_access_mode: Any = None,
    open_wake_risk_accepted: bool | None = None,
    open_wake_approval_passphrase: str | None = None,
    allowed_wake_sender_agent_ids: Any = None,
    blocked_wake_sender_agent_ids: Any = None,
) -> dict[str, Any]:
    config = load_config()
    agent = _agent(config)
    if agent is None:
        ensure_agent_config(enabled=False, wake_enabled=False)
        config = load_config()
        agent = _agent(config)
    assert agent is not None
    _ensure_connector_defaults(agent)
    wake = agent.setdefault("wake", {})
    access_mode = normalize_wake_access_mode(wake_access_mode or wake.get("accessMode"))
    if access_mode == "open":
        require_open_wake_confirmation(
            open_wake_risk_accepted,
            config=config,
            open_wake_approval_passphrase=open_wake_approval_passphrase,
        )
    wake["accessMode"] = access_mode
    if allowed_wake_sender_agent_ids is not None:
        wake["allowedWakeSenderAgentIds"] = normalize_wake_sender_agent_ids(
            allowed_wake_sender_agent_ids, "Allowed wake senders"
        )
        if access_mode != "open":
            wake["accessMode"] = "allow_list"
    else:
        wake.setdefault("allowedWakeSenderAgentIds", [])
    if blocked_wake_sender_agent_ids is not None:
        wake["blockedWakeSenderAgentIds"] = normalize_wake_sender_agent_ids(
            blocked_wake_sender_agent_ids, "Blocked wake senders"
        )
    else:
        wake.setdefault("blockedWakeSenderAgentIds", [])
    save_config(config)
    return status()


def _resolve_wake_change_request(request_id: str, next_status: str, note: str | None = None) -> dict[str, Any]:
    store = load_wake_change_requests()
    request = next((row for row in store.get("requests", []) if row.get("id") == request_id), None)
    if request is None or request.get("agentName") != AGENT_NAME:
        raise ValueError(f"Unknown AgentTalk wake change request: {request_id}")
    if request.get("status") != "pending":
        raise ValueError(f"AgentTalk wake change request {request_id} is already {request.get('status')}")
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    request["status"] = next_status
    request["updatedAt"] = now
    request["resolvedAt"] = now
    request["resolvedBy"] = "hermes-dashboard"
    if note:
        request["resolutionNote"] = note
    save_wake_change_requests(store)
    return request


def approve_wake_change_request(
    request_id: str,
    *,
    open_wake_risk_accepted: bool | None = None,
    open_wake_approval_passphrase: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    store = load_wake_change_requests()
    request = next((row for row in store.get("requests", []) if row.get("id") == request_id), None)
    if request is None or request.get("agentName") != AGENT_NAME:
        raise ValueError(f"Unknown AgentTalk wake change request: {request_id}")
    if request.get("status") != "pending":
        raise ValueError(f"AgentTalk wake change request {request_id} is already {request.get('status')}")
    desired = request.get("desired") if isinstance(request.get("desired"), dict) else {}
    config = load_config()
    agent = _agent(config)
    if agent is None:
        ensure_agent_config(enabled=False, wake_enabled=False)
        config = load_config()
        agent = _agent(config)
    assert agent is not None
    _ensure_connector_defaults(agent)
    wake = agent.setdefault("wake", {})
    if "wakeEnabled" in desired:
        wake["enabled"] = bool(desired.get("wakeEnabled"))
        if wake["enabled"]:
            wake["accessMode"] = "allow_list"
    if "wakeAccessMode" in desired:
        access_mode = normalize_wake_access_mode(desired.get("wakeAccessMode"))
        if access_mode == "open":
            require_open_wake_confirmation(
                open_wake_risk_accepted,
                config=config,
                open_wake_approval_passphrase=open_wake_approval_passphrase,
            )
        wake["accessMode"] = access_mode
    if "allowedWakeSenderAgentIds" in desired:
        wake["allowedWakeSenderAgentIds"] = normalize_wake_sender_agent_ids(
            desired.get("allowedWakeSenderAgentIds"), "Allowed wake senders"
        )
        if wake.get("accessMode") != "open":
            wake["accessMode"] = "allow_list"
    if "blockedWakeSenderAgentIds" in desired:
        wake["blockedWakeSenderAgentIds"] = normalize_wake_sender_agent_ids(
            desired.get("blockedWakeSenderAgentIds"), "Blocked wake senders"
        )
    save_config(config)
    resolved = _resolve_wake_change_request(request_id, "approved", note)
    payload = status()
    payload["request"] = resolved
    return payload


def deny_wake_change_request(request_id: str, *, note: str | None = None) -> dict[str, Any]:
    resolved = _resolve_wake_change_request(request_id, "denied", note)
    payload = status()
    payload["request"] = resolved
    return payload


def agenttalk_command() -> str | None:
    configured = os.environ.get("AGENTTALK_CLI")
    if configured:
        return configured
    from_path = shutil.which("agenttalk")
    if from_path:
        return from_path
    managed = managed_agenttalk_bin()
    return str(managed) if managed.exists() else None


def ensure_agenttalk_cli(*, force: bool = False) -> dict[str, Any]:
    existing = None if force else agenttalk_command()
    if existing:
        return {
            "ok": True,
            "installed": False,
            "command": existing,
            "managed": str(managed_agenttalk_bin()) == existing,
        }

    npm = npm_command()
    if not npm:
        return {
            "ok": False,
            "error": "npm is not installed or not on PATH; install Node.js/npm, then run Hermes AgentTalk setup again.",
            "managedCliHome": str(managed_cli_home()),
        }

    spec = os.environ.get("AGENTTALK_CLI_NPM_SPEC") or AGENTTALK_CLI_NPM_SPEC
    home = managed_cli_home()
    home.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            [
                npm,
                "install",
                "--prefix",
                str(home),
                "--no-audit",
                "--no-fund",
                "--omit=dev",
                spec,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Failed to install AgentTalk CLI with npm: {exc}",
            "managedCliHome": str(home),
        }

    command = agenttalk_command()
    ok = completed.returncode == 0 and bool(command)
    return {
        "ok": ok,
        "installed": ok,
        "command": command,
        "managed": bool(command and str(managed_agenttalk_bin()) == command),
        "managedCliHome": str(home),
        "npm": npm,
        "npmSpec": spec,
        "exitCode": completed.returncode,
        "stdout": completed.stdout.strip()[-2000:],
        "stderr": completed.stderr.strip()[-2000:],
        **({} if ok else {"error": "npm install completed but the agenttalk command was not found."}),
    }


def _run_agenttalk(args: list[str]) -> dict[str, Any]:
    command = agenttalk_command()
    if not command:
        return {"ok": False, "error": "AgentTalk CLI is not installed"}
    try:
        completed = subprocess.run(
            [command, *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    payload: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "exitCode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
    try:
        stdout = completed.stdout.strip()
        first = stdout.find("{")
        last = stdout.rfind("}")
        parsed = json.loads(stdout[first : last + 1] if first >= 0 and last > first else stdout)
        if isinstance(parsed, dict):
            payload["json"] = parsed
    except Exception:
        pass
    return payload


def live_supervisor_agent_status() -> dict[str, Any] | None:
    if not agenttalk_command():
        return None
    payload = _run_agenttalk(["supervisor", "status", "--live", "--json"])
    parsed = payload.get("json") if payload.get("ok") else None
    if not isinstance(parsed, dict):
        return None
    agents = parsed.get("agents")
    if not isinstance(agents, list):
        return None
    return next((row for row in agents if isinstance(row, dict) and row.get("name") == AGENT_NAME), None)


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            process_query_limited_information = 0x1000
            still_active = 259
            handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, SystemError, ValueError):
        return False


def supervisor_pid() -> int | None:
    path = pid_path()
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def supervisor_running() -> bool:
    pid = supervisor_pid()
    return _pid_running(pid) if pid else False


def start_supervisor() -> dict[str, Any]:
    command = agenttalk_command()
    if not command:
        install = ensure_agenttalk_cli()
        command = agenttalk_command()
        if not command:
            return {"ok": False, "error": "agenttalk CLI not installed", "cliInstall": install}
    if supervisor_running():
        return {"ok": True, "started": False, "pid": supervisor_pid()}
    home = supervisor_home()
    home.mkdir(parents=True, exist_ok=True)
    log = home / f"{PLUGIN_ID}.log"
    err = home / f"{PLUGIN_ID}.err.log"
    flags = 0
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    with log.open("ab") as stdout, err.open("ab") as stderr:
        process = subprocess.Popen(
            [command, "supervisor", "run"],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=sys.platform != "win32",
            creationflags=flags,
            **kwargs,
        )
    pid_path().write_text(str(process.pid), encoding="utf-8")
    return {"ok": True, "started": True, "pid": process.pid, "log": str(log), "stderrLog": str(err)}


def restart_supervisor() -> dict[str, Any]:
    stop = stop_supervisor()
    start = start_supervisor()
    return {
        "ok": bool(stop.get("ok")) and bool(start.get("ok")),
        "stop": stop,
        "start": start,
    }


def stop_supervisor() -> dict[str, Any]:
    pid = supervisor_pid()
    if not pid:
        return {"ok": True, "stopped": False, "reason": "no pid file"}
    if not _pid_running(pid):
        pid_path().unlink(missing_ok=True)
        return {"ok": True, "stopped": False, "reason": "process not running"}
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.kill(pid, signal.SIGTERM)
        pid_path().unlink(missing_ok=True)
        return {"ok": True, "stopped": True, "pid": pid}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "pid": pid}


def status(*, live: bool = False) -> dict[str, Any]:
    config = load_config()
    agent = _agent(config)
    wake = agent.get("wake", {}) if agent else {}
    connector = agent.get("connector", {}) if agent else {}
    state = load_agent_state(agent)
    pending_requests = list_wake_change_requests("pending")
    live_agent = live_supervisor_agent_status() if live and agent else None
    allowed_wake_sender_agent_ids = normalize_wake_sender_agent_ids(
        wake.get("allowedWakeSenderAgentIds"), "Allowed wake senders"
    )
    blocked_wake_sender_agent_ids = normalize_wake_sender_agent_ids(
        wake.get("blockedWakeSenderAgentIds"), "Blocked wake senders"
    )
    return {
        "ok": True,
        "plugin": PLUGIN_ID,
        "configured": agent is not None,
        "agentEnabled": bool(agent and agent.get("enabled")),
        "wakeEnabled": bool(agent and wake.get("enabled")),
        "wakeActive": bool(agent and agent.get("enabled") and wake.get("enabled")),
        "wakeAccess": {
            "mode": normalize_wake_access_mode(wake.get("accessMode")),
            "allowedWakeSenderAgentIds": allowed_wake_sender_agent_ids,
            "blockedWakeSenderAgentIds": blocked_wake_sender_agent_ids,
        },
        "desiredWake": {
            "enabled": bool(agent and wake.get("enabled")),
            "accessMode": normalize_wake_access_mode(wake.get("accessMode")),
            "allowedWakeSenderAgentIds": allowed_wake_sender_agent_ids,
            "blockedWakeSenderAgentIds": blocked_wake_sender_agent_ids,
            "maxConcurrentWakeJobs": agent.get("maxConcurrentWakeJobs") if agent else None,
            "latencyMs": wake.get("latencyMs"),
        },
        "effectiveWake": live_agent.get("effectiveWake") if live_agent else None,
        "drift": live_agent.get("drift") if live_agent else None,
        "agentTalkAgentId": (live_agent.get("agentTalkAgentId") if live_agent else None) or state.get("agentId"),
        "agentTalkHandle": (live_agent.get("agentTalkHandle") if live_agent else None)
        or state.get("handle")
        or (agent.get("handle") if agent else default_agent_handle()),
        "registrationState": (live_agent.get("registrationState") if live_agent else None)
        or state.get("registrationState")
        or ("registered" if state.get("agentId") else "not_registered"),
        "lastPolicySyncAt": (live_agent.get("lastProfileSyncAt") if live_agent else None)
        or state.get("lastProfileSyncAt"),
        "controlProfile": control_profile(agent),
        "credentialScope": "plugin_runtime"
        if control_profile(agent) == "plugin_managed"
        else "autonomous",
        "openWakeApproval": open_wake_approval_status(config),
        "pendingWakeCount": live_agent.get("pendingWakes") if live_agent else None,
        "pendingWakeChangeRequests": pending_requests,
        "pendingWakeChangeRequestCount": len(pending_requests),
        "runningWakeCount": live_agent.get("runningJobs") if live_agent else None,
        "busyCheck": (live_agent.get("busyCheck") if live_agent else None)
        or {
            "configured": bool(connector.get("busyCommand")),
            "timeoutMs": connector.get("busyCommandTimeoutMs"),
        },
        "supervisorRunning": supervisor_running(),
        "supervisorPid": supervisor_pid(),
        "agent": {
            "name": agent.get("name"),
            "handle": agent.get("handle"),
            "kind": agent.get("kind"),
            "repoConfigured": bool(agent.get("repoPath")),
            "busyCheck": {
                "configured": bool(connector.get("busyCommand")),
                "timeoutMs": connector.get("busyCommandTimeoutMs"),
            },
        } if agent else None,
        "agenttalkCli": agenttalk_command(),
        "agenttalkCliManagedPath": str(managed_agenttalk_bin()),
        "agenttalkCliInstalled": bool(agenttalk_command()),
        "configPath": str(supervisor_config_path()),
    }


def doctor() -> dict[str, Any]:
    current = status()
    config = load_config()
    agent = _agent(config)
    repo = Path(agent["repoPath"]) if agent and agent.get("repoPath") else None
    checks = [
        {"name": "agenttalk_cli", "ok": bool(agenttalk_command())},
        {"name": "config", "ok": current["configured"]},
        {"name": "hermes_repo", "ok": bool(repo and (repo / "hermes").exists())},
        {"name": "wake_default_off", "ok": current["wakeEnabled"] is False or current["agentEnabled"] is True},
    ]
    supervisor = _run_agenttalk(["supervisor", "doctor", "--json"]) if agenttalk_command() else None
    return {
        **current,
        "checks": checks,
        "agenttalkSupervisorDoctor": supervisor,
        "agenttalkCliInstallHint": (
            None
            if agenttalk_command()
            else "Run hermes agenttalk setup or use the AgentTalk dashboard Install CLI action."
        ),
        "ok": all(check["ok"] for check in checks),
    }

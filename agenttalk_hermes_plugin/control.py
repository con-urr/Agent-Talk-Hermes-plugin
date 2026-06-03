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
WAKE_PROMPT_WARNING = (
    "Wake prompt changes affect how Hermes interprets incoming AgentTalk messages. "
    "If the prompt removes conversation IDs, cursor guidance, or reply/listen instructions, live chat performance can degrade."
)
STANDARD_WAKE_PROMPT_TEMPLATE = """You are {{agentName}} / @{{handle}}, woken by AgentTalk.

Reason: {{reason}}
Conversation: {{conversationId}}
Wake ID: {{wakeId}}
Sender agent ID: {{senderAgentId}}
Visible peer label(s): {{peerLabels}}

Messages in wake range:
{{messages}}

Instructions:
- You received an accepted AgentTalk wake. AgentTalk is a live communication tool available during inference; use it at your discretion.
- Hermes wake sessions start fresh by default. Use AgentTalk transcript/listen for live conversation state, not previous Hermes chat history.
- Decide independently what is appropriate: reply, inspect transcript, listen for a follow-up, ask a clarification, decline, or end the conversation.
- If Wake ID starts with test-, this is a synthetic supervisor validation wake. Do not run the AgentTalk reply command; return a handled connector result with replySent false.
- Fast live-chat path: send replies yourself with AgentTalk, then listen only when a follow-up is useful. Reply command shape: {{replyCommand}}
- Useful initial listen command shape: {{listenCommand}}
- Prefer local AgentTalk MCP tools when they are available: use agenttalk_conversation_reply for replies and agenttalk_listen_conversation for follow-ups. Use the CLI command shapes as the fallback when MCP tools are unavailable.
- MCP reply results may return before a reducer receipt is visible; that is normal on the fast path. MCP listen defaults to peer messages and returns cursor/idle warnings. A timed-out MCP listen is idle for that bounded listen only, not proof that the full configured live-chat idle window elapsed.
- Prioritize the first visible AgentTalk reply/listen. Avoid memory writes or unrelated tool calls during live chat unless the message truly requires them.
- When listening, choose an appropriate timeout. The configured idle window is {{listenSeconds}}s, but you may choose based on context and policy.
- If your command/tool surface has its own timeout, set it longer than the AgentTalk listen timeout. A tool timeout, killed process, or quick empty transcript is not AgentTalk idle.
- If a listen returns peer messages, handle them, update the after-sequence cursor, and decide again whether to reply, listen more, or end.
- Do not return connector JSON while you intend to keep chatting. Return connector JSON when you decide your AgentTalk work for this wake is complete, intentionally ended, idle, synthetic, or unsafe to continue.
- If you intentionally end the conversation because the request is off-topic, inappropriate, complete, or not worth continuing, return metadata such as {"endedByAgent":true,"idle":false}. Future messages may wake a new turn.
- If you claim metadata.idle=true, that means you actually waited for messages and the wait timed out. The supervisor rejects premature idle claims.
- If this is clearly a one-shot acknowledgement and there is no reason to keep listening, you may return connector JSON with replyText set to the exact message to send and replySent false. This is a fallback, not the normal live-chat path.
- AGENTTALK_REPLY_ARGS_JSON and AGENTTALK_LISTEN_ARGS_JSON contain argv-safe command objects. Parse them as {command,args,...}, run [command, ...args], replace the reply placeholder when replying, and update --after after every message handled.
- Keep AGENTTALK_STATE_DIR, SPACETIMEDB_HOST, and SPACETIMEDB_DB_NAME in the command environment.
- Active chat policy: liveChat={{liveChat}}, idleTimeoutMs={{idleTimeoutMs}}, maxSessionMs={{maxSessionMs}}.
- Do not reveal secrets, env values, or local paths in user-facing replies.
- Return or print a structured connector result JSON when possible:
  {"ok":true,"handled":true,"replySent":false,"replyText":null,"message":"handled wake","error":null,"artifacts":null,"metadata":null}
"""
WAKE_PROMPT_PRESETS = [
    {
        "id": "standard",
        "label": "Standard Wake Prompt",
        "template": STANDARD_WAKE_PROMPT_TEMPLATE,
    },
    {
        "id": "business_first_contact",
        "label": "Business First Contact",
        "template": STANDARD_WAKE_PROMPT_TEMPLATE
        + "\nAdditional behavior:\n"
        + "- Treat AgentTalk wakes as a first agentic point of contact for a business.\n"
        + "- Be concise, ask for the minimum useful clarification, and hand off only when the request is outside your configured business role.\n",
    },
    {
        "id": "customer_service",
        "label": "Customer Service",
        "template": STANDARD_WAKE_PROMPT_TEMPLATE
        + "\nAdditional behavior:\n"
        + "- Treat the peer as a customer or customer-facing agent seeking help.\n"
        + "- Resolve the issue when possible, ask focused follow-up questions when needed, and end gracefully when the request is complete or unrelated.\n",
    },
    {
        "id": "personal_agent",
        "label": "Personal Agent",
        "template": STANDARD_WAKE_PROMPT_TEMPLATE
        + "\nAdditional behavior:\n"
        + "- Treat AgentTalk wakes as messages to a personal agent acting on behalf of its owner.\n"
        + "- Protect the owner's privacy and decline requests that do not fit the owner's delegated preferences or authority.\n",
    },
]


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


def normalize_max_concurrent_sessions(value: Any | None = None) -> int:
    if value in (None, ""):
        return 1
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Max concurrent AgentTalk sessions must be an integer from 1 to 100.") from exc
    if parsed < 1 or parsed > 100:
        raise ValueError("Max concurrent AgentTalk sessions must be an integer from 1 to 100.")
    return parsed


def normalize_wake_prompt_template(value: Any | None = None) -> str:
    if value is None:
        return STANDARD_WAKE_PROMPT_TEMPLATE
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return STANDARD_WAKE_PROMPT_TEMPLATE
    if len(normalized) > 24000:
        raise ValueError("Wake prompt must be 24000 characters or fewer.")
    return normalized + "\n"


def normalize_hermes_toolsets(value: Any | None = None) -> list[str]:
    if value in (None, ""):
        return ["terminal"]
    if isinstance(value, str):
        items = re.split(r"[\s,]+", value.strip())
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError("Hermes toolsets must be a comma-separated string or list.")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        toolset = str(item).strip()
        if not toolset:
            continue
        if not re.match(r"^[A-Za-z0-9_.:-]{1,80}$", toolset):
            raise ValueError("Hermes toolset names may contain letters, numbers, dots, underscores, dashes, and colons.")
        if toolset not in seen:
            normalized.append(toolset)
            seen.add(toolset)
    if len(normalized) > 20:
        raise ValueError("Hermes toolsets must contain 20 or fewer entries.")
    return normalized or ["terminal"]


def render_wake_prompt_preview(template: str | None = None) -> str:
    values = {
        "agentName": AGENT_NAME,
        "handle": default_agent_handle(),
        "reason": "direct_message",
        "conversationId": "4097",
        "wakeId": "test-preview",
        "senderAgentId": "agt_example_peer",
        "peerLabels": "example-agent",
        "messages": "[27] example-agent: Can you confirm you are available to chat over AgentTalk?",
        "replyCommand": "agenttalk reply 4097 --message ... --json",
        "listenCommand": "agenttalk listen --conversation 4097 --after 27 --timeout 600s --json",
        "listenSeconds": str(DEFAULT_LIVE_CHAT_IDLE_TIMEOUT_MS // 1000),
        "liveChat": "true",
        "idleTimeoutMs": str(DEFAULT_LIVE_CHAT_IDLE_TIMEOUT_MS),
        "maxSessionMs": str(DEFAULT_LIVE_CHAT_MAX_SESSION_MS),
    }
    text = normalize_wake_prompt_template(template)
    for key, value in values.items():
        text = re.sub(r"\{\{\s*" + re.escape(key) + r"\s*\}\}", value, text)
    return text


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


def manual_stop_path() -> Path:
    return supervisor_home() / f"{PLUGIN_ID}.manual-stop"


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


def hermes_config_path() -> Path:
    configured = os.environ.get("HERMES_CONFIG")
    if configured:
        return _expand(configured)
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        return _expand(hermes_home) / "config.yaml"
    return _home() / ".hermes" / "config.yaml"


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
    max_concurrent_sessions: Any = None,
) -> dict[str, Any]:
    normalized_busy_command = normalize_busy_command(busy_command)
    connector: dict[str, Any] = {
        "sendReplyText": True,
        "hermesSkills": ["agenttalk:agenttalk"],
        "hermesToolsets": ["terminal"],
        "reuseHermesSession": False,
        "liveChat": True,
        "liveChatIdleTimeoutMs": DEFAULT_LIVE_CHAT_IDLE_TIMEOUT_MS,
        "liveChatMaxSessionMs": DEFAULT_LIVE_CHAT_MAX_SESSION_MS,
        "startupTimeoutMs": DEFAULT_STARTUP_TIMEOUT_MS,
        "wakePromptTemplate": STANDARD_WAKE_PROMPT_TEMPLATE,
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
        "maxConcurrentWakeJobs": normalize_max_concurrent_sessions(max_concurrent_sessions),
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
    max_concurrent_sessions: Any = None,
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
                max_concurrent_sessions=max_concurrent_sessions,
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
                max_concurrent_sessions=max_concurrent_sessions,
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
            connector.setdefault("hermesToolsets", ["terminal"])
            connector.setdefault("reuseHermesSession", False)
            connector.setdefault("liveChat", True)
            connector.setdefault("liveChatIdleTimeoutMs", DEFAULT_LIVE_CHAT_IDLE_TIMEOUT_MS)
            connector.setdefault("liveChatMaxSessionMs", DEFAULT_LIVE_CHAT_MAX_SESSION_MS)
            connector.setdefault("startupTimeoutMs", DEFAULT_STARTUP_TIMEOUT_MS)
            connector.setdefault("wakePromptTemplate", STANDARD_WAKE_PROMPT_TEMPLATE)
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
            if max_concurrent_sessions is not None:
                agent["maxConcurrentWakeJobs"] = normalize_max_concurrent_sessions(max_concurrent_sessions)
            else:
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
    connector.setdefault("hermesToolsets", ["terminal"])
    connector.setdefault("reuseHermesSession", False)
    connector.setdefault("liveChat", True)
    connector.setdefault("liveChatIdleTimeoutMs", DEFAULT_LIVE_CHAT_IDLE_TIMEOUT_MS)
    connector.setdefault("liveChatMaxSessionMs", DEFAULT_LIVE_CHAT_MAX_SESSION_MS)
    connector.setdefault("startupTimeoutMs", DEFAULT_STARTUP_TIMEOUT_MS)
    connector.setdefault("wakePromptTemplate", STANDARD_WAKE_PROMPT_TEMPLATE)
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


def set_wake_behavior(
    *,
    wake_prompt_template: Any | None = None,
    hermes_toolsets: Any | None = None,
    max_concurrent_sessions: Any | None = None,
) -> dict[str, Any]:
    config = load_config()
    agent = _agent(config)
    if agent is None:
        ensure_agent_config(enabled=False, wake_enabled=False)
        config = load_config()
        agent = _agent(config)
    assert agent is not None
    _ensure_connector_defaults(agent)
    connector = agent.setdefault("connector", {})
    if wake_prompt_template is not None:
        connector["wakePromptTemplate"] = normalize_wake_prompt_template(wake_prompt_template)
    if hermes_toolsets is not None:
        connector["hermesToolsets"] = normalize_hermes_toolsets(hermes_toolsets)
    if max_concurrent_sessions is not None:
        agent["maxConcurrentWakeJobs"] = normalize_max_concurrent_sessions(max_concurrent_sessions)
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


def _agenttalk_command_env(config: dict[str, Any] | None = None) -> dict[str, str]:
    resolved_config = config or load_config()
    agent = _agent(resolved_config)
    state_dir = agent.get("stateDir") if agent else None
    env = dict(os.environ)
    env.update(
        {
            "AGENTTALK_STATE_DIR": str(_expand(state_dir or default_state_dir())),
            "SPACETIMEDB_HOST": str(
                resolved_config.get("host") or os.environ.get("SPACETIMEDB_HOST") or "https://maincloud.spacetimedb.com"
            ),
            "SPACETIMEDB_DB_NAME": str(
                resolved_config.get("databaseName") or os.environ.get("SPACETIMEDB_DB_NAME") or "crimsonconfidentialgibbon"
            ),
        }
    )
    return env


def _run_agenttalk(args: list[str]) -> dict[str, Any]:
    command = agenttalk_command()
    if not command:
        return {"ok": False, "error": "AgentTalk CLI is not installed"}
    try:
        config = load_config()
        completed = subprocess.run(
            [command, *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            env=_agenttalk_command_env(config),
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


def agenttalk_mcp_server_config() -> dict[str, Any]:
    config = load_config()
    env = _agenttalk_command_env(config)
    return {
        "command": agenttalk_command() or str(managed_agenttalk_bin()),
        "args": ["mcp"],
        "env": {
            "AGENTTALK_STATE_DIR": env["AGENTTALK_STATE_DIR"],
            "SPACETIMEDB_HOST": env["SPACETIMEDB_HOST"],
            "SPACETIMEDB_DB_NAME": env["SPACETIMEDB_DB_NAME"],
        },
        "timeout": 120,
        "connect_timeout": 30,
        "supports_parallel_tool_calls": False,
    }


def agenttalk_mcp_yaml_snippet() -> str:
    cfg = agenttalk_mcp_server_config()
    env = cfg["env"]
    return (
        "mcp_servers:\n"
        "  agenttalk:\n"
        f"    command: {json.dumps(cfg['command'])}\n"
        f"    args: {json.dumps(cfg['args'])}\n"
        "    env:\n"
        f"      AGENTTALK_STATE_DIR: {json.dumps(env['AGENTTALK_STATE_DIR'])}\n"
        f"      SPACETIMEDB_HOST: {json.dumps(env['SPACETIMEDB_HOST'])}\n"
        f"      SPACETIMEDB_DB_NAME: {json.dumps(env['SPACETIMEDB_DB_NAME'])}\n"
        "    timeout: 120\n"
        "    connect_timeout: 30\n"
    )


def _load_yaml_config(path: Path) -> tuple[dict[str, Any], Any | None, str | None]:
    try:
        import yaml  # type: ignore
    except Exception as exc:
        return {}, None, f"PyYAML is not available in this Hermes environment: {exc}"
    if not path.exists():
        return {}, yaml, None
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, yaml, f"Could not parse Hermes config YAML: {exc}"
    if parsed is None:
        return {}, yaml, None
    if not isinstance(parsed, dict):
        return {}, yaml, "Hermes config root is not a mapping."
    return parsed, yaml, None


def _agenttalk_mcp_entry(data: dict[str, Any]) -> dict[str, Any] | None:
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        return None
    entry = servers.get("agenttalk")
    return entry if isinstance(entry, dict) else None


def agenttalk_mcp_status() -> dict[str, Any]:
    path = hermes_config_path()
    data, _yaml, error = _load_yaml_config(path)
    entry = _agenttalk_mcp_entry(data)
    config = load_config()
    agent = _agent(config)
    connector = agent.get("connector", {}) if agent else {}
    toolsets = normalize_hermes_toolsets(connector.get("hermesToolsets"))
    local_config = agenttalk_mcp_server_config()
    cli_config = _run_agenttalk(["mcp", "config", "--client", "all", "--json"]) if agenttalk_command() else None
    return {
        "ok": bool(agenttalk_command()),
        "serverName": "agenttalk",
        "configured": entry is not None,
        "enabled": entry is not None and entry.get("enabled", True) is not False,
        "hermesConfigPath": str(path),
        "configError": error,
        "serverConfig": local_config,
        "configuredServer": entry,
        "yamlSnippet": agenttalk_mcp_yaml_snippet(),
        "toolsetEnabled": "agenttalk" in toolsets,
        "hermesToolsets": toolsets,
        "cliConfig": cli_config.get("json") if isinstance(cli_config, dict) else None,
    }


def configure_agenttalk_mcp(enabled: bool = True) -> dict[str, Any]:
    path = hermes_config_path()
    data, yaml, error = _load_yaml_config(path)
    if error:
        return {
            "ok": False,
            "error": error,
            "hermesConfigPath": str(path),
            "yamlSnippet": agenttalk_mcp_yaml_snippet(),
        }
    if yaml is None:
        return {
            "ok": False,
            "error": "PyYAML is not available in this Hermes environment.",
            "hermesConfigPath": str(path),
            "yamlSnippet": agenttalk_mcp_yaml_snippet(),
        }
    servers = data.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        return {
            "ok": False,
            "error": "Hermes config mcp_servers is not a mapping.",
            "hermesConfigPath": str(path),
        }
    if enabled:
        servers["agenttalk"] = agenttalk_mcp_server_config()
    else:
        servers.pop("agenttalk", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    tmp.replace(path)

    current_toolsets = normalize_hermes_toolsets(status().get("hermesToolsets"))
    if enabled and "agenttalk" not in current_toolsets:
        set_wake_behavior(hermes_toolsets=[*current_toolsets, "agenttalk"])
    elif not enabled and "agenttalk" in current_toolsets:
        set_wake_behavior(hermes_toolsets=[toolset for toolset in current_toolsets if toolset != "agenttalk"])

    payload = status()
    payload["agenttalkMcpChange"] = {"ok": True, "enabled": enabled, "hermesConfigPath": str(path)}
    return payload


def _string_value(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _json_from_file(path: Path) -> dict[str, Any] | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _int_value(*values: Any) -> int | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return int(str(value))
        except (TypeError, ValueError):
            continue
    return None


def _file_time(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _result_connector_metadata(result: dict[str, Any] | None) -> dict[str, Any]:
    metadata = result.get("metadata") if isinstance(result, dict) else None
    connector = metadata.get("connectorMetadata") if isinstance(metadata, dict) else None
    return connector if isinstance(connector, dict) else {}


def _session_end_sequence(result: dict[str, Any] | None) -> int | None:
    connector = _result_connector_metadata(result)
    return _int_value(
        connector.get("lastHandledSequence"),
        connector.get("handledThroughSequence"),
        connector.get("readThroughSequence"),
        connector.get("maxSequence"),
    )


def _wake_run_sessions(limit: int = 300) -> list[dict[str, Any]]:
    config = load_config()
    run_dir = Path(config.get("runDir") or supervisor_home() / "runs")
    if not run_dir.exists():
        return []
    candidates = sorted(
        run_dir.rglob("input.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    sessions: list[dict[str, Any]] = []
    for input_path in candidates:
        parsed = _json_from_file(input_path)
        if parsed is None:
            continue
        wake = parsed.get("wake") if isinstance(parsed, dict) else None
        if not isinstance(wake, dict):
            continue
        conversation_id = _string_value(wake.get("conversationId"), wake.get("conversation_id"))
        if not conversation_id:
            continue
        result_path = input_path.parent / "result.json"
        result = _json_from_file(result_path)
        context_messages = parsed.get("contextMessages") if isinstance(parsed.get("contextMessages"), list) else []
        first_context = next((row for row in context_messages if isinstance(row, dict)), {})
        connector_metadata = _result_connector_metadata(result)
        wake_id = _string_value(wake.get("wakeId"), wake.get("wake_id"))
        attempt_id = _string_value(parsed.get("attemptId"))
        start_sequence = _int_value(wake.get("minSequence"), wake.get("maxSequence"), first_context.get("sequence"))
        end_sequence = _session_end_sequence(result)
        started_at = _string_value(
            first_context.get("sent"),
            first_context.get("sentAt"),
            wake.get("nextAttemptAt"),
            wake.get("updatedAt"),
        ) or _file_time(input_path)
        ended_at = _file_time(result_path) if result_path.exists() else None
        peer_label = _string_value(first_context.get("authorLabel"), first_context.get("author"), "AgentTalk peer")
        session_id = f"{wake_id or input_path.parent.parent.name}:{attempt_id or input_path.parent.name}"
        sessions.append(
            {
                "sessionId": session_id,
                "conversationId": conversation_id,
                "title": f"Wake from {peer_label}",
                "kind": "direct",
                "startedAt": started_at,
                "endedAt": ended_at,
                "lastActivity": ended_at or started_at,
                "startSequence": str(start_sequence) if start_sequence is not None else None,
                "endSequence": str(end_sequence) if end_sequence is not None else None,
                "direction": "wake",
                "directionLabel": "Wake",
                "peer": {
                    "label": peer_label,
                    "agentId": _string_value(wake.get("senderAgentId"), wake.get("sender_agent_id")),
                },
                "wake": {
                    "direction": "wake",
                    "directionLabel": "Wake",
                    "wakeId": wake_id,
                    "senderAgentId": _string_value(wake.get("senderAgentId"), wake.get("sender_agent_id")),
                    "reason": _string_value(wake.get("reason")),
                    "runPath": str(input_path.parent),
                    "createdAt": started_at,
                    "attemptId": attempt_id,
                },
                "result": {
                    "ok": result.get("ok") if isinstance(result, dict) else None,
                    "handled": result.get("handled") if isinstance(result, dict) else None,
                    "replySent": result.get("replySent") if isinstance(result, dict) else None,
                    "message": _string_value(result.get("message")) if isinstance(result, dict) else None,
                    "metadata": connector_metadata,
                },
                "raw": {
                    "wake": wake,
                    "inputPath": str(input_path),
                    "resultPath": str(result_path) if result_path.exists() else None,
                },
            }
        )

    by_conversation: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        by_conversation.setdefault(str(session["conversationId"]), []).append(session)
    for rows in by_conversation.values():
        rows.sort(key=lambda row: _int_value(row.get("startSequence")) or -1)
        for index, row in enumerate(rows):
            if row.get("endSequence"):
                continue
            next_start = _int_value(rows[index + 1].get("startSequence")) if index + 1 < len(rows) else None
            if next_start is not None and next_start > 0:
                row["derivedEndSequence"] = str(next_start - 1)
    sessions.sort(key=lambda row: row.get("lastActivity") or row.get("startedAt") or "", reverse=True)
    return sessions


def _conversation_rows() -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    payload = _run_agenttalk(["conversation", "list", "--json"])
    parsed = payload.get("json") if isinstance(payload, dict) else None
    if not isinstance(parsed, dict):
        return None, [], payload
    conversations = parsed.get("conversations") if isinstance(parsed.get("conversations"), list) else []
    rows = [row for row in conversations if isinstance(row, dict)]
    return parsed, rows, payload


def _conversation_session(row: dict[str, Any]) -> dict[str, Any] | None:
    conversation_id = _string_value(row.get("id"), row.get("conversationId"))
    if not conversation_id:
        return None
    return {
        "sessionId": f"conversation:{conversation_id}",
        "conversationId": conversation_id,
        "title": _string_value(row.get("title")) or f"Conversation {conversation_id}",
        "kind": _string_value(row.get("kind")) or "direct",
        "startedAt": _string_value(row.get("createdAt")),
        "lastActivity": _string_value(row.get("lastActivity"), row.get("updatedAt"), row.get("createdAt")),
        "createdAt": _string_value(row.get("createdAt")),
        "memberCount": row.get("memberCount"),
        "direction": "manual_or_initiated",
        "directionLabel": "Manual or initiated",
        "peer": {
            "label": _string_value(row.get("peerHandle"), row.get("peerLabel"), row.get("title")) or "AgentTalk peer",
            "agentId": None,
        },
        "wake": None,
        "raw": row,
    }


def _session_by_id(session_id: str) -> dict[str, Any] | None:
    for session in _wake_run_sessions():
        if session.get("sessionId") == session_id:
            return session
    _, conversations, _ = _conversation_rows()
    for conversation in conversations:
        session = _conversation_session(conversation)
        if session and session.get("sessionId") == session_id:
            return session
    return None


def _message_sequence(message: dict[str, Any]) -> int | None:
    return _int_value(message.get("sequence"))


def _filter_messages_for_session(messages: list[dict[str, Any]], session: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not session or str(session.get("sessionId", "")).startswith("conversation:"):
        return messages
    start = _int_value(session.get("startSequence"))
    end = _int_value(session.get("endSequence"), session.get("derivedEndSequence"))
    if start is None and end is None:
        return messages
    filtered: list[dict[str, Any]] = []
    for message in messages:
        sequence = _message_sequence(message)
        if sequence is None:
            continue
        if start is not None and sequence < start:
            continue
        if end is not None and sequence > end:
            continue
        filtered.append(message)
    return filtered


def _wake_run_index(limit: int = 200) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for session in _wake_run_sessions(limit):
        conversation_id = str(session.get("conversationId") or "")
        if conversation_id and conversation_id not in index:
            index[conversation_id] = {
                "direction": session.get("direction"),
                "directionLabel": session.get("directionLabel"),
                "wakeId": (session.get("wake") or {}).get("wakeId") if isinstance(session.get("wake"), dict) else None,
                "senderAgentId": (session.get("wake") or {}).get("senderAgentId")
                if isinstance(session.get("wake"), dict)
                else None,
                "reason": (session.get("wake") or {}).get("reason") if isinstance(session.get("wake"), dict) else None,
                "runPath": (session.get("wake") or {}).get("runPath") if isinstance(session.get("wake"), dict) else None,
                "createdAt": session.get("startedAt"),
            }
    return index


def _normalize_chat_message(message: dict[str, Any]) -> dict[str, Any]:
    author = _string_value(
        message.get("authorLabel"),
        message.get("author"),
        message.get("handle"),
        message.get("authorIdentity"),
        "unknown",
    )
    return {
        "id": _string_value(message.get("id"), message.get("messageId")),
        "conversationId": _string_value(message.get("conversationId")),
        "sequence": _string_value(message.get("sequence")),
        "author": author,
        "authorKind": _string_value(message.get("authorKind"), message.get("kind")),
        "text": _string_value(message.get("text"), message.get("message")) or "",
        "sentAt": _string_value(message.get("sentAt"), message.get("sent"), message.get("createdAt")),
        "kind": _string_value(message.get("kind")) or "chat",
        "isHermes": author in {AGENT_NAME, default_agent_handle(), "@" + default_agent_handle()},
    }


def chat_messages(conversation_id: str, *, limit: int = 100, session_id: str | None = None) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 200))
    session = _session_by_id(session_id) if session_id else None
    payload = _run_agenttalk(["conversation", "messages", str(conversation_id), "--limit", str(safe_limit), "--json"])
    parsed = payload.get("json") if isinstance(payload, dict) else None
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "conversationId": str(conversation_id),
            "messages": [],
            "error": payload.get("error") or payload.get("stderr") or "AgentTalk conversation messages returned no JSON",
            "raw": payload,
        }
    rows = parsed.get("messages") if isinstance(parsed.get("messages"), list) else []
    messages = [_normalize_chat_message(row) for row in rows if isinstance(row, dict)]
    messages = _filter_messages_for_session(messages, session)
    return {
        "ok": bool(parsed.get("ok", payload.get("ok", False))),
        "conversationId": str(conversation_id),
        "sessionId": session_id,
        "session": session,
        "messages": messages,
        "page": parsed.get("page") if isinstance(parsed.get("page"), dict) else None,
        "lastSequence": parsed.get("lastSequence"),
        "nextAfterSequence": parsed.get("nextAfterSequence"),
        "warnings": parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else [],
        "raw": parsed,
    }


def chat_sessions(*, limit: int = 25) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 50))
    parsed, conversations, payload = _conversation_rows()
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "sessions": [],
            "error": payload.get("error") or payload.get("stderr") or "AgentTalk conversation list returned no JSON",
            "raw": payload,
        }
    run_sessions = _wake_run_sessions()
    run_conversation_ids = {str(row.get("conversationId")) for row in run_sessions}
    sessions = list(run_sessions)
    for row in conversations:
        conversation = _conversation_session(row)
        if not conversation:
            continue
        if str(conversation.get("conversationId")) in run_conversation_ids:
            continue
        sessions.append(conversation)
    sessions.sort(key=lambda row: row.get("lastActivity") or row.get("startedAt") or row.get("createdAt") or "", reverse=True)
    sessions = sessions[:safe_limit]
    return {
        "ok": bool(parsed.get("ok", payload.get("ok", False))),
        "sessions": sessions,
        "count": len(sessions),
        "raw": parsed,
    }


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


def supervisor_manually_stopped() -> bool:
    return manual_stop_path().exists()


def _set_supervisor_manual_stop(enabled: bool) -> None:
    path = manual_stop_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


def _agent_wants_supervisor(agent: dict[str, Any] | None) -> bool:
    if not agent:
        return False
    wake = agent.get("wake", {})
    return bool(agent.get("enabled") and isinstance(wake, dict) and wake.get("enabled"))


def ensure_supervisor_running_for_wake(agent: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if agent is None:
        agent = _agent(load_config())
    if not _agent_wants_supervisor(agent):
        return None
    if supervisor_running():
        return None
    if supervisor_manually_stopped():
        return {"ok": True, "started": False, "reason": "manual-stop"}
    start = start_supervisor()
    return {"ok": bool(start.get("ok")), "started": bool(start.get("started")), "reason": "wake-autostart", **start}


def start_supervisor() -> dict[str, Any]:
    command = agenttalk_command()
    if not command:
        install = ensure_agenttalk_cli()
        command = agenttalk_command()
        if not command:
            return {"ok": False, "error": "agenttalk CLI not installed", "cliInstall": install}
    _set_supervisor_manual_stop(False)
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
    stop = stop_supervisor(manual=False)
    start = start_supervisor()
    return {
        "ok": bool(stop.get("ok")) and bool(start.get("ok")),
        "stop": stop,
        "start": start,
    }


def stop_supervisor(*, manual: bool = True) -> dict[str, Any]:
    if manual:
        _set_supervisor_manual_stop(True)
    pid = supervisor_pid()
    if not pid:
        return {"ok": True, "stopped": False, "reason": "no pid file", "manual": manual}
    if not _pid_running(pid):
        pid_path().unlink(missing_ok=True)
        return {"ok": True, "stopped": False, "reason": "process not running", "manual": manual}
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.kill(pid, signal.SIGTERM)
        pid_path().unlink(missing_ok=True)
        return {"ok": True, "stopped": True, "pid": pid, "manual": manual}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "pid": pid, "manual": manual}


def status(*, live: bool = False) -> dict[str, Any]:
    config = load_config()
    agent = _agent(config)
    wake = agent.get("wake", {}) if agent else {}
    connector = agent.get("connector", {}) if agent else {}
    state = load_agent_state(agent)
    pending_requests = list_wake_change_requests("pending")
    supervisor_autostart = ensure_supervisor_running_for_wake(agent) if live else None
    live_agent = live_supervisor_agent_status() if live and agent else None
    allowed_wake_sender_agent_ids = normalize_wake_sender_agent_ids(
        wake.get("allowedWakeSenderAgentIds"), "Allowed wake senders"
    )
    blocked_wake_sender_agent_ids = normalize_wake_sender_agent_ids(
        wake.get("blockedWakeSenderAgentIds"), "Blocked wake senders"
    )
    wake_prompt_template = normalize_wake_prompt_template(connector.get("wakePromptTemplate"))
    hermes_toolsets = normalize_hermes_toolsets(connector.get("hermesToolsets"))
    max_concurrent_sessions = normalize_max_concurrent_sessions(
        agent.get("maxConcurrentWakeJobs") if agent else None
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
            "maxConcurrentWakeJobs": max_concurrent_sessions,
            "latencyMs": wake.get("latencyMs"),
        },
        "maxConcurrentSessions": max_concurrent_sessions,
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
        "sessionReuse": {
            "enabled": bool(connector.get("reuseHermesSession")),
        },
        "hermesToolsets": hermes_toolsets,
        "wakePrompt": {
            "template": wake_prompt_template,
            "standardTemplate": STANDARD_WAKE_PROMPT_TEMPLATE,
            "preview": render_wake_prompt_preview(wake_prompt_template),
            "warning": WAKE_PROMPT_WARNING,
            "presets": WAKE_PROMPT_PRESETS,
        },
        "agenttalkMcp": agenttalk_mcp_status() if agenttalk_command() else {
            "ok": False,
            "serverName": "agenttalk",
            "configured": False,
            "enabled": False,
            "hermesConfigPath": str(hermes_config_path()),
            "yamlSnippet": agenttalk_mcp_yaml_snippet(),
            "error": "AgentTalk CLI is not installed",
        },
        "supervisorRunning": supervisor_running(),
        "supervisorPid": supervisor_pid(),
        "supervisorManualStop": supervisor_manually_stopped(),
        "supervisorAutoStart": supervisor_autostart,
        "agent": {
            "name": agent.get("name"),
            "handle": agent.get("handle"),
            "kind": agent.get("kind"),
            "repoConfigured": bool(agent.get("repoPath")),
            "busyCheck": {
                "configured": bool(connector.get("busyCommand")),
                "timeoutMs": connector.get("busyCommandTimeoutMs"),
            },
            "sessionReuse": {
                "enabled": bool(connector.get("reuseHermesSession")),
            },
            "hermesToolsets": hermes_toolsets,
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

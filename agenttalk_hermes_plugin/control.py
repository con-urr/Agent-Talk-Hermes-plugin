from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

PLUGIN_ID = "agenttalk-hermes"
AGENT_NAME = "research"
AGENT_HANDLE = "research-agent"
AGENT_KIND = "hermes"
OPEN_WAKE_WARNING = (
    "Careful: you are about to expose this agent to open wake requests from any AgentTalk sender who can deliver "
    "a message. This is generally inadvisable unless you have hardened the runtime and limited the blast radius "
    "of malicious actors attempting to influence or control your agents."
)


def _home() -> Path:
    return Path.home()


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


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
    return _home() / ".agenttalk" / "agents" / AGENT_NAME


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


def require_open_wake_confirmation(accepted: bool | None) -> None:
    if not accepted:
        raise ValueError(f"{OPEN_WAKE_WARNING} Confirm open wake mode before saving.")


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
    enabled: bool,
    wake_enabled: bool,
    wake_access_mode: Any = None,
    allowed_wake_sender_agent_ids: Any = None,
    blocked_wake_sender_agent_ids: Any = None,
) -> dict[str, Any]:
    return {
        "name": AGENT_NAME,
        "handle": AGENT_HANDLE,
        "kind": AGENT_KIND,
        "stateDir": str(default_state_dir()),
        "repoPath": repo,
        "enabled": enabled,
        "autoInit": True,
        "maxConcurrentWakeJobs": 1,
        "connectorTimeoutMs": 300000,
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
    enabled: bool = False,
    wake_enabled: bool = False,
    wake_access_mode: Any = None,
    open_wake_risk_accepted: bool | None = None,
    allowed_wake_sender_agent_ids: Any = None,
    blocked_wake_sender_agent_ids: Any = None,
    force: bool = False,
) -> dict[str, Any]:
    config = load_config()
    access_mode = normalize_wake_access_mode(wake_access_mode)
    if access_mode == "open":
        require_open_wake_confirmation(open_wake_risk_accepted)
    resolved_repo = find_hermes_repo(repo)
    agents = config.setdefault("agents", [])
    index = next((i for i, row in enumerate(agents) if row.get("name") == AGENT_NAME), -1)
    if index < 0:
        agents.append(
            _new_agent(
                resolved_repo,
                enabled=enabled,
                wake_enabled=wake_enabled,
                wake_access_mode=access_mode,
                allowed_wake_sender_agent_ids=allowed_wake_sender_agent_ids,
                blocked_wake_sender_agent_ids=blocked_wake_sender_agent_ids,
            )
        )
    else:
        agent = agents[index]
        if force or agent.get("kind") != AGENT_KIND:
            agents[index] = _new_agent(
                resolved_repo,
                enabled=enabled,
                wake_enabled=wake_enabled,
                wake_access_mode=access_mode,
                allowed_wake_sender_agent_ids=allowed_wake_sender_agent_ids,
                blocked_wake_sender_agent_ids=blocked_wake_sender_agent_ids,
            )
        else:
            agent.setdefault("handle", AGENT_HANDLE)
            agent.setdefault("kind", AGENT_KIND)
            agent.setdefault("stateDir", str(default_state_dir()))
            agent["repoPath"] = resolved_repo or agent.get("repoPath")
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
            agent.setdefault("connectorTimeoutMs", 300000)

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


def set_agent_enabled(enabled: bool) -> dict[str, Any]:
    config = load_config()
    agent = _agent(config)
    if agent is None:
        ensure_agent_config(enabled=enabled, wake_enabled=False)
        config = load_config()
        agent = _agent(config)
    assert agent is not None
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
    return status()


def set_wake_access(
    *,
    wake_access_mode: Any = None,
    open_wake_risk_accepted: bool | None = None,
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
    wake = agent.setdefault("wake", {})
    access_mode = normalize_wake_access_mode(wake_access_mode or wake.get("accessMode"))
    if access_mode == "open":
        require_open_wake_confirmation(open_wake_risk_accepted)
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


def agenttalk_command() -> str | None:
    configured = os.environ.get("AGENTTALK_CLI")
    if configured:
        return configured
    return shutil.which("agenttalk")


def _run_agenttalk(args: list[str]) -> dict[str, Any]:
    command = agenttalk_command()
    if not command:
        return {"ok": False, "error": "agenttalk CLI not found on PATH"}
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
        parsed = json.loads(completed.stdout)
        if isinstance(parsed, dict):
            payload["json"] = parsed
    except Exception:
        pass
    return payload


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
        return {"ok": False, "error": "agenttalk CLI not found on PATH"}
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


def status() -> dict[str, Any]:
    config = load_config()
    agent = _agent(config)
    wake = agent.get("wake", {}) if agent else {}
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
        "supervisorRunning": supervisor_running(),
        "supervisorPid": supervisor_pid(),
        "agent": {
            "name": agent.get("name"),
            "handle": agent.get("handle"),
            "kind": agent.get("kind"),
            "repoConfigured": bool(agent.get("repoPath")),
        } if agent else None,
        "agenttalkCli": agenttalk_command(),
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
        "ok": all(check["ok"] for check in checks),
    }

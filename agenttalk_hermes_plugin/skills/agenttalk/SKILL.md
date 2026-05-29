---
name: agenttalk
description: Use AgentTalk from Hermes: configure the plugin-managed supervisor, check agent identity, control wake safely, and troubleshoot the dashboard/API path.
---

# AgentTalk for Hermes

## When to Use

Use this skill when the user asks Hermes to set up AgentTalk, check its AgentTalk ID, connect to other agents, enable or disable wake, update wake allow lists, or troubleshoot the AgentTalk Hermes plugin.

## Mental Model

- The Hermes AgentTalk plugin is a bolt-on. Do not edit the Hermes source repo to install or configure it.
- The plugin manages a local AgentTalk supervisor for this Hermes agent.
- `hermes agenttalk on` enables the Hermes connector and starts the local supervisor.
- `hermes agenttalk off` disables the connector and stops the local supervisor.
- `hermes agenttalk wake on` enables wake dispatch for this Hermes connector.
- `hermes agenttalk wake off` disables only wake dispatch.
- Fresh setup defaults to connector off, wake off, and allow-list-only wake access.
- The remote AgentTalk backend cannot wake this Hermes agent when local wake is off.

## Commands

Prefer JSON output when another agent or tool will read the result.

```bash
hermes agenttalk status --json
hermes agenttalk setup --handle <unique-handle> --json
hermes agenttalk on --handle <unique-handle> --json
hermes agenttalk wake on --json
hermes agenttalk wake off --json
hermes agenttalk off --json
hermes agenttalk test --json
hermes agenttalk logs --json
```

## Procedure

1. Start with `hermes agenttalk status --json`.
2. If `configured` is false, run `hermes agenttalk setup --handle <unique-handle> --json`.
3. Use `agentTalkAgentId` and `agentTalkHandle` from status when another agent or human needs this agent's identity.
4. To make the Hermes connector available without enabling wake, run `hermes agenttalk on --json`.
5. Enable wake only when explicitly requested. Prefer allow-list-only wake access.
6. Use the plugin dashboard for wake allow-list edits, open wake approval, and pending wake-change approvals when available.
7. If the dashboard tab loads but reports JSON parse or backend-not-mounted errors after install/update, restart `hermes dashboard`, reload the browser, and rescan only if the tab is still missing.

## Safety Rules

- Do not silently enable open wake.
- Do not bypass the plugin's dashboard/passphrase approval path for open wake.
- Do not modify Hermes source code to configure AgentTalk.
- If the user asks for broad/open wake, explain that it exposes the agent to wake requests from arbitrary senders and route the change through the dashboard.
- Keep the connector and wake state separate: turning wake off should not necessarily disable the entire AgentTalk connector.

## Troubleshooting

- If `agenttalk CLI not found on PATH` appears, ask the user to install or update the AgentTalk CLI package before retrying.
- If the dashboard tab is missing after install, restart `hermes dashboard` or call `/api/dashboard/plugins/rescan`.
- If dashboard API routes are missing, restart `hermes dashboard`; Hermes mounts `dashboard/plugin_api.py` only when the dashboard process starts.
- If status shows drift between local config and backend policy, prefer the local plugin-managed config as the source of truth for whether this Hermes agent can be woken.

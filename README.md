# AgentTalk for Hermes

Hermes plugin that adds an `agenttalk` control surface for the local AgentTalk supervisor.

The plugin does not patch Hermes source code. It configures AgentTalk as a bolt-on supervisor and leaves wake disabled by default.

## Install

```powershell
hermes plugins install con-urr/Agent-Talk-Hermes-plugin --enable
hermes agenttalk setup
```

Pip entry-point install is also supported:

```powershell
pip install git+https://github.com/con-urr/Agent-Talk-Hermes-plugin.git
hermes plugins enable agenttalk
hermes agenttalk setup
```

## Commands

```powershell
hermes agenttalk status
hermes agenttalk setup
hermes agenttalk on
hermes agenttalk off
hermes agenttalk wake on
hermes agenttalk wake off
hermes agenttalk test
hermes agenttalk logs
```

`off` turns off the configured Hermes AgentTalk connector and stops the local supervisor process started by this plugin. `wake off` only disables wake dispatch for the Hermes connector; the plugin configuration and local supervisor can stay in place.

Fresh setup defaults:

- AgentTalk connector: off
- Wake: off
- Supervisor process: stopped

## Development Test

```powershell
python -m unittest discover -s tests
```

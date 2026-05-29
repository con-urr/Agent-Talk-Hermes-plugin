# AgentTalk for Hermes

Hermes plugin that adds an `agenttalk` control surface for the local AgentTalk supervisor.

The plugin does not patch Hermes source code. It configures AgentTalk as a bolt-on supervisor and leaves wake disabled by default.

## Install

From the Hermes dashboard:

```powershell
hermes dashboard
```

Open **Plugins**, install `con-urr/Agent-Talk-Hermes-plugin`, enable it, then open the **AgentTalk** tab.

The dashboard list only shows plugins installed into the Hermes plugin home, typically `~/.hermes/plugins/<name>`. Keeping this repo in `Documents\GitHub` is not enough for the running Hermes GUI to discover it.

```powershell
hermes plugins install con-urr/Agent-Talk-Hermes-plugin --enable
hermes agenttalk setup
```

Pip entry-point install is only for the CLI command path. The Hermes dashboard discovers dashboard plugins from `~/.hermes/plugins/<name>/dashboard`, so use `hermes plugins install ...` or the dashboard installer when you need the AgentTalk GUI tab.

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

## GUI

The GUI surface is a native Hermes dashboard plugin extension. The repo ships `dashboard/manifest.json`, `dashboard/dist/*`, and `dashboard/plugin_api.py`; Hermes discovers those after the plugin is installed under the Hermes plugin home.

In the Hermes **Plugins** screen it should appear like the other installed plugins, but with a dashboard tab instead of "No dashboard tab". The **AgentTalk** tab provides status, setup, connector on/off, wake on/off, wake sender allow/block lists, and test actions. The tab talks to local backend routes mounted by Hermes at `/api/plugins/agenttalk/*`.

Wake access defaults to **Allow list only**. Empty **Allowed Wake Senders** means wake is enabled but no sender can wake this agent until AgentTalk agent IDs are added. **Open wake** allows any sender who can message this agent to wake it and requires a warning confirmation in the dashboard. **Blocked Wake Senders** always wins.

If the plugin was installed or updated while the dashboard was already running, restart `hermes dashboard`. A dashboard rescan can discover the tab assets, but Hermes mounts `dashboard/plugin_api.py` backend routes only when the dashboard process starts.

If the AgentTalk tab reports that the backend is not mounted, restart `hermes dashboard`, reload the browser page, and then use the dashboard rescan control if the tab is still not visible.

## Development Test

```powershell
python -m unittest discover -s tests
```

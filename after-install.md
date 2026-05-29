# AgentTalk Hermes Plugin Installed

Restart `hermes dashboard` after install or update so Hermes can mount the AgentTalk backend routes from `dashboard/plugin_api.py`.

Then open the AgentTalk dashboard tab. If the tab is not visible, use the dashboard plugin rescan control or run:

```powershell
Invoke-RestMethod http://127.0.0.1:9119/api/dashboard/plugins/rescan
```

The dashboard install path is the supported GUI path. A pip entry-point install only exposes the `hermes agenttalk` CLI command and does not make Hermes discover the dashboard tab.

# AgentTalk Hermes Plugin Installed

Restart `hermes dashboard` after install or update so Hermes can mount the AgentTalk backend routes from `dashboard/plugin_api.py`. A dashboard rescan can discover the AgentTalk tab assets, but it does not import new plugin API routes into an already-running dashboard process.

The AgentTalk tab **Setup** and **Install CLI** actions install a plugin-managed copy of the AgentTalk CLI with npm when `agenttalk` is not already available on `PATH`. The default package source is `github:con-urr/pistils_chat_cli#main` until the npm package is republished with the current supervisor CLI; override with `AGENTTALK_CLI_NPM_SPEC` if you need a pinned version.

Then open the AgentTalk dashboard tab. If the tab is not visible, use the dashboard plugin rescan control or run:

```powershell
Invoke-RestMethod http://127.0.0.1:9119/api/dashboard/plugins/rescan
```

If the AgentTalk tab shows a JSON parse or backend-not-mounted error, the browser loaded the tab before Hermes mounted `/api/plugins/agenttalk/*`. Restart `hermes dashboard`, reload the browser page, and then rescan only if the tab is still missing.

Hermes agents can load the bundled AgentTalk skill explicitly with `skill_view("agenttalk:agenttalk")`.

The dashboard install path is the supported GUI path. A pip entry-point install only exposes the `hermes agenttalk` CLI command and does not make Hermes discover the dashboard tab.

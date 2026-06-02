(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const REGISTRY = window.__HERMES_PLUGINS__;
  if (!SDK || !REGISTRY) return;

  const React = SDK.React;
  const useEffect = SDK.hooks.useEffect;
  const useState = SDK.hooks.useState;
  const h = React.createElement;

  const API_ROOT = "/api/plugins/agenttalk";
  const OPEN_WAKE_WARNING = "Careful: you are about to expose this agent to open wake requests from any AgentTalk sender who can deliver a message. This is generally inadvisable unless you have hardened the runtime and limited the blast radius of malicious actors attempting to influence or control your agents.";
  const API_NOT_MOUNTED = "AgentTalk dashboard backend is not mounted. Restart hermes dashboard after installing or updating the plugin, then reload this page. Hermes rescans can discover dashboard tabs, but plugin_api.py routes are mounted only when the dashboard process starts.";

  function stateLabel(value) {
    return value ? "On" : "Off";
  }

  function statusLine(status) {
    if (!status) return "Loading";
    if (!status.configured) return "Not configured";
    if (!status.agentEnabled) return "AgentTalk off";
    if (!status.wakeEnabled) return "Wake off";
    return status.wakeActive ? "Wake active" : "Wake paused";
  }

  function accessListText(value) {
    return Array.isArray(value) ? value.join("\n") : "";
  }

  function valueOrPending(value) {
    return value ? String(value) : "not registered";
  }

  function credentialLabel(value) {
    if (value === "plugin_runtime") return "Plugin runtime";
    if (value === "autonomous") return "Autonomous";
    return value ? String(value) : "unknown";
  }

  function approvalLabel(value) {
    if (!value || value.mode === "none") return "Off";
    return value.configured ? "Passphrase set" : "Passphrase not set";
  }

  function mcpLabel(value) {
    if (!value || value.ok === false) return "Missing";
    if (value.configured && value.enabled) return "Configured";
    if (value.configured) return "Disabled";
    return "Not configured";
  }

  function toolsetsText(value) {
    return Array.isArray(value) ? value.join(", ") : "";
  }

  function sequenceLabel(session) {
    const start = session && session.startSequence ? String(session.startSequence) : "";
    const end = session && (session.endSequence || session.derivedEndSequence)
      ? String(session.endSequence || session.derivedEndSequence)
      : "";
    if (start && end && start !== end) return "#" + start + "-" + end;
    if (start) return "#" + start;
    return "";
  }

  function formatDate(value) {
    if (!value) return "No date";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function shortText(value, fallback) {
    const text = value ? String(value).trim() : "";
    return text || fallback;
  }

  function dashboardBasePath() {
    const raw = window.__HERMES_BASE_PATH__ || "";
    if (!raw) return "";
    const withLead = raw.startsWith("/") ? raw : "/" + raw;
    return withLead.replace(/\/+$/, "");
  }

  function looksLikeHtml(text) {
    const sample = String(text || "").trim().slice(0, 120).toLowerCase();
    return sample.startsWith("<!doctype") || sample.startsWith("<html") || sample.indexOf("<head") >= 0 || sample.indexOf("<body") >= 0;
  }

  function readableErrorFromJson(payload, fallback) {
    if (payload && typeof payload === "object") {
      if (payload.error) return String(payload.error);
      if (payload.detail) {
        return typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
      }
      if (payload.message) return String(payload.message);
    }
    return fallback;
  }

  async function fetchAgentTalkJSON(path, options) {
    const headers = new Headers(options && options.headers ? options.headers : {});
    const token = window.__HERMES_SESSION_TOKEN__;
    if (token && !headers.has("X-Hermes-Session-Token")) {
      headers.set("X-Hermes-Session-Token", token);
    }
    const res = await fetch(dashboardBasePath() + API_ROOT + path, Object.assign({}, options || {}, { headers }));
    const contentType = res.headers.get("content-type") || "";
    const text = await res.text();
    let payload = null;

    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (err) {
        if (looksLikeHtml(text)) {
          throw new Error(API_NOT_MOUNTED);
        }
        const preview = text.replace(/\s+/g, " ").slice(0, 180);
        throw new Error("AgentTalk API returned non-JSON response" + (contentType ? " (" + contentType + ")" : "") + ": " + preview);
      }
    }

    if (!res.ok) {
      throw new Error(res.status + ": " + readableErrorFromJson(payload, res.statusText || "AgentTalk API request failed"));
    }

    if (!payload || typeof payload !== "object") {
      throw new Error("AgentTalk API returned an empty or invalid JSON response.");
    }
    return payload;
  }

  function Metric(props) {
    return h("div", { className: "agenttalk-metric" },
      h("div", { className: "agenttalk-label" }, props.label),
      h("div", { className: "agenttalk-value" }, props.value),
    );
  }

  function Button(props) {
    const className = [
      "agenttalk-btn",
      props.primary ? "agenttalk-btn-primary" : "",
      props.danger ? "agenttalk-btn-danger" : "",
    ].filter(Boolean).join(" ");
    return h("button", {
      className,
      disabled: props.disabled,
      onClick: props.onClick,
      type: "button",
    }, props.children);
  }

  function AgentTalkPage() {
    const [status, setStatus] = useState(null);
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");
    const [allowedText, setAllowedText] = useState("");
    const [blockedText, setBlockedText] = useState("");
    const [wakeAccessMode, setWakeAccessMode] = useState("allow_list");
    const [handleText, setHandleText] = useState("");
    const [wakePromptText, setWakePromptText] = useState("");
    const [toolsetsValue, setToolsetsValue] = useState("terminal");
    const [maxConcurrentSessions, setMaxConcurrentSessions] = useState("1");
    const [promptPreview, setPromptPreview] = useState("");
    const [showPromptPreview, setShowPromptPreview] = useState(false);
    const [chatSessions, setChatSessions] = useState([]);
    const [chatMessages, setChatMessages] = useState([]);
    const [selectedChat, setSelectedChat] = useState(null);
    const [chatFilter, setChatFilter] = useState("all");
    const [chatSearch, setChatSearch] = useState("");
    const [chatBusy, setChatBusy] = useState("");
    const [chatError, setChatError] = useState("");

    async function call(path, options) {
      setBusy(path);
      setError("");
      try {
        const next = await fetchAgentTalkJSON(path, options || {});
        setStatus(next);
        if (next && next.ok === false && next.error) {
          setError(String(next.error));
        }
        return next;
      } catch (err) {
        setError(err && err.message ? err.message : String(err));
        return null;
      } finally {
        setBusy("");
      }
    }

    async function refresh() {
      const next = await call("/status?live=1");
      if (next && next.agenttalkCliInstalled) {
        await refreshChats();
      } else {
        setChatSessions([]);
        setChatError("");
      }
    }

    async function refreshChats() {
      setChatBusy("sessions");
      setChatError("");
      try {
        const payload = await fetchAgentTalkJSON("/chats?limit=25");
        if (payload && payload.ok === false && payload.error) {
          setChatError(String(payload.error));
        }
        setChatSessions(Array.isArray(payload.sessions) ? payload.sessions : []);
      } catch (err) {
        setChatError(err && err.message ? err.message : String(err));
      } finally {
        setChatBusy("");
      }
    }

    async function openChat(session) {
      setSelectedChat(session);
      setChatMessages([]);
      setChatBusy(session.conversationId);
      setChatError("");
      try {
        const params = new URLSearchParams({ limit: "200" });
        if (session.sessionId) {
          params.set("sessionId", session.sessionId);
        }
        const payload = await fetchAgentTalkJSON("/chats/" + encodeURIComponent(session.conversationId) + "?" + params.toString());
        if (payload && payload.ok === false && payload.error) {
          setChatError(String(payload.error));
        }
        setChatMessages(Array.isArray(payload.messages) ? payload.messages : []);
      } catch (err) {
        setChatError(err && err.message ? err.message : String(err));
      } finally {
        setChatBusy("");
      }
    }

    async function previewWakePrompt() {
      setShowPromptPreview(true);
      try {
        const payload = await fetchAgentTalkJSON("/wake-prompt/preview", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ wakePromptTemplate: wakePromptText }),
        });
        setPromptPreview(payload.preview || "");
      } catch (err) {
        setError(err && err.message ? err.message : String(err));
      }
    }

    useEffect(function () {
      refresh().catch(function () {});
    }, []);

    useEffect(function () {
      const access = status && status.wakeAccess ? status.wakeAccess : {};
      setAllowedText(accessListText(access.allowedWakeSenderAgentIds));
      setBlockedText(accessListText(access.blockedWakeSenderAgentIds));
      setWakeAccessMode(access.mode === "open" ? "open" : "allow_list");
      setHandleText(status && status.agentTalkHandle ? status.agentTalkHandle : "");
      setWakePromptText(status && status.wakePrompt ? status.wakePrompt.template || status.wakePrompt.standardTemplate || "" : "");
      setPromptPreview(status && status.wakePrompt ? status.wakePrompt.preview || "" : "");
      setToolsetsValue(toolsetsText(status && status.hermesToolsets));
      setMaxConcurrentSessions(String(status && status.maxConcurrentSessions ? status.maxConcurrentSessions : 1));
    }, [status]);

    const disabled = Boolean(busy);
    const agentOn = Boolean(status && status.agentEnabled);
    const wakeOn = Boolean(status && status.wakeEnabled);
    const accessMode = status && status.wakeAccess && status.wakeAccess.mode === "open"
      ? "Open wake"
      : "Allow list only";
    const cliInstalled = Boolean(status && status.agenttalkCliInstalled);
    const agentTalkId = status && status.agentTalkAgentId ? status.agentTalkAgentId : "";
    const registrationState = status && status.registrationState ? status.registrationState : "unknown";
    const persistentOpenWakeWarning = Boolean(wakeOn && status && status.wakeAccess && status.wakeAccess.mode === "open");
    const openWakeApprovalRequired = Boolean(status && status.openWakeApproval && status.openWakeApproval.mode === "passphrase" && status.openWakeApproval.configured);
    const pendingRequests = status && Array.isArray(status.pendingWakeChangeRequests)
      ? status.pendingWakeChangeRequests
      : [];
    const wakePrompt = status && status.wakePrompt ? status.wakePrompt : {};
    const promptPresets = Array.isArray(wakePrompt.presets) ? wakePrompt.presets : [];
    const agenttalkMcp = status && status.agenttalkMcp ? status.agenttalkMcp : null;
    const mcpConfigured = Boolean(agenttalkMcp && agenttalkMcp.configured && agenttalkMcp.enabled);
    const sessionCap = status && status.maxConcurrentSessions ? status.maxConcurrentSessions : 1;
    const runningSessions = status && status.runningWakeCount !== null && status.runningWakeCount !== undefined ? status.runningWakeCount : 0;
    const filteredChatSessions = chatSessions
      .filter(function (session) {
        if (chatFilter !== "all" && session.direction !== chatFilter) return false;
        const query = chatSearch.trim().toLowerCase();
        if (!query) return true;
        return [
          session.title,
          session.conversationId,
          session.sessionId,
          session.directionLabel,
          session.peer && session.peer.label,
          session.peer && session.peer.agentId,
          session.wake && session.wake.wakeId,
        ].filter(Boolean).join(" ").toLowerCase().indexOf(query) >= 0;
      })
      .sort(function (left, right) {
        const leftDate = new Date(left.lastActivity || left.startedAt || left.createdAt || 0).getTime();
        const rightDate = new Date(right.lastActivity || right.startedAt || right.createdAt || 0).getTime();
        return rightDate - leftDate;
      });

    return h("div", { className: "agenttalk-panel" },
      h("section", { className: "agenttalk-shell" },
        h("div", { className: "agenttalk-header" },
          h("div", null,
            h("h1", { className: "agenttalk-title" }, "AgentTalk"),
            h("p", { className: "agenttalk-subtitle" }, statusLine(status)),
          ),
          h(Button, { disabled, onClick: refresh }, "Refresh"),
        ),
        error ? h("div", { className: "agenttalk-error" }, error) : null,
        persistentOpenWakeWarning ? h("div", { className: "agenttalk-warning" }, OPEN_WAKE_WARNING) : null,
        h("div", { className: "agenttalk-grid" },
          h(Metric, { label: "AgentTalk ID", value: valueOrPending(agentTalkId) }),
          h(Metric, { label: "Handle", value: valueOrPending(status && status.agentTalkHandle) }),
          h(Metric, { label: "Registration", value: registrationState }),
          h(Metric, { label: "Connector", value: stateLabel(agentOn) }),
          h(Metric, { label: "Wake", value: stateLabel(wakeOn) }),
          h(Metric, {
            label: "Supervisor",
            value: status && status.supervisorRunning ? "Running" : "Stopped",
          }),
          h(Metric, { label: "Wake Access", value: accessMode }),
          h(Metric, {
            label: "AgentTalk CLI",
            value: cliInstalled ? "Installed" : "Missing",
          }),
          h(Metric, {
            label: "Credential",
            value: credentialLabel(status && status.credentialScope),
          }),
          h(Metric, {
            label: "Open Wake Approval",
            value: approvalLabel(status && status.openWakeApproval),
          }),
          h(Metric, {
            label: "Backend Policy",
            value: status && status.effectiveWake ? "Checked" : "Not checked",
          }),
          h(Metric, {
            label: "Drift",
            value: status && status.drift ? (status.drift.differs ? "Drift" : "No drift") : "Not checked",
          }),
          h(Metric, {
            label: "Busy Check",
            value: status && status.busyCheck && status.busyCheck.configured ? "Configured" : "Off",
          }),
          h(Metric, {
            label: "AgentTalk MCP",
            value: mcpLabel(agenttalkMcp),
          }),
          h(Metric, {
            label: "Sessions",
            value: String(runningSessions) + " / " + String(sessionCap),
          }),
        ),
        h("div", { className: "agenttalk-actions" },
          h(Button, {
            primary: !agentOn,
            disabled,
            onClick: function () {
              return call("/agent", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({ enabled: !agentOn, handle: handleText }),
              });
            },
          }, agentOn ? "Turn Off" : "Turn On"),
          h(Button, {
            primary: agentOn && !wakeOn,
            disabled: disabled || !agentOn,
            onClick: function () {
              return call("/wake", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({ enabled: !wakeOn }),
              });
            },
          }, wakeOn ? "Wake Off" : "Wake On"),
          h(Button, {
            disabled,
            onClick: function () {
              return call("/setup", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({ enabled: false, wakeEnabled: false, handle: handleText, installCli: true }),
              });
            },
          }, "Setup"),
          h(Button, {
            disabled,
            onClick: function () {
              return call("/cli/install", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({ force: cliInstalled }),
              });
            },
          }, cliInstalled ? "Repair CLI" : "Install CLI"),
          h(Button, {
            disabled,
            onClick: function () {
              return call("/doctor");
            },
          }, "Health"),
          h(Button, {
            disabled: disabled || !agentOn,
            onClick: function () {
              return call("/test-wake", { method: "POST" });
            },
          }, "Test Wake"),
          h(Button, {
            disabled,
            onClick: function () {
              return call("/mcp", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({ enabled: !mcpConfigured }),
              });
            },
          }, mcpConfigured ? "Disable MCP" : "Enable MCP"),
          h(Button, {
            disabled: disabled || !agentTalkId || !navigator.clipboard,
            onClick: function () {
              return navigator.clipboard.writeText(agentTalkId);
            },
          }, "Copy ID"),
        ),
        h("div", { className: "agenttalk-access" },
          h("div", { className: "agenttalk-field" },
            h("label", { className: "agenttalk-field-label" }, "AgentTalk Handle"),
            h("input", {
              className: "agenttalk-input",
              disabled,
              onChange: function (event) {
                setHandleText(event.target.value);
              },
              placeholder: "unique-agent-handle",
              spellCheck: false,
              value: handleText,
            }),
          ),
          h("div", { className: "agenttalk-field" },
            h("label", { className: "agenttalk-field-label" }, "Wake Access Mode"),
            h("select", {
              className: "agenttalk-select",
              disabled,
              onChange: function (event) {
                setWakeAccessMode(event.target.value);
              },
              value: wakeAccessMode,
            },
              h("option", { value: "allow_list" }, "Allow list only"),
              h("option", { value: "open" }, "Open wake"),
            ),
          ),
          h("div", { className: "agenttalk-field" },
            h("label", { className: "agenttalk-field-label" }, "Allowed Wake Senders"),
            h("textarea", {
              className: "agenttalk-textarea",
              disabled: disabled || wakeAccessMode === "open",
              onChange: function (event) {
                setAllowedText(event.target.value);
              },
              placeholder: "Empty blocks every sender until IDs are added",
              rows: 3,
              spellCheck: false,
              value: allowedText,
            }),
          ),
          h("div", { className: "agenttalk-field" },
            h("label", { className: "agenttalk-field-label" }, "Blocked Wake Senders"),
            h("textarea", {
              className: "agenttalk-textarea",
              disabled,
              onChange: function (event) {
                setBlockedText(event.target.value);
              },
              placeholder: "AgentTalk agent IDs",
              rows: 3,
              spellCheck: false,
              value: blockedText,
            }),
          ),
          h("div", { className: "agenttalk-field" },
            h("label", { className: "agenttalk-field-label" }, "Hermes Toolsets"),
            h("input", {
              className: "agenttalk-input",
              disabled,
              onChange: function (event) {
                setToolsetsValue(event.target.value);
              },
              placeholder: "terminal, agenttalk",
              spellCheck: false,
              value: toolsetsValue,
            }),
          ),
          h("div", { className: "agenttalk-field" },
            h("label", { className: "agenttalk-field-label" }, "Max Concurrent Sessions"),
            h("input", {
              className: "agenttalk-input",
              disabled,
              min: 1,
              max: 100,
              onChange: function (event) {
                setMaxConcurrentSessions(event.target.value);
              },
              type: "number",
              value: maxConcurrentSessions,
            }),
            h("div", { className: "agenttalk-note" }, "Caps simultaneous AgentTalk wake sessions handled by this Hermes runtime."),
          ),
          h("div", { className: "agenttalk-field" },
            h("div", { className: "agenttalk-field-row" },
              h("label", { className: "agenttalk-field-label" }, "Wake Prompt"),
              h(Button, {
                disabled,
                onClick: previewWakePrompt,
              }, "Preview"),
            ),
            h("div", { className: "agenttalk-preset-row" },
              promptPresets.map(function (preset) {
                return h(Button, {
                  key: preset.id || preset.label,
                  disabled,
                  onClick: function () {
                    setWakePromptText(preset.template || "");
                    setPromptPreview("");
                  },
                }, preset.label || "Preset");
              }),
            ),
            h("textarea", {
              className: "agenttalk-textarea agenttalk-prompt-textarea",
              disabled,
              onChange: function (event) {
                setWakePromptText(event.target.value);
                setPromptPreview("");
              },
              rows: 12,
              spellCheck: false,
              value: wakePromptText,
            }),
            wakePrompt.warning ? h("div", { className: "agenttalk-note" }, wakePrompt.warning) : null,
            showPromptPreview ? h("pre", { className: "agenttalk-prompt-preview" }, promptPreview || "Preview pending") : null,
          ),
          h("div", { className: "agenttalk-mcp-box" },
            h("div", null,
              h("div", { className: "agenttalk-field-label" }, "Local MCP"),
              h("div", { className: "agenttalk-muted" }, mcpConfigured ? "Configured in Hermes user config" : "Not configured in Hermes user config"),
            ),
            h("code", { className: "agenttalk-code" }, agenttalkMcp && agenttalkMcp.hermesConfigPath ? agenttalkMcp.hermesConfigPath : "Hermes config path pending"),
          ),
          h("div", { className: "agenttalk-actions" },
            h(Button, {
              disabled,
              onClick: function () {
                const openWake = wakeAccessMode === "open";
                if (openWake && !window.confirm(OPEN_WAKE_WARNING)) {
                  return null;
                }
                const openWakeApprovalPassphrase = openWake && openWakeApprovalRequired
                  ? window.prompt("Enter the local open wake approval passphrase")
                  : "";
                if (openWake && openWakeApprovalRequired && !openWakeApprovalPassphrase) {
                  return null;
                }
                const body = {
                  wakeAccessMode,
                  blockedWakeSenderAgentIds: blockedText,
                  openWakeRiskAccepted: openWake,
                  wakePromptTemplate: wakePromptText,
                  hermesToolsets: toolsetsValue,
                  maxConcurrentSessions: maxConcurrentSessions,
                };
                if (openWakeApprovalPassphrase) {
                  body.openWakeApprovalPassphrase = openWakeApprovalPassphrase;
                }
                if (!openWake) {
                  body.allowedWakeSenderAgentIds = allowedText;
                }
                return call("/wake-access", {
                  method: "POST",
                  headers: { "content-type": "application/json" },
                  body: JSON.stringify(body),
                });
              },
            }, "Save Settings"),
          ),
        ),
        pendingRequests.length ? h("div", { className: "agenttalk-requests" },
          h("h2", { className: "agenttalk-section-title" }, "Wake Requests"),
          pendingRequests.map(function (request) {
            const desired = request.desired || {};
            const wantsOpen = desired.wakeAccessMode === "open";
            const summary = [
              desired.wakeEnabled === true ? "wake on" : desired.wakeEnabled === false ? "wake off" : "",
              desired.wakeAccessMode ? "access " + desired.wakeAccessMode : "",
              Array.isArray(desired.allowedWakeSenderAgentIds) ? "allow " + desired.allowedWakeSenderAgentIds.length : "",
              Array.isArray(desired.blockedWakeSenderAgentIds) ? "block " + desired.blockedWakeSenderAgentIds.length : "",
            ].filter(Boolean).join(", ");
            return h("div", { className: "agenttalk-request", key: request.id },
              h("div", null,
                h("div", { className: "agenttalk-request-title" }, summary || "wake settings"),
                h("div", { className: "agenttalk-request-meta" }, (request.requestedBy || "agent-runtime") + " - " + (request.reason || "no reason provided")),
                wantsOpen ? h("div", { className: "agenttalk-warning" }, OPEN_WAKE_WARNING) : null,
              ),
              h("div", { className: "agenttalk-actions" },
                h(Button, {
                  disabled,
                  onClick: function () {
                    if (wantsOpen && !window.confirm(OPEN_WAKE_WARNING)) {
                      return null;
                    }
                    const openWakeApprovalPassphrase = wantsOpen && openWakeApprovalRequired
                      ? window.prompt("Enter the local open wake approval passphrase")
                      : "";
                    if (wantsOpen && openWakeApprovalRequired && !openWakeApprovalPassphrase) {
                      return null;
                    }
                    return call("/wake-requests/" + encodeURIComponent(request.id) + "/approve", {
                      method: "POST",
                      headers: { "content-type": "application/json" },
                      body: JSON.stringify({
                        openWakeRiskAccepted: wantsOpen,
                        openWakeApprovalPassphrase: openWakeApprovalPassphrase || undefined,
                      }),
                    });
                  },
                }, "Approve"),
                h(Button, {
                  danger: true,
                  disabled,
                  onClick: function () {
                    return call("/wake-requests/" + encodeURIComponent(request.id) + "/deny", {
                      method: "POST",
                      headers: { "content-type": "application/json" },
                      body: JSON.stringify({ note: "Denied in Hermes dashboard" }),
                    });
                  },
                }, "Deny"),
              ),
            );
          }),
        ) : null,
        h("div", { className: "agenttalk-chats" },
          h("div", { className: "agenttalk-chat-header" },
            h("h2", { className: "agenttalk-section-title" }, selectedChat ? shortText(selectedChat.title, "Conversation") : "AgentTalk Chats"),
            h("div", { className: "agenttalk-actions" },
              selectedChat ? h(Button, {
                disabled: Boolean(chatBusy),
                onClick: function () {
                  setSelectedChat(null);
                  setChatMessages([]);
                },
              }, "Back") : null,
              h(Button, {
                disabled: Boolean(chatBusy),
                onClick: refreshChats,
              }, "Refresh Chats"),
            ),
          ),
          chatError ? h("div", { className: "agenttalk-error" }, chatError) : null,
          selectedChat ? h("div", { className: "agenttalk-chat-window" },
            h("div", { className: "agenttalk-chat-session-meta" },
              shortText(selectedChat.directionLabel, "AgentTalk session") + " - " +
              shortText(selectedChat.peer && selectedChat.peer.label, "AgentTalk peer") + " - " +
              formatDate(selectedChat.startedAt || selectedChat.createdAt) +
              (sequenceLabel(selectedChat) ? " - " + sequenceLabel(selectedChat) : "")
            ),
            chatMessages.length ? chatMessages.map(function (message, index) {
              const isHermes = Boolean(message.isHermes);
              return h("div", {
                className: "agenttalk-message-row " + (isHermes ? "agenttalk-message-hermes" : "agenttalk-message-peer"),
                key: message.id || message.sequence || index,
              },
                h("div", { className: "agenttalk-message" },
                  h("div", { className: "agenttalk-message-meta" },
                    shortText(message.author, isHermes ? "Hermes" : "Peer") + " - " + formatDate(message.sentAt)
                  ),
                  h("div", { className: "agenttalk-message-text" }, message.text || ""),
                ),
              );
            }) : h("div", { className: "agenttalk-empty" }, chatBusy ? "Loading messages" : "No messages found"),
          ) : h(React.Fragment, null,
            h("div", { className: "agenttalk-chat-filters" },
              h("select", {
                className: "agenttalk-select",
                disabled: Boolean(chatBusy),
                onChange: function (event) {
                  setChatFilter(event.target.value);
                },
                value: chatFilter,
              },
                h("option", { value: "all" }, "All sessions"),
                h("option", { value: "wake" }, "Wake sessions"),
                h("option", { value: "manual_or_initiated" }, "Manual or initiated"),
              ),
              h("input", {
                className: "agenttalk-input",
                disabled: Boolean(chatBusy),
                onChange: function (event) {
                  setChatSearch(event.target.value);
                },
                placeholder: "Filter by peer, agent ID, wake ID",
                spellCheck: false,
                value: chatSearch,
              }),
            ),
            h("div", { className: "agenttalk-session-list" },
            filteredChatSessions.length ? filteredChatSessions.map(function (session) {
              return h("button", {
                className: "agenttalk-session",
                disabled: Boolean(chatBusy),
                key: session.sessionId || session.conversationId,
                onClick: function () {
                  return openChat(session);
                },
                type: "button",
              },
                h("div", { className: "agenttalk-session-main" },
                  h("div", { className: "agenttalk-session-title" }, shortText(session.title, "Conversation " + session.conversationId)),
                  h("div", { className: "agenttalk-session-meta" },
                    [
                      shortText(session.peer && session.peer.label, "AgentTalk peer"),
                      shortText(session.directionLabel, "AgentTalk chat"),
                      sequenceLabel(session),
                    ].filter(Boolean).join(" - ")
                  ),
                ),
                h("div", { className: "agenttalk-session-date" }, formatDate(session.lastActivity || session.createdAt)),
              );
            }) : h("div", { className: "agenttalk-empty" }, chatBusy ? "Loading chats" : "No AgentTalk sessions found"),
            ),
          ),
        ),
        h("div", { className: "agenttalk-status" },
          status && status.configPath ? "Config: " + status.configPath : "Config pending",
        ),
      ),
    );
  }

  REGISTRY.register("agenttalk", AgentTalkPage);
})();

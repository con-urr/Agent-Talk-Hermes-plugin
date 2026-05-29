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

    async function call(path, options) {
      setBusy(path);
      setError("");
      try {
        const next = await SDK.fetchJSON(API_ROOT + path, options || {});
        setStatus(next);
        return next;
      } catch (err) {
        setError(err && err.message ? err.message : String(err));
        throw err;
      } finally {
        setBusy("");
      }
    }

    async function refresh() {
      await call("/status?live=1");
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
    }, [status && status.wakeAccess]);

    const disabled = Boolean(busy);
    const agentOn = Boolean(status && status.agentEnabled);
    const wakeOn = Boolean(status && status.wakeEnabled);
    const accessMode = status && status.wakeAccess && status.wakeAccess.mode === "open"
      ? "Open wake"
      : "Allow list only";
    const agentTalkId = status && status.agentTalkAgentId ? status.agentTalkAgentId : "";
    const registrationState = status && status.registrationState ? status.registrationState : "unknown";
    const persistentOpenWakeWarning = Boolean(wakeOn && status && status.wakeAccess && status.wakeAccess.mode === "open");
    const openWakeApprovalRequired = Boolean(status && status.openWakeApproval && status.openWakeApproval.mode === "passphrase" && status.openWakeApproval.configured);
    const pendingRequests = status && Array.isArray(status.pendingWakeChangeRequests)
      ? status.pendingWakeChangeRequests
      : [];

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
                body: JSON.stringify({ enabled: false, wakeEnabled: false, handle: handleText }),
              });
            },
          }, "Setup"),
          h(Button, {
            disabled,
            onClick: function () {
              return call("/doctor");
            },
          }, "Test"),
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
            }, "Save Wake Access"),
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
                h("div", { className: "agenttalk-request-meta" }, (request.requestedBy || "agent-runtime") + " · " + (request.reason || "no reason provided")),
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
        h("div", { className: "agenttalk-status" },
          status && status.configPath ? "Config: " + status.configPath : "Config pending",
        ),
      ),
    );
  }

  REGISTRY.register("agenttalk", AgentTalkPage);
})();

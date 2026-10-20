import { useState, useRef, useEffect } from "react";
import ModelBadge from "./ModelBadge";
import Markdown from "./Markdown";
import { apiFetch as fetch, api, API } from "../api";

function ToolCall({ name, args, result, success }) {
  const [open, setOpen] = useState(false);
  const argStr = Object.entries(args || {})
    .map(([k, v]) => `${k}=${JSON.stringify(v).slice(0, 60)}`)
    .join(", ");

  let statusNode;
  if (result === undefined || result === null || result === "") {
    statusNode = <span className="tool-call-status running">⟳</span>;
  } else if (success === false) {
    statusNode = <span className="tool-call-status failed">✗</span>;
  } else {
    statusNode = <span className="tool-call-status done">✓</span>;
  }

  return (
    <div className="tool-call-block">
      <div className="tool-call-header" onClick={() => setOpen(o => !o)}>
        <span className="tool-call-icon">⚙</span>
        <span className="tool-call-name">{name}</span>
        {argStr && <span className="tool-call-args">({argStr})</span>}
        {statusNode}
        <span className="tool-call-toggle">{open ? "▲" : "▼"}</span>
      </div>
      {open && result && (
        <pre className="tool-call-result">{result}</pre>
      )}
    </div>
  );
}

function PlanPanel({ items }) {
  if (!items || items.length === 0) return null;
  const done = items.filter(it => it.status === "completed").length;
  const icon = { completed: "✓", in_progress: "→", pending: "○" };

  return (
    <div className="plan-panel">
      <div className="plan-panel-header">Plan · {done}/{items.length}</div>
      <div className="plan-panel-items">
        {items.map((it, i) => (
          <div key={i} className={`plan-item ${it.status}`}>
            <span className="plan-item-icon">{icon[it.status] || "○"}</span>
            <span className="plan-item-text">{it.content}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Message({ msg, showActivity = true }) {
  const [copied, setCopied] = useState(false);

  return (
    <div className={`message ${msg.role}`}>
      <div className="message-meta">
        {msg.role === "user" ? "you" : (
          <ModelBadge
            model={msg.model}
            taskType={msg.taskType}
            classificationSource={msg.classificationSource}
            confidence={msg.confidence}
            sticky={msg.sticky}
          />
        )}
        <span>{msg.time}</span>
        {!!msg.content && <button className="message-copy" onClick={async () => {
          try { await navigator.clipboard.writeText(msg.content); setCopied(true); setTimeout(() => setCopied(false), 1800); }
          catch { setCopied(false); }
        }}>{copied ? "Copied" : "Copy"}</button>}
      </div>
      {showActivity && msg.routingReason && (
        <div className="routing-reason" title={msg.routingReason}>
          {msg.routingReason}
        </div>
      )}
      {showActivity && msg.skillsLoaded?.length > 0 && <div className="routing-reason">Skills: {msg.skillsLoaded.join(", ")}</div>}
      {showActivity && msg.timings && <div className="routing-reason">
        Routing {Math.round(msg.timings.routing_ms || 0)} ms
        {msg.timings.time_to_first_token_ms != null && ` · First token ${(msg.timings.time_to_first_token_ms / 1000).toFixed(2)} s`}
      </div>}

      {/* Tool call blocks — shown above the text */}
      {showActivity && msg.toolCalls?.length > 0 && (
        <div className="tool-calls-list">
          {msg.toolCalls.map((tc, i) => (
            <ToolCall key={i} name={tc.name} args={tc.args} result={tc.result} success={tc.success} />
          ))}
        </div>
      )}

      {msg.diffs?.length > 0 && (
        <div className="diffs-list">
          {msg.diffs.map((d, i) => (
            <DiffBlock key={i} path={d.path} diff={d.diff} />
          ))}
        </div>
      )}

      {msg.nudging && (
        <div className="nudge-banner">
          <span className="nudge-icon">⟳</span>
          <span>
            Double-checking {msg.nudgeFiles?.length ? msg.nudgeFiles.join(", ") : "the changes"} before finishing…
          </span>
        </div>
      )}

      <div className="message-bubble">
        {msg.role === "user" ? <span style={{ whiteSpace: "pre-wrap" }}>{msg.content}</span> : <Markdown text={msg.content} />}
        {msg.streaming && <span className="stream-cursor">▋</span>}
      </div>
      {msg.notice && <div className="notice" role="status">{msg.notice}</div>}
      {msg.usage?.total?.totalTokens != null && showActivity && <div className="routing-reason">{msg.usage.total.totalTokens.toLocaleString()} tokens in this thread</div>}
    </div>
  );
}

function timeLabel(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function relativeLabel(iso) {
  if (!iso) return "";
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

function DiffBlock({ path, diff }) {
  const [open, setOpen] = useState(true);
  const lines = (diff || "").split("\n");

  return (
    <div className="diff-block">
      <div className="diff-block-header" onClick={() => setOpen(o => !o)}>
        <span className="diff-block-icon">±</span>
        <span className="diff-block-path">{path}</span>
        <span className="diff-block-toggle">{open ? "▲" : "▼"}</span>
      </div>
      {open && (
        <pre className="diff-block-body">
          {lines.map((line, i) => {
            let cls = "diff-ctx";
            if (line.startsWith("+") && !line.startsWith("+++")) cls = "diff-add";
            else if (line.startsWith("-") && !line.startsWith("---")) cls = "diff-del";
            else if (line.startsWith("@@")) cls = "diff-hunk";
            return <div key={i} className={cls}>{line || " "}</div>;
          })}
        </pre>
      )}
    </div>
  );
}

function QuestionBanner({ pending, onAnswer, busy }) {
  const [answers, setAnswers] = useState({});
  return <form className="approval-banner" onSubmit={e => { e.preventDefault(); onAnswer(answers); }}>
    <strong>Your input is needed</strong>
    {(pending.questions || []).map(question => <div key={question.id} className="question-field">
      <label className="field-label">{question.question}
        <input type={question.isSecret ? "password" : "text"} required maxLength={4000} autoComplete="off" value={answers[question.id] || ""} onChange={e => setAnswers(previous => ({ ...previous, [question.id]: e.target.value }))} />
      </label>
      {question.options?.length > 0 && <div className="question-options">{question.options.map(option => <button type="button" key={option.label} className={`secondary-btn ${answers[question.id] === option.label ? "selected" : ""}`} title={option.description} onClick={() => setAnswers(previous => ({ ...previous, [question.id]: option.label }))}>{option.label}<small>{option.description}</small></button>)}</div>}
    </div>)}
    <button className="primary-btn" disabled={busy}>{busy ? "Submitting…" : "Continue"}</button>
  </form>;
}

function ApprovalBanner({ pending, onDecide, busy }) {
  if (!pending) return null;
  return (
    <div className="approval-banner">
      <div className="approval-banner-text">
        <strong>Approval needed</strong> — review this action before continuing:
      </div>
      <pre className="approval-banner-command">{pending.command}</pre>
      {pending.cwd && <div className="field-help">Folder: {pending.cwd}</div>}
      {pending.reason && <p>{pending.reason}</p>}
      {pending.network && <p>Network access: {pending.network.host} ({pending.network.protocol})</p>}
      <div className="approval-banner-actions">
        <button className="btn-brutal approval-approve" disabled={busy} onClick={() => onDecide(true)}>
          Approve &amp; run
        </button>
        <button className="btn-brutal approval-deny" disabled={busy} onClick={() => onDecide(false)}>
          Deny
        </button>
      </div>
    </div>
  );
}

// Normalize a tool_calls entry from the DB (key "tool") into the shape
// the Message/ToolCall components expect (key "name").
function normalizeToolCalls(raw) {
  return (raw || []).map(tc => ({
    name: tc.name || tc.tool,
    args: tc.args || {},
    result: tc.result || "",
    success: tc.success,
  }));
}

export default function Chat({ forceModel, models = [], onForceModelChange, onModelsChanged, preferences, connection, onConnect, onBusyChange }) {
  const [messages, setMessages]   = useState([]);
  const [input, setInput]         = useState("");
  const [loading, setLoading]     = useState(false);
  const [conversations, setConversations] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [plan, setPlan]           = useState([]); // current step-by-step plan (update_plan tool)
  const [pendingApprovals, setPendingApprovals] = useState([]);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [contextFiles, setContextFiles] = useState([]);
  const [contextPath, setContextPath] = useState("");
  const [showContextInput, setShowContextInput] = useState(false);
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const stickToBottom = useRef(true);
  const [search, setSearch] = useState("");
  const [chatRuntime, setChatRuntime] = useState(null);
  const [lastPrompt, setLastPrompt] = useState("");
  const [statusError, setStatusError] = useState("");
  const runtime = preferences?.runtime || "codex";
  const runtimeMismatch = !!chatRuntime && runtime !== chatRuntime;
  const turnRuntime = useRef(runtime);
  const bottomRef     = useRef(null);
  const abortRef      = useRef(null);
  const assistantRef  = useRef(null); // tracks the index of the current streaming message
  const conversationIdRef = useRef(null); // mirrors conversationId for use inside the stream loop

  useEffect(() => {
    if (stickToBottom.current) bottomRef.current?.scrollIntoView({ behavior: "auto" });
  }, [messages]);

  useEffect(() => {
    fetchConversations();
    return () => { abortRef.current?.abort(); };
  }, []);
  useEffect(() => { onBusyChange?.(loading); }, [loading, onBusyChange]);
  useEffect(() => {
    function shortcuts(e) {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "o") { e.preventDefault(); newChat(); }
    }
    window.addEventListener("keydown", shortcuts);
    return () => window.removeEventListener("keydown", shortcuts);
  }, [loading]);

  const now = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  async function fetchConversations() {
    try {
      const r = await fetch(`${API}/conversations`);
      const d = await r.json();
      setConversations(d.conversations || []);
    } catch {
      // Backend may be offline — leave the rail empty rather than erroring loudly.
    }
  }

  async function loadConversation(id) {
    if (loading) return;
    try {
      const r = await fetch(`${API}/conversations/${id}`);
      if (!r.ok) return;
      const d = await r.json();
      const conv = d.conversation;
      setChatRuntime(conv.runtime || "api");
      stickToBottom.current = true;
      setContextFiles([]);
      setPendingApprovals([]);
      setStatusError("");
      const mapped = (conv.messages || []).map(m => ({
        role: m.role,
        content: m.content,
        time: timeLabel(m.created_at),
        model: m.model || null,
        taskType: m.task_type || null,
        toolCalls: normalizeToolCalls(m.tool_calls),
      }));
      setConversationId(id);
      conversationIdRef.current = id;
      setMessages(mapped);
      setPlan(conv.plan || []);
    } catch {
      // ignore — conversation may have been deleted concurrently
    }
  }

  function newChat() {
    if (loading) return;
    setConversationId(null);
    conversationIdRef.current = null;
    setMessages([]);
    setPlan([]);
    setChatRuntime(null);
    setPendingApprovals([]);
    setStatusError("");
    setInput("");
    setContextFiles([]);
    stickToBottom.current = true;
  }

  async function deleteConversation(id, e) {
    e.stopPropagation();
    if (!confirm("Delete this conversation? This can't be undone.")) return;
    await fetch(`${API}/conversations/${id}`, { method: "DELETE" });
    if (id === conversationId) newChat();
    fetchConversations();
  }

  // Update a specific field on the current assistant message
  function updateAssistant(updater) {
    setMessages(prev => prev.map((m, i) =>
      i === assistantRef.current ? updater(m) : m
    ));
  }

  async function send() {
    const text = input.trim();
    if (!text || loading || !preferences || runtimeMismatch) return;
    if (runtime !== "api" && !connection?.connected) { onConnect?.(); return; }
    setLastPrompt(text);
    setStatusError("");
    turnRuntime.current = runtime;
    stickToBottom.current = true;
    const attached = runtime !== "api" ? contextFiles : [];

    setPendingApprovals([]);

    const userMsg = { role: "user", content: text + (attached.length ? "\n\nContext: " + attached.join(", ") : ""), time: now() };

    setMessages(prev => {
      const next = [...prev, userMsg];
      assistantRef.current = next.length; // placeholder will be at this index
      return next;
    });

    setInput("");
    setLoading(true);

    // Add streaming placeholder
    setMessages(prev => [...prev, {
      role: "assistant",
      content: "",
      streaming: true,
      model: null,
      taskType: null,
      toolCalls: [],
      time: now(),
    }]);

    try {
      const controller = new AbortController();
      abortRef.current = controller;

      const res = await fetch(`${API}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          conversation_id: conversationIdRef.current,
          force_model: forceModel || null,
          runtime,
          codex_model: preferences?.codex_model || null,
          reasoning_effort: preferences?.reasoning_effort || null,
          context_files: attached,
          claude_model: preferences?.claude_model || null,
          claude_effort: preferences?.claude_effort || null,
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(typeof detail.detail === "string" ? detail.detail : `Request failed (${res.status})`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let receivedDone = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          let chunk;
          try { chunk = JSON.parse(line.slice(5).trim()); }
          catch { continue; }

          if (chunk.conversation_id) {
            // Backend created or confirmed the conversation this turn belongs to.
            conversationIdRef.current = chunk.conversation_id;
            setConversationId(chunk.conversation_id);
            setChatRuntime(turnRuntime.current);

          } else if (chunk.meta) {
            // Routing metadata — model, task type, classifier source/confidence, reason
            updateAssistant(m => ({
              ...m,
              model: { display_name: chunk.model, provider: chunk.provider || "auto" },
              taskType: chunk.task_type,
              sticky: !!chunk.sticky,
              classificationSource: chunk.classification_source || null,
              confidence: typeof chunk.confidence === "number" ? chunk.confidence : null,
              routingReason: chunk.reason || null,
              skillsLoaded: chunk.skills_loaded || [],
              timings: { routing_ms: chunk.routing_ms },
            }));

          } else if (chunk.plan) {
            setPlan(chunk.plan);

          } else if (chunk.notice) {
            updateAssistant(m => ({ ...m, notice: chunk.notice }));

          } else if (chunk.usage) {
            updateAssistant(m => ({ ...m, usage: chunk.usage }));

          } else if (chunk.codex_diff !== undefined) {
            updateAssistant(m => ({ ...m, diffs: [{ path: "Workspace changes", diff: chunk.codex_diff }] }));

          } else if (chunk.question_required) {
            setPendingApprovals(previous => [...previous, { action_id: chunk.action_id, questions: chunk.questions, runtime: chunk.runtime || "codex" }]);

          } else if (chunk.approval_resolved) {
            setPendingApprovals(previous => previous.filter(action => action.action_id !== chunk.action_id));

          } else if (chunk.timing) {
            updateAssistant(m => ({ ...m, timings: chunk }));

          } else if (chunk.token) {
            // Streaming text token
            updateAssistant(m => ({ ...m, content: m.content + chunk.token, nudging: false }));

          } else if (chunk.tool_start) {
            // Tool is starting — add it to toolCalls as pending
            updateAssistant(m => ({
              ...m,
              nudging: false,
              toolCalls: [...(m.toolCalls || []), {
                id: chunk.tool_call_id,
                name: chunk.tool_start,
                args: chunk.args || {},
                result: null,
              }],
            }));

            // The plan tool's args ARE the new plan — show it immediately,
            // no need to wait for the tool result.
            if (chunk.tool_start === "update_plan" && Array.isArray(chunk.args?.items)) {
              setPlan(chunk.args.items);
            }

          } else if (chunk.tool_result) {
            if (["register_model", "configure_model", "test_model"].includes(chunk.tool_result)) onModelsChanged?.();
            // Tool finished — update the last pending tool entry
            updateAssistant(m => {
              const calls = [...(m.toolCalls || [])];
              // Find the last entry with this tool name that has no result yet
              const idx = calls.findIndex(c => chunk.tool_call_id
                ? c.id === chunk.tool_call_id
                : c.name === chunk.tool_result && c.result == null);
              if (idx >= 0) calls[idx] = { ...calls[idx], result: chunk.preview, success: chunk.success };
              return { ...m, toolCalls: calls };
            });

          } else if (chunk.diff) {
            // A write_file/edit_file call changed an existing file — show
            // exactly what changed, not just that the tool "succeeded".
            updateAssistant(m => ({
              ...m,
              diffs: [...(m.diffs || []), { path: chunk.path, diff: chunk.unified_diff }],
            }));

          } else if (chunk.approval_required) {
            // Human-gated approval for destructive shell / package / git / kill
            setPendingApprovals(previous => [...previous, {
              action_id: chunk.action_id,
              command: chunk.command || chunk.reason || "(action requires approval)",
              reason: chunk.reason,
              runtime: chunk.runtime,
              cwd: chunk.cwd,
              network: chunk.network,
            }]);

          } else if (chunk.nudge) {
            // Agent finished a reply but left code unverified — it's about
            // to loop back and check its work before really answering.
            updateAssistant(m => ({ ...m, nudging: true, nudgeFiles: chunk.files || [] }));

          } else if (chunk.done) {
            receivedDone = true;
            setContextFiles([]);
            if (turnRuntime.current !== "api") setPendingApprovals([]);
            // Stream complete — fold any final metadata from the done event
            updateAssistant(m => ({
              ...m,
              streaming: false,
              nudging: false,
              content: chunk.response ?? m.content,
              timings: chunk.timings || m.timings,
              model: chunk.model_used
                ? { display_name: chunk.model_used, provider: m.model?.provider || "auto" }
                : m.model,
              taskType: chunk.task_type || m.taskType,
              routingReason: chunk.routing_reason || m.routingReason,
            }));

          } else if (chunk.error) {
            setStatusError(chunk.error);
            updateAssistant(m => ({
              ...m,
              content: m.content + `\n\n⚠️ Error: ${chunk.error}`,
              streaming: false,
              nudging: false,
            }));
          }
        }
      }

      if (!receivedDone && !controller.signal.aborted) {
        updateAssistant(m => ({ ...m, streaming: false, notice: m.notice || "The response ended before completion. Check the connection before retrying." }));
      }

      // Refresh rail + plan panel from backend truth
      fetchConversations();
      if (conversationIdRef.current) {
        try {
          const pr = await fetch(`${API}/conversations/${conversationIdRef.current}/plan`);
          const pd = await pr.json();
          if (pd.plan) setPlan(pd.plan);
        } catch { /* ignore */ }
      }
    } catch (e) {
      if (e.name !== "AbortError") {
        updateAssistant(m => ({
          ...m,
          content: m.content || `Could not complete this task: ${e.message}`,
          streaming: false,
        }));
        setStatusError(e.message);
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
      updateAssistant(m => ({ ...m, streaming: false }));
    }
  }

  async function handleApprovalDecision(approved) {
    const action = pendingApprovals[0];
    if (!action || decisionBusy) return;
    setDecisionBusy(true);

    try {
      if (action.runtime === "codex" || action.runtime === "claude") {
        await api(`/connections/${action.runtime}/approve`, { method: "POST", body: {
          conversation_id: conversationIdRef.current, action_id: action.action_id, approved,
        } });
        setPendingApprovals(previous => previous.filter(item => item.action_id !== action.action_id));
        return;
      }
      const res = await fetch(`${API}/chat/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: conversationIdRef.current,
          action_id: action.action_id,
          approved,
        }),
      });
      if (!res.ok) throw new Error("Approval could not be submitted");
      const data = await res.json();
      setPendingApprovals(previous => previous.filter(item => item.action_id !== action.action_id));
      const text = data.output || (approved ? "Approved." : "Denied.");
      setMessages(prev => [...prev, { role: "assistant", content: text, time: now(), toolCalls: [] }]);
    } catch (e) {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: `Connection error while resolving approval: ${e.message}`,
        time: now(),
        toolCalls: [],
      }]);
    } finally { setDecisionBusy(false); }
  }

  async function answerQuestions(answers) {
    const action = pendingApprovals[0];
    if (!action || decisionBusy) return;
    setDecisionBusy(true);
    try {
      await api(`/connections/${action.runtime}/answer`, { method: "POST", body: {
        conversation_id: conversationIdRef.current, action_id: action.action_id, answers,
      } });
      setPendingApprovals(previous => previous.filter(item => item.action_id !== action.action_id));
    } catch (e) { setStatusError(e.message); }
    finally { setDecisionBusy(false); }
  }

  async function stop() {
    if (turnRuntime.current !== "api" && conversationIdRef.current) {
      try { await api(`/connections/${turnRuntime.current}/stop`, { method: "POST", body: { conversation_id: conversationIdRef.current } }); }
      catch (e) { setStatusError(`Could not confirm stop: ${e.message}`); }
    }
    abortRef.current?.abort();
    updateAssistant(m => ({ ...m, streaming: false }));
    setPendingApprovals([]);
  }

  function onKey(e) {
    if (e.key === "Enter" && !e.shiftKey && (preferences?.enter_to_send || e.ctrlKey || e.metaKey) && !e.nativeEvent.isComposing) { e.preventDefault(); send(); }
  }

  function autoResize(e) {
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
  }

  async function addContext() {
    if (window.asrar?.chooseContextFiles) {
      try {
        const files = await window.asrar.chooseContextFiles();
        setContextFiles(previous => [...new Set([...previous, ...files])].slice(0, 8));
      } catch (e) { setStatusError(e.message); }
    } else setShowContextInput(previous => !previous);
  }

  function exportConversation() {
    const content = messages.map(message => `## ${message.role === "user" ? "You" : "Asrār"}\n\n${message.content}`).join("\n\n---\n\n");
    const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `asrar-${conversationId || "chat"}.md`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return (
    <div className="chat-with-rail">
      {/* Conversation rail — persisted chats, backed by SQLite */}
      <div className="chat-rail">
        <div className="chat-rail-header">
          <button className="chat-rail-new" disabled={loading} onClick={newChat} title="Ctrl/Cmd+Shift+O">+ New chat</button>
          <input className="conversation-search" aria-label="Search conversations" placeholder="Search chats…" value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <div className="chat-rail-list">
          {conversations.length === 0 ? (
            <div className="chat-rail-empty">No saved chats yet</div>
          ) : conversations.filter(c => c.title.toLowerCase().includes(search.toLowerCase())).map(c => (
            <div
              key={c.id}
              className={`chat-rail-item ${c.id === conversationId ? "active" : ""}`}
              onClick={() => loadConversation(c.id)}
            >
              <div className="chat-rail-item-row">
                <span className="chat-rail-item-title">{c.title}</span>
                <button
                  className="chat-rail-item-delete"
                  onClick={(e) => deleteConversation(c.id, e)}
                  title="Delete" disabled={loading}
                >✕</button>
              </div>
              <span className="chat-rail-item-meta">{c.message_count} msgs · {relativeLabel(c.updated_at)}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="chat-container">
        {messages.length > 0 && <div className="conversation-actions"><button disabled={loading} onClick={exportConversation}>Export chat ↓</button><span>{chatRuntime === "codex" ? "ChatGPT subscription" : chatRuntime === "claude" ? "Claude subscription" : chatRuntime === "api" ? "API conversation" : ""}</span></div>}
        <div className="mobile-new-chat"><button className="secondary-btn" disabled={loading} onClick={newChat}>+ New chat</button></div>
        <PlanPanel items={plan} />
        {runtimeMismatch && <div className="notice">This chat uses {chatRuntime === "codex" ? "your ChatGPT subscription" : chatRuntime === "claude" ? "your Claude subscription" : "an API provider"}. Switch back to that connection or start a new chat.</div>}
        <div className="chat-messages" onScroll={event => {
          const element = event.currentTarget;
          const near = element.scrollHeight - element.scrollTop - element.clientHeight < 100;
          stickToBottom.current = near; setAwayFromBottom(!near);
        }}>
          {messages.length === 0 ? (
            <div className="chat-empty">
              <div className="chat-empty-glyph">◈</div>
              <div className="eyebrow">A SPACE FOR YOUR NEXT IDEA</div>
              <div className="chat-empty-title">What shall we work on?</div>
              <div className="chat-empty-hint">
                {runtime !== "api" ? "Explore your project, understand a problem, or build something together." : "Work with your configured API providers."}
              </div>
              <div className="starter-grid">{["Explain this codebase and how its pieces fit together", "Review this project for bugs and suggest improvements", "Help me plan and build a new feature"].map(prompt => <button key={prompt} onClick={() => setInput(prompt)}>{prompt}<span>↗</span></button>)}</div>
            </div>
          ) : (
            messages.map((m, i) => <Message key={i} msg={m} showActivity={preferences?.show_activity !== false} />)
          )}
          <div ref={bottomRef} />
        </div>
        {awayFromBottom && <button className="jump-latest" onClick={() => { stickToBottom.current = true; bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }}>Jump to latest ↓</button>}

        {pendingApprovals[0]?.questions
          ? <QuestionBanner key={pendingApprovals[0].action_id} pending={pendingApprovals[0]} onAnswer={answerQuestions} busy={decisionBusy} />
          : <ApprovalBanner pending={pendingApprovals[0]} onDecide={handleApprovalDecision} busy={decisionBusy} />}
        {statusError && (!loading || pendingApprovals.length > 0) && <div className="chat-error" role="alert"><span>{statusError}</span>{lastPrompt && !loading && <button onClick={() => { setInput(lastPrompt); setStatusError(""); }}>Edit & retry prompt</button>}</div>}

        <div className="chat-input-area">
          {runtime !== "api" && contextFiles.length > 0 && <div className="context-files">{contextFiles.map(file => <span key={file} title={file}>{file.split(/[\\/]/).pop()}<button aria-label={`Remove ${file}`} disabled={loading} onClick={() => setContextFiles(previous => previous.filter(value => value !== file))}>✕</button></span>)}</div>}
          {showContextInput && runtime !== "api" && <div className="context-input"><input aria-label="Workspace file path" placeholder="Path relative to your workspace" value={contextPath} onChange={e => setContextPath(e.target.value)} /><button className="secondary-btn" disabled={!contextPath.trim() || loading || contextFiles.length >= 8} onClick={() => { setContextFiles(previous => [...new Set([...previous, contextPath.trim()])].slice(0, 8)); setContextPath(""); setShowContextInput(false); }}>Add file</button></div>}
          <div className="chat-input-row">
            <textarea
              className="chat-textarea"
              aria-label="Message Asrār"
              placeholder={runtimeMismatch ? "Start a new chat to change connections…" : "Ask, plan, build…"}
              disabled={runtimeMismatch}
              value={input}
              onChange={e => { setInput(e.target.value); autoResize(e); }}
              onKeyDown={onKey}
              rows={1}
            />
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              {loading
                ? <button className="send-btn" aria-label="Stop task" onClick={stop} style={{ background: "var(--red)" }}>■</button>
                : <button className="send-btn" aria-label="Send message" onClick={send} disabled={!input.trim() || !preferences || runtimeMismatch}>↑</button>
              }
            </div>
          </div>
          <div className="input-hint">
            {runtime !== "api" && <button className="attach-button" disabled={loading || contextFiles.length >= 8 || runtimeMismatch} onClick={addContext}>+ Add workspace file</button>}
            {preferences?.enter_to_send ? "Enter to send · Shift+Enter for new line" : "Ctrl/Cmd+Enter to send"}{loading ? " · Stop cancels the running task" : ""}
            <span>{runtime === "codex" ? "Codex · subscription" : runtime === "claude" ? "Claude Code · subscription" : "API · provider billing"}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

import { useState, useRef, useEffect } from "react";
import ModelBadge from "./ModelBadge";

const API = "http://127.0.0.1:8000";

function ToolCall({ name, args, result }) {
  const [open, setOpen] = useState(false);
  const argStr = Object.entries(args || {})
    .map(([k, v]) => `${k}=${JSON.stringify(v).slice(0, 60)}`)
    .join(", ");

  return (
    <div className="tool-call-block">
      <div className="tool-call-header" onClick={() => setOpen(o => !o)}>
        <span className="tool-call-icon">⚙</span>
        <span className="tool-call-name">{name}</span>
        {argStr && <span className="tool-call-args">({argStr})</span>}
        {result
          ? <span className="tool-call-status done">✓</span>
          : <span className="tool-call-status running">⟳</span>}
        <span className="tool-call-toggle">{open ? "▲" : "▼"}</span>
      </div>
      {open && result && (
        <pre className="tool-call-result">{result}</pre>
      )}
    </div>
  );
}

function Message({ msg }) {
  const parts = (msg.content || "").split("```");

  return (
    <div className={`message ${msg.role}`}>
      <div className="message-meta">
        {msg.role === "user" ? "you" : (
          <ModelBadge model={msg.model} taskType={msg.taskType} />
        )}
        {msg.sticky && (
          <span className="sticky-badge" title="Stayed with the same model because a clarifying question was still open">
            ↳ continuing
          </span>
        )}
        <span>{msg.time}</span>
      </div>

      {/* Tool call blocks — shown above the text */}
      {msg.toolCalls?.length > 0 && (
        <div className="tool-calls-list">
          {msg.toolCalls.map((tc, i) => (
            <ToolCall key={i} name={tc.name} args={tc.args} result={tc.result} />
          ))}
        </div>
      )}

      <div className="message-bubble">
        {parts.map((part, i) =>
          i % 2 === 1
            ? <pre key={i}><code>{part.replace(/^\w+\n/, "")}</code></pre>
            : <span key={i} style={{ whiteSpace: "pre-wrap" }}>{part}</span>
        )}
        {msg.streaming && <span className="stream-cursor">▋</span>}
      </div>
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

// Normalize a tool_calls entry from the DB (key "tool") into the shape
// the Message/ToolCall components expect (key "name").
function normalizeToolCalls(raw) {
  return (raw || []).map(tc => ({
    name: tc.name || tc.tool,
    args: tc.args || {},
    result: tc.result || "",
  }));
}

export default function Chat({ forceModel }) {
  const [messages, setMessages]   = useState([]);
  const [input, setInput]         = useState("");
  const [loading, setLoading]     = useState(false);
  const [conversations, setConversations] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const bottomRef     = useRef(null);
  const abortRef      = useRef(null);
  const assistantRef  = useRef(null); // tracks the index of the current streaming message
  const conversationIdRef = useRef(null); // mirrors conversationId for use inside the stream loop

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    fetchConversations();
  }, []);

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
    } catch {
      // ignore — conversation may have been deleted concurrently
    }
  }

  function newChat() {
    if (loading) return;
    setConversationId(null);
    conversationIdRef.current = null;
    setMessages([]);
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
    if (!text || loading) return;

    const userMsg = { role: "user", content: text, time: now() };

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
        }),
        signal: controller.signal,
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let isNewConversation = !conversationIdRef.current;

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

          } else if (chunk.meta) {
            // Routing metadata — update model badge
            updateAssistant(m => ({
              ...m,
              model: { display_name: chunk.model, provider: chunk.provider || "auto" },
              taskType: chunk.task_type,
              sticky: !!chunk.sticky,
            }));

          } else if (chunk.token) {
            // Streaming text token
            updateAssistant(m => ({ ...m, content: m.content + chunk.token }));

          } else if (chunk.tool_start) {
            // Tool is starting — add it to toolCalls as pending
            updateAssistant(m => ({
              ...m,
              toolCalls: [...(m.toolCalls || []), {
                name: chunk.tool_start,
                args: chunk.args || {},
                result: null,
              }],
            }));

          } else if (chunk.tool_result) {
            // Tool finished — update the last pending tool entry
            updateAssistant(m => {
              const calls = [...(m.toolCalls || [])];
              // Find the last entry with this tool name that has no result yet
              const idx = calls.map((c, i) => c.name === chunk.tool_result && !c.result ? i : -1)
                             .filter(i => i >= 0).pop();
              if (idx !== undefined) calls[idx] = { ...calls[idx], result: chunk.preview };
              return { ...m, toolCalls: calls };
            });

          } else if (chunk.done) {
            // Stream complete
            updateAssistant(m => ({ ...m, streaming: false }));

          } else if (chunk.error) {
            updateAssistant(m => ({
              ...m,
              content: m.content + `\n\n⚠️ Error: ${chunk.error}`,
              streaming: false,
            }));
          }
        }
      }

      // Refresh the rail so the (possibly new, possibly re-titled-by-recency) conversation shows up.
      fetchConversations();
      if (isNewConversation) { /* nothing else to do — id was captured above */ }
    } catch (e) {
      if (e.name !== "AbortError") {
        updateAssistant(m => ({
          ...m,
          content: `Connection error: ${e.message}\n\nIs the backend running? Run: python main.py`,
          streaming: false,
        }));
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
    setLoading(false);
    updateAssistant(m => ({ ...m, streaming: false }));
  }

  function onKey(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  }

  function autoResize(e) {
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
  }

  return (
    <div className="chat-with-rail">
      {/* Conversation rail — persisted chats, backed by SQLite */}
      <div className="chat-rail">
        <div className="chat-rail-header">
          <button className="chat-rail-new" onClick={newChat}>+ New chat</button>
        </div>
        <div className="chat-rail-list">
          {conversations.length === 0 ? (
            <div className="chat-rail-empty">No saved chats yet</div>
          ) : conversations.map(c => (
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
                  title="Delete"
                >✕</button>
              </div>
              <span className="chat-rail-item-meta">{c.message_count} msgs · {relativeLabel(c.updated_at)}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="chat-container">
        <div className="chat-messages">
          {messages.length === 0 ? (
            <div className="chat-empty">
              <div className="chat-empty-glyph">⬡</div>
              <div className="chat-empty-title">Asrār is ready</div>
              <div className="chat-empty-hint">
                Ask anything — browse the web, work on files,<br />
                run commands, diagnose your PC.
              </div>
            </div>
          ) : (
            messages.map((m, i) => <Message key={i} msg={m} />)
          )}
          <div ref={bottomRef} />
        </div>

        <div className="chat-input-area">
          <div className="chat-input-row">
            <textarea
              className="chat-textarea"
              placeholder="Ask Asrār anything..."
              value={input}
              onChange={e => { setInput(e.target.value); autoResize(e); }}
              onKeyDown={onKey}
              rows={1}
            />
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              {loading
                ? <button className="send-btn" onClick={stop} style={{ background: "var(--red)" }}>■</button>
                : <button className="send-btn" onClick={send} disabled={!input.trim()}>↑</button>
              }
            </div>
          </div>
          <div className="input-hint">
            Enter to send · Shift+Enter for new line{loading ? " · ■ to stop" : ""}
          </div>
        </div>
      </div>
    </div>
  );
}

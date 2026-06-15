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

export default function Chat({ forceModel }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput]       = useState("");
  const [loading, setLoading]   = useState(false);
  const bottomRef   = useRef(null);
  const abortRef    = useRef(null);
  const assistantRef = useRef(null); // tracks the index of the current streaming message

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const now = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

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

    // Capture history BEFORE state update
    const historySnapshot = messages.map(m => ({ role: m.role, content: m.content }));

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
          history: historySnapshot,
          force_model: forceModel || null,
        }),
        signal: controller.signal,
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

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

          if (chunk.meta) {
            // Routing metadata — update model badge
            updateAssistant(m => ({
              ...m,
              model: { display_name: chunk.model, provider: chunk.provider || "auto" },
              taskType: chunk.task_type,
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

  function clearChat() {
    if (messages.length === 0) return;
    setMessages([]);
  }

  function onKey(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  }

  function autoResize(e) {
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
  }

  return (
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
            {messages.length > 0 && !loading && (
              <button
                onClick={clearChat}
                title="Clear chat"
                style={{
                  width: 34, height: 34, borderRadius: 8,
                  background: "var(--bg-raised)", color: "var(--text-muted)",
                  fontSize: 14, display: "flex", alignItems: "center", justifyContent: "center",
                  border: "1px solid var(--border-soft)",
                }}
              >✕</button>
            )}
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
  );
}

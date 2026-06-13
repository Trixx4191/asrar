import { useState, useRef, useEffect } from "react";
import ModelBadge from "./ModelBadge";

const API = "http://127.0.0.1:8000";

function Message({ msg }) {
  return (
    <div className={`message ${msg.role}`}>
      <div className="message-meta">
        {msg.role === "user" ? "you" : (
          <ModelBadge model={msg.model} taskType={msg.taskType} />
        )}
        <span>{msg.time}</span>
      </div>
      <div className="message-bubble">
        {msg.content.split("```").map((part, i) =>
          i % 2 === 1
            ? <pre key={i}><code>{part.trim()}</code></pre>
            : <span key={i}>{part}</span>
        )}
        {msg.streaming && <span className="stream-cursor">▋</span>}
      </div>
      {msg.tools?.length > 0 && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginTop: 4 }}>
          ⚙ tools: {msg.tools.join(", ")}
        </div>
      )}
    </div>
  );
}

export default function Chat({ forceModel }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput]       = useState("");
  const [loading, setLoading]   = useState(false);
  const bottomRef  = useRef(null);
  const abortRef   = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const now = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  async function send() {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg = { role: "user", content: text, time: now() };
    setMessages(prev => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    const history = messages.map(m => ({ role: m.role, content: m.content }));

    // Placeholder streaming message
    const assistantIdx = messages.length + 1;
    setMessages(prev => [...prev, {
      role: "assistant", content: "", streaming: true,
      model: null, taskType: null, tools: [], time: now(),
    }]);

    try {
      const controller = new AbortController();
      abortRef.current = controller;

      const res = await fetch(`${API}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history, force_model: forceModel || null }),
        signal: controller.signal,
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let model = null, taskType = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          try {
            const chunk = JSON.parse(line.slice(5).trim());

            if (chunk.meta) {
              model = chunk.model;
              taskType = chunk.task_type;
              setMessages(prev => prev.map((m, i) =>
                i === assistantIdx ? { ...m, model, taskType } : m
              ));
            } else if (chunk.token) {
              setMessages(prev => prev.map((m, i) =>
                i === assistantIdx ? { ...m, content: m.content + chunk.token } : m
              ));
            } else if (chunk.done) {
              setMessages(prev => prev.map((m, i) =>
                i === assistantIdx ? { ...m, streaming: false } : m
              ));
            } else if (chunk.error) {
              setMessages(prev => prev.map((m, i) =>
                i === assistantIdx ? { ...m, content: `Error: ${chunk.error}`, streaming: false } : m
              ));
            }
          } catch { }
        }
      }
    } catch (e) {
      if (e.name !== "AbortError") {
        setMessages(prev => prev.map((m, i) =>
          i === assistantIdx
            ? { ...m, content: `Connection error: ${e.message}. Is the backend running? (python main.py)`, streaming: false }
            : m
        ));
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
    setLoading(false);
    setMessages(prev => prev.map((m, i) =>
      m.streaming ? { ...m, streaming: false } : m
    ));
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
          {loading
            ? <button className="send-btn" onClick={stop} style={{ background: "var(--red)" }}>■</button>
            : <button className="send-btn" onClick={send} disabled={!input.trim()}>↑</button>
          }
        </div>
        <div className="input-hint">Enter to send · Shift+Enter for new line{loading ? " · ■ to stop" : ""}</div>
      </div>
    </div>
  );
}

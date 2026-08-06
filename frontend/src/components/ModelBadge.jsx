export default function ModelBadge({ model, taskType, classificationSource, confidence, sticky }) {
  const providerEmojis = {
    anthropic: "🟣",
    google: "🔵",
    groq: "🟡",
    deepseek: "🔴",
    openrouter: "⚪",
    mistral: "🟠",
    kimi: "🟤",
    qwen: "🟢",
  };

  if (!model) return <span style={{ color: "var(--text-muted)" }}>assistant</span>;

  const emoji = providerEmojis[model?.provider] || "⚫";
  const label = `${emoji} ${model?.display_name || model}`;

  const sourceLabel = {
    keyword: "kw",
    model: "ml",
    hybrid: "hy",
  }[classificationSource];

  return (
    <span
      className="model-badge-wrap"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 11,
        fontFamily: "var(--font-mono)",
        color: "var(--text-secondary)",
        flexWrap: "wrap",
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          padding: "2px 6px",
          background: "var(--accent-soft)",
          borderRadius: 4,
          border: "1px solid rgba(224,165,63,0.25)",
        }}
      >
        {label}
        {taskType && <span style={{ color: "var(--text-muted)" }}>· {taskType}</span>}
      </span>

      {sourceLabel && (
        <span
          className="classify-badge"
          title={
            classificationSource === "hybrid"
              ? "Hybrid classifier (keyword + model)"
              : classificationSource === "model"
              ? "Model-backed classification"
              : "Keyword classification"
          }
          style={{
            padding: "1px 6px",
            borderRadius: 4,
            border: "1px solid var(--border-soft)",
            color: "var(--text-muted)",
            fontSize: 10,
          }}
        >
          {sourceLabel}
          {typeof confidence === "number" ? ` ${Math.round(confidence * 100)}%` : ""}
        </span>
      )}

      {sticky && (
        <span className="sticky-badge" title="Sticky routing — continuing open clarification">
          ↳ sticky
        </span>
      )}
    </span>
  );
}

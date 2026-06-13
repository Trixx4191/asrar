export default function ModelBadge({ model, taskType }) {
  const providerEmojis = {
    anthropic: "🟣",
    google: "🔵",
    groq: "🟡",
    deepseek: "🔴",
    openrouter: "⚪",
    mistral: "🟠",
  };

  if (!model) return <span style={{ color: "var(--text-muted)" }}>assistant</span>;

  const emoji = providerEmojis[model?.provider] || "⚫";
  const label = `${emoji} ${model?.display_name || model}`;

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 11,
        fontFamily: "var(--font-mono)",
        color: "var(--text-secondary)",
        padding: "2px 6px",
        background: "var(--accent-soft)",
        borderRadius: 4,
      }}
    >
      {label}
      {taskType && <span style={{ color: "var(--text-muted)" }}>• {taskType}</span>}
    </span>
  );
}

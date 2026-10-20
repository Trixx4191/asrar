import { useState } from "react";
import { openExternal } from "../api";

function inline(text) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https:\/\/[^\s)]+\))/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    const link = part.match(/^\[([^\]]+)\]\((https:\/\/[^\s)]+)\)$/);
    if (link) return <a key={i} href={link[2]} target="_blank" rel="noreferrer noopener" onClick={e => { e.preventDefault(); openExternal(link[2]).catch(() => {}); }}>{link[1]}</a>;
    return part;
  });
}

function CodeBlock({ language, children }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try { await navigator.clipboard.writeText(children); setCopied(true); setTimeout(() => setCopied(false), 1800); }
    catch { setCopied(false); }
  }
  return <div className="code-block"><div className="code-block-bar"><span>{language || "code"}</span><button onClick={copy}>{copied ? "Copied" : "Copy code"}</button></div><pre><code>{children}</code></pre></div>;
}

export default function Markdown({ text }) {
  const chunks = (text || "").split("```");
  return <div className="markdown">{chunks.map((chunk, index) => {
    if (index % 2) {
      const newline = chunk.indexOf("\n");
      const language = newline >= 0 ? chunk.slice(0, newline).trim() : "";
      return <CodeBlock key={index} language={language}>{newline >= 0 ? chunk.slice(newline + 1).replace(/\n$/, "") : chunk}</CodeBlock>;
    }
    return <div key={index}>{chunk.split(/\n\s*\n/).filter(Boolean).map((block, b) => {
      const lines = block.trim().split("\n");
      if (lines.every(line => /^\s*[-*] /.test(line))) return <ul key={b}>{lines.map((line, l) => <li key={l}>{inline(line.replace(/^\s*[-*] /, ""))}</li>)}</ul>;
      if (lines.every(line => /^\s*\d+\. /.test(line))) return <ol key={b}>{lines.map((line, l) => <li key={l}>{inline(line.replace(/^\s*\d+\. /, ""))}</li>)}</ol>;
      if (lines.length > 1 && /^\|?\s*:?-{3,}/.test(lines[1])) {
        const cells = line => line.trim().replace(/^\||\|$/g, "").split("|").map(cell => cell.trim());
        return <div className="markdown-table" key={b}><table><thead><tr>{cells(lines[0]).map((cell, c) => <th key={c}>{inline(cell)}</th>)}</tr></thead><tbody>{lines.slice(2).map((line, l) => <tr key={l}>{cells(line).map((cell, c) => <td key={c}>{inline(cell)}</td>)}</tr>)}</tbody></table></div>;
      }
      return <div className="markdown-paragraph" key={b}>{lines.map((line, l) => {
        const heading = line.match(/^(#{1,4})\s+(.+)$/);
        if (heading) { const Tag = `h${heading[1].length + 1}`; return <Tag key={l}>{inline(heading[2])}</Tag>; }
        if (line.startsWith("> ")) return <blockquote key={l}>{inline(line.slice(2))}</blockquote>;
        return <span key={l}>{inline(line)}{l < lines.length - 1 && <br />}</span>;
      })}</div>;
    })}</div>;
  })}</div>;
}

export const API = window.location.pathname.startsWith("/desktop")
  ? window.location.origin : "http://127.0.0.1:8000";

export function apiFetch(path, options = {}) {
  return fetch(path.startsWith("http") ? path : `${API}${path}`, {
    ...options,
    headers: { "X-Asrar-Client": "desktop-v1", ...options.headers },
  });
}

export async function api(path, options = {}) {
  const response = await apiFetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`);
  return data;
}

export async function openExternal(url) {
  const parsed = new URL(url);
  if (parsed.protocol !== "https:") throw new Error("Only secure web links are allowed");
  if (window.asrar?.openExternal) return window.asrar.openExternal(url);
  window.open(url, "_blank", "noopener,noreferrer");
}

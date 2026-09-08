export class RequestAborted extends Error {}

export function activeProjectId() {
  if (typeof window !== "undefined") {
    const fromUrl = new URLSearchParams(window.location.search).get("projectId");
    if (fromUrl) return fromUrl;
  }
  return typeof sessionStorage === "undefined" ? "" : sessionStorage.getItem("activeProjectId") || "";
}

function adminStorageKey(projectId = activeProjectId()) {
  return projectId ? `knowledgeAdminToken:${projectId}` : "knowledgeAdminToken";
}

function projectPath(path) {
  const projectId = activeProjectId();
  if (!projectId || path.startsWith("/api/projects")) return path;
  const relative = path.startsWith("/api/") ? path.slice(4) : path.startsWith("/") ? path : `/${path}`;
  return `/api/projects/${encodeURIComponent(projectId)}${relative}`;
}

export async function request(path, options = {}, signal) {
  const storage = typeof sessionStorage === "undefined" ? null : sessionStorage;
  const projectId = activeProjectId();
  const adminToken = storage?.getItem(adminStorageKey(projectId))
    || (!projectId ? storage?.getItem("knowledgeAdminToken") : "");
  const headers = { ...(options.headers || {}) };
  if (projectId) headers["X-Project-Id"] = projectId;
  const adminPath = path.startsWith("/api/knowledge-admin/") || path.startsWith("/api/knowledge/")
    || path.startsWith("/api/ast/generate")
    || path.startsWith("/api/evaluations") || path.startsWith("/api/evaluation-suites");
  if (adminToken && adminPath) headers.Authorization = `Bearer ${adminToken}`;
  let response;
  try {
    response = await fetch(projectPath(path), { ...(options || {}), headers, signal });
  } catch (error) {
    if (error?.name === "AbortError") throw new RequestAborted("请求已中止");
    throw error;
  }
  const body = await response.json().catch(() => ({}));
  if (response.status === 401 && adminPath) {
    storage?.removeItem(adminStorageKey(projectId));
  }
  if (!response.ok) throw new Error(body.error || `请求失败 (${response.status})`);
  return body;
}

export async function streamQuery(path, payload, { signal, onEvent, onRun } = {}) {
  const projectId = activeProjectId();
  const headers = { "Content-Type": "application/json" };
  if (projectId) headers["X-Project-Id"] = projectId;
  const response = await fetch(projectPath(path), {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal,
  }).catch((error) => {
    if (error?.name === "AbortError") throw new RequestAborted("请求已中止");
    throw error;
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `请求失败 (${response.status})`);
  }
  if (!response.body) throw new Error("浏览器不支持流式响应");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "message";
  let result = null;
  let streamError = null;
  const consume = (block) => {
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim() || "message";
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) return;
    let value;
    try { value = JSON.parse(data); } catch { return; }
    if (eventName === "result") result = value;
    if (eventName === "run" && onRun) onRun(value);
    if (onEvent && eventName === "event") onEvent(value);
    if (eventName === "error") {
      streamError = value?.error || "流式查询失败";
      if (onEvent) onEvent({ eventType: "error", payload: value });
    }
    eventName = "message";
  };
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";
    blocks.forEach(consume);
    if (done) break;
  }
  if (buffer.trim()) consume(buffer);
  if (streamError) throw new Error(streamError);
  if (!result) throw new Error("流式查询未返回最终结果");
  return result;
}

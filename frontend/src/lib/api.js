export class RequestAborted extends Error {}

export async function request(path, options = {}, signal) {
  const adminToken = sessionStorage.getItem("knowledgeAdminToken");
  const headers = { ...(options.headers || {}) };
  if (adminToken && (path.startsWith("/api/knowledge-admin/") || path.startsWith("/api/knowledge/"))) headers.Authorization = `Bearer ${adminToken}`;
  let response;
  try {
    response = await fetch(path, { ...(options || {}), headers, signal });
  } catch (error) {
    if (error?.name === "AbortError") throw new RequestAborted("请求已中止");
    throw error;
  }
  const body = await response.json().catch(() => ({}));
  if (response.status === 401 && (path.startsWith("/api/knowledge-admin/") || path.startsWith("/api/knowledge/"))) {
    sessionStorage.removeItem("knowledgeAdminToken");
  }
  if (!response.ok) throw new Error(body.error || `请求失败 (${response.status})`);
  return body;
}

export async function streamQuery(path, payload, { signal, onEvent, onRun } = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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

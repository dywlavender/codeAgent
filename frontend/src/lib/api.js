export class RequestAborted extends Error {}

export async function request(path, options = {}, signal) {
  const adminToken = sessionStorage.getItem("knowledgeAdminToken");
  const headers = { ...(options.headers || {}) };
  if (adminToken && path.startsWith("/api/knowledge-admin/")) headers.Authorization = `Bearer ${adminToken}`;
  let response;
  try {
    response = await fetch(path, { ...(options || {}), headers, signal });
  } catch (error) {
    if (error?.name === "AbortError") throw new RequestAborted("请求已中止");
    throw error;
  }
  const body = await response.json().catch(() => ({}));
  if (response.status === 401 && path.startsWith("/api/knowledge-admin/")) {
    sessionStorage.removeItem("knowledgeAdminToken");
  }
  if (!response.ok) throw new Error(body.error || `请求失败 (${response.status})`);
  return body;
}

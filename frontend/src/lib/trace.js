import { RUN_STATUS_LABEL } from "./query-state.js";

export function traceTimeline(trace) {
  const items = (trace.calls || []).map((call) => ({
    id: `model:${call.attempt}`, kind: "model", title: `模型调用 ${call.attempt}`,
    subtitle: call.phase === "answer" ? "整理答案"
      : call.phase === "checkpoint" ? "调查检查点" : "调查", status: call.status,
    elapsedMs: call.elapsedMs ?? null, data: call,
  }));
  const tools = new Map();
  const events = trace.runtimeEvents?.length ? trace.runtimeEvents : trace.events || [];
  for (const event of events) {
    const value = event.payload || {};
    if (event.eventType === "tool") {
      const old = tools.get(value.id);
      tools.set(value.id, { ...old, ...value, elapsedMs: old?.elapsedMs ?? event.elapsedMs ?? null,
        completedMs: value.status !== "running" ? event.elapsedMs : undefined });
    } else if (event.eventType === "retrieval") {
      items.push({ id: `retrieval:${event.sequence}`, kind: "retrieval", title: value.label || "检索调度",
        subtitle: value.tool || "调查事项", elapsedMs: event.elapsedMs ?? null, data: value });
    } else if (event.eventType === "error") {
      items.push({ id: `error:${event.sequence}`, kind: "error", title: "运行错误", subtitle: value.error,
        status: "failed", elapsedMs: event.elapsedMs ?? trace.run?.durationMs ?? 0, data: value });
    }
  }
  for (const tool of tools.values()) items.push({ id: `tool:${tool.id}`, kind: "tool", title: tool.name,
    subtitle: tool.origin === "workflow" ? "程序调用" : "模型调用",
    status: tool.status === "running" && !["running", "cancelling"].includes(trace.run?.status) ? "interrupted" : tool.status,
    elapsedMs: tool.elapsedMs, data: tool });
  return items.sort((a, b) => (a.elapsedMs ?? Infinity) - (b.elapsedMs ?? Infinity));
}

export const traceStatus = (status) => ({ ...RUN_STATUS_LABEL, completed: "已完成", error: "失败", success: "已完成", interrupted: "未完成", skipped: "已跳过" }[status] || status || "");

export const traceStopReason = (trace) => trace?.stopReason || trace?.run?.usage?.stopReason || trace?.usage?.stopReason || "";

export function tracePath(projectId, runId) {
  return `/api/projects/${encodeURIComponent(projectId)}/query/${encodeURIComponent(runId)}/trace`;
}

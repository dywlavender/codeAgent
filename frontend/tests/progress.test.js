import test from "node:test";
import assert from "node:assert/strict";
import { buildProgress, toolTarget, toolTitle } from "../src/lib/progress.js";
import { streamQuery } from "../src/lib/api.js";

test("tool completion updates one step; partial text and snapshot do not repeat", () => {
  const events = [
    { eventType: "tool", payload: { id: "t1", name: "Read", status: "running", input: { file_path: "A.java" } } },
    { eventType: "tool", payload: { id: "t1", status: "completed", output: "source" } },
    { eventType: "text", payload: { id: "m:0", text: "校验", mode: "append" } },
    { eventType: "text", payload: { id: "m:0", text: "类型", mode: "append" } },
    { eventType: "text", payload: { id: "m:0", text: "校验类型", mode: "replace" } },
  ];
  const value = buildProgress(events);
  assert.equal(value.tools.length, 1);
  assert.equal(value.tools[0].status, "completed");
  assert.equal(toolTarget(value.tools[0]), "A.java");
  assert.equal(toolTitle(value.tools[0]), "读取文件");
  assert.equal(value.text, "校验类型");
});

test("old persisted envelopes remain readable without displaying telemetry", () => {
  const value = buildProgress([
    { eventType: "assistant", payload: { message: { content: [{ type: "tool_use", id: "t", name: "Grep", input: { pattern: "REPAYTYPE" } }] } } },
    { eventType: "user", payload: { message: { content: [{ type: "tool_result", tool_use_id: "t", is_error: true, content: "missing" }] } } },
    { eventType: "system", payload: { subtype: "thinking_tokens", estimated_tokens: 390 } },
  ]);
  assert.equal(value.tools.length, 1);
  assert.equal(value.tools[0].status, "error");
  assert.equal(value.label, "正在分析");
  assert.equal(value.text, "");
});

test("SSE handles split UTF-8 and delivers text before final result", async () => {
  const originalFetch = globalThis.fetch;
  const bytes = new TextEncoder().encode('event: event\ndata: {"eventType":"text","payload":{"id":"m","text":"中文","mode":"append"}}\n\nevent: result\ndata: {"answer":"中文"}\n\n');
  let offset = 0;
  globalThis.fetch = async () => new Response(new ReadableStream({ pull(controller) {
    if (offset >= bytes.length) return controller.close();
    controller.enqueue(bytes.slice(offset, offset + 5)); offset += 5;
  } }));
  try {
    const received = [];
    const result = await streamQuery("/api/query/stream", {}, { onEvent: (event) => received.push(event) });
    assert.equal(received[0].payload.text, "中文");
    assert.equal(result.answer, "中文");
  } finally { globalThis.fetch = originalFetch; }
});

test("SSE exposes run ID before progress and accepts cancelled result", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response('event: run\ndata: {"runId":"r","conversationId":"c"}\n\nevent: result\ndata: {"status":"cancelled","answer":""}\n\n');
  try {
    const runs = [];
    const result = await streamQuery("/api/query/stream", {}, { onRun: (run) => runs.push(run) });
    assert.equal(runs[0].runId, "r");
    assert.equal(result.status, "cancelled");
  } finally { globalThis.fetch = originalFetch; }
});

import test from "node:test";
import assert from "node:assert/strict";
import { tracePath, traceTimeline } from "../src/lib/trace.js";

test("trace joins tool inputs and full outputs while retaining model and scheduling order", () => {
  const output = "源码原文\n".repeat(4000);
  const items = traceTimeline({ calls: [{ attempt: 1, phase: "investigation", elapsedMs: 5, status: "completed" }],
    runtimeEvents: [
      { sequence: 1, eventType: "retrieval", elapsedMs: 0, payload: { label: "精确定位" } },
      { sequence: 2, eventType: "tool", elapsedMs: 1, payload: { id: "r", name: "Read", status: "running", origin: "workflow", input: { path: "a.java" } } },
      { sequence: 3, eventType: "tool", elapsedMs: 3, payload: { id: "r", name: "Read", status: "completed", output } },
    ] });
  assert.deepEqual(items.map((item) => item.kind), ["retrieval", "tool", "model"]);
  assert.equal(items[1].data.output, output);
  assert.deepEqual(items[1].data.input, { path: "a.java" });
  assert.equal(items[1].subtitle, "程序调用");
});

test("direct runtime calls become inspectable model steps", () => {
  const calls = [1, 2, 3].map((attempt) => ({
    attempt, phase: attempt === 3 ? "answer" : "investigation", elapsedMs: attempt * 10,
    status: "completed", durationMs: 8, inputTextChars: 120,
    messages: [{ role: "system", content: "规则" }, { role: "human", content: "问题" }],
    tools: [{ type: "function", function: { name: "Read" } }],
    responses: [{ role: "ai", content: `回答 ${attempt}` }],
  }));
  const items = traceTimeline({ calls, runtimeEvents: [] });
  assert.deepEqual(items.map((item) => item.kind), ["model", "model", "model"]);
  assert.deepEqual(items.map((item) => item.title), ["模型调用 1", "模型调用 2", "模型调用 3"]);
  assert.equal(items[2].subtitle, "整理答案");
  assert.equal(items[1].data.messages[0].role, "system");
  assert.equal(items[1].data.responses[0].content, "回答 2");
});

test("historical events and failures remain inspectable with an explicit project route", () => {
  const items = traceTimeline({ calls: [], events: [{ eventType: "error", payload: { error: "fixture failure" } }] });
  assert.equal(items[0].kind, "error");
  assert.equal(items[0].data.error, "fixture failure");
  assert.equal(tracePath("alpha", "alpha-RUN-1"), "/api/projects/alpha/query/alpha-RUN-1/trace");
});

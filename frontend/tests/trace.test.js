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

test("historical events and failures remain inspectable with an explicit project route", () => {
  const items = traceTimeline({ calls: [], events: [{ eventType: "error", payload: { error: "fixture failure" } }] });
  assert.equal(items[0].kind, "error");
  assert.equal(items[0].data.error, "fixture failure");
  assert.equal(tracePath("alpha", "alpha-RUN-1"), "/api/projects/alpha/query/alpha-RUN-1/trace");
});

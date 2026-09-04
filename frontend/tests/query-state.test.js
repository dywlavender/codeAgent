import test from "node:test";
import assert from "node:assert/strict";
import { isActiveRun, mergeConversations, turnFromRun, watchRun } from "../src/lib/query-state.js";

test("restored running and cancelling tasks remain loading", () => {
  for (const status of ["running", "cancelling"]) {
    assert.equal(isActiveRun({ status }), true);
    assert.equal(turnFromRun({ id: "r", status }).status, "loading");
  }
  assert.equal(turnFromRun({ id: "r", status: "cancelled" }).status, "cancelled");
  assert.equal(turnFromRun({ id: "r", status: "failed", error: "failure" }).error, "failure");
});

test("watch restored task until cancellation, replacing snapshots", async () => {
  const controller = new AbortController();
  const snapshots = ["running", "cancelling", "cancelled"].map((status) => ({ runId: "r", status }));
  const updates = [];
  const result = await watchRun("r", {
    signal: controller.signal, getRun: async () => snapshots.shift(),
    onUpdate: (run) => updates.push(run.status), wait: async () => {},
  });
  assert.deepEqual(updates, ["running", "cancelling", "cancelled"]);
  assert.equal(result.status, "cancelled");
});

test("aborted watcher never applies a late response", async () => {
  const controller = new AbortController();
  let updates = 0;
  const result = await watchRun("r", { signal: controller.signal,
    getRun: async () => { controller.abort(); return { status: "completed" }; },
    onUpdate: () => updates++, wait: async () => {},
  });
  assert.equal(result, null);
  assert.equal(updates, 0);
});

test("conversation pages append and deduplicate by conversation, not run", () => {
  const items = mergeConversations([{ conversationId: "c1", id: "old" }], [
    { conversationId: "c1", id: "new" }, { conversationId: "c2", id: "r2" },
  ]);
  assert.deepEqual(items.map((item) => item.id), ["new", "r2"]);
});

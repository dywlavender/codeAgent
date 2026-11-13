import test from "node:test";
import assert from "node:assert/strict";
import { request } from "../src/lib/api.js";

test("retrieval writes use the active project's route and admin credential", async () => {
  const original = { window: globalThis.window, sessionStorage: globalThis.sessionStorage, fetch: globalThis.fetch };
  const calls = [];
  try {
    globalThis.window = { location: { search: "?projectId=project-a" } };
    globalThis.sessionStorage = { getItem: (key) => key === "knowledgeAdminToken:project-a" ? "project-a-token" : null };
    globalThis.fetch = async (path, options) => {
      calls.push({ path, options });
      return { ok: true, status: 200, json: async () => ({ id: "retrieval-example" }) };
    };
    await request("/api/retrieval-suites", { method: "POST", body: "{}" });
    await request("/api/retrieval-evaluations", { method: "POST", body: "{}" });
    assert.equal(calls[0].path, "/api/projects/project-a/retrieval-suites");
    assert.equal(calls[1].path, "/api/projects/project-a/retrieval-evaluations");
    for (const { options } of calls) {
      assert.equal(options.headers.Authorization, "Bearer project-a-token");
      assert.equal(options.headers["X-Project-Id"], "project-a");
    }
  } finally { Object.assign(globalThis, original); }
});

test("a rejected retrieval credential only clears that project's saved token", async () => {
  const original = { window: globalThis.window, sessionStorage: globalThis.sessionStorage, fetch: globalThis.fetch };
  const removed = [];
  try {
    globalThis.window = { location: { search: "?projectId=project-b" } };
    globalThis.sessionStorage = { getItem: () => "old-token", removeItem: (key) => removed.push(key) };
    globalThis.fetch = async () => ({ ok: false, status: 401, json: async () => ({ error: "管理员认证失败" }) });
    await assert.rejects(request("/api/retrieval-evaluations", { method: "POST" }), /管理员/);
    assert.deepEqual(removed, ["knowledgeAdminToken:project-b"]);
  } finally { Object.assign(globalThis, original); }
});

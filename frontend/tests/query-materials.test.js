import test from "node:test";
import assert from "node:assert/strict";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { JSDOM } from "jsdom";
import { useQueryRun } from "../src/features/query/useQueryRun.js";

test("new questions and historical followups use combined materials despite saved legacy modes", async () => {
  const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/?projectId=alpha#/agent" });
  const previous = Object.fromEntries(["window", "document", "sessionStorage", "fetch", "IS_REACT_ACT_ENVIRONMENT"]
    .map((name) => [name, globalThis[name]]));
  Object.assign(globalThis, { window: dom.window, document: dom.window.document,
    sessionStorage: dom.window.sessionStorage, IS_REACT_ACT_ENVIRONMENT: true });
  sessionStorage.setItem("queryMode", "ast");
  sessionStorage.setItem("queryMode:alpha", "none");
  const sent = [];
  globalThis.fetch = async (url, options) => {
    if (url.endsWith("/conversations/legacy")) return new Response(JSON.stringify({ items: [{
      id: "old-run", conversationId: "legacy", question: "历史问题", answer: "历史答案",
      status: "completed", mode: "ast", astVersionId: "ast-v1",
    }] }));
    assert.ok(url.endsWith("/query/stream"));
    sent.push(JSON.parse(options.body));
    const run = { runId: `run-${sent.length}`, conversationId: sent.at(-1).conversationId || "current", mode: "combined" };
    return new Response(`event: run\ndata: ${JSON.stringify(run)}\n\nevent: result\ndata: ${JSON.stringify({
      ...run, status: "completed", answer: "本地回答", events: [],
    })}\n\n`);
  };
  let query;
  function Harness() {
    query = useQueryRun({ projectId: "alpha", navigate() {}, setError() {}, onHistoryChanged: async () => {} });
    return null;
  }
  const root = createRoot(document.getElementById("root"));
  try {
    await act(async () => root.render(React.createElement(Harness)));
    await act(async () => query.submit("新问题"));
    assert.equal(sent[0].mode, "combined");
    await act(async () => query.restore({ conversationId: "legacy" }));
    assert.equal(query.turns[0].result.mode, "ast");
    assert.equal(query.turns[0].result.astVersionId, "ast-v1");
    await act(async () => query.submit("追问"));
    assert.equal(sent[1].conversationId, "legacy");
    assert.equal(sent[1].mode, "combined");
    assert.equal(query.turns[0].result.mode, "ast");
    assert.equal(query.turns[1].mode, "combined");
  } finally {
    await act(async () => root.unmount());
    dom.window.close();
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete globalThis[name];
      else globalThis[name] = value;
    }
  }
});

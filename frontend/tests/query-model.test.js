import test from "node:test";
import assert from "node:assert/strict";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { JSDOM } from "jsdom";
import { useQueryRun } from "../src/features/query/useQueryRun.js";

test("model list follows Pi and the selected model is sent with the question", async () => {
  const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/?projectId=alpha#/agent" });
  const previous = Object.fromEntries(["window", "document", "sessionStorage", "fetch", "IS_REACT_ACT_ENVIRONMENT"]
    .map((name) => [name, globalThis[name]]));
  Object.assign(globalThis, { window: dom.window, document: dom.window.document,
    sessionStorage: dom.window.sessionStorage, IS_REACT_ACT_ENVIRONMENT: true });
  const sent = [];
  globalThis.fetch = async (url, options) => {
    if (url.endsWith("/pi/models")) return new Response(JSON.stringify({
      items: [
        { id: "deepseek/deepseek-v4-pro", name: "DeepSeek V4 Pro", provider: "deepseek" },
        { id: "zcode/glm-5.3-flash", name: "glm-5.3-flash", provider: "zcode" },
      ],
      defaultModel: "deepseek/deepseek-v4-pro",
      configuredModel: null,
      selectedModel: "deepseek/deepseek-v4-pro",
    }));
    assert.ok(url.endsWith("/query/stream"));
    sent.push(JSON.parse(options.body));
    const run = { runId: `run-${sent.length}`, conversationId: "current", mode: "combined" };
    return new Response(`event: run\ndata: ${JSON.stringify(run)}\n\nevent: result\ndata: ${JSON.stringify({
      ...run, status: "completed", answer: "回答", events: [],
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
    assert.deepEqual(query.models.map((item) => item.id),
      ["deepseek/deepseek-v4-pro", "zcode/glm-5.3-flash"]);
    assert.equal(query.defaultModel, "deepseek/deepseek-v4-pro");
    assert.equal(query.model, "");
    await act(async () => query.submit("默认模型问题"));
    assert.equal(sent[0].model, undefined);

    await act(async () => query.setModel("zcode/glm-5.3-flash"));
    assert.equal(sessionStorage.getItem("queryModel:alpha"), "zcode/glm-5.3-flash");
    await act(async () => query.submit("指定模型问题"));
    assert.equal(sent[1].model, "zcode/glm-5.3-flash");
    assert.equal(sent[1].mode, "combined");
  } finally {
    await act(async () => root.unmount());
    dom.window.close();
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete globalThis[name];
      else globalThis[name] = value;
    }
  }
});

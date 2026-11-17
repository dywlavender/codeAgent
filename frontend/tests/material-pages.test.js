import assert from "node:assert/strict";
import { once } from "node:events";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { MessageChannel } from "node:worker_threads";
import React, { act } from "react";
import { JSDOM } from "jsdom";
import { createServer } from "vite";

const dom = new JSDOM('<div id="root"></div>', {
  url: "http://localhost/?projectId=alpha", pretendToBeVisual: true,
});
const { window } = dom;
const { document } = window;
const channels = [];
let vite;
let createRoot;
let previous;

before(async () => {
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  const globals = {
    window, document, sessionStorage: window.sessionStorage, localStorage: window.localStorage,
    Element: window.Element, HTMLElement: window.HTMLElement, SVGElement: window.SVGElement,
    ShadowRoot: window.ShadowRoot, MutationObserver: window.MutationObserver,
    ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
    // Select and Menu schedule work on ports that Node keeps open until teardown.
    MessageChannel: class extends MessageChannel {
      constructor() {
        super();
        channels.push(this);
      }
    },
    getComputedStyle: (element) => window.getComputedStyle(element),
    requestAnimationFrame: window.requestAnimationFrame.bind(window),
    cancelAnimationFrame: window.cancelAnimationFrame.bind(window),
    IS_REACT_ACT_ENVIRONMENT: true,
  };
  previous = Object.fromEntries([...Object.keys(globals), "fetch"].map((key) => [key, globalThis[key]]));
  Object.assign(globalThis, globals);
  ({ createRoot } = await import("react-dom/client"));
  vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    server: { middlewareMode: true, hmr: false, watch: null },
  });
});

after(async () => {
  await vite?.close();
  window.close();
  for (const { port1, port2 } of channels) {
    port1.close();
    port2.close();
  }
  for (const [key, value] of Object.entries(previous || {})) {
    if (value === undefined) delete globalThis[key];
    else globalThis[key] = value;
  }
});

async function mount(t, Component, api, page) {
  window.history.replaceState(null, "", `/?projectId=alpha#/${page}`);
  window.sessionStorage.clear();
  globalThis.fetch = async (path, options) => api(new URL(path, window.location.href), options);
  const root = createRoot(document.getElementById("root"));
  t.after(() => act(async () => root.unmount()));
  await act(async () => root.render(React.createElement(Component, { projectId: "alpha" })));
}

function textElement(selector, label, parent = document) {
  const normalized = (value) => value.replace(/\s+/g, "");
  const node = [...parent.querySelectorAll(selector)].find((item) => normalized(item.textContent) === normalized(label));
  assert.ok(node, `找不到 ${selector} ${label}`);
  return node;
}

function click(node) {
  assert.ok(node, "点击目标应存在");
  return act(async () => node.dispatchEvent(new window.MouseEvent("click", { bubbles: true })));
}

function inputText(field, value) {
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(field), "value").set;
  return act(async () => {
    setter.call(field, value);
    field.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}

async function choose(label, option) {
  await act(async () => {
    document.querySelector(`[role="combobox"][aria-label="${label}"]`)
      .dispatchEvent(new window.MouseEvent("mousedown", { bubbles: true }));
  });
  await click(textElement(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option", option));
}

test("AST page searches symbols, expands neighbors, opens call-site source and clears a stale graph", async (t) => {
  const { AstPage } = await vite.ssrLoadModule("/src/features/ast/AstPage.jsx");
  const versionId = "ast-example";
  const symbol = (name, lineStart) => ({
    symbolId: name, name, qualifiedName: `Loan.${name}`, lineStart,
    kind: "METHOD", path: "repos/loan/Loan.java", repositoryId: "loan",
  });
  const apply = symbol("apply", 4);
  const check = symbol("check", 8);
  const validate = symbol("validate", 12);
  const relation = (from, to) => ({ from, to, relationId: `${from.name}-${to.name}`, relationType: "CALLS" });
  const requests = [];
  let stale = false;
  await mount(t, AstPage, (url) => {
    const path = url.pathname.replace("/api/projects/alpha/ast", "");
    if (!path) return Response.json({ status: stale ? "stale" : "available", currentVersionId: versionId,
      current: { versionId, sourceRepositories: [{ id: "loan", path: "repos/loan" }] } });
    assert.equal(url.searchParams.get("versionId"), versionId);
    requests.push([path, Object.fromEntries(url.searchParams)]);
    if (stale) return Response.json({ error: "AST 版本已过期" }, { status: 409 });
    if (path === "/symbols") return Response.json({ symbols: [apply] });
    if (path === "/symbols/apply/source") return Response.json({
      path: apply.path, focusLine: 5, lineStart: 5, lineEnd: 5, content: "check();",
    });
    if (path === "/symbols/check/source") return Response.json({
      path: apply.path, focusLine: 9, lineStart: 9, lineEnd: 9, content: "riskService.check();",
    });
    const outgoing = path === "/symbols/apply/relations" ? [relation(apply, check)] : [relation(check, validate)];
    const relations = url.searchParams.get("direction") === "incoming" ? [] : outgoing;
    const rawCalls = path === "/symbols/check/relations"
      ? [{ relationId: "check-raw", from: check, targetName: "riskService.check()",
          location: { lineStart: 9 }, status: "raw" }]
      : [];
    return Response.json({ relations, unresolved: [], rawCalls });
  }, "ast");

  await inputText(document.querySelector('input[aria-label="符号名称"]'), "Loan.apply");
  await click(textElement("button", "搜索"));
  await click(document.querySelector('[aria-label="Loan.apply · loan:4"]'));
  assert.ok(document.querySelector('[aria-label="CALLS：Loan.apply → Loan.check"]'));
  await click(document.querySelector('[aria-label$="Loan.check"]:not([aria-label^="CALLS"])'));
  await click(textElement("button", "展开下游"));
  assert.ok(document.querySelector('[aria-label="CALLS：Loan.check → Loan.validate"]'));
  assert.ok(document.querySelector('[aria-label="待解析 riskService.check()"]'), "未解析调用应以线索节点显示");

  await click(document.querySelector('[aria-label="待解析 riskService.check()"]'));
  let pane = document.querySelector(".ast-source-pane");
  assert.match(pane.textContent, /repos\/loan\/Loan.java:9/);
  assert.match(pane.textContent, /riskService\.check\(\);/);

  await click(document.querySelector('[aria-label="CALLS：Loan.apply → Loan.check"]'));
  pane = document.querySelector(".ast-source-pane");
  assert.match(pane.textContent, /repos\/loan\/Loan.java:5/);
  assert.match(pane.textContent, /check\(\);/);
  assert.deepEqual(requests.map(([path, query]) => [path, query.query || query.direction || query.relationId]), [
    ["/symbols", "Loan.apply"], ["/symbols/apply/relations", "incoming"],
    ["/symbols/apply/relations", "outgoing"], ["/symbols/check/relations", "outgoing"],
    ["/symbols/check/source", "check-raw"], ["/symbols/apply/source", "apply-check"],
  ]);

  stale = true;
  await click(textElement("button", "展开上游"));
  assert.equal(document.querySelector('[aria-label="AST 代码关系图"]'), null);
  await click(textElement("button", "查看索引状态"));
  assert.match(document.body.textContent, /源码已变化，请重新生成索引后查询关系/);
});

test("System Guide switches navigation views and publishes an edited draft on confirmation", async (t) => {
  const { SystemGuidesPage } = await vite.ssrLoadModule("/src/features/knowledge-admin/SystemGuidesPage.jsx");
  const app = (systemId, id, name) => ({
    id, name, repositoryId: "loan", path: `${systemId}/applications/${id}.md`, published: id !== "api",
  });
  const api = app("core", "api", "审批应用");
  const systems = [
    { id: "core", name: "贷款中台", path: "core/SYSTEM.md", published: true,
      applications: [api, app("core", "audit", "审计应用")] },
    { id: "ops", name: "运营系统", path: "ops/SYSTEM.md", published: true,
      applications: [app("ops", "job", "运营任务")] },
  ];
  const generated = "# 草稿\n\n## 导航摘要\n\n候选规则";
  const reviewed = "# 审批应用\n\n## 导航摘要\n\n人工核对后的规则";
  const saves = [];
  let published = null;
  await mount(t, SystemGuidesPage, (url, options) => {
    const base = "/api/projects/alpha/knowledge/system-guides";
    assert.ok(url.pathname.startsWith(base));
    const path = url.pathname.slice(base.length);
    const systemId = url.searchParams.get("systemId");
    const applicationId = url.searchParams.get("applicationId");
    const repositoryId = url.searchParams.get("repositoryId");
    if (path === "") return Response.json({ systems });
    if (path === "/draft") {
      assert.equal(options.method, "POST");
      assert.deepEqual(JSON.parse(options.body), { systemId: "core", applicationId: "api" });
      return Response.json({ content: generated, sourceFiles: ["repos/loan/Approval.java"], runId: "draft" });
    }
    if (path === "/file" && options.method === "PUT") {
      const body = JSON.parse(options.body);
      saves.push(body);
      published = body.content;
      api.published = true;
      return Response.json({ name: api.path, content: published });
    }
    assert.equal(systemId, "core");
    if (path === "/file") return Response.json({
      name: applicationId ? `core/applications/${applicationId}.md` : "core/SYSTEM.md",
      content: applicationId === "api" ? published : "已有文档",
    });
    assert.equal(path, "/preview");
    return Response.json({
      scope: repositoryId ? { repositoryIds: [repositoryId] } : { systemIds: [systemId],
        ...(applicationId ? { applicationIds: [applicationId] } : {}) },
      guidance: ["中台通用规则", applicationId !== "api" || repositoryId ? "审计应用规则" : null,
        repositoryId ? "运营任务规则" : null, applicationId !== "audit" ? published : null].filter(Boolean).join("\n"),
      documents: [],
    });
  }, "guides");

  const guidance = () => document.querySelector(".system-guide-effective").textContent;
  const previewView = () => document.querySelector('[aria-label="导航预览视角"]').closest(".ant-select").textContent;
  const editor = () => document.querySelector('textarea[aria-label="系统导航 Markdown"]');
  assert.match(guidance(), /中台通用规则\n审计应用规则/);
  assert.match(previewView(), /系统视角/);
  await click(textElement(".ant-tree-title", "审批应用"));
  assert.equal(guidance(), "中台通用规则");
  assert.match(previewView(), /应用视角/);
  await choose("导航预览视角", "仓库视角 · loan");
  assert.match(guidance(), /运营任务规则/);
  await choose("导航预览视角", "应用视角");
  assert.equal(guidance(), "中台通用规则");

  await click(textElement("button", "AI 生成草稿"));
  assert.equal(editor().value, generated);
  assert.equal(saves.length, 0);
  assert.equal(guidance(), "中台通用规则");
  await inputText(editor(), reviewed);
  await click(textElement("button", "发布导航"));
  assert.deepEqual(saves, [{ systemId: "core", applicationId: "api", previousContent: null, content: reviewed }]);
  assert.match(guidance(), /人工核对后的规则/);

  await click(textElement(".ant-tree-title", "审计应用 · 已发布"));
  assert.equal(editor().value, "已有文档");
  assert.equal(guidance(), "中台通用规则\n审计应用规则");
});

test("materials overview switches projects and displays each project's projection state", async (t) => {
  const { default: App } = await vite.ssrLoadModule("/src/App.jsx");
  await mount(t, App, (url) => {
    if (url.pathname === "/api/projects") return Response.json({
      items: [{ id: "alpha", name: "审批工程" }, { id: "beta", name: "运营工程" }],
    });
    if (url.pathname.endsWith("/workspace")) return Response.json({});
    if (url.pathname.endsWith("/conversations") || url.pathname.endsWith("/pi/models")) return Response.json({ items: [] });
    assert.match(url.pathname, /^\/api\/projects\/(alpha|beta)\/materials$/);
    const alpha = url.pathname.includes("/alpha/");
    return Response.json({
      systemGuides: { documentCount: 0, systemCount: 1 }, businessContext: { documentCount: 1 },
      ast: { status: "missing" }, projection: { status: alpha ? "stale" : "synced", importedDocumentCount: 1 },
      rawMaterials: { root: `/materials/${alpha ? "alpha" : "beta"}`,
        items: [{ name: alpha ? "审批需求.md" : "运营需求.md" }] }, sources: [],
    });
  }, "library");

  assert.match(document.body.textContent, /需要处理：业务关系图已落后于最新业务文档/);
  assert.match(document.body.textContent, /知识图谱待更新/);
  assert.match(document.body.textContent, /审批需求.md/);
  await choose("选择工程", "运营工程");
  assert.equal(new URL(window.location.href).searchParams.get("projectId"), "beta");
  await act(async () => {
    const navigated = once(window, "hashchange", { signal: AbortSignal.timeout(2000) });
    textElement('[role="menuitem"]', "知识总览").click();
    await navigated;
  });
  assert.match(document.body.textContent, /知识图谱已同步/);
  assert.match(document.body.textContent, /运营需求.md/);
  assert.doesNotMatch(document.body.textContent, /待更新|需要处理|审批需求.md/);
});

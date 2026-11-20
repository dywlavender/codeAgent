const PAGE_IDS = ["agent", "materials", "guides", "graph", "evaluation", "ast", "admin", "settings"];

const PAGE_LABELS = {
  agent: "问答",
  materials: "资料总览",
  guides: "系统导航",
  graph: "知识图谱",
  ast: "AST 索引",
  evaluation: "效果验证",
  admin: "业务知识",
  settings: "工程设置",
};

/* 资料页面共享顶栏；其余页面只显示自身名称。 */
const MATERIALS_PAGES = new Set(["materials", "guides", "graph", "ast", "admin"]);

function pageGroup(page) {
  return MATERIALS_PAGES.has(page) ? "资料" : null;
}

function pageFromHash() {
  const id = window.location.hash.replace(/^#\/?/, "").split("?", 1)[0];
  return PAGE_IDS.includes(id) ? id : "agent";
}

function setProjectInUrl(projectId) {
  const url = new URL(window.location.href);
  if (projectId) url.searchParams.set("projectId", projectId);
  else url.searchParams.delete("projectId");
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

export { PAGE_IDS, PAGE_LABELS, pageGroup, pageFromHash, setProjectInUrl };

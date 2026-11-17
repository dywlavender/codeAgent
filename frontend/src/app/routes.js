const PAGE_IDS = ["agent", "library", "guides", "graph", "evaluation", "ast", "admin", "settings"];

const PAGE_LABELS = {
  agent: "新对话",
  library: "资料总览",
  guides: "系统导航",
  graph: "知识图谱",
  ast: "AST 结构",
  evaluation: "效果验证",
  admin: "业务补充知识维护",
  settings: "工程设置",
};

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

export { PAGE_IDS, PAGE_LABELS, pageFromHash, setProjectInUrl };

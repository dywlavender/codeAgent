const PAGE_IDS = ["agent", "library", "graph", "evaluation", "ast", "admin", "settings"];

const PAGE_LABELS = {
  agent: "新对话",
  library: "项目资料",
  graph: "知识图谱",
  ast: "AST 管理",
  evaluation: "效果验证",
  admin: "业务补充知识维护",
  settings: "工程设置",
};

function pageFromHash() {
  const id = window.location.hash.replace(/^#\/?/, "");
  return PAGE_IDS.includes(id) ? id : "agent";
}

function setProjectInUrl(projectId) {
  const url = new URL(window.location.href);
  if (projectId) url.searchParams.set("projectId", projectId);
  else url.searchParams.delete("projectId");
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

export { PAGE_IDS, PAGE_LABELS, pageFromHash, setProjectInUrl };

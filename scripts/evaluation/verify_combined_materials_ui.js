// Run against the normal local app; this inspection never submits a model query.
async (page) => {
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1600, height: 1000 });
  const workspaceUrl = "/api/projects/loan-withdraw-acceptance/workspace";
  const response = await page.request.get(new URL(workspaceUrl, page.url()).href);
  const workspace = await response.json();
  assert(workspace.workspace.mode === "combined", "Production workspace is not combined");
  assert(workspace.sources.some((item) => ["baseline", "business_context"].includes(item.kind)
    && item.authorizationConfigured), "Business material not mounted");
  const ast = workspace.sources.find((item) => item.kind === "ast");
  assert(ast?.authorizationConfigured && ast.versionId, "Prepared AST not mounted with business material");

  await page.evaluate(() => sessionStorage.setItem("queryMode:loan-withdraw-acceptance", "ast"));
  await page.reload();
  await page.getByRole("button", { name: "选择调查范围", exact: true }).click();
  const menu = page.getByRole("tooltip");
  await menu.locator(".ctx-row").filter({ hasText: "AST 结构" }).waitFor({ state: "visible" });
  await menu.hover();
  assert((await menu.innerText()).includes("基础资料、业务补充和可用 AST 可同时检索"), "Combined material explanation missing");
  assert(await page.locator(".mode-chip, .mode-option").count() === 0, "Mutually exclusive mode selector remains");
  await page.screenshot({ path: "output/playwright/combined-materials.png", scale: "css" });

  // Simulate only the status response to inspect unavailable AST without
  // modifying a project, generating an index, or calling a model.
  const routePattern = "**" + workspaceUrl;
  await page.route(routePattern, async (route) => {
    const body = structuredClone(workspace);
    body.workspace.astStatus = "stale";
    body.workspace.astVersionId = null;
    body.sources = body.sources.map((item) => item.kind === "ast"
      ? { ...item, status: "MISSING", availability: "stale", versionId: null, authorizationConfigured: false } : item);
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  try {
    await page.reload();
    await page.getByRole("button", { name: "选择调查范围", exact: true }).click();
    await menu.getByText("待更新", { exact: true }).waitFor({ state: "visible" });
    await menu.hover();
    assert((await menu.innerText()).includes("基础资料、业务补充和可用 AST 可同时检索"), "Other materials disappeared with stale AST");
    await page.screenshot({ path: "output/playwright/combined-materials-stale.png", scale: "css" });
  } finally {
    await page.unroute(routePattern);
    await page.evaluate(() => sessionStorage.removeItem("queryMode:loan-withdraw-acceptance"));
    await page.reload();
  }
  assert(errors.length === 0, "Browser runtime errors: " + errors.join("; "));
  return { mode: workspace.workspace.mode, astVersionId: ast.versionId, preparedAstWithBusinessContext: true,
    selectorRemoved: true, staleStatusDisplayed: true, externalModelCalls: 0, browserErrors: errors };
}

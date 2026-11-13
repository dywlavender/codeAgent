// Run with playwright-cli run-code --filename after opening the local workbench.
async (page) => {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const selectDrawdown = async () => {
    await page.getByRole("combobox", { name: "选择入口接口", exact: true }).fill("GET /api/drawdowns");
    await page.getByRole("option", { name: "GET /api/drawdowns/{channelRefNo}", exact: true }).click();
    await page.getByRole("button", { name: "接口 ChannelController.drawdownDetail", exact: true }).waitFor({ state: "visible" });
  };
  await selectDrawdown();
  const canvas = page.getByRole("group", { name: "接口调用链", exact: true });
  assert(await canvas.locator('[data-item="node"]').count() === 4, "Expected controller → service → client → remote endpoint");
  const http = page.getByRole("button", { name: "HTTP 调用：MiddleClient.drawdownDetail → DrawdownController.detail", exact: true });
  await http.click();
  const dialog = page.getByRole("dialog", { name: "HTTP 调用", exact: true });
  await dialog.locator("code").filter({ hasText: "http://localhost:8092" }).waitFor({ state: "visible" });
  assert((await dialog.locator("code").allTextContents()).some((text) => text.includes("@GetMapping")), "Target handler evidence missing");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();

  const fit = page.getByRole("button", { name: "适应画布", exact: true });
  await fit.click();
  const initial = await canvas.getAttribute("viewBox");
  await page.getByRole("button", { name: "放大调用链", exact: true }).click();
  assert(initial !== await canvas.getAttribute("viewBox"), "Zoom had no effect");
  await fit.click();
  assert(initial === await canvas.getAttribute("viewBox"), "Fit did not restore viewport");
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + 80, box.y + 70);
  await page.mouse.down();
  await page.mouse.move(box.x + 150, box.y + 105, { steps: 8 });
  await page.mouse.up();
  assert(initial !== await canvas.getAttribute("viewBox"), "Panning had no effect");
  await fit.click();

  await page.getByRole("button", { name: "接口 DrawdownController.detail", exact: true }).click();
  await page.getByRole("button", { name: "从此接口继续查看", exact: true }).click();
  await page.getByRole("button", { name: "服务方法 DrawdownService.detail", exact: true }).waitFor({ state: "visible" });
  assert(await canvas.locator('[data-item="node"]').count() === 2, "Remote endpoint expansion is incorrect");

  await page.getByText("业务关系", { exact: true }).click();
  await page.getByRole("img", { name: "知识图谱关系视图", exact: true }).waitFor({ state: "visible" });
  await page.getByText("调用链", { exact: true }).click();
  await selectDrawdown();
  await page.screenshot({ path: "output/playwright/call-chain-final.png", scale: "css", animations: "disabled" });
  await http.click();
  await page.getByRole("dialog", { name: "HTTP 调用", exact: true }).locator("code").filter({ hasText: "http://localhost:8092" }).waitFor({ state: "visible" });
  await page.screenshot({ path: "output/playwright/call-chain-evidence-final.png", scale: "css", animations: "disabled" });
  assert(errors.length === 0, "Browser runtime errors: " + errors.join("; "));
  return { callSpine: true, httpEvidence: true, zoom: true, pan: true,
    downstream: true, businessGraphPreserved: true, browserErrors: errors };
}

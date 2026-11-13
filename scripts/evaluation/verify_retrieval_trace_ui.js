// Open the isolated preview prepared by preview_retrieval_trace.py first.
async (page) => {
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const complete = page.getByRole("button", { name: /^检索调度与轨迹回看：/ }).first();
  await complete.click();
  await page.getByRole("button", { name: "查看调用轨迹", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "调用轨迹", exact: true });
  await drawer.getByRole("button", { name: "模型调用 1", exact: true }).waitFor({ state: "visible" });
  await drawer.getByRole("button", { name: "模型调用 1", exact: true }).click();
  const inspector = drawer.getByRole("region", { name: "调用详情", exact: true });
  const request = await inspector.innerText();
  assert(request.includes("TraceResponseFields"), "Program response evidence missing from actual model request");
  assert(request.includes("TraceBusinessRules"), "Business branch evidence missing from actual model request");
  assert(request.includes("HTTP_RESPONSE"), "Cross-system connection evidence missing");
  await page.screenshot({ path: "output/playwright/retrieval-trace-model.png", animations: "disabled", scale: "css" });

  await drawer.getByText("工具", { exact: true }).click();
  await drawer.getByRole("button", { name: "TraceResponseFields", exact: true }).click();
  assert((await inspector.innerText()).includes("完整工具结果"), "Full tool body is not inspectable");
  assert((await inspector.innerText()).includes("channelRefNo"), "Expected real source missing from tool result");
  await inspector.locator("details").filter({ hasText: "源码原文" }).locator("summary").click();
  assert((await inspector.innerText()).includes("ChannelController.java"), "Numbered source originals cannot be expanded");
  await page.screenshot({ path: "output/playwright/retrieval-trace-tool.png", animations: "disabled", scale: "css" });

  await drawer.getByText("调度", { exact: true }).click();
  const decisions = drawer.getByRole("navigation", { name: "调用步骤", exact: true }).getByRole("button");
  assert(await decisions.count() > 0, "Scheduling decisions missing");
  await decisions.first().click();
  assert((await inspector.innerText()).includes("needs"), "Initial retrieval requirements missing");
  await drawer.getByRole("tab", { name: "调查事项", exact: true }).click();
  assert((await drawer.getByRole("tabpanel").innerText()).includes("semanticProof"), "Task state missing");
  await drawer.getByRole("tab", { name: "完整记录", exact: true }).click();
  assert((await drawer.getByRole("tabpanel").innerText()).includes("runtimeEvents"), "Full archive missing");
  const downloading = page.waitForEvent("download");
  await drawer.getByRole("button", { name: "导出 JSON", exact: true }).click();
  const download = await downloading;
  await download.saveAs("output/playwright/retrieval-trace-export.json");
  await drawer.getByRole("button", { name: "Close", exact: true }).click();

  await page.getByRole("button", { name: /^失败轨迹回看：/ }).first().click();
  await page.getByRole("button", { name: "查看调用轨迹", exact: true }).click();
  await drawer.getByRole("button", { name: "模型调用 1", exact: true }).click();
  assert((await inspector.innerText()).includes("本地模拟的模型请求失败"), "Failed model request cannot be reviewed");
  await page.screenshot({ path: "output/playwright/retrieval-trace-failure.png", animations: "disabled", scale: "css" });
  await drawer.getByRole("button", { name: "Close", exact: true }).click();

  await page.getByRole("button", { name: "开始新对话", exact: true }).click();
  await page.getByRole("textbox", { name: "输入问题", exact: true }).fill("本地禁用模型：验证新失败运行的轨迹入口");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await page.getByText("分析未完成", { exact: true }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "查看调用轨迹", exact: true }).click();
  await drawer.getByText("0 次模型请求", { exact: true }).waitFor({ state: "visible" });
  await drawer.getByRole("button", { name: "Close", exact: true }).click();
  await complete.click();
  await page.getByRole("button", { name: "查看调用轨迹", exact: true }).click();
  await drawer.getByText("工具", { exact: true }).click();
  await drawer.getByRole("button", { name: "TraceResponseFields", exact: true }).click();
  assert(errors.length === 0, "Browser runtime errors: " + errors.join("; "));
  return { modelInputs: true, toolOriginals: true, scheduling: true, tasks: true, export: download.suggestedFilename(),
    failedModelRequest: true, newFailureTraceEntry: true, browserErrors: errors };
}

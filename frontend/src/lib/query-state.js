export const isActiveRun = (run) => ["running", "cancelling"].includes(run?.status);

export const RUN_STATUS_LABEL = {
  running: "运行中",
  cancelling: "正在停止",
  completed: "已完成",
  failed: "失败",
  cancelled: "已停止",
};

export function turnFromRun(run) {
  return { id: run.runId || run.id, question: run.question,
    status: isActiveRun(run) ? "loading" : run.status === "completed" ? "success" : run.status === "cancelled" ? "cancelled" : "error",
    error: run.error, result: run, detail: run };
}

export function mergeConversations(current, page) {
  const items = new Map(current.map((item) => [item.conversationId, item]));
  for (const item of page) items.set(item.conversationId, item);
  return [...items.values()];
}

export async function watchRun(runId, { getRun, onUpdate, signal, wait = pause }) {
  while (!signal.aborted) {
    const run = await getRun(runId, signal);
    if (signal.aborted) return null;
    onUpdate(run);
    if (!isActiveRun(run)) return run;
    await wait(1000, signal);
  }
  return null;
}

function pause(ms, signal) {
  return new Promise((resolve) => {
    const done = () => { clearTimeout(timer); signal.removeEventListener("abort", done); resolve(); };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
    if (signal.aborted) done();
  });
}

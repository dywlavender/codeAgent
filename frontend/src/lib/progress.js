// Live updates and persisted history share the same stable step/block IDs.
export function buildProgress(events = []) {
  const tools = new Map();
  const texts = new Map();
  let phase = "starting";
  let label = "正在准备工作区";
  for (const event of events) {
    const type = event.eventType || event.event_type || event.type;
    const data = event.payload || {};
    if (type === "status") {
      phase = data.phase;
      label = data.label;
    } else if (type === "tool") {
      const id = data.id || `tool-${event.sequence}`;
      tools.set(id, { ...tools.get(id), ...data, id });
      phase = data.status === "running" ? "tool" : "thinking";
      label = data.status === "running" ? toolTitle(data) : "正在分析检索结果";
    } else if (type === "text") {
      texts.set(data.id, data.mode === "append" ? (texts.get(data.id) || "") + data.text : data.text);
      phase = "text";
      label = "正在回答";
    } else if (type === "system" && (data.subtype || event.subtype) === "thinking_tokens") {
      phase = "thinking";
      label = "正在分析";
    } else if (["tool_use", "tool_result", "assistant", "user"].includes(type)) {
      // Read old history without exposing raw runtime envelopes.
      const blocks = data.message?.content || [data];
      for (const block of blocks) {
        if (block.type === "tool_use" || type === "tool_use") {
          const id = block.id || `legacy-${event.sequence}`;
          tools.set(id, { id, name: block.name, input: block.input || { path: block.path }, status: "unknown" });
        } else if (block.type === "tool_result" || type === "tool_result") {
          const id = block.tool_use_id || block.id;
          const output = typeof block.content === "string" ? block.content : (block.content || []).map((item) => item.text || "").join("\n");
          if (tools.has(id)) tools.set(id, { ...tools.get(id), status: block.is_error ? "error" : "completed", output });
        }
      }
    }
  }
  return { tools: [...tools.values()], text: [...texts.values()].filter(Boolean).join("\n\n"), phase, label };
}

export function toolTitle(tool) {
  return ({ Read: "读取文件", Grep: "搜索代码", Glob: "查找文件" })[tool.name] || tool.name || "工具执行";
}

export function toolTarget(tool) {
  const input = tool.input || {};
  return input.file_path || input.pattern || input.path || "";
}

// 从工具调用里提取"实际读取过的文件"：只有带 file_path 的调用算文件证据，
// Grep/Glob 的目录参数和搜索模式不当作文件卡片展示。
export function fileEvidence(tools = []) {
  const byPath = new Map();
  for (const tool of tools) {
    const path = tool.input?.file_path || "";
    if (!path) continue;
    // Glob/Bash 常把目录塞进 file_path；没有扩展名末段按目录处理，不生成文件卡片
    const last = path.split("/").filter(Boolean).at(-1) || "";
    if (!last.includes(".")) continue;
    const entry = byPath.get(path) || { path, snippet: "" };
    if (tool.output && tool.output.trim()) {
      const text = tool.output.trim();
      entry.snippet = entry.snippet ? `${entry.snippet}\n…\n${text}` : text;
      if (entry.snippet.length > 1200) entry.snippet = `${entry.snippet.slice(0, 1200)}…`;
    }
    byPath.set(path, entry);
  }
  return [...byPath.values()];
}

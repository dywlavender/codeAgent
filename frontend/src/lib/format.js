export function groupEvidence(evidence = []) {
  return (evidence || []).reduce((groups, item) => {
    (groups[item.sourceType] ||= []).push(item);
    return groups;
  }, { CODE: [], BUSINESS: [], REQUIREMENT: [] });
}

export function buildQueryHistory(turns) {
  return turns
    .filter((turn) => turn.status === "success" && turn.result)
    .slice(-4)
    .map((turn) => ({
      question: turn.question,
      resolvedQuestion: turn.result.resolvedQuestion || turn.result.resolved_question || turn.result.answer?.resolvedQuestion || turn.result.answer?.resolved_question || turn.question,
      entities: turn.result.entities || turn.result.resolvedEntities || turn.result.resolved_entities || turn.result.answer?.entities || stateEntities(turn.detail?.state),
    }));
}

function stateEntities(state = {}) {
  return [...new Set([
    ...(state.business_objects || state.businessObjects || []),
    ...(state.processes || []),
    ...(state.systems || []),
    ...(state.field_hints || state.fieldHints || []),
    ...(state.table_hints || state.tableHints || []),
    ...(state.code_hints || state.codeHints || []),
  ])].slice(0, 24);
}

export function answerModeLabel(value) {
  if (!value) return "";
  const labels = {
    MODEL: "模型归纳",
    DIRECT: "直接回答",
    CONTEXTUAL: "上下文回答",
    CLARIFICATION: "需要澄清",
    EVIDENCE_ONLY: "证据回答",
    FALLBACK: "规则归纳",
  };
  return labels[String(value).toUpperCase()] || String(value);
}

export function normalizeSteps(detail) {
  if (!detail?.steps) return [];
  return detail.steps.map((step) => ({ ...step, tools: detail.toolCalls?.filter((tool) => tool.step_id === step.id) || [] }));
}

export function stepSummary(step) {
  try {
    const output = JSON.parse(step.output_summary_json || "{}");
    if (output.gaps !== undefined) return `${output.gaps} 个证据缺口`;
    if (output.calls !== undefined) return `${output.calls} 次工具调用`;
    if (output.count !== undefined) return `读取 ${output.count} 条证据`;
    if (output.intent) return `识别为 ${intentLabel(output.intent)}`;
    if (output.facts !== undefined) return `${output.facts} 条确定事实`;
  } catch { /* malformed historical summary */ }
  return step.evidence_count ? `累计 ${step.evidence_count} 条 Evidence` : `第 ${step.iteration} 轮`;
}

export function evidenceTitle(item) {
  if (item.sourceType === "CODE") return item.symbol || item.sourceId;
  if (item.sourceType === "BUSINESS") return `${item.sourceId} 业务事实`;
  return `${item.sourceId} 需求证据`;
}

export function formatLocation(value = {}) {
  return value.file ? `${value.file}:${value.startLine || ""}` : value.chunkId || value.knowledgeId || "";
}

export function sourceLabel(name) {
  return ({ CODE: "代码", BUSINESS: "业务知识", REQUIREMENT: "需求" })[name] || name;
}

export function statusLabel(name) {
  return ({ SUFFICIENT: "证据充分", INSUFFICIENT: "证据不足", CONFLICT: "存在冲突" })[name] || name;
}

export function intentLabel(name) {
  return ({ BUSINESS_LOGIC: "业务逻辑", DATA_TRACE: "数据流转", RULE_REASON: "规则原因", CROSS_PROCESS: "跨流程关系" })[name] || name;
}

export function stepLabel(name) {
  return ({
    UNDERSTAND: "理解问题",
    INITIAL_SEARCH: "三源初搜",
    INITIAL_SEARCH_TOOLS: "调用初搜工具",
    LOAD_SUMMARY: "加载摘要",
    EVALUATE: "评估证据",
    PLAN_EXPANSION: "规划补证据",
    EXPAND_EVIDENCE: "扩展证据",
    EXPAND_EVIDENCE_TOOLS: "调用扩展工具",
    READ_RAW_EVIDENCE: "读取原始证据",
    BUILD_ANSWER: "生成回答",
  })[name] || name;
}

export function formatTime(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

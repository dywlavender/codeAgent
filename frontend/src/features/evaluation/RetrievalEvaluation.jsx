import React, { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Collapse, Empty, Input, Select, Space, Table, Typography } from "antd";
import { request } from "../../lib/api.js";

const percent = (value) => value == null ? "—" : `${Math.round(value * 100)}%`;
const strategyName = (value) => ({ lexical: "词项 · 70 行", lexical_compact: "词项 · 30 行", lexical_methods: "词项 · 方法" }[value] || value);
const policyName = (value) => ({ ranked: "原顺序", diverse: "去重复排序", focused: "候选内段落选择", whole: "整段保留" }[value] || value);
const statusName = (value) => ({ completed: "已完成", running: "运行中", prepared: "已准备", failed: "失败", interrupted: "已中断" }[value] || value);

export function RetrievalEvaluation({ projectId, onRequireUnlock }) {
  const [suites, setSuites] = useState([]);
  const [runs, setRuns] = useState([]);
  const [suiteId, setSuiteId] = useState(null);
  const [runId, setRunId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [selected, setSelected] = useState(null);
  const [model, setModel] = useState("configured-default");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [waitingAnswer, setWaitingAnswer] = useState(false);
  const [connection, setConnection] = useState(null);
  const [contextPolicy, setContextPolicy] = useState("diverse");
  const [candidateStrategy, setCandidateStrategy] = useState(null);
  const [structureVersion, setStructureVersion] = useState(null);
  const [matchCandidateBudget, setMatchCandidateBudget] = useState(true);
  const answerBaseline = useRef(0);

  async function load() {
    const [library, history] = await Promise.all([request("/api/retrieval-suites"), request("/api/retrieval-evaluations")]);
    setSuites(library.items); setRuns(history.items); setConnection(library.connection);
    setSuiteId((current) => current || library.items[0]?.id || null);
    setRunId((current) => current || history.items[0]?.id || null);
  }
  function failed(reason) {
    setError(reason.message);
    if (/管理员|401|credential/.test(reason.message)) onRequireUnlock?.();
  }
  useEffect(() => {
    let active = true;
    setSuites([]); setRuns([]); setSuiteId(null); setRunId(null); setDetail(null); setSelected(null); setWaitingAnswer(false); setCandidateStrategy(null); setStructureVersion(null);
    // Abort stale project responses, including a project switch during an import.
    const controller = new AbortController();
    Promise.all([request("/api/retrieval-suites", {}, controller.signal), request("/api/retrieval-evaluations", {}, controller.signal)])
      .then(([library, history]) => { if (active) { setSuites(library.items); setRuns(history.items); setConnection(library.connection); setSuiteId(library.items[0]?.id || null); setRunId(history.items[0]?.id || null); } })
      .catch((reason) => { if (active) failed(reason); });
    return () => { active = false; controller.abort(); };
  }, [projectId]); // eslint-disable-line

  useEffect(() => {
    if (!runId) return undefined;
    let active = true;
    let timer;
    async function poll() {
      try {
        const value = await request(`/api/retrieval-evaluations/${runId}`);
        if (!active) return;
        setDetail(value);
        if (value.status === "running" || waitingAnswer || value.answers.some((a) => a.status === "running")) timer = setTimeout(poll, 1500);
        if (waitingAnswer && value.answers.length > answerBaseline.current && value.answers.every((a) => a.status !== "running")) setWaitingAnswer(false);
      } catch (reason) { if (active) failed(reason); }
    }
    poll();
    return () => { active = false; clearTimeout(timer); };
  }, [runId, waitingAnswer]); // eslint-disable-line

  async function importFile(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true); setError("");
    try {
      const suite = JSON.parse(await file.text());
      await request("/api/retrieval-suites", { method: "POST", body: JSON.stringify(suite) });
      await load(); setSuiteId(suite.id);
    } catch (reason) { failed(reason); } finally { setBusy(false); }
  }
  async function start() {
    setBusy(true); setError(""); setSelected(null);
    try {
      const result = await request("/api/retrieval-evaluations", { method: "POST", body: JSON.stringify({ suiteId, budgets: [4000, 12000] }) });
      setRunId(result.id); setDetail(null); await load();
    } catch (reason) { failed(reason); } finally { setBusy(false); }
  }
  async function open(row) {
    try { setSelected(await request(`/api/retrieval-evaluations/${runId}/cases/${row.caseId}?strategy=${row.strategy}&budget=${row.budgetChars}`)); }
    catch (reason) { failed(reason); }
  }
  async function compareContexts() {
    setBusy(true); setError(""); setSelected(null);
    try {
      const result = await request(`/api/retrieval-evaluations/${runId}/context-comparisons`, {
        method: "POST", body: JSON.stringify({ sourceStrategy: candidateSource, contextPolicy, budgets: [12000], split: "development" }),
      });
      setDetail((current) => current?.id === runId ? { ...current, strategies: [result, ...current.strategies] } : current);
    } catch (reason) { failed(reason); } finally { setBusy(false); }
  }
  async function saveStructure() {
    setBusy(true); setError("");
    try {
      const result = await request(`/api/retrieval-evaluations/${runId}/structure-snapshots`, { method: "POST", body: "{}" });
      setStructureVersion(result.versionId);
      setDetail((current) => current?.id === runId ? { ...current, structures: [result, ...(current.structures || [])] } : current);
    } catch (reason) { failed(reason); } finally { setBusy(false); }
  }
  async function compareMethods() {
    setBusy(true); setError(""); setSelected(null);
    try {
      const result = await request(`/api/retrieval-evaluations/${runId}/method-comparisons`, {
        method: "POST", body: JSON.stringify({ structureVersionId: selectedStructure?.versionId, contextPolicy: "ranked", compareTo: "lexical", budgets: [12000], split: "development", matchCandidateBudget, candidateLimit: matchCandidateBudget ? 500 : 40 }),
      });
      setCandidateStrategy(result.strategy);
      setDetail((current) => current?.id === runId ? { ...current, strategies: [result, ...current.strategies] } : current);
    } catch (reason) { failed(reason); } finally { setBusy(false); }
  }
  async function answer() {
    setError("");
    try {
      answerBaseline.current = detail?.answers.length || 0;
      await request(`/api/retrieval-evaluations/${runId}/answers`, { method: "POST", body: JSON.stringify({ caseId: selected.caseId, strategy: selected.strategy, budget: selected.budgetChars, model }) });
      setWaitingAnswer(true);
    } catch (reason) { failed(reason); }
  }
  const rows = detail?.strategies.flatMap((s) => s.items) || [];
  const candidateSource = detail?.strategies.some((s) => s.strategy === candidateStrategy) ? candidateStrategy : (detail?.strategies.find((s) => s.strategy === "lexical") || detail?.strategies[0])?.strategy;
  const selectedStructure = detail?.structures?.find((s) => s.versionId === structureVersion) || detail?.structures?.[0];
  const columns = [
    { title: "问题", dataIndex: "question", render: (text, row) => <Button type="link" style={{ whiteSpace: "normal", textAlign: "left", height: "auto" }} onClick={() => open(row)}>{text}</Button> },
    { title: "集合", dataIndex: "split", width: 80, render: (v) => v === "holdout" ? "保留题" : "开发题" },
    { title: "检索策略", width: 130, render: (_, r) => strategyName(r.retrievalStrategy || r.strategy) },
    { title: "上下文选择", width: 150, render: (_, r) => <span title={r.strategy}>{policyName(r.contextPolicy || "ranked")}</span> },
    { title: "字符预算", dataIndex: "budgetChars", width: 100 },
    { title: "候选覆盖", width: 100, render: (_, r) => percent(r.candidateMetrics.coverage) },
    { title: "上下文覆盖", width: 120, render: (_, r) => percent(r.contextMetrics.coverage) },
    { title: "覆盖变化", width: 110, render: (_, r) => r.comparison ? `${r.comparison.coverageDelta > 0 ? "+" : ""}${percent(r.comparison.coverageDelta)}` : "—" },
  ];
  return <Space direction="vertical" size="middle" style={{ width: "100%" }}>
    {error && <Alert type="error" showIcon title={error} />}
    <Card className="panel-card">
      <Space wrap>
        <Select aria-label="检索题库" style={{ minWidth: 240 }} value={suiteId} onChange={setSuiteId} placeholder="导入证据标注题库" options={suites.map((s) => ({ value: s.id, label: `${s.name} · ${s.caseCount} 题${s.annotationStatus === "superseded" ? "（已修订）" : ""}`, disabled: s.annotationStatus === "superseded" }))} />
        <Button type="primary" loading={busy} disabled={!suiteId} onClick={start}>运行检索基准</Button>
        <Button onClick={() => document.getElementById("retrieval-suite-upload").click()}>导入 JSON 题库</Button>
        <input id="retrieval-suite-upload" hidden type="file" accept=".json" onChange={importFile} />
        <Select aria-label="检索批次" style={{ minWidth: 240 }} value={runId} onChange={(id) => { setRunId(id); setDetail(null); setSelected(null); setCandidateStrategy(null); setStructureVersion(null); }} placeholder="历史批次" options={runs.map((r) => ({ value: r.id, label: `${r.id} · ${statusName(r.status)}` }))} />
      </Space>
      <Space wrap style={{ marginTop: 12 }}>
        <Select aria-label="方法位置版本" value={selectedStructure?.versionId} onChange={setStructureVersion} style={{ width: 280 }} placeholder="先显式保存已有方法位置" options={(detail?.structures || []).map((s) => ({ value: s.versionId, label: `${s.versionId} · ${s.methodCount} 个方法` }))} />
        <Button onClick={saveStructure} loading={busy} disabled={detail?.status !== "completed"}>保存已有方法位置</Button>
        <Checkbox checked={matchCandidateBudget} onChange={(e) => setMatchCandidateBudget(e.target.checked)}>匹配旧候选原文预算</Checkbox>
        <Button onClick={compareMethods} loading={busy} disabled={detail?.status !== "completed" || !selectedStructure || !detail?.strategies.some((s) => s.strategy === "lexical")}>对照方法单元</Button>
        {selectedStructure && <Typography.Text type="secondary">{selectedStructure.fileCount} 个文件已核对</Typography.Text>}
      </Space>
      <Space wrap style={{ marginTop: 12 }}>
        <Select aria-label="候选来源策略" value={candidateSource} onChange={setCandidateStrategy} style={{ width: 280 }} placeholder="候选来源" options={(detail?.strategies || []).map((s) => ({ value: s.strategy, label: `${strategyName(s.retrievalStrategy || s.strategy)} · ${policyName(s.contextPolicy || "ranked")} · ${s.strategy}` }))} />
        <Select aria-label="上下文选择策略" value={contextPolicy} onChange={setContextPolicy} style={{ width: 190 }} options={[{ value: "diverse", label: "去重复排序" }, { value: "focused", label: "候选内段落选择" }, { value: "whole", label: "整段保留" }]} />
        <Button onClick={compareContexts} loading={busy} disabled={detail?.status !== "completed" || !detail?.strategies.length}>复用候选对照</Button>
        <Typography.Text type="secondary">本批次候选 · 开发题 · 12,000 字符</Typography.Text>
      </Space>
      <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>分别检查候选召回和交给模型的上下文。覆盖率只衡量已标注的必要证据，不代表答案正确或材料已经充分。</Typography.Paragraph>
    </Card>
    {detail?.error && <Alert type="error" title={detail.error} />}
    {detail && <Typography.Text type="secondary">{statusName(detail.status)} · {detail.documents ?? "—"} 个文件 · {detail.annotationSource === "assistant_source_checked_not_human_reviewed" ? "助手核对源码，标注待人工复核" : `标注：${detail.annotationStatus || "待加载"}`}</Typography.Text>}
    {rows.length ? <Table rowKey={(r) => `${r.strategy}-${r.caseId}-${r.budgetChars}`} dataSource={rows} columns={columns} pagination={{ pageSize: 10 }} /> : <Empty description={detail?.status === "running" ? "正在冻结资料和执行检索" : "运行后查看每道题的证据覆盖"} />}
    {selected && <Card className="panel-card" title={selected.question}>
      <Typography.Paragraph>上下文 {selected.contextChars} 字符；缺少的标注：{selected.contextMetrics.groups.filter((g) => !g.covered).map((g) => g.id).join("、") || "已覆盖全部标注，充分性仍待复核"}</Typography.Paragraph>
      {selected.candidateCount != null && <Typography.Paragraph type="secondary">候选 {selected.candidateCount} 项，原文与定位头合计 {selected.candidateChars} 字符{selected.candidateBudgetChars != null ? `，预算 ${selected.candidateBudgetChars} 字符` : ""}。</Typography.Paragraph>}
      {selected.comparison && <Typography.Paragraph>与 {selected.comparison.strategy} 比较：{selected.comparison.candidatesUnchanged ? "候选相同" : "候选有变化"}；新增标注：{selected.comparison.gainedGroups.join("、") || "无"}；丢失标注：{selected.comparison.lostGroups.join("、") || "无"}。</Typography.Paragraph>}
      {selected.structureVersionId && <Typography.Paragraph type="secondary">方法位置版本：{selected.structureVersionId}</Typography.Paragraph>}
      <Typography.Paragraph type="secondary">配置模型：{connection?.enabled && connection?.provider === "openai" ? connection.model : "Claude Code 当前配置"}</Typography.Paragraph>
      <Space wrap><Input aria-label="固定证据答题模型" value={model} onChange={(e) => setModel(e.target.value)} placeholder="模型名称；configured-default 使用当前配置" style={{ width: 300 }} /><Button onClick={answer} loading={waitingAnswer} disabled={!model.trim()}>用相同证据答题</Button><Typography.Text type="secondary">{connection?.enabled && connection?.provider === "openai" ? "关闭搜索；输出上限 4096 token，接口不支持金额硬限。" : "关闭继续搜索；每次支出上限 $0.5。"}</Typography.Text></Space>
      <Collapse style={{ marginTop: 12 }} items={[{ key: "context", label: "查看交给模型的原文", children: <pre style={{ whiteSpace: "pre-wrap", maxHeight: 480, overflow: "auto" }}>{selected.context}</pre> }]} />
    </Card>}
    {detail?.answers.map((a, i) => <Card className="panel-card" key={i} title={`${a.caseId} · ${a.requestedModel} · ${statusName(a.status)}`}><Typography.Paragraph type="secondary">实际报告：{a.reportedModels?.join("、") || "未报告"}；{a.contextChars} 字符；{a.elapsedSeconds ?? "—"} 秒</Typography.Paragraph><Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>{a.answer || a.error || "正在答题"}</Typography.Paragraph></Card>)}
  </Space>;
}

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight, CaretRight, Check, Copy, Download, FileArrowUp, FileText, Play, Plus,
  SkipBack, Stop, Trash, WarningCircle,
} from "@phosphor-icons/react";
import {
  Alert, Button, Card, Checkbox, Collapse, Empty, Flex, Input, List, Modal, Popconfirm,
  Progress, Select, Segmented, Space, Switch, Tabs, Tag, Typography,
} from "antd";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { request } from "../lib/api.js";
import { formatRelative } from "../lib/format.js";

const ARMS = ["code_only", "optional", "ast"];
const ARM_ORDER = ["code_only", "optional", "ast", "overview", "overview_ast", "preloaded", "legacy", "revised"];
const ARM_LABEL = {
  code_only: "无主干", optional: "有主干", ast: "AST", overview: "仅总览", overview_ast: "总览 + AST",
  preloaded: "预加载主干", legacy: "旧版主干", revised: "新版主干",
};

function armsFor(detail) {
  const configured = Object.keys(detail?.arms || {});
  if (!configured.length) return ARMS;
  return [...ARM_ORDER.filter((arm) => configured.includes(arm)), ...configured.filter((arm) => !ARM_ORDER.includes(arm))];
}
const CATEGORY_LABEL = {
  business_investigation: "业务调查", business_flow: "业务流程", technical_flow: "流程分析",
  code_detail: "代码细节", code_control: "代码细节", business_rule: "业务规则", boundary: "业务边界",
  uncategorized: "未分类",
};
const ACTIVE = ["running", "cancelling"];
const FINISHED = ["completed", "cancelled", "failed", "interrupted"];

function adminError(reason, onRequireUnlock) {
  const text = String(reason?.message || "");
  if (text.includes("401") || text.includes("credential") || text.includes("管理员")) {
    onRequireUnlock?.();
    return "需要管理员口令后才能执行该操作。";
  }
  return text || "请求失败";
}

function duration(seconds) {
  if (seconds == null) return "—";
  const total = Math.round(seconds);
  return total < 90 ? `${total} 秒` : `${Math.floor(total / 60)} 分 ${total % 60} 秒`;
}

function statusText(status) {
  return { running: "运行中", cancelling: "正在停止", completed: "已完成", cancelled: "已停止", failed: "运行失败", interrupted: "已中断" }[status] || status || "未知";
}

function Dot({ status }) {
  return <span className={`eval-dot ${status === "completed" ? "ok" : ACTIVE.includes(status) ? "run" : status === "failed" ? "bad" : "idle"}`} />;
}

function exportMarkdown(name, markdown) {
  const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

function downloadTemplate() {
  const sample = {
    name: "我的测试案例",
    scope: "填写适用业务、系统或源码版本",
    cases: [{
      "案例编号": "case-001", "问题": "请填写实际发送给答题模型的问题", "参考答案": "请填写评审时使用的参考答案",
      "题目分类": "业务调查", "评分要点": "一条独立判断点\n另一条独立判断点", "适用范围": "融商贷 / 当前版本",
      "依据": "docs/xxx.md；src/xxx.java:10",
    }],
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(sample, null, 2)], { type: "application/json;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "测试案例模板.json";
  anchor.click();
  URL.revokeObjectURL(url);
}

async function filePayload(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]);
  return { filename: file.name, encoding: "base64", content: btoa(binary) };
}

function suggestChecks(answer) {
  const lines = String(answer || "").split(/\n+/).map((line) => line.replace(/^\s*(?:[-*•]|\d+[.、)])\s*/, "").trim()).filter((line) => line.length >= 8);
  const sentences = String(answer || "").split(/[。！？!?；;]+/).map((line) => line.trim()).filter((line) => line.length >= 10);
  const values = lines.length >= 2 ? lines : sentences;
  return [...new Set(values)].slice(0, 8).map((value) => value.length > 90 ? `${value.slice(0, 90)}…` : value);
}

// ---------------------------------------------------------------- 新建实验

function ExperimentSetup({ setup, onStart, starting, error, onGoAst, onGoCases }) {
  const suites = setup?.suites || [];
  const [suiteId, setSuiteId] = useState(setup?.lastUsedSuiteId || suites[0]?.id || null);
  const [repeats, setRepeats] = useState(1);
  const [selectedIds, setSelectedIds] = useState(null);
  const [judge, setJudge] = useState(true);
  const suite = suites.find((item) => item.id === suiteId);
  const cases = (suite?.cases || []).filter((item) => !item.disabled);
  const selected = selectedIds ? cases.filter((item) => selectedIds.includes(item.id)) : cases;
  const missingRubricCases = selected.filter((item) => !item.checks?.length);
  const astReady = setup?.ast?.status === "available";
  useEffect(() => {
    const next = setup?.lastUsedSuiteId || suites[0]?.id || null;
    setSuiteId(next);
    setSelectedIds(null);
  }, [setup?.lastUsedSuiteId, suites.length]); // eslint-disable-line
  const total = selected.length * 3 * repeats;
  const ready = Boolean(suiteId && selected.length && setup?.cli?.available && setup?.baseline?.readable && astReady
    && (!judge || !missingRubricCases.length));
  return (
    <div className="experiment-setup">
      <div className="page-heading compact-heading">
        <div><span className="page-kicker">对比实验</span><h1>一键运行三组实验</h1><p>同一批问题、源码和知识资料版本，分别使用无主干、有主干、AST 模式回答。</p></div>
      </div>
      {error && <Alert type="error" showIcon message={error} />}
      <Card className="panel-card launch-card">
        <div className="launch-fields">
          <label><span>题库</span><Select value={suiteId} placeholder="选择题库" onChange={(value) => { setSuiteId(value); setSelectedIds(null); }} options={suites.map((item) => ({ value: item.id, label: `${item.name} · ${item.caseCount} 题` }))} /></label>
          <label><span>执行范围</span><Select mode="multiple" value={selectedIds ?? cases.map((item) => item.id)} placeholder="全部启用案例" maxTagCount="responsive" onChange={(values) => setSelectedIds(values.length === cases.length ? null : values)} options={cases.map((item) => ({ value: item.id, label: `${item.id} · ${CATEGORY_LABEL[item.category] || item.category || "未分类"}` }))} /></label>
          <label><span>重复次数</span><Segmented value={repeats} onChange={setRepeats} options={[{ value: 1, label: "1 次" }, { value: 2, label: "2 次" }, { value: 3, label: "3 次" }]} /></label>
        </div>
        <div className="launch-mode-row"><span className="field-title">实验模式</span>{ARMS.map((arm) => <div className="mode-pill fixed" key={arm}><Check size={14} weight="bold" /> {ARM_LABEL[arm]}<small>{arm === "code_only" ? "源码、README、需求" : arm === "optional" ? "＋业务补充知识" : "＋结构总览与 AST 索引"}</small></div>)}</div>
        {missingRubricCases.length > 0 && <Alert style={{ marginTop: 14 }} type="warning" showIcon message={`有 ${missingRubricCases.length} 个案例没有评分要点，不能直接启动自动初评。`} description={<Space wrap><span>{missingRubricCases.slice(0, 8).map((item) => item.id).join("、")}{missingRubricCases.length > 8 ? "…" : ""}</span><Button type="link" onClick={onGoCases}>前往测试案例补齐评分要点 <ArrowRight size={13} /></Button></Space>} />}
        <div className="readiness-row"><div><span>资料状态</span><div className="readiness-items"><Tag color={setup?.businessContext?.readable ?? setup?.baseline?.readable ? "success" : "error"}>业务补充知识 {(setup?.businessContext?.readable ?? setup?.baseline?.readable) ? `${(setup?.businessContext || setup?.baseline).documents?.length || 0} 份` : "不可用"}</Tag><Tag color={astReady ? "success" : setup?.ast?.status === "stale" ? "warning" : "default"}>AST {astReady ? `${setup.ast.currentVersionId} · ${setup.ast.current?.generatedAt ? new Date(setup.ast.current.generatedAt).toLocaleString("zh-CN") : "可用"}` : setup?.ast?.status === "stale" ? "待更新" : "未生成"}</Tag></div></div>{!astReady && <Button type="link" onClick={onGoAst}>前往生成 AST <ArrowRight size={13} /></Button>}</div>
        <div className="launch-foot"><div><Checkbox checked={judge} onChange={(event) => setJudge(event.target.checked)}>启用自动初评</Checkbox><span>{judge ? `预计 ${total} 次答题 + ${total} 次初评` : `仅答题：预计 ${total} 次答题；完成后可手动复核`}</span></div><Button type="primary" icon={<Play size={14} weight="fill" />} disabled={!ready} loading={starting} onClick={() => onStart({ suiteId, repeats, comparison: "abc", judge, caseIds: selectedIds || undefined, astVersionId: setup.ast.currentVersionId })}>{judge ? "开始三组对比" : "开始三组答题"}</Button></div>
      </Card>
      {!suites.length && <Empty description="还没有测试案例，请先在“测试案例”导入题库" />}
      {!setup?.cli?.available && <Alert style={{ marginTop: 14 }} type="warning" showIcon message={`未找到 ${setup?.cli?.command || "claude"} 命令，当前无法执行实验。`} />}
    </div>
  );
}

// ---------------------------------------------------------------- 进度与总览

function BatchHeader({ detail, onStop, onRejudge, onRetry, onExport, onNew }) {
  const active = ACTIVE.includes(detail.status);
  const armCount = armsFor(detail).length;
  const hasFailedAnswers = (detail.rows || []).some((row) => Object.values(row.runs || {}).some((run) => run?.status === "failed"));
  return <>
    <div className="batch-header"><div><span className="page-kicker">实验批次</span><h1>{detail.suite?.name || "三组对比"} <span className="inline-status"><Dot status={detail.status} />{statusText(detail.status)}</span></h1><p>{detail.settings?.retryMode ? `只重试 ${detail.settings.retryJobCount || 0} 个失败答题任务 · 原批次 ${detail.settings.retryOf || "未知"}` : `${detail.settings?.questions || 0} 题 × ${armCount} 组 × ${detail.settings?.repeats || 1} 轮`} · {formatRelative(detail.createdAt)} · {duration(detail.elapsedSeconds)}</p></div><Space wrap>{active && <Popconfirm title="停止这批实验？" description="已完成答案和评分会保留，未开始任务不会继续安排。" okText="停止" cancelText="继续" onConfirm={onStop}><Button danger icon={<Stop size={14} />}>停止</Button></Popconfirm>}{FINISHED.includes(detail.status) && hasFailedAnswers && <Button onClick={onRetry}>重试失败答题</Button>}{FINISHED.includes(detail.status) && detail.settings?.judge !== false && <Popconfirm title="重新初评已有答案？" description="只重做评分，不重新答题；旧评分会保留。" okText="重新初评" cancelText="取消" onConfirm={onRejudge}><Button>重新初评</Button></Popconfirm>}{FINISHED.includes(detail.status) && <Button icon={<Download size={14} />} onClick={() => onExport("review")}>导出报告</Button>}<Button icon={<SkipBack size={14} />} onClick={onNew}>新建实验</Button></Space></div>
  </>;
}

function ProgressLanes({ detail }) {
  const answers = detail.progress?.answers || {};
  const judge = detail.progress?.judge || {};
  const answerPercent = answers.total ? Math.round((answers.completed + answers.failed + answers.cancelled) * 100 / answers.total) : 0;
  const judgePercent = judge.total ? Math.round((judge.completed + judge.failed) * 100 / judge.total) : 0;
  return <div className="progress-lanes"><div className="progress-lane"><div><b>答题进度</b><span>{answers.completed || 0} 完成 · {answers.failed || 0} 失败 · {answers.running || 0} 进行中 / {answers.total || 0}</span></div><Progress percent={answerPercent} status={answers.failed ? "exception" : "active"} /></div>{detail.settings?.judge !== false ? <div className="progress-lane"><div><b>评分进度</b><span>{judge.completed || 0} 有效初评 · {judge.failed || 0} 失败 / {judge.total || 0}</span></div><Progress percent={judgePercent} status={judge.failed ? "exception" : "active"} /></div> : <div className="progress-lane progress-lane-muted"><div><b>评分进度</b><span>本批未启用自动初评，可在逐题详情中人工复核。</span></div></div>}</div>;
}

function MetricValue({ value, possible }) { return value == null ? <span className="metric-pending">待评</span> : <b>{possible ? `${value}/${possible}` : value}</b>; }

function SummaryTable({ detail, source }) {
  const summary = detail.summaries?.[source];
  if (source === "review" && !(detail.reviewCoverage?.reviewed)) return <Alert type="info" showIcon message="尚无人工复核记录，先显示模型初评或打开逐题详情进行复核。" />;
  if (!summary) return <Alert type="info" showIcon message={source === "review" ? "尚无人工复核记录，先显示模型初评或逐题复核。" : detail.settings?.judge === false ? "本批未启用自动初评。" : "尚无有效初评。评分失败不能当作零分。"} />;
  const arms = armsFor(detail);
  const completed = {};
  for (const row of detail.rows || []) for (const arm of arms) completed[arm] = (completed[arm] || 0) + (row.runs?.[arm]?.status === "completed" ? 1 : 0);
  const planned = (detail.settings?.questions || 0) * (detail.settings?.repeats || 1);
  const metricRows = [
    ["答题完成数", arm => `${completed[arm] || 0}/${planned}`],
    ["有效评分数", () => `${summary.qualityBlocks}/${summary.plannedBlocks}`],
    ["核心结论正确性", arm => <MetricValue value={summary.aggregates?.[arm]?.metrics?.core?.score} possible={summary.aggregates?.[arm]?.metrics?.core?.possible} />],
    ["关键环节覆盖", arm => <MetricValue value={summary.aggregates?.[arm]?.metrics?.coverage?.score} possible={summary.aggregates?.[arm]?.metrics?.coverage?.possible} />],
    ["源码依据充分性", arm => <MetricValue value={summary.aggregates?.[arm]?.metrics?.evidence?.score} possible={summary.aggregates?.[arm]?.metrics?.evidence?.possible} />],
    ["平均答题耗时", arm => `${summary.aggregates?.[arm]?.meanSeconds ?? "—"}${summary.aggregates?.[arm]?.meanSeconds == null ? "" : " 秒"}`],
    ["工具调用数", arm => summary.aggregates?.[arm]?.toolCalls ?? "—"],
  ];
  return <>{detail.settings?.judge === false && <Alert style={{ marginBottom: 10 }} type="info" showIcon message="本批仅答题，未运行自动初评；质量指标不适用，答案和成本数据仍已保留。" />}<div className="summary-table" style={{ "--summary-arm-count": arms.length }}><div className="summary-row summary-head"><span>指标</span>{arms.map((arm) => <span key={arm}>{ARM_LABEL[arm] || arm}</span>)}</div>{metricRows.map(([label, render]) => <div className="summary-row" key={label}><span>{label}</span>{arms.map((arm) => <span key={arm}>{render(arm)}</span>)}</div>)}<div className="summary-foot">质量分母只统计参与同一配对且都有有效评分的相同题目；失败评分不计为零分。AST 生成耗时单独记录：{detail.settings?.astVersionId ? `${detail.settings.astVersionId} · ${detail.protocol?.referencePreparation?.astGenerationSeconds || detail.settings.astGenerationSeconds || "—"} 秒` : "本批未使用"}。</div></div></>;
}

function ComparisonConclusion({ detail, source, comparison }) {
  const summary = detail.summaries?.[source];
  const control = comparison === "backboneAst" ? "optional" : "code_only";
  const treatment = comparison === "ast" ? "ast" : comparison === "backboneAst" ? "ast" : "optional";
  const pairs = (summary?.pairs || []).filter((item) => item.arm === treatment && (item.controlArm || "code_only") === control);
  const scored = pairs.filter((item) => item.scored);
  if (!summary || !pairs.length) return null;
  const better = scored.filter((item) => item.scoreDelta > 0).length;
  const worse = scored.filter((item) => item.scoreDelta < 0).length;
  const same = scored.length - better - worse;
  const timedPairs = pairs.filter((item) => item.secondsDelta != null);
  const timeDelta = timedPairs.length ? Math.round((timedPairs.reduce((total, item) => total + item.secondsDelta, 0) / timedPairs.length) * 10) / 10 : null;
  return <div className="comparison-note"><b>{ARM_LABEL[treatment]} vs {ARM_LABEL[control]}</b><span>在 {pairs.length} 道完成配对题中，{scored.length} 道有有效评分；{ARM_LABEL[treatment]} 组 {better} 道更好、{same} 道持平、{worse} 道更差。</span>{timeDelta != null && <span>平均耗时 {timeDelta >= 0 ? "增加" : "减少"} {Math.abs(timeDelta)} 秒。</span>}<small>未参与配对或评分失败的题目不用于质量结论；质量、速度、调用数分开看。</small></div>;
}

// ---------------------------------------------------------------- 逐题详情

function parseEvidence(text) {
  const match = /([^\s:：]+):([0-9]+)/.exec(String(text || ""));
  return match ? { path: match[1], line: Number(match[2]) } : null;
}

function Trace({ run }) {
  if (!run?.trace?.length) return <span className="muted-text">暂无调查轨迹</span>;
  return <Collapse ghost size="small" items={[{ key: "trace", label: `查看调查轨迹 · ${run.trace.length} 步`, children: <div className="trace-list">{run.trace.map((item, index) => <div className="trace-row" key={item.id || index}><code>{item.name}</code><span>{item.input || ""}</span><small>{item.status === "error" ? "失败" : "完成"}</small></div>)}</div> }]} />;
}

function ReviewBlock({ run, checks, arm, draft, setDraft, onSave, saving }) {
  const modelChecks = run?.modelReview?.checks || [];
  const revisions = run?.reviews || [];
  const latest = revisions[revisions.length - 1];
  const displayChecks = latest?.checks?.map((value, index) => ({ met: value === 1, reason: latest.issues?.[index] || "人工复核记录", evidence: "人工复核" })) || modelChecks;
  if (!run || run.status !== "completed") return null;
  if (!checks.length) return <div className="review-block"><div className="review-title"><b>逐项评分</b><span>本批未启用自动初评</span></div><p className="muted-text">当前案例没有固定评分要点，因此没有可比较的质量分数。请先在测试案例中补齐并检查要点，再启动新的实验。</p></div>;
  const initialLabel = (item) => item ? (item.met ? "满足" : "不满足") : "未完成";
  return <div className="review-block"><div className="review-title"><b>逐项评分</b><span>{latest ? "人工复核（模型初评已保留）" : run.modelReview?.status === "completed" ? "模型初评" : "待评分"}</span></div>{checks.map((check, index) => { const item = displayChecks[index]; const modelItem = modelChecks[index]; const value = draft?.checks?.[index]; return <div className={`review-item ${item?.met ? "met" : item ? "miss" : "pending"}`} key={`${arm}-${index}`}><div className="review-check-line"><span>{index + 1}. {check}</span><Segmented size="small" value={value == null ? "pending" : value ? "yes" : "no"} onChange={(next) => setDraft({ ...draft, checks: draft.checks.map((current, position) => position === index ? next === "yes" ? 1 : next === "no" ? 0 : null : current) })} options={[{ value: "yes", label: "满足" }, { value: "no", label: "不满足" }, { value: "pending", label: "待核查" }]} /></div><div className="review-status-line"><small>模型初评：{initialLabel(modelItem)}</small><small>当前复核：{value == null ? "待核查" : value ? "满足" : "不满足"}</small></div>{item && <><p>{item.reason}</p>{item.evidence && <small>依据：{item.evidence}</small>}</>}</div>; })}<Input.TextArea value={draft?.issues || ""} onChange={(event) => setDraft({ ...draft, issues: event.target.value })} placeholder="遗漏与错误；一行一条" autoSize={{ minRows: 2, maxRows: 4 }} /><Button size="small" onClick={() => onSave(arm)} loading={saving}>保存人工复核</Button></div>;
}

function AnswerColumn({ arm, run, checks, draft, setDraft, onSave, saving, onEvidence }) {
  const status = run?.status || "pending";
  return <div className="answer-column"><div className="answer-column-head"><div><span className={`arm-marker arm-${arm}`} /> <b>{ARM_LABEL[arm]}</b></div><small>{status === "completed" ? `${duration(run.elapsedSeconds)} · ${run.toolCalls || 0} 工具` : status === "failed" ? "答题失败" : status === "running" ? "正在答题" : "未开始"}</small></div>{run?.error && <Alert type="error" showIcon message={run.error} />}{status === "completed" ? <div className="eval-answer-markdown"><Markdown remarkPlugins={[remarkGfm]}>{run.answer || "（无回答内容）"}</Markdown></div> : <div className="answer-placeholder">{status === "running" ? "正在生成答案…" : status === "failed" ? "本组没有可评分答案。" : "任务尚未开始。"}</div>}{run?.modelReview?.status === "failed" && <div className="review-failure">评分失败：{run.modelReview.error || "请人工复核"}</div>}{status === "completed" && <ReviewBlock run={run} checks={checks} arm={arm} draft={draft} setDraft={setDraft} onSave={onSave} saving={saving} />}{status === "completed" && <div className="answer-evidence"><Trace run={run} /><div className="reference-use">资料使用：{run.referenceUsage?.overviewInjected ? "总览已注入" : "未注入总览"} · 实际读取 {run.referenceUsage?.referencePathsAccessed?.length || 0} 份主干/AST 文件</div>{(run.modelReview?.checks || []).map((item, index) => item.evidence && <button className="evidence-link" key={index} onClick={() => onEvidence(item.evidence)}>查看源码依据 · {parseEvidence(item.evidence)?.path || item.evidence}</button>)}</div>}</div>;
}

function CaseDetail({ evalId, row, arms = ARMS, onChanged, onRequireUnlock }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [drafts, setDrafts] = useState({});
  const [savingArm, setSavingArm] = useState("");
  const [source, setSource] = useState(null);
  const armKey = arms.join("|");
  useEffect(() => { if (!evalId || !row) return; request(`/api/evaluations/${evalId}/cases/${row.caseId}/repeats/${row.repeat}`).then((value) => { setData(value); const next = {}; for (const arm of arms) { const reviews = value.runs?.[arm]?.reviews || []; const latest = reviews[reviews.length - 1]; next[arm] = { checks: latest?.checks ? [...latest.checks] : (value.case?.checks || []).map(() => null), issues: (latest?.issues || []).join("\n"), note: latest?.note || "" }; } setDrafts(next); }).catch((reason) => setError(reason.message)); }, [evalId, row?.caseId, row?.repeat, armKey]); // eslint-disable-line
  async function saveReview(arm) { const run = data?.runs?.[arm]; if (!run?.answerId) return; setSavingArm(arm); setError(""); try { await request(`/api/evaluations/${evalId}/reviews/${run.answerId}`, { method: "PUT", body: JSON.stringify({ checks: drafts[arm].checks, issues: drafts[arm].issues.split("\n").map((item) => item.trim()).filter(Boolean), note: drafts[arm].note, operatorType: "user" }) }); const fresh = await request(`/api/evaluations/${evalId}/cases/${row.caseId}/repeats/${row.repeat}`); setData(fresh); onChanged?.(); } catch (reason) { setError(adminError(reason, onRequireUnlock)); } finally { setSavingArm(""); } }
  async function openEvidence(text) { const parsed = parseEvidence(text); if (!parsed) { setError("该评分依据没有可定位的文件:行号"); return; } for (const item of data?.sources || []) { try { const value = await request(`/api/evaluations/${evalId}/sources/${item.id}?path=${encodeURIComponent(parsed.path)}&start=${Math.max(1, parsed.line - 12)}&end=${parsed.line + 12}`); setSource({ ...value, target: parsed.line }); return; } catch { /* try next snapshot */ } } setError(`冻结源码中没有定位到 ${parsed.path}`); }
  if (!data) return <div className="case-detail-loading">{error || "加载逐题详情…"}</div>;
  return <div className="case-detail">{error && <Alert type="error" showIcon message={error} />}<div className="reference-answer"><b>参考答案</b>{data.case?.referenceAnswer ? <Markdown remarkPlugins={[remarkGfm]}>{data.case.referenceAnswer}</Markdown> : <p className="muted-text">未填写</p>}<small>只提供给评审环节，不会发送给答题模型。</small></div><div className="rubric-strip"><b>统一评分要点</b>{data.case?.checks?.length ? data.case.checks.map((item, index) => <span key={index}>{index + 1}. {item}</span>) : <span>尚未设置评分要点</span>}</div><div className="answer-grid" style={{ "--answer-arm-count": arms.length }}>{arms.map((arm) => <AnswerColumn key={arm} arm={arm} run={data.runs?.[arm]} checks={data.case?.checks || []} draft={drafts[arm]} setDraft={(next) => setDrafts((current) => ({ ...current, [arm]: next }))} onSave={saveReview} saving={savingArm === arm} onEvidence={openEvidence} />)}</div><Modal open={Boolean(source)} title={`${source?.repositoryId || "源码"} · ${source?.path || ""}`} footer={null} onCancel={() => setSource(null)} width={780}><div className="source-lines">{source?.lines?.map((line) => <div className={line.n === source.target ? "target" : ""} key={line.n}><code>{line.n}</code><span>{line.text || " "}</span></div>)}</div></Modal></div>;
}

function PairCaseList({ detail, source, onOpen, expanded, onChanged, onRequireUnlock }) {
  const arms = armsFor(detail);
  const summary = detail.summaries?.[source];
  const pairMap = useMemo(() => Object.fromEntries((summary?.pairs || []).map((item) => [`${item.questionId}-r${item.repeat}-${item.arm}-${item.controlArm || "code_only"}`, item])), [summary]);
  return <div className="case-list"><div className="section-title"><h2>逐题比较</h2><span>{detail.rows?.length || 0} 个问题区组</span></div>{(detail.rows || []).map((row) => { const isOpen = expanded?.caseId === row.caseId && expanded?.repeat === row.repeat; const values = arms.map((arm) => row.runs?.[arm]); const scored = Object.values(pairMap).some((item) => item?.questionId === row.caseId && item?.repeat === row.repeat && item.scored); return <React.Fragment key={`${row.caseId}-${row.repeat}`}><button className={`case-row ${isOpen ? "open" : ""}`} onClick={() => onOpen(isOpen ? null : row)}><span className="case-row-id">{row.caseId}</span><span className="case-row-question">{row.question}</span><span className="case-row-meta">{CATEGORY_LABEL[row.category] || row.category || "未分类"} · {values.filter((item) => item?.status === "completed").length}/{arms.length} 完成 {scored ? "· 有效评分" : ""}</span><CaretRight size={15} className={isOpen ? "rotated" : ""} /></button>{isOpen && <CaseDetail evalId={detail.id} row={row} arms={arms} onChanged={onChanged} onRequireUnlock={onRequireUnlock} />}</React.Fragment>; })}</div>;
}

function BatchView({ detail, onStop, onRejudge, onRetry, onExport, onNew, onRefresh, onRequireUnlock }) {
  const [source, setSource] = useState("model");
  const [comparison, setComparison] = useState("backbone");
  const [expanded, setExpanded] = useState(null);
  const finished = FINISHED.includes(detail.status);
  const arms = armsFor(detail);
  const comparisonOptions = [
    { value: "all", label: arms.length === 3 ? "三组总览" : "全部组总览" },
    ...(arms.includes("optional") && arms.includes("code_only") ? [{ value: "backbone", label: "有主干 vs 无主干" }] : []),
    ...(arms.includes("ast") && arms.includes("code_only") ? [{ value: "ast", label: "AST vs 无主干" }] : []),
    ...(arms.includes("optional") && arms.includes("ast") ? [{ value: "backboneAst", label: "有主干 vs AST" }] : []),
  ];
  useEffect(() => { if (!comparisonOptions.some((item) => item.value === comparison)) setComparison("all"); }, [detail.id, arms.join("|")]); // eslint-disable-line
  useEffect(() => { if (detail.reviewCoverage?.reviewed) setSource("review"); }, [detail.reviewCoverage?.reviewed]);
  return <div className="batch-view"><BatchHeader detail={detail} onStop={onStop} onRejudge={onRejudge} onRetry={onRetry} onExport={onExport} onNew={onNew} />{(detail.error || detail.actionError) && <Alert style={{ marginBottom: 12 }} type="error" showIcon message={detail.error || detail.actionError} />}{!finished && <ProgressLanes detail={detail} />}<div className="result-toolbar"><div><span className="page-kicker">结果</span><h2>先看质量，再看成本</h2></div>{finished && <Segmented value={source} onChange={setSource} options={[{ value: "model", label: "模型初评" }, { value: "review", label: "人工复核" }]} />}<Select value={comparison} onChange={setComparison} options={comparisonOptions} /></div>{finished && comparison === "all" && <SummaryTable detail={detail} source={source} />}{finished && comparison !== "all" && <ComparisonConclusion detail={detail} source={source} comparison={comparison} />}{finished && detail.settings?.astVersionId && <div className="ast-cost-note">AST 资料版本：<b>{detail.settings.astVersionId}</b> · AST 生成耗时不计入答题耗时。资料实际使用记录放在逐题轨迹中。</div>}<PairCaseList detail={detail} source={source} onOpen={setExpanded} expanded={expanded} onChanged={onRefresh} onRequireUnlock={onRequireUnlock} /></div>;
}

// ---------------------------------------------------------------- 题库

function CaseLibrary({ setup, onChanged, onRequireUnlock }) {
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [importSummary, setImportSummary] = useState(null);
  const [mappingUpload, setMappingUpload] = useState(null);
  const [mappingColumns, setMappingColumns] = useState([]);
  const [mapping, setMapping] = useState({ question: "", referenceAnswer: "", id: "", category: "", checks: "", scope: "", evidence: "" });
  const [checksPreview, setChecksPreview] = useState(null);
  const inputRef = useRef(null);
  const suites = setup?.suites || [];
  useEffect(() => { if (selectedId === null && suites.length) setSelectedId(suites[0].id); if (selectedId && selectedId !== "new" && !suites.some((item) => item.id === selectedId)) setSelectedId(suites[0]?.id || null); }, [suites, selectedId]);
  useEffect(() => { const suite = suites.find((item) => item.id === selectedId); setDraft(suite ? JSON.parse(JSON.stringify(suite)) : selectedId === "new" ? { name: "", scope: "", origin: "real", cases: [] } : null); }, [selectedId, suites]);
  function updateCase(index, patch) { setDraft((value) => ({ ...value, cases: value.cases.map((item, position) => position === index ? { ...item, ...patch } : item) })); }
  async function save() { if (!draft) return; setBusy(true); setError(""); try { const payload = { name: draft.name, scope: draft.scope, origin: draft.origin, cases: draft.cases, requireReference: true }; if (selectedId === "new") await request("/api/evaluation-suites", { method: "POST", body: JSON.stringify(payload) }); else await request(`/api/evaluation-suites/${selectedId}`, { method: "PUT", body: JSON.stringify(payload) }); await onChanged(); } catch (reason) { setError(adminError(reason, onRequireUnlock)); } finally { setBusy(false); } }
  async function finishImport(result) { setImportSummary(result.importSummary || null); setMappingUpload(null); setMappingColumns([]); await onChanged(); if (result.id) setSelectedId(result.id); }
  async function importFile(file) { if (!file) return; setBusy(true); setError(""); try { const upload = await filePayload(file); const result = await request("/api/evaluation-suites", { method: "POST", body: JSON.stringify({ upload }) }); if (result.mappingRequired) { setMappingUpload(upload); setMappingColumns(result.columns || []); setMapping((current) => ({ ...current, question: result.columns?.[0] || "", referenceAnswer: result.columns?.[1] || "" })); setImportSummary(null); } else await finishImport(result); } catch (reason) { setError(adminError(reason, onRequireUnlock)); } finally { setBusy(false); if (inputRef.current) inputRef.current.value = ""; } }
  async function importMapped() { if (!mappingUpload || !mapping.question || !mapping.referenceAnswer) return; setBusy(true); setError(""); try { const result = await request("/api/evaluation-suites", { method: "POST", body: JSON.stringify({ upload: mappingUpload, mapping: Object.fromEntries(Object.entries(mapping).filter(([, value]) => value)) }) }); await finishImport(result); } catch (reason) { setError(adminError(reason, onRequireUnlock)); } finally { setBusy(false); } }
  async function importExample() { setBusy(true); setError(""); try { const result = await request("/api/evaluation-suites", { method: "POST", body: JSON.stringify({ example: true }) }); setImportSummary(result.importSummary || null); await onChanged(); setSelectedId(result.id); } catch (reason) { setError(adminError(reason, onRequireUnlock)); } finally { setBusy(false); } }
  function duplicateCase(index) { setDraft((value) => { const copy = { ...value.cases[index], id: `${value.cases[index].id || "case"}-copy`, disabled: false }; return { ...value, cases: [...value.cases.slice(0, index + 1), copy, ...value.cases.slice(index + 1)] }; }); }
  function failedValue(row, names) { for (const name of names) if (row?.[name] != null) return row[name]; return ""; }
  function updateFailed(index, field, value) { setImportSummary((current) => ({ ...current, errors: (current.errors || []).map((item, position) => position === index ? { ...item, values: { ...(item.values || {}), [field]: value } } : item) })); }
  function addFailed(index) { const item = importSummary?.errors?.[index]; const values = item?.values || {}; const question = failedValue(values, ["问题", "question", "题目"]); const referenceAnswer = failedValue(values, ["参考答案", "referenceAnswer", "reference_answer", "answer"]); if (!question || !referenceAnswer) return; const next = { id: failedValue(values, ["案例编号", "id"]) || `case-${String((draft?.cases?.length || 0) + 1).padStart(3, "0")}`, question, referenceAnswer, checks: failedValue(values, ["评分要点", "checks"]).split?.(/\n|；/).filter(Boolean) || [], category: failedValue(values, ["题目分类", "category"]) || "uncategorized", scope: failedValue(values, ["适用范围", "scope"]), evidence: failedValue(values, ["依据", "evidence"]), disabled: false }; setDraft((current) => current ? { ...current, cases: [...(current.cases || []), next] } : current); setImportSummary((current) => ({ ...current, failed: Math.max(0, (current.failed || 0) - 1), errors: (current.errors || []).filter((_, position) => position !== index) })); }
  const currentCaseCount = draft?.cases?.length || 0;
  const mappingFields = [{ key: "question", label: "问题", required: true }, { key: "referenceAnswer", label: "参考答案", required: true }, { key: "id", label: "案例编号" }, { key: "category", label: "题目分类" }, { key: "checks", label: "评分要点" }, { key: "scope", label: "适用范围" }, { key: "evidence", label: "依据" }];
  /*
  return <div className="cases-page"><div className="page-heading compact-heading"><div><span className="page-kicker">测试案例</span><h1>题库与评分标准</h1><p>导入问题和参考答案后，统一维护评分要点；实验开始时会冻结本轮题库版本。</p></div><Space wrap><Button icon={<Download size={15} />} onClick={downloadTemplate}>下载模板</Button><Button icon={<FileArrowUp size={15} />} onClick={() => inputRef.current?.click()}>上传文件</Button><input ref={inputRef} type="file" hidden accept=".json,.xlsx,.xlsm,.csv,.tsv" onChange={(event) => importFile(event.target.files?.[0])} /><Button onClick={importExample} loading={busy}>导入项目示例案例</Button></Space></div>{error && <Alert type="error" showIcon message={error} closable onClose={() => setError("")} />}{importSummary && <Alert style={{ margin: "12px 0" }} type={importSummary.failed ? "warning" : "success"} showIcon message={`导入完成：成功 ${importSummary.success} 条、失败 ${importSummary.failed} 条`} description={importSummary.errors?.length ? <List size="small" dataSource={importSummary.errors} renderItem={(item) => <List.Item>第 {item.row} 行：{item.error}</List.Item>} /> : "失败行可在原文件修正后重新上传；已导入的有效行无需重复整理。"} />}</div><div className="case-library-layout"><aside className="suite-list panel-card"><div className="section-title"><h2>题库</h2><Button type="text" icon={<Plus size={15} />} onClick={() => setSelectedId("new")} /></div>{suites.map((suite) => <button className={`suite-item ${suite.id === selectedId ? "on" : ""}`} key={suite.id} onClick={() => setSelectedId(suite.id)}><b>{suite.name}</b><small>修订 {suite.revision} · {suite.caseCount} 题</small></button>)}{!suites.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无题库" />}<Button block type="dashed" icon={<Plus size={14} />} onClick={() => setSelectedId("new")}>新建题库</Button></aside>{draft ? <main className="case-editor"><Card className="panel-card editor-head"><Flex justify="space-between" align="center" wrap="wrap" gap={10}><div><h2>{selectedId === "new" ? "新建测试题库" : draft.name}</h2><span className="muted-text">保存后产生新版本，历史实验继续使用原题库快照。</span></div><Button type="primary" loading={busy} onClick={save}>保存题库</Button></Flex><Flex gap={10} wrap="wrap" style={{ marginTop: 14 }}><Input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="题库名称" style={{ maxWidth: 320 }} /><Input value={draft.scope} onChange={(event) => setDraft({ ...draft, scope: event.target.value })} placeholder="题库适用范围" style={{ flex: 1, minWidth: 280 }} /></Flex></Card><div className="case-editor-list">{draft.cases?.map((item, index) => <Card className={`panel-card case-editor-card ${item.disabled ? "disabled" : ""}`} key={`${item.id}-${index}`}><Flex justify="space-between" align="center" gap={8}><div><b>{item.id || `case-${String(index + 1).padStart(3, "0")}`}</b><Tag style={{ marginLeft: 8 }}>{CATEGORY_LABEL[item.category] || item.category || "未分类"}</Tag>{item.disabled && <Tag color="default">已禁用</Tag>}</div><Space><Switch size="small" checked={!item.disabled} onChange={(checked) => updateCase(index, { disabled: !checked })} checkedChildren="启用" unCheckedChildren="禁用" /><Button type="text" size="small" icon={<Copy size={14} />} onClick={() => duplicateCase(index)} /><Button type="text" danger size="small" icon={<Trash size={14} />} onClick={() => setDraft({ ...draft, cases: draft.cases.filter((_, position) => position !== index) })} /></Space></Flex><div className="case-form-grid"><label>案例编号<Input value={item.id} onChange={(event) => updateCase(index, { id: event.target.value })} /></label><label>题目分类<Input value={item.category || ""} onChange={(event) => updateCase(index, { category: event.target.value })} /></label><label className="wide">问题<Input.TextArea value={item.question} onChange={(event) => updateCase(index, { question: event.target.value })} autoSize={{ minRows: 2, maxRows: 6 }} /></label><label className="wide">参考答案<Input.TextArea value={item.referenceAnswer || ""} onChange={(event) => updateCase(index, { referenceAnswer: event.target.value })} autoSize={{ minRows: 4, maxRows: 10 }} /></label><label className="wide">评分要点<Input.TextArea value={(item.checks || []).join("\n")} onChange={(event) => updateCase(index, { checks: event.target.value.split("\n").map((value) => value.trim()).filter(Boolean) })} placeholder="一条一个独立判断点；不填写也可先保存" autoSize={{ minRows: 3, maxRows: 8 }} /></label><div className="case-form-actions"><Button size="small" onClick={() => setChecksPreview({ index, values: suggestChecks(item.referenceAnswer) })}>从参考答案生成评分要点</Button>{item.scope && <span>适用范围：{item.scope}</span>}{item.evidence && <span>依据：{item.evidence}</span>}</div></div></Card>)}<Button type="dashed" block icon={<Plus size={14} />} onClick={() => setDraft({ ...draft, cases: [...(draft.cases || []), { id: `case-${String(currentCaseCount + 1).padStart(3, "0")}`, question: "", referenceAnswer: "", checks: [], category: "uncategorized", disabled: false }] })}>添加案例</Button></div></main> : <Empty description="选择题库或新建一个题库" />}</div><Modal open={Boolean(checksPreview)} title="检查并保存评分要点" okText="保存到案例" cancelText="取消" onCancel={() => setChecksPreview(null)} onOk={() => { updateCase(checksPreview.index, { checks: checksPreview.values.filter(Boolean) }); setChecksPreview(null); }} width={620}><p className="muted-text">下面内容由参考答案拆分得到，请检查是否每条都代表一个独立判断点。保存前不会改变题库。</p><Input.TextArea value={checksPreview?.values?.join("\n") || ""} onChange={(event) => setChecksPreview({ ...checksPreview, values: event.target.value.split("\n") })} autoSize={{ minRows: 6, maxRows: 12 }} /></Modal></div>;
  */
  return (
    <div className="cases-page">
      <div className="page-heading compact-heading">
        <div><span className="page-kicker">测试案例</span><h1>题库与评分标准</h1><p>导入问题和参考答案后，统一维护评分要点；实验开始时会冻结本轮题库版本。</p></div>
        <Space wrap><Button icon={<Download size={15} />} onClick={downloadTemplate}>下载模板</Button><Button icon={<FileArrowUp size={15} />} onClick={() => inputRef.current?.click()}>上传文件</Button><input ref={inputRef} type="file" hidden accept=".json,.xlsx,.xlsm,.csv,.tsv" onChange={(event) => importFile(event.target.files?.[0])} /><Button onClick={importExample} loading={busy}>导入项目示例案例</Button></Space>
      </div>
      <div className="upload-dropzone" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); importFile(event.dataTransfer.files?.[0]); }}><FileArrowUp size={17} /><span>拖入 Excel、JSON 或 CSV 文件即可导入</span><small>标准模板自动识别；非标准表格会进入列映射</small></div>
      {error && <Alert type="error" showIcon message={error} closable onClose={() => setError("")} />}
      {mappingUpload && <Card className="panel-card mapping-card" size="small"><div className="section-title"><div><h2>列映射</h2><span className="muted-text">未识别为标准模板，请确认表格列对应关系；必填列必须选择。</span></div></div><div className="mapping-grid">{mappingFields.map((field) => <label key={field.key}>{field.label}{field.required && <em>必填</em>}<Select allowClear value={mapping[field.key] || undefined} placeholder="不导入" options={mappingColumns.map((column) => ({ value: column, label: column }))} onChange={(value) => setMapping((current) => ({ ...current, [field.key]: value || "" }))} /></label>)}</div><Button type="primary" loading={busy} disabled={!mapping.question || !mapping.referenceAnswer} onClick={importMapped}>按映射导入</Button></Card>}
      {importSummary && <Alert style={{ margin: "12px 0" }} type={importSummary.failed ? "warning" : "success"} showIcon message={`导入完成：成功 ${importSummary.success} 条、失败 ${importSummary.failed} 条`} description={importSummary.errors?.length ? <List size="small" dataSource={importSummary.errors} renderItem={(item) => <List.Item>第 {item.row} 行：{item.error}</List.Item>} /> : "失败行可在原文件修正后重新上传；已导入的有效行无需重复整理。"} />}
      {importSummary?.errors?.length > 0 && <div className="import-fix-list"><b>修正失败行后加入当前题库</b>{importSummary.errors.map((item, index) => <div className="import-fix-row" key={`${item.row}-${index}`}><span>第 {item.row} 行</span><Input size="small" value={failedValue(item.values, ["案例编号", "id"])} onChange={(event) => updateFailed(index, "案例编号", event.target.value)} placeholder="案例编号（可留空）" /><Input size="small" value={failedValue(item.values, ["问题", "question", "题目"])} onChange={(event) => updateFailed(index, "问题", event.target.value)} placeholder="问题" /><Input size="small" value={failedValue(item.values, ["参考答案", "referenceAnswer", "answer"])} onChange={(event) => updateFailed(index, "参考答案", event.target.value)} placeholder="参考答案" /><Button size="small" disabled={!failedValue(item.values, ["问题", "question", "题目"]) || !failedValue(item.values, ["参考答案", "referenceAnswer", "answer"])} onClick={() => addFailed(index)}>加入</Button></div>)}</div>}
      <div className="case-library-layout">
        <aside className="suite-list panel-card">
          <div className="section-title"><h2>题库</h2><Button type="text" icon={<Plus size={15} />} onClick={() => setSelectedId("new")} /></div>
          {suites.map((suite) => <button className={`suite-item ${suite.id === selectedId ? "on" : ""}`} key={suite.id} onClick={() => setSelectedId(suite.id)}><b>{suite.name}</b><small>修订 {suite.revision} · {suite.caseCount} 题</small></button>)}
          {!suites.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无题库" />}
          <Button block type="dashed" icon={<Plus size={14} />} onClick={() => setSelectedId("new")}>新建题库</Button>
        </aside>
        {draft ? <main className="case-editor">
          <Card className="panel-card editor-head"><Flex justify="space-between" align="center" wrap="wrap" gap={10}><div><h2>{selectedId === "new" ? "新建测试题库" : draft.name}</h2><span className="muted-text">保存后产生新版本，历史实验继续使用原题库快照。</span></div><Button type="primary" loading={busy} onClick={save}>保存题库</Button></Flex><Flex gap={10} wrap="wrap" style={{ marginTop: 14 }}><Input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="题库名称" style={{ maxWidth: 320 }} /><Input value={draft.scope} onChange={(event) => setDraft({ ...draft, scope: event.target.value })} placeholder="题库适用范围" style={{ flex: 1, minWidth: 280 }} /></Flex></Card>
          <div className="case-editor-list">
            {draft.cases?.map((item, index) => <Card className={`panel-card case-editor-card ${item.disabled ? "disabled" : ""}`} key={`${item.id}-${index}`}>
              <Flex justify="space-between" align="center" gap={8}><div><b>{item.id || `case-${String(index + 1).padStart(3, "0")}`}</b><Tag style={{ marginLeft: 8 }}>{CATEGORY_LABEL[item.category] || item.category || "未分类"}</Tag>{item.disabled && <Tag>已禁用</Tag>}</div><Space><Switch size="small" checked={!item.disabled} onChange={(checked) => updateCase(index, { disabled: !checked })} checkedChildren="启用" unCheckedChildren="禁用" /><Button type="text" size="small" icon={<Copy size={14} />} onClick={() => duplicateCase(index)} /><Button type="text" danger size="small" icon={<Trash size={14} />} onClick={() => setDraft({ ...draft, cases: draft.cases.filter((_, position) => position !== index) })} /></Space></Flex>
              <div className="case-form-grid"><label>案例编号<Input value={item.id} onChange={(event) => updateCase(index, { id: event.target.value })} /></label><label>题目分类<Input value={item.category || ""} onChange={(event) => updateCase(index, { category: event.target.value })} /></label><label className="wide">问题<Input.TextArea value={item.question} onChange={(event) => updateCase(index, { question: event.target.value })} autoSize={{ minRows: 2, maxRows: 6 }} /></label><label className="wide">参考答案<Input.TextArea value={item.referenceAnswer || ""} onChange={(event) => updateCase(index, { referenceAnswer: event.target.value })} autoSize={{ minRows: 4, maxRows: 10 }} /></label><label className="wide">评分要点<Input.TextArea value={(item.checks || []).join("\n")} onChange={(event) => updateCase(index, { checks: event.target.value.split("\n").map((value) => value.trim()).filter(Boolean) })} placeholder="一条一个独立判断点；不填写也可先保存" autoSize={{ minRows: 3, maxRows: 8 }} /></label><div className="case-form-actions"><Button size="small" onClick={() => setChecksPreview({ index, values: suggestChecks(item.referenceAnswer) })}>从参考答案生成评分要点</Button>{item.scope && <span>适用范围：{item.scope}</span>}{item.evidence && <span>依据：{item.evidence}</span>}</div></div>
            </Card>)}
            <Button type="dashed" block icon={<Plus size={14} />} onClick={() => setDraft({ ...draft, cases: [...(draft.cases || []), { id: `case-${String(currentCaseCount + 1).padStart(3, "0")}`, question: "", referenceAnswer: "", checks: [], category: "uncategorized", disabled: false }] })}>添加案例</Button>
          </div>
        </main> : <Empty description="选择题库或新建一个题库" />}
      </div>
      <Modal open={Boolean(checksPreview)} title="检查并保存评分要点" okText="保存到案例" cancelText="取消" onCancel={() => setChecksPreview(null)} onOk={() => { updateCase(checksPreview.index, { checks: checksPreview.values.filter(Boolean) }); setChecksPreview(null); }} width={620}><p className="muted-text">下面内容由参考答案拆分得到，请检查是否每条都代表一个独立判断点。保存前不会改变题库。</p><Input.TextArea value={checksPreview?.values?.join("\n") || ""} onChange={(event) => setChecksPreview({ ...checksPreview, values: event.target.value.split("\n") })} autoSize={{ minRows: 6, maxRows: 12 }} /></Modal>
    </div>
  );
}

// ---------------------------------------------------------------- 历史

function History({ items, onOpen, onRerun, rerunning }) {
  if (!items.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有历史实验批次" />;
  return <div className="history-list">{items.map((item) => { const summary = item.summaries?.review || item.summaries?.model; return <Card className="panel-card history-row" key={item.id}><div><b>{item.suite?.name || "未命名题库"}</b><p>{statusText(item.status)} · {formatRelative(item.createdAt)} · {item.questions || 0} 题 × {item.repeats || 1} 轮</p></div><div className="history-score">{summary?.qualityBlocks ? `有效配对 ${summary.qualityBlocks} · 无主干 ${summary.score?.code_only || "待评"} · 有主干 ${summary.score?.optional || "待评"} · AST ${summary.score?.ast || "待评"}` : "尚无有效评分"}</div><Space><Button type="link" onClick={() => onOpen(item)}>查看</Button><Button type="link" onClick={() => onRerun(item)} loading={rerunning === item.id}>重跑</Button></Space></Card>; })}</div>;
}

// ---------------------------------------------------------------- 页面

export function EvaluationPage({ projectId, onRequireUnlock }) {
  const [tab, setTab] = useState("experiment");
  const [setup, setSetup] = useState(null);
  const [items, setItems] = useState([]);
  const [currentId, setCurrentId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [starting, setStarting] = useState(false);
  const [rerunning, setRerunning] = useState("");
  const pollRef = useRef(null);
  async function loadSetup() { const value = await request("/api/evaluations/setup"); setSetup(value); return value; }
  async function loadItems() { const value = await request("/api/evaluations"); setItems(value.items || []); return value; }
  async function loadAll(forceSelect = false) { const [, list] = await Promise.all([loadSetup(), loadItems()]); if (forceSelect || !currentId) setCurrentId(list.activeEvaluationId || list.items?.[0]?.id || null); }
  useEffect(() => {
    setCurrentId(null); setDetail(null); setItems([]); setSetup(null); setTab("experiment");
    loadAll(true).catch((reason) => setError(reason.message));
  }, [projectId]); // eslint-disable-line
  useEffect(() => { if (!currentId) { setDetail(null); return undefined; } let cancelled = false; request(`/api/evaluations/${currentId}`).then((value) => { if (!cancelled) setDetail(value); }).catch((reason) => { if (!cancelled) setError(reason.message); }); return () => { cancelled = true; }; }, [currentId]);
  const active = ACTIVE.includes(detail?.status) || detail?.rejudge?.status === "running";
  useEffect(() => { clearInterval(pollRef.current); if (!active || !currentId) return undefined; pollRef.current = setInterval(async () => { try { const value = await request(`/api/evaluations/${currentId}`); setDetail(value); if (!ACTIVE.includes(value.status)) await loadItems(); } catch { /* next poll */ } }, 1800); return () => clearInterval(pollRef.current); }, [active, currentId]);
  async function start(payload) { setStarting(true); setActionError(""); try { const result = await request("/api/evaluations", { method: "POST", body: JSON.stringify(payload) }); setCurrentId(result.evaluationId); setTab("experiment"); await loadItems(); } catch (reason) { setActionError(adminError(reason, onRequireUnlock)); } finally { setStarting(false); } }
  async function action(path) { setActionError(""); try { await request(path, { method: "POST", body: "{}" }); setDetail(await request(`/api/evaluations/${currentId}`)); } catch (reason) { setActionError(adminError(reason, onRequireUnlock)); } }
  async function retryFailed() { setActionError(""); try { const result = await request(`/api/evaluations/${currentId}/retry`, { method: "POST", body: "{}" }); setCurrentId(result.evaluationId); setDetail(null); setTab("experiment"); await loadItems(); } catch (reason) { setActionError(adminError(reason, onRequireUnlock)); } }
  async function rerun(item) { setRerunning(item.id); try { await start({ suiteId: item.suite?.id, repeats: item.repeats || item.settings?.repeats || 1, judge: item.judge !== false, comparison: "abc", astVersionId: setup?.ast?.currentVersionId }); } finally { setRerunning(""); } }
  async function exportReport(source) { try { const value = await request(`/api/evaluations/${currentId}/report?source=${source}`); exportMarkdown(`${currentId}-${source}.md`, value.markdown); } catch (reason) { setActionError(adminError(reason, onRequireUnlock)); } }
  const refreshDetail = () => currentId ? request(`/api/evaluations/${currentId}`).then(setDetail).catch(() => {}) : null;
  return <div className="page-wrap eval-page">{error && <Alert type="error" showIcon message={error} />}{actionError && <Alert style={{ marginTop: 10 }} type="error" showIcon message={actionError} />}{setup && <Tabs className="workbench-tabs" activeKey={tab} onChange={setTab} items={[{ key: "experiment", label: "对比实验", children: detail ? <BatchView detail={{ ...detail, actionError }} onStop={() => action(`/api/evaluations/${currentId}/cancel`)} onRejudge={() => action(`/api/evaluations/${currentId}/rejudge`)} onRetry={retryFailed} onExport={exportReport} onNew={() => { setCurrentId(null); setDetail(null); setActionError(""); }} onRefresh={refreshDetail} onRequireUnlock={onRequireUnlock} /> : <ExperimentSetup setup={setup} onStart={start} starting={starting} error={actionError} onGoAst={() => { window.location.hash = "#/ast"; }} onGoCases={() => setTab("cases")} /> }, { key: "history", label: "历史批次", children: <History items={items} onOpen={(item) => { setCurrentId(item.id); setTab("experiment"); }} onRerun={rerun} rerunning={rerunning} /> }, { key: "cases", label: "测试案例", children: <CaseLibrary setup={setup} onChanged={async () => { await loadSetup(); await loadItems(); }} onRequireUnlock={onRequireUnlock} /> }]} />}</div>;
}

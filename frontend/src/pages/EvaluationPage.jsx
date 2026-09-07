import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight, CaretRight, Download, FileText, MagnifyingGlass, Play, SkipBack, Stop,
} from "@phosphor-icons/react";
import {
  Button, Checkbox, Collapse, Empty, Flex, Input, Modal, Popconfirm, Segmented, Select,
  Space, Switch, Tabs,
} from "antd";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { request } from "../lib/api.js";
import { formatRelative } from "../lib/format.js";

const ARM_LABEL = { code_only: "无主干", optional: "有主干" };
const ORIGIN_LABEL = { real: "真实历史问题", known_regression: "已知回归题", synthetic: "合成场景" };
const CATEGORY_LABEL = {
  business_flow: "业务流程", code_control: "普通代码", business_rule: "业务规则",
  boundary: "业务边界", uncategorized: "未分类",
};
const CONTROL_ARM = "code_only";
const TREATMENT_ARM = "optional";
const ACTIVE_STATUSES = ["running", "cancelling"];
const FINISHED_STATUSES = ["completed", "cancelled", "failed", "interrupted"];

function Dot({ kind }) {
  const color = kind === "ok" ? "#1B7A46" : kind === "bad" ? "#A53831" : kind === "run" ? "#B5892B" : "#A5A29B";
  return <span className="eval-dot" style={{ background: color }} />;
}

function handleAdminError(reason, onRequireUnlock) {
  const text = String(reason?.message || "");
  if (text.includes("401") || text.includes("凭证") || text.includes("credential") || text.includes("administrator")) {
    onRequireUnlock?.();
    return "需要管理员口令后才能执行该操作。";
  }
  return text || "请求失败";
}

function exportMarkdown(name, markdown) {
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  const total = Math.round(seconds);
  if (total < 90) return `${total} 秒`;
  return `${Math.floor(total / 60)} 分 ${total % 60} 秒`;
}

function pairMap(summary) {
  const map = {};
  for (const pair of summary?.pairs || []) map[`${pair.questionId}-r${pair.repeat}-${pair.arm}`] = pair;
  return map;
}

function since(iso) {
  if (!iso) return "";
  const started = Date.parse(iso);
  if (Number.isNaN(started)) return "";
  return `${Math.max(1, Math.round((Date.now() - started) / 1000))}s`;
}

function parseEvidence(text) {
  const match = /([^\s:：][^:：]*?):(\d+)/.exec(String(text || "").trim());
  if (!match) return null;
  return { path: match[1].replace(/^["'`]|["'`]$/g, ""), line: parseInt(match[2], 10) };
}

// ---------------------------------------------------------------- S1 发起

function LaunchSection({ setup, onStart, starting }) {
  const suites = setup.suites || [];
  const [suiteId, setSuiteId] = useState(setup.lastUsedSuiteId || suites[0]?.id || null);
  const [repeats, setRepeats] = useState(2);
  const [judge, setJudge] = useState(true);
  const [selectedCases, setSelectedCases] = useState(null);
  const suite = suites.find((item) => item.id === suiteId);
  const suiteCases = suite?.cases || [];

  useEffect(() => { setSuiteId(setup.lastUsedSuiteId || suites[0]?.id || null); }, [setup.lastUsedSuiteId, suites.length]); // eslint-disable-line

  const questionCount = selectedCases ? selectedCases.length : suiteCases.length;
  const answers = questionCount * 2 * repeats;
  const ready = suites.length > 0 && setup.cli?.available && setup.baseline?.readable && setup.baseline?.documents?.length > 0;
  return (
    <div className="eval-hero">
      <h1>发起对照验证</h1>
      <p className="eval-hero-sub">同一组问题，分别不接入 / 接入知识主干作答，对比质量与成本。</p>
      <div className="eval-config">
        <div className="eval-config-row">
          <span className="eval-config-label">题库</span>
          <Select
            value={suiteId} variant="borderless"
            placeholder={suites.length ? "选择题库" : "暂无题库，请先在「验证题库」中导入"}
            options={suites.map((item) => ({ value: item.id, label: `${item.name}（${item.caseCount} 题）` }))}
            onChange={(value) => { setSuiteId(value); setSelectedCases(null); }}
          />
        </div>
        <div className="eval-config-row">
          <span className="eval-config-label">轮次</span>
          <Segmented size="small" value={repeats} onChange={setRepeats}
            options={[{ value: 1, label: "1" }, { value: 2, label: "2" }, { value: 3, label: "3" }]} />
        </div>
        <div className="eval-config-row">
          <span className="eval-config-label">模型初评</span>
          <Switch size="small" checked={judge} onChange={setJudge} />
        </div>
        {suiteCases.length > 0 && (
          <div className="eval-config-row">
            <span className="eval-config-label">题目</span>
            <Select
              variant="borderless" mode="multiple" style={{ minWidth: 260, flex: 1 }}
              placeholder="全部题目"
              value={selectedCases ?? suiteCases.map((item) => item.id)}
              options={suiteCases.map((item) => ({ value: item.id, label: `${item.id} · ${CATEGORY_LABEL[item.category] || item.category}` }))}
              onChange={(values) => setSelectedCases(values.length === suiteCases.length ? null : values)}
            />
          </div>
        )}
        <div className="eval-config-foot">
          <span className="eval-caption">
            {answers} 次答题{judge ? ` · 最多 ${answers} 次初评` : ""}
            {setup.cli?.available ? "" : ` · 未找到 ${setup.cli?.command || "claude"} 命令`}
          </span>
          <Button type="primary" icon={<Play size={13} weight="fill" />} loading={starting}
            disabled={!ready || !suiteId || questionCount === 0}
            onClick={() => onStart({ suiteId, repeats, judge, caseIds: selectedCases ?? undefined })}>
            开始验证
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 双面板

function statusOf(detail) {
  return {
    running: { text: "运行中", pulse: true }, cancelling: { text: "正在停止", pulse: true },
    completed: { text: "已完成", dot: "ok" }, cancelled: { text: "已停止", dot: "idle" },
    failed: { text: "运行失败", dot: "bad" }, interrupted: { text: "已中断", dot: "run" },
  }[detail.status] || { text: detail.status, dot: "idle" };
}

function BatchHead({ detail, onStop, onRejudge, onExport, onNewConfig }) {
  const active = ACTIVE_STATUSES.includes(detail.status);
  const status = statusOf(detail);
  const coverage = detail.reviewCoverage;
  return (
    <>
      <div className="eval-head">
        <h2>
          {detail.suite?.name || "验证批次"}
          <span className="eval-status">
            {status.pulse ? <span className="activity-pulse" /> : <Dot kind={status.dot} />}
            {status.text}
          </span>
        </h2>
        <span className="eval-actions">
          {active && <Popconfirm title="停止这批验证？" description="将停止安排新任务并取消进行中的调用，已完成结果保留。"
            okText="停止" cancelText="继续运行" onConfirm={onStop}>
            <Button size="small" danger icon={<Stop size={13} />}>停止</Button>
          </Popconfirm>}
          {FINISHED_STATUSES.includes(detail.status) && (
            <Popconfirm title="对已有答案重新初评？" description="新建评分修订并保留旧评分，不会重新答题。"
              okText="重新初评" cancelText="取消" onConfirm={onRejudge}>
              <Button size="small">重新初评</Button>
            </Popconfirm>
          )}
          {FINISHED_STATUSES.includes(detail.status) && (
            <Button size="small" icon={<Download size={13} />} onClick={() => onExport("review")}>导出报告</Button>
          )}
          <Button size="small" icon={<SkipBack size={13} />} onClick={onNewConfig}>新验证</Button>
        </span>
      </div>
      <p className="eval-subline">
        {detail.settings?.questions} 题 × 2 组 × {detail.settings?.repeats} 轮
        {detail.settings?.repeats === 1 ? "（单轮）" : ""}
        <span className="sep">·</span>{formatRelative(detail.createdAt)}
        <span className="sep">·</span>{formatDuration(detail.elapsedSeconds)}
        {coverage ? <><span className="sep">·</span>复核 {coverage.reviewed}/{coverage.answers}</> : null}
      </p>
    </>
  );
}

function collectJobs(detail) {
  const jobs = [];
  (detail.rows || []).forEach((row) => {
    for (const arm of [CONTROL_ARM, TREATMENT_ARM]) {
      const run = row.runs?.[arm];
      if (run) jobs.push({ ...run, armKey: arm, caseId: row.caseId, repeat: row.repeat });
    }
  });
  return jobs;
}

function RunningVersus({ detail }) {
  const jobs = collectJobs(detail);
  const perArm = detail.settings?.questions * detail.settings?.repeats || 0;
  const panels = [CONTROL_ARM, TREATMENT_ARM].map((arm) => {
    const armJobs = jobs.filter((job) => job.armKey === arm);
    const answered = armJobs.filter((job) => job.status === "completed").length;
    const judged = armJobs.filter((job) => job.modelStatus === "completed").length;
    const running = armJobs.find((job) => job.status === "running");
    return { arm, answered, judged, running };
  });
  return (
    <div className="eval-versus">
      {panels.map(({ arm, answered, judged, running }) => (
        <div className="eval-panel" key={arm}>
          <div className="eval-arm"><Dot kind={running ? "run" : "idle"} /> {ARM_LABEL[arm]}</div>
          <div className="eval-stat"><em>答题</em><b>{answered}</b><span className="unit">/ {perArm}</span></div>
          {detail.settings?.judge && (
            <div className="eval-stat"><em>初评</em><b>{judged}</b><span className="unit">/ {perArm}</span></div>
          )}
          {running && (
            <div className="eval-live"><span className="activity-pulse" /> 正在作答 {running.caseId} · 第 {running.repeat} 轮（{since(running.startedAt)}）</div>
          )}
        </div>
      ))}
    </div>
  );
}

function FinishedVersus({ detail, source, setSource }) {
  const summary = detail.summaries?.[source];
  if (!summary) return null;
  const left = summary.aggregates[CONTROL_ARM];
  const right = summary.aggregates[TREATMENT_ARM];
  const delta = (a, b, round = false) => {
    if (a == null || b == null) return null;
    const value = b - a;
    return round ? Math.round(value * 100) / 100 : value;
  };
  const signed = (value) => (value > 0 ? `+${value}` : `${value}`);
  const deltas = [
    { name: "检查项", value: delta(left.score, right.score), good: 1 },
    { name: "额外问题", value: delta(left.issues, right.issues), good: -1 },
    { name: "耗时", value: delta(left.meanSeconds, right.meanSeconds, true), good: -1, unit: "s" },
    { name: "工具", value: delta(left.toolCalls, right.toolCalls), good: 0 },
  ].filter((item) => item.value != null);
  return (
    <>
      <div className="eval-versus">
        {[CONTROL_ARM, TREATMENT_ARM].map((arm) => {
          const agg = summary.aggregates[arm];
          return (
            <div className="eval-panel" key={arm}>
              <div className="eval-arm"><Dot kind={arm === TREATMENT_ARM ? "ok" : "idle"} /> {ARM_LABEL[arm]}</div>
              <div className="eval-stat"><em>检查项</em><b>{agg.score == null ? "待评" : `${agg.score}/${agg.possible}`}</b></div>
              <div className="eval-stat"><em>耗时</em><b>{agg.meanSeconds ?? "—"}</b><span className="unit">秒</span></div>
              <div className="eval-stat"><em>工具</em><b>{agg.toolCalls ?? "—"}</b>{agg.toolErrors ? <span className="unit">（错误 {agg.toolErrors}）</span> : null}</div>
            </div>
          );
        })}
      </div>
      <p className="eval-delta-line">
        有主干 vs 无主干：
        {deltas.map((item, index) => (
          <span key={item.name}>
            {index > 0 ? " · " : " "}
            {item.name} <b className={item.good === 0 ? "" : item.good > 0 ? (item.value > 0 ? "good" : "bad") : (item.value < 0 ? "good" : "bad")}>
              {signed(item.value)}{item.unit || ""}
            </b>
          </span>
        ))}
        {summary.qualityBlocks === 0 ? " · 暂无可比评分" : ""}
        {"　"}
        <span className="src">
          来源：{source === "review" ? "复核记录" : "模型初评"}（{summary.qualityBlocks} 配对）·{" "}
          <span className="link" onClick={() => setSource(source === "review" ? "model" : "review")}>
            改看{source === "review" ? "模型初评" : "复核记录"}
          </span>
        </span>
      </p>
    </>
  );
}

// ---------------------------------------------------------------- 逐题 + 页内展开

function CaseDetail({ evalId, caseKey, onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [reviewArm, setReviewArm] = useState(CONTROL_ARM);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [sourceModal, setSourceModal] = useState(null);
  const caseId = caseKey?.caseId;
  const repeat = caseKey?.repeat;

  useEffect(() => {
    if (!evalId || !caseId) return;
    setError("");
    request(`/api/evaluations/${evalId}/cases/${caseId}/repeats/${repeat}`)
      .then((value) => { setData(value); initDraft(value); })
      .catch((reason) => setError(reason.message));
  }, [evalId, caseId, repeat]); // eslint-disable-line

  function initDraft(value) {
    const next = {};
    for (const arm of [CONTROL_ARM, TREATMENT_ARM]) {
      const revisions = value.runs?.[arm]?.reviews || [];
      const latest = revisions[revisions.length - 1];
      next[arm] = {
        checks: latest ? [...latest.checks] : (value.case?.checks || []).map(() => null),
        issues: (latest?.issues || []).join("\n"),
        note: latest?.note || "",
      };
    }
    setDraft(next);
    const firstOpen = [CONTROL_ARM, TREATMENT_ARM].find((arm) =>
      value.runs?.[arm]?.status === "completed" && !(value.runs?.[arm]?.reviews || []).length);
    if (firstOpen) setReviewArm(firstOpen);
  }

  async function saveReview() {
    const answerId = data.runs[reviewArm]?.answerId;
    if (!answerId) return;
    setSaving(true);
    setError("");
    try {
      await request(`/api/evaluations/${evalId}/reviews/${answerId}`, {
        method: "PUT",
        body: JSON.stringify({
          checks: draft[reviewArm].checks,
          issues: draft[reviewArm].issues.split("\n").map((item) => item.trim()).filter(Boolean),
          note: draft[reviewArm].note,
          operatorType: "user",
        }),
      });
      const fresh = await request(`/api/evaluations/${evalId}/cases/${caseId}/repeats/${repeat}`);
      setData(fresh);
      initDraft(fresh);
      onChanged?.();
    } catch (reason) {
      setError(handleAdminError(reason));
    } finally {
      setSaving(false);
    }
  }

  async function openSource(text) {
    const match = /([^\s:：][^:：]*?):(\d+)/.exec(String(text || "").trim());
    if (!match) {
      setError("该引用没有可解析的文件位置，请核对引用文本。");
      return;
    }
    const parsed = { path: match[1].replace(/^["'`]|["'`]$/g, ""), line: parseInt(match[2], 10) };
    setError("");
    for (const source of data.sources || []) {
      try {
        const value = await request(`/api/evaluations/${evalId}/sources/${source.id}?path=${encodeURIComponent(parsed.path)}&start=${Math.max(1, parsed.line - 20)}&end=${parsed.line + 20}`);
        setSourceModal({ ...value, target: parsed.line });
        return;
      } catch { /* 尝试下一个仓库快照 */ }
    }
    setError(`未能在本轮冻结快照中定位 ${parsed.path}；请直接核对答案中的引用文本。`);
  }

  const checks = data?.case?.checks || [];
  return (
    <div className="eval-detail">
      {error && <div className="eval-note" style={{ marginBottom: 10 }}>{error}</div>}
      {data && (
        <>
          <p className="eval-q">{data.case.question}</p>
          <div className="eval-answers">
            {[CONTROL_ARM, TREATMENT_ARM].map((arm) => {
              const run = data.runs?.[arm];
              const statusText = !run || run.status === "pending" ? "未开始"
                : run.status === "completed" ? "回答完成" : run.status === "running" ? "调查中" : run.status === "failed" ? "调用失败" : run.status;
              const usage = run?.usage || {};
              return (
                <div className="eval-answer" key={arm}>
                  <h4>
                    <Dot kind={run?.status === "completed" ? "ok" : run?.status === "failed" ? "bad" : run?.status === "running" ? "run" : "idle"} />
                    {ARM_LABEL[arm]}
                    <span className="eval-ans-stats">
                      {run?.status === "completed" ? `${run.elapsedSeconds ?? "—"}s · ${run.toolCalls} 工具` : statusText}
                    </span>
                  </h4>
                  {run?.error && <div className="eval-note" style={{ marginBottom: 8 }}>{run.error}</div>}
                  <div className="eval-answer-body">
                    {run?.status
                      ? <Markdown remarkPlugins={[remarkGfm]}>{run.answer || "（无回答内容）"}</Markdown>
                      : <span className="eval-caption">任务未开始</span>}
                  </div>
                  {run?.status === "completed" && (
                    <div className="eval-answer-foot">
                      <Collapse size="small" style={{ background: "transparent" }} items={[{
                        key: "trace", label: <span style={{ fontSize: 12, color: "#2F6382" }}>调查记录（{run.trace?.length ?? 0}）</span>,
                        children: (
                          <>
                            {(run.trace || []).map((item, index) => (
                              <div key={item.id || index} style={{ display: "flex", gap: 8, alignItems: "baseline", padding: "3px 0", borderBottom: "1px dashed var(--border)", fontSize: 11.5 }}>
                                <code style={{ fontFamily: "var(--mono)", color: "#2F6382" }}>{item.name}</code>
                                <span style={{ minWidth: 0, overflowWrap: "anywhere", color: "#55564f" }}>{item.input}</span>
                                <span style={{ marginLeft: "auto", color: "var(--faint)", fontFamily: "var(--mono)", fontSize: 10.5, whiteSpace: "nowrap" }}>
                                  {item.firstEventSeconds ?? "—"}s{item.error ? ` · ${item.error}` : ""}
                                </span>
                              </div>
                            ))}
                            {!run.trace?.length && <span className="eval-caption">没有记录到工具调用。</span>}
                            <div className="eval-caption" style={{ marginTop: 6 }}>
                              用量：输入 {usage.input_tokens ?? "—"} · 缓存 {usage.cache_read_input_tokens ?? "—"} · 输出 {usage.output_tokens ?? "—"} tokens
                              {run.metadata?.reportedModels?.length ? `；模型 ${run.metadata.reportedModels.join("、")}` : ""}
                            </div>
                          </>
                        ),
                      }]} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <div className="eval-checks-h">判分（初评）</div>
          {checks.map((check, index) => {
            const judgeCell = (arm) => {
              const item = data.runs?.[arm]?.modelReview?.checks?.[index];
              if (!item) return <span className="eval-caption">未评分</span>;
              return (
                <>
                  <span className={`mark ${item.met ? "yes" : "no"}`}>{item.met ? "✓" : "✗"}</span>
                  <span>{item.reason}</span>
                  {item.candidateQuote && <div className="quote">“{item.candidateQuote}”</div>}
                  {item.evidence && (
                    <div className="ev">
                      <span className="link" onClick={() => openSource(item.evidence)}>
                        <MagnifyingGlass size={10} /> {parseEvidence(item.evidence)?.path}:{parseEvidence(item.evidence)?.line} ↗
                      </span>
                    </div>
                  )}
                </>
              );
            };
            return (
              <div className="eval-check" key={index}>
                <span className="std">{check}</span>
                <span>
                  <span className="eval-caption" style={{ display: "block", marginBottom: 2 }}>{ARM_LABEL[CONTROL_ARM]}初评</span>
                  {judgeCell(CONTROL_ARM)}
                </span>
                <span>
                  <span className="eval-caption" style={{ display: "block", marginBottom: 2 }}>{ARM_LABEL[TREATMENT_ARM]}初评</span>
                  {judgeCell(TREATMENT_ARM)}
                </span>
              </div>
            );
          })}
          <div className="eval-review-bar">
            <Segmented size="small" value={reviewArm} onChange={(value) => setReviewArm(value)}
              options={[CONTROL_ARM, TREATMENT_ARM].map((arm) => ({ value: arm, label: `复核${ARM_LABEL[arm]}` }))} />
            <div className="eval-review-inputs">
              <Input size="small" placeholder="额外问题（每行一条）" style={{ flex: 1 }}
                value={draft[reviewArm]?.issues || ""}
                onChange={(event) => setDraft((current) => ({
                  ...current, [reviewArm]: { ...current[reviewArm], issues: event.target.value },
                }))} />
              <Input size="small" placeholder="说明（可选）" style={{ flex: 1, maxWidth: 180 }}
                value={draft[reviewArm]?.note || ""}
                onChange={(event) => setDraft((current) => ({
                  ...current, [reviewArm]: { ...current[reviewArm], note: event.target.value },
                }))} />
            </div>
            <Button type="primary" size="small" loading={saving}
              disabled={data.runs?.[reviewArm]?.status !== "completed"}
              onClick={saveReview}>保存复核</Button>
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
            <Button size="small" type="text" onClick={onClose}>收起</Button>
          </div>
          <Modal open={Boolean(sourceModal)} onCancel={() => setSourceModal(null)} footer={null}
            width={860} title={
              <Flex align="center" gap={8}>
                <FileText size={15} /> {sourceModal?.repositoryId} · {sourceModal?.path}
                <span className="eval-caption">本轮资料（冻结快照）</span>
              </Flex>
            }>
            {sourceModal && (
              <div className="eval-source">
                {sourceModal.lines.map((line) => (
                  <div key={line.n} className={line.n === sourceModal.target ? "eval-source-line on" : "eval-source-line"}>
                    <span className="eval-source-num">{line.n}</span>
                    <span>{line.text || " "}</span>
                  </div>
                ))}
                {sourceModal.end < sourceModal.totalLines && (
                  <div className="eval-caption" style={{ padding: "6px 12px" }}>
                    … 共 {sourceModal.totalLines} 行，仅展示上下文窗口
                  </div>
                )}
              </div>
            )}
          </Modal>
        </>
      )}
    </div>
  );
}

function CaseListSection({ detail, pairs, expandedKey, onToggleCase, active }) {
  const rows = detail.rows || [];
  return (
    <>
      <div className="eval-list-h">逐题</div>
      {rows.length === 0 && <span className="eval-caption">还没有可对照的题目。</span>}
      {rows.map((row) => {
        const expanded = expandedKey && expandedKey.caseId === row.caseId && expandedKey.repeat === row.repeat;
        const control = row.runs?.[CONTROL_ARM];
        const treatment = row.runs?.[TREATMENT_ARM];
        const pair = pairs[`${row.caseId}-r${row.repeat}-${TREATMENT_ARM}`];
        const cellText = (run, prefix) => {
          if (!run) return "—";
          if (run.status === "completed") {
            const score = prefix === "control" ? pair?.controlScore : pair?.treatmentScore;
            return `✓ ${score || `${run.elapsedSeconds ?? "—"}s`}`;
          }
          if (run.status === "running") return `⟳ ${since(run.startedAt) || "进行中"}`;
          if (run.status === "failed") return "✗ 失败";
          if (run.status === "cancelled" || run.status === "skipped") return "已停止";
          return "排队";
        };
        const reviewedCount = [control, treatment].filter((run) => run?.reviewStatus === "reviewed").length;
        const reviewText = reviewedCount === 2 ? "已复核" : reviewedCount === 1 ? "部分复核" : active ? "" : "待复核";
        return (
          <React.Fragment key={`${row.caseId}-r${row.repeat}`}>
            <div className="eval-qrow" onClick={() => onToggleCase(expanded ? null : row)}>
              <b>{row.caseId}</b>
              <span className="kind">{CATEGORY_LABEL[row.category] || row.category}{detail.settings?.repeats > 1 ? ` · 第 ${row.repeat} 轮` : ""}</span>
              <span className="eval-score">
                <span className={control?.status === "failed" ? "bad" : ""}>{cellText(control, "control")}</span>
                {" · "}
                <span className={treatment?.status === "failed" ? "bad" : ""}>{cellText(treatment, "treatment")}</span>
                {reviewText ? ` · ${reviewText}` : ""}
              </span>
              <span className="caret" style={{ transform: expanded ? "rotate(90deg)" : "none" }}>▸</span>
            </div>
            {expanded && <CaseDetail evalId={detail.id} caseKey={expandedKey}
              onClose={() => onToggleCase(null)} onChanged={() => {}} />}
          </React.Fragment>
        );
      })}
    </>
  );
}

function BatchView({ detail, expandedKey, onToggleCase, onRefreshDetail, onStop, onRejudge, onExport, onNewConfig, actionError }) {
  const [source, setSource] = useState("review");
  const active = ACTIVE_STATUSES.includes(detail.status);
  const finished = FINISHED_STATUSES.includes(detail.status);

  useEffect(() => {
    if (finished) {
      setSource((detail.reviewCoverage?.reviewed ?? 0) > 0 ? "review" : "model");
    }
  }, [finished, detail.reviewCoverage?.reviewed]); // eslint-disable-line

  const summary = detail.summaries?.[source];
  const pairs = useMemo(() => pairMap(summary), [summary]);
  return (
    <>
      <BatchHead detail={detail} onStop={onStop} onRejudge={onRejudge} onExport={onExport} onNewConfig={onNewConfig} />
      {(actionError || detail.error) && (
        <div className="eval-note" style={{ margin: "12px 0" }}>{actionError || detail.error}</div>
      )}
      {active && <RunningVersus detail={detail} />}
      {finished && <FinishedVersus detail={detail} source={source} setSource={setSource} />}
      <CaseListSection detail={detail} pairs={pairs} expandedKey={expandedKey} onToggleCase={onToggleCase} active={active} />
    </>
  );
}

// ---------------------------------------------------------------- 历史

function HistorySection({ items, onOpen, onRerun, rerunning }) {
  if (!items.length) {
    return (
      <div className="eval-hero" style={{ paddingTop: "10vh" }}>
        <h1 style={{ fontSize: 19 }}>还没有验证批次</h1>
        <p className="eval-hero-sub">在「本次验证」中开始第一批对照。</p>
      </div>
    );
  }
  const fmtScore = (summary) => {
    if (!summary?.qualityBlocks) return null;
    return `${summary.score?.[CONTROL_ARM]} 对 ${summary.score?.[TREATMENT_ARM]}（${summary.qualityBlocks} 配对）`;
  };
  return (
    <div>
      {items.map((item) => {
        const model = item.summaries?.model;
        const review = item.summaries?.review;
        const statusText = {
          running: "运行中", cancelling: "正在停止", completed: "已完成",
          cancelled: "已停止", failed: "运行失败", interrupted: "已中断",
        }[item.status] || item.status;
        return (
          <div key={item.id} className="eval-hist-row">
            <span className="name">{item.suite?.name || "—"}</span>
            <span className="eval-meta">
              {statusText} · {formatRelative(item.createdAt)} · {item.questions ?? "—"} 题 × {item.repeats ?? "—"} 轮
              {model ? ` · ${model.meanSeconds?.[CONTROL_ARM] ?? "—"}s / ${model.meanSeconds?.[TREATMENT_ARM] ?? "—"}s` : ""}
            </span>
            <span className="eval-hist-score">
              {fmtScore(review) && <>复核 <span className="mono" style={{ color: "var(--ink)" }}>{fmtScore(review)}</span>　</>}
              {!fmtScore(review) && fmtScore(model) && <>初评 <span className="mono" style={{ color: "var(--ink)" }}>{fmtScore(model)}</span>　</>}
              <span className="link" onClick={() => onOpen(item)}>查看</span>·
              <span className="link" onClick={async () => {
                const report = await request(`/api/evaluations/${item.id}/report?source=review`).catch(() => null)
                  || await request(`/api/evaluations/${item.id}/report?source=model`);
                exportMarkdown(`${item.id}-report.md`, report.markdown);
              }}>导出</span>·
              <Popconfirm title="用相同设置发起新验证？" okText="开始" cancelText="取消"
                onConfirm={() => onRerun(item)}>
                <span className="link">{rerunning === item.id ? "发起中…" : "重新验证"}</span>
              </Popconfirm>
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- 题库

const CATEGORY_OPTIONS = [
  { value: "business_flow", label: "业务流程" }, { value: "code_control", label: "普通代码" },
  { value: "business_rule", label: "业务规则" }, { value: "boundary", label: "业务边界" },
  { value: "uncategorized", label: "未分类" },
];

function emptySuite() {
  return { name: "", scope: "", origin: "synthetic", cases: [] };
}

function SuitesSection({ suites, onChanged, onError }) {
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [importPath, setImportPath] = useState("evaluations/withdraw.json");

  useEffect(() => {
    if (selectedId === null && suites.length) setSelectedId(suites[0].id);
    if (selectedId && selectedId !== "new" && !suites.some((item) => item.id === selectedId)) setSelectedId(suites[0]?.id ?? null);
  }, [suites, selectedId]);

  useEffect(() => {
    if (selectedId === "new") { setDraft(emptySuite()); return; }
    const found = suites.find((item) => item.id === selectedId);
    setDraft(found ? JSON.parse(JSON.stringify(found)) : null);
  }, [selectedId, suites]);

  async function save() {
    setSaving(true);
    try {
      const payload = { name: draft.name, scope: draft.scope, origin: draft.origin, cases: draft.cases };
      if (selectedId === "new") {
        await request("/api/evaluation-suites", { method: "POST", body: JSON.stringify(payload) });
      } else {
        await request(`/api/evaluation-suites/${selectedId}`, { method: "PUT", body: JSON.stringify(payload) });
      }
      onChanged();
    } catch (reason) {
      onError(handleAdminError(reason));
    } finally {
      setSaving(false);
    }
  }

  async function doImport() {
    setSaving(true);
    try {
      await request("/api/evaluation-suites", { method: "POST", body: JSON.stringify({ importPath }) });
      onChanged();
    } catch (reason) {
      onError(handleAdminError(reason));
    } finally {
      setSaving(false);
    }
  }

  const updateCase = (index, patch) => setDraft((current) => ({
    ...current,
    cases: current.cases.map((item, position) => position === index ? { ...item, ...patch } : item),
  }));

  return (
    <div className="eval-two-col">
      <div>
        <div className="eval-caption" style={{ marginBottom: 8 }}>验证题库</div>
        {suites.map((item) => (
          <button key={item.id} className={`eval-suite-item ${item.id === selectedId ? "on" : ""}`}
            onClick={() => setSelectedId(item.id)}>
            <b>{item.name}</b>
            <small>修订 {item.revision} · {item.caseCount} 题 · {ORIGIN_LABEL[item.origin] || "题库"}</small>
          </button>
        ))}
        <Button size="small" onClick={() => setSelectedId("new")}>＋ 新建题库</Button>
        <div style={{ margin: "10px 0 6px" }}>
          <Input size="small" value={importPath} onChange={(event) => setImportPath(event.target.value)}
            placeholder="项目内 JSON 路径" />
        </div>
        <Button size="small" onClick={doImport} loading={saving}>从现有 JSON 导入</Button>
      </div>
      {draft ? (
        <div className="eval-panel-card">
          <Flex justify="space-between" align="center" style={{ marginBottom: 12 }} wrap="wrap" gap={10}>
            <div>
              <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>{selectedId === "new" ? "新建题库" : "编辑题库"}</h3>
              <span className="eval-caption">保存产生新修订；已运行批次保留旧题库快照</span>
            </div>
            <Button type="primary" size="small" loading={saving} onClick={save}>保存</Button>
          </Flex>
          <Flex vertical gap={10}>
            <Flex gap={10} wrap="wrap">
              <Input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                placeholder="题库名称" style={{ maxWidth: 300 }} />
              <Select value={draft.origin} style={{ width: 160 }} options={
                Object.entries(ORIGIN_LABEL).map(([value, label]) => ({ value, label }))}
                onChange={(value) => setDraft({ ...draft, origin: value })} />
            </Flex>
            <Input value={draft.scope} onChange={(event) => setDraft({ ...draft, scope: event.target.value })}
              placeholder="适用范围说明（显示在报告开头）" />
            {draft.cases.map((item, index) => (
              <div key={index} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "10px 14px" }}>
                <Flex justify="space-between" align="center" style={{ marginBottom: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>题目 {index + 1}：{item.id || "未命名"}</span>
                  <Button size="small" type="text" danger
                    onClick={() => setDraft({ ...draft, cases: draft.cases.filter((_, position) => position !== index) })}>
                    删除
                  </Button>
                </Flex>
                <Flex vertical gap={6}>
                  <Flex gap={8} wrap="wrap">
                    <Input value={item.id} onChange={(event) => updateCase(index, { id: event.target.value })}
                      placeholder="题目 id" style={{ width: 220 }} />
                    <Select value={item.category || "uncategorized"} style={{ width: 120 }} options={CATEGORY_OPTIONS}
                      onChange={(value) => updateCase(index, { category: value })} />
                  </Flex>
                  <Input.TextArea value={item.question} rows={2} placeholder="问题原文"
                    onChange={(event) => updateCase(index, { question: event.target.value })} />
                  <Input.TextArea value={(item.checks || []).join("\n")} rows={Math.max(3, (item.checks || []).length)}
                    placeholder="判分标准：每行一条（只给评分者与复核者）"
                    onChange={(event) => updateCase(index, { checks: event.target.value.split("\n") })} />
                </Flex>
              </div>
            ))}
            <div>
              <Button size="small" onClick={() => setDraft({
                ...draft, cases: [...draft.cases, { id: "", question: "", checks: [], category: "uncategorized" }],
              })}>＋ 添加题目</Button>
            </div>
          </Flex>
        </div>
      ) : (
        <div className="eval-panel-card"><Empty description="选择或新建一个题库" /></div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 页面主体

export function EvaluationPage({ onRequireUnlock }) {
  const [tab, setTab] = useState("current");
  const [setup, setSetup] = useState(null);
  const [items, setItems] = useState([]);
  const [currentId, setCurrentId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [starting, setStarting] = useState(false);
  const [rerunning, setRerunning] = useState("");
  const [expandedCase, setExpandedCase] = useState(null);
  const pollRef = useRef(null);
  const isActiveDetail = (value) => Boolean(value) &&
    (ACTIVE_STATUSES.includes(value.status) || value.rejudge?.status === "running");
  const active = isActiveDetail(detail);

  async function loadSetup() {
    const data = await request("/api/evaluations/setup");
    setSetup(data);
    return data;
  }

  async function loadList() {
    const data = await request("/api/evaluations");
    setItems(data.items || []);
    return data;
  }

  useEffect(() => {
    Promise.all([loadSetup(), loadList()])
      .then(([, list]) => {
        setCurrentId(list.activeEvaluationId || list.items?.[0]?.id || null);
      })
      .catch((reason) => setError(reason.message));
  }, []);

  useEffect(() => {
    if (!currentId) { setDetail(null); return; }
    let cancelled = false;
    const load = () => request(`/api/evaluations/${currentId}`)
      .then((value) => { if (!cancelled) setDetail(value); })
      .catch((reason) => { if (!cancelled) setError(reason.message); });
    load();
    return () => { cancelled = true; };
  }, [currentId]);

  useEffect(() => {
    // 运行中（含重新初评）约 2 秒轮询；任务结束停止轮询并刷新历史。
    if (!active) { clearInterval(pollRef.current); pollRef.current = null; return undefined; }
    pollRef.current = setInterval(() => {
      request(`/api/evaluations/${currentId}`)
        .then(async (value) => {
          const wasActive = isActiveDetail(detail);
          setDetail(value);
          if (wasActive && !isActiveDetail(value)) await loadList().catch(() => {});
        })
        .catch(() => {});
    }, 2000);
    return () => clearInterval(pollRef.current);
  }, [active, currentId]); // eslint-disable-line

  async function start(payload) {
    setStarting(true);
    setActionError("");
    try {
      const result = await request("/api/evaluations", { method: "POST", body: JSON.stringify(payload) });
      setCurrentId(result.evaluationId);
      setTab("current");
      await loadList().catch(() => {});
    } catch (reason) {
      setActionError(handleAdminError(reason, onRequireUnlock));
    } finally {
      setStarting(false);
    }
  }

  async function runAction(path) {
    setActionError("");
    try {
      await request(path, { method: "POST", body: "{}" });
      request(`/api/evaluations/${currentId}`).then(setDetail).catch(() => {});
    } catch (reason) {
      setActionError(handleAdminError(reason, onRequireUnlock));
    }
  }

  async function rerun(item) {
    setRerunning(item.id);
    try {
      await start({ suiteId: item.suite?.id, repeats: item.repeats, judge: item.judge });
    } finally {
      setRerunning("");
    }
  }

  async function exportReport(source) {
    try {
      const report = await request(`/api/evaluations/${currentId}/report?source=${source}`);
      exportMarkdown(`${currentId}-${source}-report.md`, report.markdown);
    } catch (reason) {
      setActionError(handleAdminError(reason, onRequireUnlock));
    }
  }

  return (
    <div className="page-wrap eval-page">
      {error && (
        <div className="eval-shell" style={{ marginBottom: 10 }}>
          <div className="eval-note">页面数据加载失败：{error}</div>
        </div>
      )}
      <div className="eval-shell">
        <Tabs activeKey={tab} onChange={setTab} className="eval-tabs"
          items={[
            {
              key: "current", label: "本次验证",
              children: !setup ? null : currentId && detail ? (
                <BatchView detail={detail}
                  expandedKey={expandedCase}
                  onToggleCase={(row) => setExpandedCase(row ? { caseId: row.caseId, repeat: row.repeat } : null)}
                  onRefreshDetail={() => request(`/api/evaluations/${currentId}`).then(setDetail).catch(() => {})}
                  onStop={() => runAction(`/api/evaluations/${currentId}/cancel`)}
                  onRejudge={() => runAction(`/api/evaluations/${currentId}/rejudge`)}
                  onExport={exportReport}
                  onNewConfig={() => { setCurrentId(null); setDetail(null); setActionError(""); setExpandedCase(null); }}
                  actionError={actionError} />
              ) : (
                <Flex vertical gap={12}>
                  {actionError && <div className="eval-note">无法开始验证：{actionError}</div>}
                  <LaunchSection setup={setup} onStart={start} starting={starting} />
                </Flex>
              ),
            },
            {
              key: "history", label: "历史记录",
              children: <HistorySection items={items} onOpen={(item) => { setCurrentId(item.id); setTab("current"); }}
                onRerun={rerun} rerunning={rerunning} />,
            },
            {
              key: "suites", label: "验证题库",
              children: setup && <SuitesSection suites={setup.suites || []} onError={setError}
                onChanged={() => { loadSetup().catch((reason) => setError(reason.message)); }} />,
            },
          ]}
        />
      </div>
    </div>
  );
}

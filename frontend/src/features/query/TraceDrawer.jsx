import React, { useEffect, useMemo, useState } from "react";
import { Alert, Button, Drawer, Empty, List, Segmented, Spin, Tabs, Typography } from "antd";
import { DownloadSimple, ArrowClockwise } from "@phosphor-icons/react";
import { request } from "../../lib/api.js";
import { isActiveRun } from "../../lib/query-state.js";
import { tracePath, traceStatus, traceStopReason, traceTimeline } from "../../lib/trace.js";
import "./trace.css";

const FILTERS = [{ label: "全部", value: "all" }, { label: "模型", value: "model" },
  { label: "工具", value: "tool" }, { label: "调度", value: "retrieval" }];
const AST_TOOLS = new Set(["find_code_symbol", "get_code_symbol", "find_code_relations"]);
const PROMPT_SECTIONS = { role: "角色", behavior: "调查与回答", project: "本轮工程与资料",
  navigation: "系统导航", capabilities: "能力说明" };
function parsed(value) { try { return JSON.parse(value); } catch { return null; } }
function json(value) {
  if (typeof value !== "string") return JSON.stringify(value, null, 2);
  const data = parsed(value);
  return data && typeof data === "object" ? JSON.stringify(data, null, 2) : value;
}

export function TraceDrawer({ projectId, runId, onClose }) {
  const [trace, setTrace] = useState(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!runId || !projectId) return undefined;
    const controller = new AbortController();
    let timer;
    setTrace(null); setError(""); setSelected(null); setFilter("all");
    async function load() {
      try {
        const value = await request(tracePath(projectId, runId), {}, controller.signal);
        if (controller.signal.aborted) return;
        setTrace(value); setError("");
        if (isActiveRun(value.run)) timer = setTimeout(load, 1500);
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason.message);
      }
    }
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [projectId, runId, revision]);
  const timeline = useMemo(() => trace ? traceTimeline(trace) : [], [trace]);
  const execution = trace?.run.execution;
  const stopReason = trace ? traceStopReason(trace) : "";
  const visible = timeline.filter((item) => filter === "all" || filter === item.kind);
  const active = visible.find((item) => item.id === selected) || visible.find((item) => item.kind === "model") || visible[0];
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(trace, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `${runId}.trace.json`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <Drawer title="调用轨迹" open={Boolean(runId)} onClose={onClose} size={1100} className="trace-drawer"
    extra={<div className="trace-actions"><Button icon={<ArrowClockwise size={14} />} onClick={() => setRevision((n) => n + 1)}>刷新</Button>
      <Button disabled={!trace} icon={<DownloadSimple size={14} />} onClick={download}>导出 JSON</Button></div>}>
    {error && <Alert type="error" showIcon title="读取轨迹失败" description={error} />}
    {!trace && !error && <div className="trace-loading"><Spin aria-label="读取调用轨迹" /></div>}
    {trace && <>
      <div className="trace-summary"><p>{trace.run.question}</p><div>
        <span>{traceStatus(trace.run.status)}</span><span>{trace.calls.length} 次模型请求</span>
        {["budget", "context", "no_progress"].includes(stopReason) && <span>{stopReason === "context" ? "上下文预算不足" : stopReason === "no_progress" ? "连续无新增证据" : "达到调查预算"}</span>}
        {execution?.profileId && <span>{execution.profileId}</span>}
        {(execution?.model || trace.settings?.requestedModel) && <span>{execution?.model || trace.settings.requestedModel}</span>}
        <span>{timeline.filter((item) => item.kind === "tool" && item.status !== "skipped").length} 次工具调用</span>
        {trace.run.durationMs > 0 && <span>{(trace.run.durationMs / 1000).toFixed(2)} 秒</span>}
      </div><code>{runId}</code></div>
      {trace.historical && <Alert type="info" showIcon title="历史运行仅保留已有事件，未归档的模型请求无法补回。" />}
      {trace.requestSnapshotAvailable === false && !trace.historical && <Alert type="info" showIcon title="本轨迹只保存模型调用摘要，未保存每轮完整请求 messages/tools。" />}
      {trace.run.error && <Alert type="error" showIcon title={trace.run.error} />}
      <Tabs defaultActiveKey="timeline" items={[
        { key: "timeline", label: "调用过程", children: <>
          <Segmented options={FILTERS} value={filter} onChange={setFilter} aria-label="轨迹类型" />
          <div className="trace-workspace">
            <nav className="trace-list" aria-label="调用步骤">
              {visible.map((item) => <button type="button" key={item.id} aria-label={item.title}
                aria-pressed={item.id === active?.id} onClick={() => setSelected(item.id)}>
                <span className="trace-step-top"><strong>{item.title}</strong><small>{item.elapsedMs == null ? "—" : `${(item.elapsedMs / 1000).toFixed(2)}s`}</small></span>
                <span className="trace-step-bottom"><span>{item.subtitle}</span><small>{traceStatus(item.status)}</small></span>
              </button>)}
              {!visible.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有此类记录" />}
            </nav>
            <section className="trace-inspector" aria-label="调用详情">{active && <TraceDetail item={active} />}</section>
          </div>
        </> },
        { key: "state", label: "调查事项", children: <>
          <p className="trace-note">原文已读与结构证据已交付分别记录，不表示业务语义已证明。</p>
          <JsonBlock value={trace.retrievalPlan || trace.investigation || { note: "本轮没有调查事项记录" }} />
        </> },
        ...(execution?.materials?.length ? [{ key: "materials", label: "本轮资料",
          children: <MaterialUse run={trace.run} steps={timeline} /> }] : []),
        ...(trace.systemPrompt ? [{ key: "prompt", label: "系统提示词",
          children: <PromptSnapshot prompt={trace.systemPrompt} /> }] : []),
        ...(execution?.profileId ? [{ key: "execution", label: "运行配置", children: <JsonBlock value={execution} /> }] : []),
        { key: "raw", label: "完整记录", children: <JsonBlock value={trace} /> },
      ]} />
    </>}
  </Drawer>;
}

function PromptSnapshot({ prompt }) {
  const observed = prompt.observed || [];
  return <>
    <p className="trace-note">{prompt.variant === "layered" ? "分层候选" : "现行提示词"} · {prompt.sessionResumed ? "复用会话" : "新会话"}</p>
    {observed.length ? observed.map((snapshot, index) => <section key={index}>
      <h4>{observed.length === 1 ? "Pi 请求前的生效提示词" : `Pi 生效提示词 ${index + 1}`}</h4>
      <pre className="trace-code">{snapshot.text}</pre>
      <details><summary>已启用工具的说明</summary><ul>
        {snapshot.tools.map((tool) => <li key={tool.name}><strong>{tool.name}</strong> {tool.description}</li>)}
      </ul></details>
    </section>) : <>
      <p className="trace-note">未观测到 Pi 请求前的提示词。这里显示 CodeAgent 提交的内容。</p>
      <pre className="trace-code">{prompt.text}</pre>
      <p className="trace-note">配置工具 · {prompt.enabledTools.join("、")}</p>
    </>}
    <details><summary>CodeAgent 装配片段</summary>
      {prompt.sections.map((section) => <section key={section.kind}>
        <h4>{PROMPT_SECTIONS[section.kind]}</h4><pre className="trace-code">{section.text}</pre>
      </section>)}
    </details>
  </>;
}

function MaterialUse({ run, steps }) {
  const execution = run.execution;
  const ast = execution.tools?.find((item) => item.capability === "ast");
  const calls = steps.filter((item) => item.kind === "tool" && item.status !== "skipped"
    && AST_TOOLS.has(item.data.name)).length;
  const readFiles = run.usage?.verification?.readFiles;
  return <List dataSource={execution.materials} renderItem={(source) => {
    const toolOnly = source.delivery === "tools";
    const delivered = toolOnly ? ast?.status === "ready" : source.available;
    const files = !toolOnly && source.path && Array.isArray(readFiles)
      ? readFiles.filter((file) => file.startsWith(`${source.path}/`)) : null;
    return <List.Item>
      <div>
        <Typography.Text strong>{source.label}</Typography.Text>
        <div className="trace-note">
          {delivered ? "已装配" : "未装配"}
          {toolOnly ? ` · 结构工具调用 ${calls} 次`
            : files ? ` · 交付 ${files.length} 份文件原文` : " · 原文读取记录不可用"}
          {source.kind === "ast" && source.version && ` · ${source.version}`}
        </div>
        {files?.length > 0 && <details><summary>查看已读文件</summary>
          <ul>{files.map((file) => <li key={file}><code>{file}</code></li>)}</ul>
        </details>}
      </div>
    </List.Item>;
  }} />;
}

function JsonBlock({ value }) { return <pre className="trace-code">{json(value)}</pre>; }

function TraceDetail({ item }) {
  const value = item.data;
  const result = item.kind === "tool" ? parsed(value.output) : null;
  const sources = result?.evidence || (result?.path && result?.content ? [result] : []);
  if (item.kind === "model") return <>
    <h3>{item.title} · {item.subtitle}</h3>
    <p className="trace-note">{traceStatus(value.status)}{value.durationMs != null && ` · ${(value.durationMs / 1000).toFixed(2)} 秒`} · 输入 {value.inputTextChars} 字符</p>
    <h4>请求消息</h4>
    {(value.messages || []).map((message, index) => <div className="trace-message" key={index}>
      <strong>{message.role}{message.name ? ` · ${message.name}` : ""}</strong>
      <JsonBlock value={message.content} />
      {message.toolCalls?.length > 0 && <JsonBlock value={message.toolCalls} />}
    </div>)}
    <details><summary>工具定义 · {value.tools?.length || 0}</summary><JsonBlock value={value.tools} /></details>
    <h4>公开响应</h4><JsonBlock value={value.responses || { errorType: value.errorType, error: value.error, status: value.status }} />
    <details><summary>用量与本次调查状态</summary><JsonBlock value={{ usage: value.usage,
      contextBudget: value.contextBudget, investigation: value.investigation }} /></details>
  </>;
  if (item.kind === "tool") return <>
    <h3>{item.title} · {item.subtitle}</h3>
    {value.reason && <p>{value.reason}</p>}
    <h4>输入参数</h4><JsonBlock value={value.input || {}} />
    {sources.length > 0 && <details><summary>源码原文 · {sources.length} 段</summary>
      {sources.map((source, index) => <div key={index}><code>{source.path}:{source.start || source.lineStart}–{source.end || source.lineEnd}</code>
        <JsonBlock value={source.content} /></div>)}
    </details>}
    <h4>完整工具结果</h4><JsonBlock value={value.output ?? "等待工具结果"} />
  </>;
  return <><h3>{item.title}</h3><JsonBlock value={value} /></>;
}

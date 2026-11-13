import React, { useEffect, useMemo, useState } from "react";
import { Alert, Button, Drawer, Empty, Segmented, Spin, Tabs } from "antd";
import { DownloadSimple, ArrowClockwise } from "@phosphor-icons/react";
import { request } from "../../lib/api.js";
import { isActiveRun } from "../../lib/query-state.js";
import { tracePath, traceStatus, traceTimeline } from "../../lib/trace.js";
import "./trace.css";

const FILTERS = [{ label: "全部", value: "all" }, { label: "模型", value: "model" },
  { label: "工具", value: "tool" }, { label: "调度", value: "retrieval" }];
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
        {["budget", "context"].includes(trace.stopReason) && <span>{trace.stopReason === "context" ? "上下文预算不足" : "达到调查预算"}</span>}
        {execution?.profileId && <span>{execution.profileId}</span>}
        {(execution?.model || trace.settings?.requestedModel) && <span>{execution?.model || trace.settings.requestedModel}</span>}
        <span>{timeline.filter((item) => item.kind === "tool" && item.status !== "skipped").length} 次工具调用</span>
        {trace.run.durationMs > 0 && <span>{(trace.run.durationMs / 1000).toFixed(2)} 秒</span>}
      </div><code>{runId}</code></div>
      {trace.historical && <Alert type="info" showIcon title="历史运行仅保留已有事件，未归档的模型请求无法补回。" />}
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
        ...(execution?.profileId ? [{ key: "execution", label: "运行配置", children: <JsonBlock value={execution} /> }] : []),
        { key: "raw", label: "完整记录", children: <JsonBlock value={trace} /> },
      ]} />
    </>}
  </Drawer>;
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
    <details><summary>用量与本次调查状态</summary><JsonBlock value={{ usage: value.usage, investigation: value.investigation }} /></details>
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

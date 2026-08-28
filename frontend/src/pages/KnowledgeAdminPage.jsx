import React, { useEffect, useState } from "react";
import { ArrowClockwise, Play, WarningCircle } from "@phosphor-icons/react";
import { Alert, Button, Card, Empty, Flex, Skeleton, Space, Splitter, Tag, Typography } from "antd";
import { request } from "../lib/api.js";
import { formatTime } from "../lib/format.js";

export function KnowledgeAdminPage({ onRequireUnlock }) {
  const [items, setItems] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState("");
  const [error, setError] = useState("");

  async function load(keepSelection = true) {
    setLoading(true);
    setError("");
    try {
      const data = await request("/api/knowledge/functions?q=");
      setItems(data.items || []);
      if (keepSelection && selectedId) setDetail((data.items || []).find((item) => item.id === selectedId) || null);
    } catch (reason) { setError(reason.message); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(false); }, []);

  async function refresh() {
    setRunning("refresh"); setError("");
    try {
      await request("/api/knowledge/refresh", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ analyze: true }),
      });
      await load();
    } catch (reason) {
      setError(reason.message);
      if (String(reason.message).includes("凭证") || String(reason.message).includes("credential")) onRequireUnlock?.();
    } finally { setRunning(""); }
  }

  async function analyze() {
    if (!selectedId) return;
    setRunning(selectedId); setError("");
    try {
      const value = await request(`/api/knowledge/functions/${encodeURIComponent(selectedId)}/analyze`, { method: "POST" });
      setDetail(value);
      setItems((current) => current.map((item) => item.id === value.id ? value : item));
    } catch (reason) {
      setError(reason.message);
      if (String(reason.message).includes("凭证") || String(reason.message).includes("credential")) onRequireUnlock?.();
    } finally { setRunning(""); }
  }

  return (
    <div className="page-wrap">
      <Flex justify="space-between" align="center" style={{ maxWidth: 1180, margin: "0 auto 14px", width: "100%" }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>功能知识</Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>人工文档提供功能和入口，Agent 补充检索索引、流程与规则。</Typography.Text>
        </div>
        <Button type="primary" icon={<ArrowClockwise size={15} />} loading={running === "refresh"} onClick={refresh}>更新知识库</Button>
      </Flex>
      {error && <Alert type="error" showIcon title="操作未完成" description={error} closable onClose={() => setError("")} style={{ maxWidth: 1180, margin: "0 auto 12px" }} />}
      <Splitter style={{ maxWidth: 1180, margin: "0 auto", width: "100%", height: "calc(100dvh - 225px)" }}>
        <Splitter.Panel defaultSize="36%" min="26%" max="52%">
          <div className="result-list">
            {loading ? <Skeleton active paragraph={{ rows: 8 }} style={{ padding: 16 }} /> : items.length === 0 ? (
              <Empty description="在配置的知识目录放入功能文档，然后点击更新知识库。" style={{ marginTop: 90 }} />
            ) : items.map((item) => <FunctionRow key={item.id} item={item} active={item.id === selectedId} onClick={() => { setSelectedId(item.id); setDetail(item); }} />)}
          </div>
        </Splitter.Panel>
        <Splitter.Panel>
          <div className="detail-scroll">
            {detail ? <FunctionDetail item={detail} running={running === detail.id} onAnalyze={analyze} /> : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择一个功能查看入口、关键表和分析结果" style={{ marginTop: 120 }} />
            )}
          </div>
        </Splitter.Panel>
      </Splitter>
    </div>
  );
}

function FunctionRow({ item, active, onClick }) {
  const coverage = item.analysis?.coverage || {};
  return <button className={`r-row ${active ? "on" : ""}`} onClick={onClick}>
    <span className="r-glyph biz">知</span><span className="r-copy"><b>{item.name}</b><small>{item.definition?.summary || "暂无功能说明"}</small><small style={{ color: "#a3a29a" }}>{coverage.entryCount ?? item.entries?.length ?? 0} 个入口 · {coverage.evidenceCount ?? 0} 条代码证据</small></span><AnalysisStatus value={item.analysis?.status} />
  </button>;
}

function FunctionDetail({ item, running, onAnalyze }) {
  const definition = item.definition || {}; const analysis = item.analysis || {};
  return <Card styles={{ body: { padding: "20px 24px" } }}>
    <Flex justify="space-between" align="flex-start" gap={16}><div><Typography.Text type="secondary" code style={{ fontSize: 11 }}>{item.id}</Typography.Text><Typography.Title level={5} style={{ margin: "6px 0 4px" }}>{item.name}</Typography.Title><Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{definition.summary}</Typography.Paragraph></div><Button icon={<Play size={14} />} loading={running} onClick={onAnalyze}>重新分析</Button></Flex>
    <Section title="标签与别名"><Flex gap={6} wrap="wrap">{[...(definition.tags || []), ...(definition.aliases || [])].map((value) => <Tag key={value} bordered={false}>{value}</Tag>)}</Flex></Section>
    <Section title="业务场景"><Lines items={definition.scenarios} /></Section>
    <Section title="工程与入口"><Space direction="vertical" size={6} style={{ width: "100%" }}>{(item.entries || []).map((entry) => <div className="knowledge-line" key={entry.id}><Flex justify="space-between" gap={8}><span><b>{entry.project_name}</b> · {entry.entry_type} · <code>{entry.class_name}</code></span><EntryStatus value={entry.resolution_status} /></Flex>{entry.location && <small>{entry.location.path}:{entry.location.line_start}</small>}</div>)}</Space></Section>
    <Section title="关键表"><Lines items={(item.keyTables || []).map((table) => `${table.table_name}：${table.purpose}`)} /></Section>
    <Section title="Agent 业务流程总结"><AnalysisLines items={analysis.flow} empty="当前没有足够证据生成业务流程。" /></Section>
    <Section title="Agent 核心业务规则"><AnalysisLines items={analysis.rules} empty="当前没有足够证据生成核心规则。" /></Section>
    <Section title="分析状态"><Flex gap={8} align="center"><AnalysisStatus value={analysis.status} /><Typography.Text type="secondary" style={{ fontSize: 12 }}>{analysis.message || `最近分析 ${formatTime(analysis.analyzedAt)}`}</Typography.Text></Flex></Section>
  </Card>;
}

function Section({ title, children }) { return <><Typography.Text type="secondary" style={{ fontSize: 11, letterSpacing: ".06em", display: "block", margin: "18px 0 8px" }}>{title}</Typography.Text>{children}</>; }
function Lines({ items = [] }) { return items.length ? <Space direction="vertical" size={5} style={{ width: "100%" }}>{items.map((item, index) => <div className="knowledge-line" key={index}>{item}</div>)}</Space> : <Typography.Text type="secondary" style={{ fontSize: 12 }}>暂未填写。</Typography.Text>; }
function AnalysisLines({ items = [], empty }) { return items.length ? <Space direction="vertical" size={5} style={{ width: "100%" }}>{items.map((item, index) => <div className="knowledge-line" key={index}>{item.sequence ? `${item.sequence}. ` : ""}{item.statement}<small style={{ display: "block", color: "#a3a29a" }}>{item.evidence_ids?.length || 0} 条代码证据</small></div>)}</Space> : <Typography.Text type="secondary" style={{ fontSize: 12 }}>{empty}</Typography.Text>; }
function EntryStatus({ value }) { const [color, label] = ({ RESOLVED: ["green", "已定位"], AMBIGUOUS: ["orange", "多个候选"], NOT_FOUND: ["red", "未定位"], PENDING: ["default", "等待定位"] }[value] || ["default", value || "未知"]); return <Tag color={color} style={{ margin: 0 }}>{label}</Tag>; }
function AnalysisStatus({ value }) { const [color, label] = ({ READY: ["green", "分析完成"], INDEXED: ["blue", "索引完成"], STALE: ["orange", "代码已变化"], INSUFFICIENT: ["orange", "证据不足"], FAILED: ["red", "分析失败"], NOT_RUN: ["default", "未分析"] }[value] || ["default", value || "未分析"]); return <Tag color={color} icon={value === "FAILED" ? <WarningCircle size={11} /> : undefined} style={{ margin: 0 }}>{label}</Tag>; }

import React, { useEffect, useMemo, useState } from "react";
import { ArrowClockwise, GitBranch, WarningCircle } from "@phosphor-icons/react";
import { Alert, Button, Card, Empty, Flex, Input, Select, Skeleton, Space, Splitter, Tag, Typography } from "antd";
import { request } from "../lib/api.js";

const TYPES = [
  ["", "全部类型"], ["SYSTEM", "系统"], ["BUSINESS_TERM", "业务术语"],
  ["CAPABILITY", "业务能力"], ["FLOW", "业务流程"], ["RULE", "业务规则"],
];

export function KnowledgeAdminPage({ onRequireUnlock }) {
  const [items, setItems] = useState([]);
  const [relations, setRelations] = useState([]);
  const [selected, setSelected] = useState(null);
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState("");
  const [error, setError] = useState("");
  const [summary, setSummary] = useState(null);

  async function load(nextQuery = query, nextType = type) {
    setLoading(true); setError("");
    try {
      const params = new URLSearchParams();
      if (nextQuery.trim()) params.set("q", nextQuery.trim());
      if (nextType) params.set("type", nextType);
      const data = await request(`/api/knowledge/entities?${params}`);
      setItems(data.items || []); setRelations(data.relations || []);
      if (selected) {
        const found = [...(data.items || []), ...(data.relations || [])].find((item) => item.id === selected.id);
        if (found) setSelected(found);
      }
    } catch (reason) { setError(reason.message); }
    finally { setLoading(false); }
  }

  useEffect(() => { load("", ""); }, []);

  async function refresh() {
    setRunning("refresh"); setError("");
    try {
      const result = await request("/api/knowledge/baselines/refresh", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mapCode: true, useModel: true }),
      });
      setSummary(result); await load();
    } catch (reason) { handleAdminError(reason); }
    finally { setRunning(""); }
  }

  async function remap() {
    setRunning("mapping"); setError("");
    try {
      const result = await request("/api/knowledge/mappings/rebuild", { method: "POST" });
      setSummary(result); await load();
    } catch (reason) { handleAdminError(reason); }
    finally { setRunning(""); }
  }

  function handleAdminError(reason) {
    setError(reason.message);
    if (String(reason.message).includes("凭证") || String(reason.message).includes("credential")) onRequireUnlock?.();
  }

  const list = useMemo(() => [...items, ...relations], [items, relations]);
  return (
    <div className="page-wrap">
      <Flex justify="space-between" align="center" style={{ maxWidth: 1180, margin: "0 auto 14px", width: "100%" }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>业务知识维护</Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>导入自然语言业务基线，形成结构化知识，并建立独立的代码映射。</Typography.Text>
        </div>
        <Space>
          <Button icon={<GitBranch size={15} />} loading={running === "mapping"} onClick={remap}>重新映射代码</Button>
          <Button type="primary" icon={<ArrowClockwise size={15} />} loading={running === "refresh"} onClick={refresh}>导入业务基线</Button>
        </Space>
      </Flex>
      {error && <Alert type="error" showIcon title="操作未完成" description={error} closable onClose={() => setError("")} style={{ maxWidth: 1180, margin: "0 auto 12px" }} />}
      {summary && <ImportSummary value={summary} />}
      <Flex gap={8} style={{ maxWidth: 1180, margin: "0 auto 10px" }}>
        <Input.Search value={query} onChange={(event) => setQuery(event.target.value)} onSearch={() => load()} allowClear placeholder="搜索术语、能力、规则或关系" />
        <Select value={type} style={{ width: 150 }} options={TYPES.map(([value, label]) => ({ value, label }))} onChange={(value) => { setType(value); load(query, value); }} />
      </Flex>
      <Splitter style={{ maxWidth: 1180, margin: "0 auto", width: "100%", height: "calc(100dvh - 255px)" }}>
        <Splitter.Panel defaultSize="38%" min="28%" max="52%">
          <div className="result-list">
            {loading ? <Skeleton active paragraph={{ rows: 8 }} style={{ padding: 16 }} /> : list.length === 0 ? (
              <Empty description="在业务基线目录放入 Markdown，然后点击导入业务基线。" style={{ marginTop: 90 }} />
            ) : list.map((item) => <KnowledgeRow key={item.id} item={item} active={item.id === selected?.id} onClick={() => setSelected(item)} />)}
          </div>
        </Splitter.Panel>
        <Splitter.Panel>
          <div className="detail-scroll">{selected ? <KnowledgeDetail item={selected} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择一条知识查看来源、关系和代码映射" style={{ marginTop: 120 }} />}</div>
        </Splitter.Panel>
      </Splitter>
    </div>
  );
}

function ImportSummary({ value }) {
  const counts = value.entityCounts || {}; const mapping = value.mappingCounts || {};
  return <Alert type="success" showIcon title={`已导入 ${value.sourceCount ?? "-"} 份业务基线`} description={`知识 ${Object.values(counts).reduce((sum, item) => sum + Number(item || 0), 0)} 条；已验证映射 ${mapping.VERIFIED || 0}，候选 ${mapping.CANDIDATE || 0}，未定位 ${mapping.UNRESOLVED || 0}`} style={{ maxWidth: 1180, margin: "0 auto 12px" }} />;
}

function KnowledgeRow({ item, active, onClick }) {
  const isRelation = item.type === "RELATION";
  return <button className={`r-row ${active ? "on" : ""}`} onClick={onClick}>
    <span className="r-glyph biz">{isRelation ? "关" : "知"}</span>
    <span className="r-copy"><b>{isRelation ? `${item.from} → ${item.to}` : item.name}</b><small>{isRelation ? relationLabel(item.relation) : item.definition}</small><small style={{ color: "#a3a29a" }}>{typeLabel(item.type)} · {item.mappings?.length || 0} 个代码映射</small></span>
    <KnowledgeStatus value={item.status} />
  </button>;
}

function KnowledgeDetail({ item }) {
  const isRelation = item.type === "RELATION";
  return <Card styles={{ body: { padding: "20px 24px" } }}>
    <Typography.Text type="secondary" code style={{ fontSize: 11 }}>{item.id}</Typography.Text>
    <Typography.Title level={5} style={{ margin: "6px 0 4px" }}>{isRelation ? `${item.from} ${relationLabel(item.relation)} ${item.to}` : item.name}</Typography.Title>
    <Typography.Paragraph type="secondary">{isRelation ? (item.scope ? `适用范围：${item.scope}` : "业务关系") : item.definition}</Typography.Paragraph>
    <Flex gap={6} wrap="wrap"><Tag>{typeLabel(item.type)}</Tag><KnowledgeStatus value={item.status} />{(item.aliases || []).map((value) => <Tag key={value}>{value}</Tag>)}</Flex>
    {!isRelation && <><Section title="结构化信息"><pre className="knowledge-json">{JSON.stringify(item.attributes || {}, null, 2)}</pre></Section><Section title="业务关系"><Lines items={(item.relations || []).map((value) => value.type === "RELATION" ? `${value.from} ${relationLabel(value.relation)} ${value.to}` : String(value))} /></Section></>}
    <Section title="代码映射"><Mappings items={item.mappings || []} /></Section>
    <Section title="知识来源"><div className="knowledge-line">{item.source?.path || item.sourceId || "未知来源"}</div></Section>
  </Card>;
}

function Mappings({ items }) {
  if (!items.length) return <Typography.Text type="secondary" style={{ fontSize: 12 }}>尚未建立代码映射。</Typography.Text>;
  return <Space direction="vertical" size={6} style={{ width: "100%" }}>{items.map((item) => <div className="knowledge-line" key={item.id}><Flex justify="space-between" gap={8}><code>{item.codeReference || "未定位"}</code><MappingStatus value={item.status} /></Flex><small>{item.relation} · 可信度 {Math.round((item.confidence || 0) * 100)}%{item.message ? ` · ${item.message}` : ""}</small></div>)}</Space>;
}

function Section({ title, children }) { return <><Typography.Text type="secondary" style={{ fontSize: 11, letterSpacing: ".06em", display: "block", margin: "18px 0 8px" }}>{title}</Typography.Text>{children}</>; }
function Lines({ items = [] }) { return items.length ? <Space direction="vertical" size={5} style={{ width: "100%" }}>{items.map((item, index) => <div className="knowledge-line" key={index}>{item}</div>)}</Space> : <Typography.Text type="secondary" style={{ fontSize: 12 }}>暂无。</Typography.Text>; }
function KnowledgeStatus({ value }) { const [color, label] = ({ VERIFIED: ["green", "已确认"], CANDIDATE: ["blue", "候选"], CONFLICTED: ["red", "存在冲突"], UNRESOLVED: ["orange", "未定位"], DEPRECATED: ["default", "已废弃"] }[value] || ["default", value || "未知"]); return <Tag color={color} style={{ margin: 0 }}>{label}</Tag>; }
function MappingStatus({ value }) { const [color, label] = ({ VERIFIED: ["green", "已验证"], CANDIDATE: ["blue", "候选"], UNRESOLVED: ["orange", "未定位"], CONFLICTED: ["red", "冲突"] }[value] || ["default", value || "未知"]); return <Tag color={color} icon={value === "CONFLICTED" ? <WarningCircle size={11} /> : undefined} style={{ margin: 0 }}>{label}</Tag>; }
function typeLabel(value) { return ({ SYSTEM: "系统", BUSINESS_TERM: "业务术语", CAPABILITY: "业务能力", FLOW: "业务流程", RULE: "业务规则", RELATION: "业务关系" }[value] || value); }
function relationLabel(value) { return ({ TRIGGERS: "触发", PRODUCES: "产生", BELONGS_TO: "属于", DEPENDS_ON: "依赖", HANDLED_BY: "由…处理" }[value] || value); }

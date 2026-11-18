import React, { useEffect, useState } from "react";
import { ArrowClockwise } from "@phosphor-icons/react";
import { Alert, Button, Card, Flex, Select, Tag } from "antd";
import { request } from "../../lib/api.js";
import { BusinessContextEditor } from "./BusinessContextEditor.jsx";
import { PROJECTION_STATUS, ProjectionNotice, projectionParser } from "./ProjectionNotice.jsx";
import "./knowledge-page.css";

const PARSERS = [
  { value: "claude", label: "Claude Code 结构化" },
  { value: "markdown", label: "Markdown 规则解析（无需模型）" },
];

/* 业务知识维护：以 Markdown 原文为中心的三栏布局（文档列表 / 原文编辑 / 结构化投影）。
 * 实体与关系的浏览归知识图谱页；这里只保留投影状态与显式导入。
 */
export function KnowledgeAdminPage({ projectId, adminUnlocked, onRequireUnlock }) {
  const [projection, setProjection] = useState(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    request("/api/materials/projection", {}, controller.signal)
      .then((data) => { if (!controller.signal.aborted) setProjection(data); })
      .catch(() => {});
    return () => controller.abort();
  }, [revision, projectId]);
  const updateProjection = () => setRevision((value) => value + 1);

  return (
    <div className="page-wrap knowledge-page">
      <div className="knowledge-heading">
        <div><h1>业务知识</h1><p>维护代码无法直接说明的业务术语、规则和约定；业务图谱由这些文档按需投影。</p></div>
        <Button href="#/graph">在知识图谱中查看 →</Button>
      </div>
      <ProjectionNotice state={projection} />
      <div className="knowledge-grid">
        <BusinessContextEditor adminUnlocked={adminUnlocked} onRequireUnlock={onRequireUnlock}
          onSaved={updateProjection} />
        <ProjectionPane revision={revision} projection={projection}
          onRequireUnlock={onRequireUnlock} onImported={updateProjection} />
      </div>
    </div>
  );
}

function ProjectionPane({ revision, projection, onRequireUnlock, onImported }) {
  const [parser, setParser] = useState("claude");
  const [running, setRunning] = useState("");
  const [error, setError] = useState("");
  const [summary, setSummary] = useState(null);
  const [counts, setCounts] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    request("/api/knowledge/entities", {}, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) {
          setCounts({ entities: data.items?.length ?? 0, relations: data.relations?.length ?? 0 });
        }
      })
      .catch(() => {});
    return () => controller.abort();
  }, [revision]);

  async function refresh() {
    setRunning("refresh"); setError("");
    try {
      const result = await request("/api/knowledge/baselines/refresh", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parser }),
      });
      setSummary(result);
      onImported?.();
    } catch (reason) {
      setError(reason.message);
      if (reason.status === 401) onRequireUnlock?.();
    } finally {
      setRunning("");
    }
  }

  const [statusLabel, statusColor] = PROJECTION_STATUS[projection?.status] || PROJECTION_STATUS.empty;
  return <Card title="结构化投影" size="small" extra={<Tag color={statusColor}>{statusLabel}</Tag>}
    className="knowledge-projection">
    <p className="knowledge-projection-note">这里展示文档派生的结构化状态；业务事实以人工维护的原文为准。</p>
    <dl className="guide-kv">
      <dt>已导入来源</dt><dd>{projection?.importedDocumentCount ?? "—"}</dd>
      <dt>实体与规则</dt><dd>{counts ? counts.entities : "…"}</dd>
      <dt>业务关系</dt><dd>{counts ? counts.relations : "…"}</dd>
      <dt>最近导入</dt><dd>{projection?.importedAt ? new Date(projection.importedAt).toLocaleString("zh-CN") : "—"}</dd>
      <dt>解析器</dt><dd>{projectionParser(projection?.parser) || "—"}</dd>
    </dl>
    <Flex gap={6} wrap align="center" style={{ marginTop: 14 }}>
      <Select aria-label="投影解析器" value={parser} options={PARSERS} onChange={setParser} style={{ width: 190 }} />
      <Button type="primary" icon={<ArrowClockwise size={15} />} loading={running === "refresh"}
        onClick={refresh}>更新投影</Button>
    </Flex>
    {error && <Alert type="error" showIcon title="操作未完成" description={error} closable
      onClose={() => setError("")} style={{ marginTop: 12 }} />}
    {summary && <ImportSummary value={summary} />}
    <Button type="link" href="#/evaluation" style={{ marginTop: 10, paddingLeft: 0 }}>验证问答效果 →</Button>
  </Card>;
}

function ImportSummary({ value }) {
  const counts = value.entityCounts || {}; const anchors = value.anchorCounts || {};
  return <Alert type="success" showIcon title={`已导入 ${value.sourceCount ?? "-"} 份业务补充知识`}
    description={`知识 ${Object.values(counts).reduce((sum, item) => sum + Number(item || 0), 0)} 条；调查入口 ${anchors.ACTIVE || 0} 个，候选 ${anchors.CANDIDATE || 0} 个，未解析 ${anchors.UNRESOLVED || 0} 个`}
    style={{ marginTop: 12 }} />;
}

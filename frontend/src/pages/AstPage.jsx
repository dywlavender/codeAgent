import React, { useEffect, useRef, useState } from "react";
import { ArrowClockwise, CheckCircle, Clock, Code, FileText, Gear, WarningCircle } from "@phosphor-icons/react";
import { Alert, Button, Card, Empty, Flex, List, Modal, Progress, Tag, Typography } from "antd";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { request } from "../lib/api.js";

const STATUS = {
  missing: { label: "未生成", color: "default", icon: <Clock size={16} /> },
  generating: { label: "生成中", color: "processing", icon: <Gear size={16} /> },
  available: { label: "可用", color: "success", icon: <CheckCircle size={16} /> },
  stale: { label: "待更新", color: "warning", icon: <WarningCircle size={16} /> },
  failed: { label: "生成失败", color: "error", icon: <WarningCircle size={16} /> },
};

function adminMessage(error, onRequireUnlock) {
  const text = String(error?.message || "");
  if (text.includes("401") || text.includes("credential") || text.includes("管理员")) {
    onRequireUnlock?.();
    return "需要管理员口令后才能生成 AST。";
  }
  return text || "请求失败";
}

export function AstPage({ projectId, onRequireUnlock }) {
  const [status, setStatus] = useState(null);
  const [documents, setDocuments] = useState(null);
  const [document, setDocument] = useState(null);
  const [error, setError] = useState("");
  const [generating, setGenerating] = useState(false);
  const pollRef = useRef(null);

  async function load() {
    const value = await request("/api/ast");
    setStatus(value);
    return value;
  }

  useEffect(() => {
    setStatus(null);
    setDocuments(null);
    setDocument(null);
    load().catch((reason) => setError(reason.message));
    return () => clearInterval(pollRef.current);
  }, [projectId]);

  useEffect(() => {
    clearInterval(pollRef.current);
    if (status?.status !== "generating") return undefined;
    pollRef.current = setInterval(() => load().catch(() => {}), 1800);
    return () => clearInterval(pollRef.current);
  }, [status?.status]);

  async function generate() {
    setGenerating(true);
    setError("");
    try {
      await request("/api/ast/generate", { method: "POST", body: "{}" });
      await load();
    } catch (reason) {
      setError(adminMessage(reason, onRequireUnlock));
    } finally {
      setGenerating(false);
    }
  }

  async function openDocuments() {
    try {
      setDocuments(await request(`/api/ast/documents?versionId=${encodeURIComponent(status?.currentVersionId || "")}`));
    } catch (reason) { setError(reason.message); }
  }

  async function openDocument(path) {
    try {
      setDocument(await request(`/api/ast/document?versionId=${encodeURIComponent(documents.versionId)}&path=${encodeURIComponent(path)}`));
    } catch (reason) { setError(reason.message); }
  }

  const current = status?.current || {};
  const meta = STATUS[status?.status] || STATUS.missing;
  return (
    <div className="page-wrap ast-page">
      <div className="page-heading compact-heading">
        <div>
          <span className="page-kicker">资料准备</span>
          <h1>AST 管理</h1>
          <p>AST 只在你点击生成时更新。主对话和三组实验共用当前可用版本。</p>
        </div>
        <Button type="primary" icon={status?.status === "available" || status?.status === "stale" ? <ArrowClockwise size={15} /> : <Code size={15} />}
          loading={generating || status?.status === "generating"} onClick={generate}>
          {status?.status === "available" || status?.status === "stale" ? "重新生成" : "生成 AST"}
        </Button>
      </div>
      {error && <Alert type="error" showIcon message={error} closable onClose={() => setError("")} />}
      <div className="ast-grid">
        <Card className="panel-card ast-status-card">
          <Flex align="center" gap={9} className="section-title"><span className={`status-icon status-${status?.status}`}>{meta.icon}</span><h2>当前状态</h2><Tag color={meta.color}>{meta.label}</Tag></Flex>
          {status?.status === "generating" ? (
            <div className="ast-progress-block"><Progress percent={status.progress || 0} status="active" /><p>{status.phase === "preparing" ? "准备源码" : `正在解析 ${String(status.phase || "源码").replace("parsing:", "")}`}，任务会在后台继续。</p></div>
          ) : status?.status === "missing" ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未生成 AST 资料" />
          ) : status?.status === "failed" ? (
            <Alert type="error" showIcon message="上次生成失败" description={status.error || "请重试"} />
          ) : (
            <div className="ast-facts">
              <div><span>当前版本</span><b>{status?.currentVersionId || "—"}</b></div>
              <div><span>生成时间</span><b>{current.generatedAt ? new Date(current.generatedAt).toLocaleString("zh-CN") : "—"}</b></div>
              <div><span>生成耗时</span><b>{current.generationSeconds == null ? "—" : `${current.generationSeconds} 秒`}</b></div>
              <div><span>结构资料</span><b>{current.documentCount == null ? "—" : `${current.documentCount} 份`}</b></div>
            </div>
          )}
          {status?.status === "stale" && <Alert style={{ marginTop: 16 }} type="warning" showIcon message="源码发生变化，当前 AST 仍可查看但不建议用于新实验。" />}
        </Card>
        <Card className="panel-card ast-scope-card">
          <Flex align="center" gap={9} className="section-title"><FileText size={17} /><h2>覆盖范围</h2></Flex>
          <p className="panel-help">结构资料由当前项目配置中的源码仓库生成；不包含人工业务主干、参考答案或评分要点。</p>
          <div className="repo-list">{(current.sourceRepositories || []).length ? current.sourceRepositories.map((repo) => <div className="repo-row" key={repo.id}><Code size={14} /><span>{repo.id}</span><small>{repo.path}</small></div>) : <span className="muted-text">生成后显示源码仓库</span>}</div>
          <Button disabled={!['available', 'stale'].includes(status?.status)} onClick={openDocuments}>查看结构资料</Button>
        </Card>
      </div>
      <Card className="panel-card ast-rules-card">
        <Flex align="center" gap={9} className="section-title"><Gear size={17} /><h2>生成约定</h2></Flex>
        <div className="ast-rule-grid"><p>项目启动只读取状态，不生成。</p><p>进入本页、切换 AST 问答或启动实验，都不会触发生成。</p><p>源码变化后显示“待更新”，由用户点击重新生成。</p><p>已开始的实验继续使用冻结的 AST 版本。</p></div>
      </Card>
      <Modal open={Boolean(documents)} title={`结构资料 · ${documents?.versionId || ""}`} footer={null} onCancel={() => setDocuments(null)} width={720}>
        <List bordered dataSource={documents?.items || []} locale={{ emptyText: "没有结构资料" }} renderItem={(item) => <List.Item actions={[<Button type="link" key="view" onClick={() => openDocument(item.path)}>查看</Button>]}><Typography.Text code>{item.path}</Typography.Text><Typography.Text type="secondary">{item.bytes} bytes</Typography.Text></List.Item>} />
      </Modal>
      <Modal open={Boolean(document)} title={document?.path} footer={null} onCancel={() => setDocument(null)} width={840}>
        <div className="ast-document"><Markdown remarkPlugins={[remarkGfm]}>{document?.content || ""}</Markdown></div>
      </Modal>
    </div>
  );
}

import React, { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Drawer, Empty, List, Skeleton, Tag, Typography } from "antd";
import { ArrowClockwise, CaretRight } from "@phosphor-icons/react";
import { request } from "../../lib/api.js";
import { AST_STATUS } from "../ast/AstPage.jsx";
import { PROJECTION_STATUS } from "../knowledge-admin/ProjectionNotice.jsx";
import "./materials.css";

const RAW = "/api/knowledge/raw-files";
const SOURCE_STATUS = {
  READABLE: ["目录可读", "success"], EMPTY: ["空目录", "default"],
  MISSING: ["目录缺失", "warning"], UNREADABLE: ["目录不可读", "error"],
};
const date = (value) => value && new Date(value).toLocaleString("zh-CN");

/* 资料总览：资料管理台入口，汇总各资料状态与待处理事项；编辑/生成在各自页面完成。 */
export function MaterialsPage({ projectId, onRequireUnlock }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [fileError, setFileError] = useState("");
  const input = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    setError(""); setLoading(true);
    request("/api/materials", {}, controller.signal)
      .then((body) => { if (!controller.signal.aborted) setData(body); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh, projectId]);

  useEffect(() => {
    if (!file) return undefined;
    const controller = new AbortController();
    setPreview(null); setFileError("");
    request(`${RAW}/${encodeURIComponent(file.name)}`, {}, controller.signal)
      .then((body) => { if (!controller.signal.aborted) setPreview(body); })
      .catch((reason) => { if (!controller.signal.aborted) setFileError(reason.message); });
    return () => controller.abort();
  }, [file]);

  async function upload(event) {
    const selected = event.target.files?.[0];
    event.target.value = "";
    if (!selected) return;
    setUploading(true); setError("");
    try {
      await request(`${RAW}/${encodeURIComponent(selected.name)}`, { method: "PUT", body: selected });
      setRefresh((value) => value + 1);
    } catch (reason) {
      if (reason.status === 401) onRequireUnlock?.();
      setError(reason.message);
    } finally {
      setUploading(false);
    }
  }

  const guides = data?.systemGuides;
  const business = data?.businessContext;
  const projection = data?.projection;
  const ast = data?.ast;
  const astStats = ast?.current?.statistics;
  const raw = data?.rawMaterials;
  const codeMap = data?.sources?.find((item) => item.kind === "code_map");
  const download = file && `${projectId ? `/api/projects/${encodeURIComponent(projectId)}${RAW.slice(4)}` : RAW}/${encodeURIComponent(file.name)}?download=1`;

  const pending = [];
  if (guides && !guides.documentCount) {
    pending.push({ key: "guides", href: "#/guides", action: "配置", text: "系统导航待配置" });
  }
  if (projection && ["stale", "not_imported", "failed"].includes(projection.status)) {
    pending.push({ key: "projection", href: "#/admin", action: "更新",
      text: projection.status === "failed" ? "业务投影生成失败" : "业务投影待更新" });
  }
  if (ast && ["stale", "failed"].includes(ast.status)) {
    pending.push({ key: "ast", href: "#/ast", action: "管理",
      text: ast.status === "failed" ? "AST 索引生成失败" : "AST 索引待更新" });
  }

  return <div className="page-wrap materials-page">
    <div className="materials-heading">
      <h1>资料总览</h1>
      <Button icon={<ArrowClockwise size={15} />} onClick={() => setRefresh((n) => n + 1)} loading={loading}>刷新</Button>
    </div>

    {error && <Alert type="error" showIcon title={error} />}

    {loading && !data ? <Skeleton active /> : data && <>
      {pending.length > 0 && <div className="materials-pending-section">
        <Typography.Text strong>待处理事项</Typography.Text>
        <div className="pending-list">
          {pending.map((item) => (
            <div key={item.key} className="pending-item">
              <span>{item.text}</span>
              <Button size="small" type="primary" href={item.href}>{item.action}</Button>
            </div>
          ))}
        </div>
      </div>}

      <div className="materials-cards">
        <MaterialCard
          title="系统导航"
          status={guides?.documentCount ? ["已发布", "success"] : ["待配置", "default"]}
          statusDetail={guides ? `${guides.documentCount} 份文档 · ${guides.systemCount} 个系统` : ""}
          time={guides?.updatedAt}
          actions={[{ label: guides?.documentCount ? "维护" : "配置", href: "#/guides" }]}
        />

        <MaterialCard
          title="业务知识"
          status={business?.documentCount ? ["可读取", "success"] : ["暂无文档", "default"]}
          statusDetail={business ? `${business.documentCount} 份 Markdown` : ""}
          time={business?.updatedAt}
          actions={[
            { label: "维护", href: "#/admin" },
            business?.documentCount ? { label: "查看关系", href: "#/graph" } : null,
          ].filter(Boolean)}
        />

        <MaterialCard
          title="AST 索引"
          status={AST_STATUS[ast?.status] || AST_STATUS.missing}
          statusDetail={astStats ? `${astStats.symbolCount} 个符号 · ${astStats.relationCount} 条关系` : ""}
          time={ast?.current?.generatedAt}
          actions={[{ label: ast?.status === "available" ? "查看" : "管理", href: "#/ast" }]}
        />

        <MaterialCard
          title="知识图谱"
          status={PROJECTION_STATUS[projection?.status] || PROJECTION_STATUS.empty}
          statusDetail={projection ? `${projection.importedDocumentCount} 份文档已导入` : ""}
          time={projection?.importedAt}
          actions={projection?.status === "synced" ? [
            { label: "业务关系", href: "#/graph" },
            { label: "接口链", href: "#/graph?view=calls" },
          ] : [{ label: "查看", href: "#/graph" }]}
        />

        {codeMap && <MaterialCard
          title="代码地图"
          status={SOURCE_STATUS[codeMap.status] || ["未知", "default"]}
          statusDetail="随源码自动生成的定位资料"
          actions={[]}
        />}

        <MaterialCard
          title="原始资料"
          status={raw?.items?.length ? [`${raw.items.length} 份文件`, "success"] : ["暂无文件", "default"]}
          actions={[{ label: "上传", onClick: () => input.current?.click(), loading: uploading }]}
        >
          {raw?.items?.length > 0 && <List size="small" dataSource={raw.items}
            renderItem={(item) => <List.Item actions={[
              <Button key="open" size="small" type="link" onClick={() => setFile(item)}>查看</Button>,
            ]}>
              <span className="material-origin">{item.name}</span>
            </List.Item>} />}
        </MaterialCard>
      </div>
    </>}

    <input ref={input} type="file" accept=".md,.txt,.json,.pdf,.docx,.xlsx"
      onChange={upload} disabled={uploading} style={{ display: "none" }} />

    <Drawer open={Boolean(file)} title={file?.name} width={700} onClose={() => setFile(null)}
      extra={download && <Button href={download}>下载原文</Button>}>
      {fileError ? <Alert type="error" showIcon title={fileError} /> : !preview ? <Skeleton active />
        : preview.content === null ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="可下载原文件查看" /> : <>
            <pre className="raw-material-preview">{preview.content}</pre>
            {preview.truncated && <Typography.Text type="secondary">预览已截断，可下载完整原文。</Typography.Text>}
          </>}
    </Drawer>
  </div>;
}

function MaterialCard({ title, status, statusDetail, time, actions, children }) {
  return <Card className="material-card" size="small">
    <div className="card-header">
      <Typography.Title level={4} style={{ margin: 0 }}>{title}</Typography.Title>
      <Tag color={status[1]}>{status[0]}</Tag>
    </div>
    {statusDetail && <div className="card-detail">{statusDetail}</div>}
    {time && <div className="card-time">最后更新：{date(time)}</div>}
    {children}
    {actions.length > 0 && <div className="card-actions">
      {actions.map((action, idx) => (
        action.href
          ? <Button key={idx} size="small" type="text" href={action.href} style={{ paddingLeft: 0 }}>
            {action.label} <CaretRight size={12} style={{ marginLeft: 2 }} />
          </Button>
          : <Button key={idx} size="small" type="text" onClick={action.onClick} loading={action.loading} style={{ paddingLeft: 0 }}>
            {action.label} <CaretRight size={12} style={{ marginLeft: 2 }} />
          </Button>
      ))}
    </div>}
  </Card>;
}
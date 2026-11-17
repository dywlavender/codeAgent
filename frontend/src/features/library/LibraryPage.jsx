import React, { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Drawer, Empty, Flex, List, Skeleton, Tag, Typography } from "antd";
import { request } from "../../lib/api.js";
import { AST_STATUS } from "../ast/AstPage.jsx";
import { PROJECTION_STATUS, projectionParser } from "../knowledge-admin/ProjectionNotice.jsx";
import "./materials.css";

const RAW = "/api/knowledge/raw-files";
const SOURCE_STATUS = {
  READABLE: ["目录可读", "success"], EMPTY: ["空目录", "default"],
  MISSING: ["目录缺失", "warning"], UNREADABLE: ["目录不可读", "error"],
};
const date = (value) => value && new Date(value).toLocaleString("zh-CN");

export function LibraryPage({ projectId, onRequireUnlock }) {
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

  const ast = data?.ast;
  const guides = data?.systemGuides;
  const business = data?.businessContext;
  const projection = data?.projection;
  const raw = data?.rawMaterials;
  const download = file && `${projectId ? `/api/projects/${encodeURIComponent(projectId)}${RAW.slice(4)}` : RAW}/${encodeURIComponent(file.name)}?download=1`;

  return <div className="page-wrap materials-page">
    <Flex justify="end"><Button onClick={() => setRefresh((n) => n + 1)} loading={loading}>刷新状态</Button></Flex>
    {error && <Alert type="error" showIcon title={error} />}
    {loading && !data ? <Skeleton active /> : data && <>
      <div className="materials-grid">
        <MaterialCard title="系统导航" status={guides.documentCount ? ["已发布", "success"] : ["待配置", "default"]}
          value={`${guides.documentCount} 份文档 · ${guides.systemCount} 个系统`}
          time={guides.updatedAt} href="#/guides" action="维护" />
        <MaterialCard title="业务知识原文" status={business.documentCount ? ["可用", "success"] : ["暂无文档", "default"]}
          value={`${business.documentCount} 份 Markdown`} time={business.updatedAt} href="#/admin" action="维护" />
        <MaterialCard title="AST 关系索引" status={AST_STATUS[ast.status] || AST_STATUS.missing}
          value={ast.current?.statistics ? `${ast.current.statistics.symbolCount} 个符号 · ${ast.current.statistics.relationCount} 条关系`
            : ast.currentVersionId || "未生成结构索引"}
          time={ast.current?.generatedAt || ast.updatedAt} href="#/ast" action={ast.status === "available" ? "查看" : "管理"} />
        <MaterialCard title="业务关系图" status={PROJECTION_STATUS[projection.status] || PROJECTION_STATUS.empty}
          value={`${projection.importedDocumentCount} 份已导入${projection.parser ? ` · ${projectionParser(projection.parser)}` : ""}`}
          time={projection.importedAt} href="#/graph?view=business" action="查看" />
      </div>
      <Card title="原始资料" size="small" extra={<>
        <input type="file" ref={input} onChange={upload} hidden />
        <Button loading={uploading} onClick={() => input.current.click()}>上传资料</Button>
      </>}>
        <Typography.Paragraph type="secondary" className="material-origin">{raw.root}</Typography.Paragraph>
        <List size="small" dataSource={raw.items} locale={{ emptyText: "暂无原始资料" }}
          renderItem={(item) => <List.Item actions={[<Button key="open" size="small" type="link" onClick={() => setFile(item)}>查看</Button>]}>
            <span className="material-origin">{item.name}</span>
            <Typography.Text type="secondary">{date(item.modifiedAt)}</Typography.Text>
          </List.Item>} />
      </Card>
      <Card title="代码仓库与兼容资料" size="small">
        {data.sources.length ? <List size="small" dataSource={data.sources}
          renderItem={(item) => <List.Item>
            <div className="material-origin">
              <b>{item.kind === "code_map" ? "Code Map" : item.name}</b>
              {item.kind === "code_map" && <Typography.Text type="secondary"> · 自动导航资料</Typography.Text>}
              <div>{item.path}</div>
            </div>
            <Tag color={SOURCE_STATUS[item.status]?.[1]}>{SOURCE_STATUS[item.status]?.[0]}</Tag>
          </List.Item>} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未配置资料来源" />}
      </Card>
    </>}
    <Drawer open={Boolean(file)} title={file?.name} size={700} onClose={() => setFile(null)}
      extra={download && <Button href={download}>下载原文</Button>}>
      {fileError ? <Alert type="error" showIcon title={fileError} /> : !preview ? <Skeleton active />
        : preview.content === null ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="可下载原文件查看" /> : <>
            <pre className="raw-material-preview">{preview.content}</pre>
            {preview.truncated && <Typography.Text type="secondary">预览已截断，可下载查看完整原文。</Typography.Text>}
          </>}
    </Drawer>
  </div>;
}

function MaterialCard({ title, status, value, time, href, action }) {
  return <Card title={title} size="small" extra={<Tag color={status[1]}>{status[0]}</Tag>}>
    <Flex vertical gap={8}>
      <Typography.Text>{value}</Typography.Text>
      {time && <Typography.Text type="secondary">{date(time)}</Typography.Text>}
      <Button size="small" href={href} style={{ alignSelf: "start" }}>{action}</Button>
    </Flex>
  </Card>;
}

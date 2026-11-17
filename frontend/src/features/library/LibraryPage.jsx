import React, { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Drawer, Empty, Flex, List, Skeleton, Tag, Typography } from "antd";
import { ArrowClockwise } from "@phosphor-icons/react";
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

/* 知识总览：集中展示各资料的完整性、更新状态和入口；
 * 待处理横幅在客户端由 /api/materials 汇总结果推导，不新增后端接口。
 */
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

  const pending = [];
  if (projection && ["stale", "not_imported", "failed"].includes(projection.status)) {
    pending.push({ key: "projection", href: "#/admin", action: "查看业务知识 →",
      text: "业务关系图已落后于最新业务文档。问答仍可读取最新原文，图谱需要按需重新导入。" });
  }
  if (ast && ["stale", "failed"].includes(ast.status)) {
    pending.push({ key: "ast", href: "#/ast", action: "管理 AST 索引 →",
      text: "AST 结构索引待更新。问答仍可读取源码，重新生成索引后才能查询最新符号关系。" });
  }

  return <div className="page-wrap materials-page">
    <div className="materials-heading">
      <div><h1>知识总览</h1><p>管理工程的导航规则、业务知识和代码结构；集中查看各类资料的当前状态。</p></div>
      <Button icon={<ArrowClockwise size={15} />} onClick={() => setRefresh((n) => n + 1)} loading={loading}>刷新资料状态</Button>
    </div>
    {error && <Alert type="error" showIcon title={error} />}
    {loading && !data ? <Skeleton active /> : data && <>
      {pending.map((item) => <Alert key={item.key} className="materials-pending" type="warning" showIcon
        title={<span><b>需要处理：</b>{item.text}</span>}
        action={<Button size="small" href={item.href}>{item.action}</Button>} />)}
      <div className="materials-grid">
        <MaterialCard title="系统导航" status={guides.documentCount ? ["已发布", "success"] : ["待配置", "default"]}
          value={`${guides.documentCount} 份文档 · ${guides.systemCount} 个系统`}
          time={guides.updatedAt} href="#/guides" action="维护" />
        <MaterialCard title="业务知识" status={business.documentCount ? ["可读取", "success"] : ["暂无文档", "default"]}
          value={`${business.documentCount} 份 Markdown 原文`} time={business.updatedAt} href="#/admin" action="维护" />
        <MaterialCard title="知识图谱" status={PROJECTION_STATUS[projection.status] || PROJECTION_STATUS.empty}
          value={`${projection.importedDocumentCount} 份已导入${projection.parser ? ` · ${projectionParser(projection.parser)}` : ""}`}
          time={projection.importedAt} href="#/graph" action="查看" />
        <MaterialCard title="AST 结构" status={AST_STATUS[ast.status] || AST_STATUS.missing}
          value={ast.current?.statistics ? `${ast.current.statistics.symbolCount} 个符号 · ${ast.current.statistics.relationCount} 条关系`
            : ast.currentVersionId || "未生成结构索引"}
          time={ast.current?.generatedAt || ast.updatedAt} href="#/ast" action={ast.status === "available" ? "查看" : "管理"} />
      </div>
      <div className="materials-two">
        <Card title="资料与能力" size="small" extra={<span className="materials-hint">点击进入对应模块</span>}>
          <CapabilityRow glyph="导" title="系统导航" note="告诉代码问答如何找到各系统的代码入口与应用结构"
            href="#/guides" action="查看 →" />
          <CapabilityRow glyph="文" title="业务知识" note="人工维护的术语、规则与业务约定，是知识图谱的来源"
            href="#/admin" action="维护 →" />
          <CapabilityRow glyph="图" title="知识图谱" note="从已确认业务知识投影出的实体、流程和关系"
            href="#/graph" action="探索 →" />
          <CapabilityRow glyph="码" title="AST 结构" note="按符号查源码位置和已解析代码关系，不推断业务逻辑"
            href="#/ast" action="查询 →" />
        </Card>
        <div className="materials-side">
          <Card title="这些资料如何协作？" size="small">
            <p className="materials-coop">原文和结构数据各司其职，不重复维护。</p>
            <div className="kb-flow"><span className="kb-flow-num">1</span><div><b>系统导航 + 业务知识</b><small>分别提供代码定位规则与业务含义</small></div></div>
            <div className="kb-flow"><span className="kb-flow-num">2</span><div><b>知识图谱 + AST 结构</b><small>分别展示业务关系投影与源码结构关系</small></div></div>
            <div className="kb-flow"><span className="kb-flow-num">3</span><div><b>代码问答按需检索</b><small>所有结构信息只是调查辅助，实现以源码为准</small></div></div>
          </Card>
          <Card title="原始资料" size="small" extra={<>
            <input type="file" ref={input} onChange={upload} hidden />
            <Button size="small" loading={uploading} onClick={() => input.current.click()}>上传资料</Button>
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
        </div>
      </div>
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

function CapabilityRow({ glyph, title, note, href, action }) {
  return <a className="r-row cap-row" href={href}>
    <span className="r-glyph">{glyph}</span>
    <span className="r-copy"><b>{title}</b><small>{note}</small></span>
    <span className="cap-go">{action}</span>
  </a>;
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

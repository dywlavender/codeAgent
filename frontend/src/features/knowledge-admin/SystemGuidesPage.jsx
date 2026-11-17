import React, { useEffect, useState } from "react";
import { Alert, Button, Card, Empty, Flex, Input, Select, Skeleton, Tabs, Tag, Tree, Typography } from "antd";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { request } from "../../lib/api.js";
import { TraceDrawer } from "../query/TraceDrawer.jsx";
import "./system-guides.css";

const BASE = "/api/knowledge/system-guides";

function targetParams(system, app) {
  return { systemId: system.id, ...(app ? { applicationId: app.id } : {}) };
}

const SCOPE_NOTES = {
  system: ["系统视角", "系统通用导航 + 本系统下应用导航。未发布草稿不参与问答。"],
  application: ["应用视角", "所属系统的通用规则 + 当前应用的补充导航。"],
  repository: ["仓库视角", "显示该仓库关联的应用与对应系统规则；源码访问仍按仓库授权。"],
};

export function SystemGuidesPage({ projectId, adminUnlocked, onRequireUnlock }) {
  const [systems, setSystems] = useState(null);
  const [target, setTarget] = useState(null);
  const [document, setDocument] = useState(null);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState("preview");
  const [evidence, setEvidence] = useState([]);
  const [draftRun, setDraftRun] = useState(null);
  const [showTrace, setShowTrace] = useState(false);
  const [preview, setPreview] = useState(null);
  const [pending, setPending] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const dirty = document && draft !== (document.content ?? "");
  const discard = () => !dirty || window.confirm("放弃当前未保存的修改？");
  const repository = preview?.scope.repositoryIds?.[0] ?? "";

  function showError(reason) {
    if (reason.status === 401) onRequireUnlock?.();
    if (reason.runId) setDraftRun(reason.runId);
    setError(reason.message);
  }

  async function loadTarget(params, signal) {
    const query = new URLSearchParams(params);
    const [file, effective] = await Promise.all([
      request(`${BASE}/file?${query}`, {}, signal),
      request(`${BASE}/preview?${query}`, {}, signal),
    ]);
    if (signal?.aborted) return;
    setTarget(params);
    setDocument(file);
    setDraft(file.content ?? "");
    setPreview(effective);
    setEvidence([]);
    setDraftRun(null);
    setMessage("");
  }

  useEffect(() => {
    const controller = new AbortController();
    setPending("load"); setError("");
    request(BASE, {}, controller.signal).then(async ({ systems: data }) => {
      if (controller.signal.aborted) return;
      setSystems(data);
      if (data.length) await loadTarget(targetParams(data[0]), controller.signal);
    }).catch((reason) => {
      if (!controller.signal.aborted) showError(reason);
    }).finally(() => {
      if (!controller.signal.aborted) setPending("");
    });
    return () => controller.abort();
  }, [adminUnlocked]);

  async function work(action, job) {
    setPending(action); setError(""); setMessage("");
    try {
      await job();
    } catch (reason) {
      showError(reason);
    } finally {
      setPending("");
    }
  }

  async function refreshPreview(params, repo = repository) {
    const query = new URLSearchParams({ ...params, ...(repo ? { repositoryId: repo } : {}) });
    setPreview(await request(`${BASE}/preview?${query}`));
  }

  function save() {
    work("save", async () => {
      const saved = await request(`${BASE}/file`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...target, previousContent: document.content, content: draft }),
      });
      setDocument({ ...document, ...saved });
      setDraft(saved.content);
      setEvidence([]);
      setMessage("已发布系统导航");
      const [{ systems: data }] = await Promise.all([request(BASE), refreshPreview(target)]);
      setSystems(data);
    });
  }

  function generate() {
    work("draft", async () => {
      setDraftRun(null);
      const suggestion = await request(`${BASE}/draft`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(target),
      });
      setDraft(suggestion.content);
      setEvidence(suggestion.sourceFiles);
      setDraftRun(suggestion.runId);
      setMode("edit");
      setMessage("草稿已生成，核对后可发布；草稿不直接生效");
    });
  }

  const tree = (systems || []).map((system) => ({
    key: system.path, params: targetParams(system),
    title: `${system.name}${system.published ? " · 已发布" : ""}`,
    children: system.applications.map((app) => ({
      key: app.path, params: targetParams(system, app),
      title: `${app.name}${app.published ? " · 已发布" : ""}`,
    })),
  }));
  const publishedCount = (systems || []).flatMap((system) => [system, ...system.applications])
    .filter((item) => item.published).length;
  const repositoryIds = [...new Set((systems || []).flatMap((system) =>
    system.applications.map((app) => app.repositoryId)))];
  const scope = preview?.scope || {};
  const scopeKind = scope.repositoryIds?.length ? "repository"
    : scope.applicationIds?.length ? "application" : "system";
  const [scopeLabel, scopeNote] = SCOPE_NOTES[scopeKind];
  return <div className="page-wrap system-guides">
    <div className="guides-heading">
      <div><h1>系统导航</h1><p>按系统和应用维护代码查找规则；这些文档用于导航，不代替源码事实。</p></div>
      {systems?.length ? <Flex gap={8} wrap>
        <Tag bordered={false}>已发布 {publishedCount} 份</Tag>
        <Button disabled={!target || Boolean(pending)} loading={pending === "draft"} onClick={() => {
          if (discard()) generate();
        }}>AI 生成草稿</Button>
        <Button type="primary" disabled={!dirty || Boolean(pending)} loading={pending === "save"} onClick={save}>发布导航</Button>
      </Flex> : null}
    </div>
    {error && <Alert type="error" showIcon title={error} action={document
      ? <Button size="small" disabled={Boolean(pending)}
          onClick={() => { if (discard()) work("load", () => loadTarget(target)); }}>重新打开</Button>
      : onRequireUnlock && <Button size="small" onClick={onRequireUnlock}>管理员入口</Button>} />}
    {message && <Alert type="success" showIcon title={message} />}
    {!systems ? (pending ? <Skeleton active /> : null) : !systems.length
      ? <Empty description="在工程配置中登记系统和应用后即可维护导航" />
      : <div className="system-guides-grid">
          <Card title="系统与应用" size="small">
            <Tree treeData={tree} defaultExpandAll selectedKeys={document ? [document.name] : []}
              disabled={Boolean(pending)} onSelect={(_, info) => {
                if (info.selected && discard()) work("load", () => loadTarget(info.node.params));
              }} />
            <p className="guide-tree-note">选择左侧系统或应用查看它自己的导航内容；不会自动复制其他应用的入口列表。</p>
          </Card>
          <Card title={document?.name || "导航文档"} size="small" className="system-guide-editor">
            {document ? <Flex vertical gap={10}>
              <Tabs size="small" activeKey={mode} onChange={setMode} items={[
                { key: "preview", label: "内容预览", children: draft
                  ? <div className="system-guide-preview"><Markdown remarkPlugins={[remarkGfm]}>{draft}</Markdown></div>
                  : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description="尚未编写内容；切换到「编辑 Markdown」使用模板或 AI 草稿" /> },
                { key: "edit", label: "编辑 Markdown", children: <Flex vertical gap={10}>
                  <Input.TextArea aria-label="系统导航 Markdown" value={draft} rows={19}
                    style={{ fontFamily: "var(--font-code)" }} readOnly={Boolean(pending)}
                    onChange={(event) => { setDraft(event.target.value); setMessage(""); }} />
                  <Flex justify="end" gap={6} wrap>
                    <Button disabled={Boolean(pending)} onClick={() => {
                      if ((!draft || window.confirm("用模板替换编辑区内容？"))) {
                        setDraft(document.template); setEvidence([]); setMessage("");
                      }
                    }}>使用模板</Button>
                  </Flex>
                  {evidence.length > 0 && <details>
                    <summary>草稿调查读取的源码（{evidence.length}）</summary>
                    <ul>{evidence.map((file) => <li key={file}><code>{file}</code></li>)}</ul>
                  </details>}
                  {draftRun && <Button type="link" onClick={() => setShowTrace(true)}>查看草稿调查</Button>}
                </Flex> },
              ]} />
              <p className="system-guide-foot">已发布版本与编辑草稿分离：草稿发布后才参与问答导航。</p>
            </Flex> : <Skeleton active />}
          </Card>
          <Card title="实际生效的导航" size="small" extra={<Tag bordered={false}>只读</Tag>}>
            <Flex vertical gap={8}>
              <Select aria-label="导航预览视角" value={repository} disabled={Boolean(pending) || !target}
                options={[{ value: "", label: target?.applicationId ? "应用视角" : "系统视角" },
                  ...repositoryIds.map((id) => ({ value: id, label: `仓库视角 · ${id}` }))]}
                onChange={(value) => work("preview", () => refreshPreview(target, value))} />
              <div className="guide-scope-note"><b>{scopeLabel}</b><p>{scopeNote}</p></div>
              <dl className="guide-kv">
                <dt>使用方式</dt><dd>作为本轮问答的导航摘要；完整文档可按需读取</dd>
                <dt>事实依据</dt><dd>定位线索，不能代替当前源码内容</dd>
                <dt>管理方式</dt><dd>管理员修改并确认后发布</dd>
              </dl>
              {preview?.guidance ? <>
                <pre className="system-guide-effective">{preview.guidance}</pre>
                {preview.documents.length > 0 && <Typography.Text type="secondary">
                  可按需读取 {preview.documents.length} 份导航文档
                </Typography.Text>}
              </> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此范围未登记可装配的应用导航" />}
            </Flex>
          </Card>
        </div>}
      <TraceDrawer projectId={projectId} runId={showTrace ? draftRun : null} onClose={() => setShowTrace(false)} />
  </div>;
}

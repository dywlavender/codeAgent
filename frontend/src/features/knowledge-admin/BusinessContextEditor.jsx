import React, { useEffect, useState } from "react";
import { Alert, Button, Card, Empty, Flex, Input, Skeleton, Tabs, Typography } from "antd";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { request, RequestAborted } from "../../lib/api.js";
import { BusinessContextSuggestion } from "./BusinessContextSuggestion.jsx";

const FILES = "/api/knowledge/context/files";
const filePath = (name) => `${FILES}/${encodeURIComponent(name)}`;

/* 业务知识以 Markdown 原文为中心：左侧文档列表，右侧阅读/编辑；投影状态由页面右栏展示。
 * 本组件输出两个 Card（列表 + 编辑器），由父级网格拼成三栏布局。
 */
export function BusinessContextEditor({ adminUnlocked, onRequireUnlock, onSaved }) {
  const [files, setFiles] = useState(null);
  const [document, setDocument] = useState(null);
  const [name, setName] = useState("");
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState("preview");
  const [pending, setPending] = useState("load");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [checking, setChecking] = useState(false);

  const creating = document?.content === null;
  const dirty = document && (creating ? Boolean(name || draft) : draft !== document.content);

  function showDocument(next) {
    setDocument(next);
    setName(next?.name ?? "");
    setDraft(next?.content ?? "");
    setSaved(false);
    setChecking(false);
    setMode("preview");
  }

  function showError(reason) {
    if (reason instanceof RequestAborted) return;
    setError(reason.message);
    checkAdminAccess(reason);
  }

  function checkAdminAccess(reason) {
    if (reason.status === 401) onRequireUnlock?.();
  }

  async function loadFiles(signal) {
    setPending("load");
    setError("");
    try {
      const folder = await request(FILES, {}, signal);
      const next = folder.items.length ? await request(filePath(folder.items[0]), {}, signal) : null;
      setFiles(folder);
      showDocument(next);
    } catch (reason) {
      showError(reason);
    } finally {
      if (!signal?.aborted) setPending("");
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    loadFiles(controller.signal);
    return () => controller.abort();
  }, [adminUnlocked]);

  function canLeaveDraft() {
    return !dirty || window.confirm("放弃当前未保存的修改？");
  }

  async function openFile(path) {
    if (!canLeaveDraft()) return;
    setPending("open");
    setError("");
    try {
      showDocument(await request(filePath(path)));
    } catch (reason) {
      showError(reason);
    } finally {
      setPending("");
    }
  }

  function createFile() {
    if (!canLeaveDraft()) return;
    setError("");
    showDocument({ name: "", content: null });
    setMode("edit");
  }

  async function save(content) {
    setPending("save");
    setError("");
    setSaved(false);
    try {
      const result = await request(filePath(name.trim()), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ previousContent: document.content, content }),
      });
      setFiles((folder) => ({ ...folder, items: [...new Set([...folder.items, result.name])].sort() }));
      showDocument(result);
      setSaved(true);
      onSaved?.();
    } catch (reason) {
      if (checking) checkAdminAccess(reason);
      else showError(reason);
      return reason.message;
    } finally {
      setPending("");
    }
  }

  const editorBody = <>
    {creating && <Input aria-label="文件名" placeholder="例如 terminology.md"
      value={name} disabled={Boolean(pending)} onChange={(event) => setName(event.target.value)} />}
    <Flex justify="space-between" align="center" wrap gap={4}>
      <label htmlFor="business-context-markdown"><Typography.Text strong>Markdown 原文</Typography.Text></label>
      {document?.modifiedAt && !creating && <Typography.Text type="secondary">
        保存于 {new Date(document.modifiedAt).toLocaleString("zh-CN")}
      </Typography.Text>}
    </Flex>
    <Input.TextArea id="business-context-markdown" value={draft}
      readOnly={!files?.editable || Boolean(pending)} rows={16}
      style={{ fontFamily: "var(--font-code)" }}
      placeholder="写明适用系统、业务约定和资料来源"
      onChange={(event) => { setDraft(event.target.value); setSaved(false); }} />
    {files?.editable && <Flex justify="end" gap={8} wrap>
      {!creating && <Button onClick={() => setChecking(true)}
        disabled={dirty || !draft.trim() || Boolean(pending)}>检查并提出修改建议</Button>}
      <Button type="primary" onClick={() => save(draft)} loading={pending === "save"}
        disabled={!dirty || !name.trim() || Boolean(pending)}>保存</Button>
    </Flex>}
  </>;

  return <>
    <Card title="业务文档" size="small" className="knowledge-doc-list"
      extra={files?.editable && <Button size="small" disabled={Boolean(pending)} onClick={createFile}>新建</Button>}>
      {pending === "load" ? <Skeleton active paragraph={{ rows: 6 }} /> : files?.items.length
        ? <div className="knowledge-file-rows">
            {files.items.map((path) => <button key={path}
              className={`r-row ${!creating && document?.name === path ? "on" : ""}`}
              disabled={Boolean(pending)} onClick={() => openFile(path)}>
              <span className="r-glyph">▤</span>
              <span className="r-copy"><b>{path}</b></span>
            </button>)}
          </div>
        : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无业务文档" />}
      <p className="knowledge-list-note">Markdown 是业务原文；结构化图谱只是它的投影，不是另一套维护源。</p>
    </Card>
    <Card title={creating ? "新建业务文档" : document?.name || "业务原文"} size="small" className="knowledge-editor">
      {error && <Alert type="error" showIcon title="操作未完成" description={error}
        action={<Button size="small" disabled={Boolean(pending)} onClick={() => {
          if (document && !creating) openFile(document.name);
          else if (canLeaveDraft()) loadFiles();
        }}>重新加载</Button>} />}
      {saved && <Alert type="success" showIcon title="已保存业务文档；业务图谱需显式更新投影" />}
      {files && !files.editable && <Alert type="info" showIcon title="历史业务基线只读"
        description="迁移到业务补充知识目录后可在这里编辑。" />}
      {pending === "load" ? <Skeleton active paragraph={{ rows: 8 }} />
        : !document ? files && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无业务文档" />
          : creating || !files.editable ? editorBody
            : <Tabs size="small" activeKey={mode} onChange={setMode} items={[
                { key: "preview", label: "阅读预览", children: draft
                  ? <div className="knowledge-markdown"><Markdown remarkPlugins={[remarkGfm]}>{draft}</Markdown></div>
                  : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无内容，切换到编辑开始编写" /> },
                { key: "edit", label: "编辑 Markdown", children: editorBody },
              ]} />}
      {!creating && document && <p className="knowledge-editor-foot">
        保存原文不会自动更新业务图谱；需要展示时再执行「更新投影」。</p>}
    </Card>
    {checking && <BusinessContextSuggestion document={document} onClose={() => setChecking(false)}
      onEdit={(content) => { setDraft(content); setSaved(false); setChecking(false); setMode("edit"); }}
      onPublish={save} onError={checkAdminAccess} publishing={pending === "save"} />}
  </>;
}

import React, { useEffect, useState } from "react";
import { Alert, Button, Card, Empty, Flex, Input, Select, Skeleton, Typography } from "antd";
import { request, RequestAborted } from "../../lib/api.js";
import { BusinessContextSuggestion } from "./BusinessContextSuggestion.jsx";

const FILES = "/api/knowledge/context/files";
const filePath = (name) => `${FILES}/${encodeURIComponent(name)}`;
const isAdminError = (error) => /credential|凭证|401/i.test(error.message);

export function BusinessContextEditor({ adminUnlocked, onRequireUnlock }) {
  const [files, setFiles] = useState(null);
  const [document, setDocument] = useState(null);
  const [name, setName] = useState("");
  const [draft, setDraft] = useState("");
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
  }

  function showError(reason) {
    if (reason instanceof RequestAborted) return;
    setError(reason.message);
    checkAdminAccess(reason);
  }

  function checkAdminAccess(reason) {
    if (isAdminError(reason)) onRequireUnlock?.();
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
    } catch (reason) {
      if (checking) checkAdminAccess(reason);
      else showError(reason);
      return reason.message;
    } finally {
      setPending("");
    }
  }

  return (
    <Flex vertical gap={12}>
      {error && <Alert type="error" showIcon title="操作未完成" description={error}
        action={<Button size="small" disabled={Boolean(pending)} onClick={() => {
          if (document && !creating) openFile(document.name);
          else if (canLeaveDraft()) loadFiles();
        }}>重新加载</Button>} />}
      {saved && <Alert type="success" showIcon title="已保存业务文档" />}
      {files && !files.editable && <Alert type="info" showIcon title="历史业务基线只读"
        description="迁移到业务补充知识目录后可在这里编辑。" />}
      {files && (
        <Flex gap={8} align="center" wrap>
          <Select aria-label="业务文档" placeholder="选择业务文档" showSearch
            value={document?.name || undefined} style={{ flex: "1 1 240px" }}
            options={files.items.map((path) => ({ value: path, label: path }))}
            disabled={Boolean(pending)} onChange={openFile} />
          {files.editable && <Button disabled={Boolean(pending)} onClick={createFile}>新建文档</Button>}
        </Flex>
      )}
      {pending === "load" ? <Skeleton active paragraph={{ rows: 8 }} /> : document ? (
        <Card size="small">
          <Flex vertical gap={10}>
            {creating && <Input aria-label="文件名" placeholder="例如 terminology.md"
              value={name} disabled={Boolean(pending)} onChange={(event) => setName(event.target.value)} />}
            <label htmlFor="business-context-markdown">
              <Typography.Text strong>Markdown 原文</Typography.Text>
            </label>
            <Input.TextArea id="business-context-markdown" value={draft}
              readOnly={!files.editable || Boolean(pending)} rows={16}
              style={{ fontFamily: "var(--font-code)" }}
              onChange={(event) => { setDraft(event.target.value); setSaved(false); }} />
            {files.editable && <Flex justify="end" gap={8} wrap>
              {!creating && <Button onClick={() => setChecking(true)}
                disabled={dirty || !draft.trim() || Boolean(pending)}>检查并提出修改建议</Button>}
              <Button type="primary" onClick={() => save(draft)} loading={pending === "save"}
                disabled={!dirty || !name.trim() || Boolean(pending)}>保存</Button>
            </Flex>}
          </Flex>
        </Card>
      ) : files && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无业务文档" />}
      {checking && <BusinessContextSuggestion document={document} onClose={() => setChecking(false)}
        onEdit={(content) => { setDraft(content); setSaved(false); setChecking(false); }}
        onPublish={save} onError={checkAdminAccess} publishing={pending === "save"} />}
    </Flex>
  );
}

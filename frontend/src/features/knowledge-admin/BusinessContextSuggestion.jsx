import React, { useEffect, useRef, useState } from "react";
import { Alert, Button, Flex, Input, Modal, Select, Typography } from "antd";
import { request, RequestAborted } from "../../lib/api.js";

const ACTIONS = {
  add: "建议新增",
  revise: "建议修订",
  unchanged: "维持原文",
  needs_confirmation: "待业务确认",
};

export function BusinessContextSuggestion({ document, onClose, onEdit, onPublish, onError, publishing }) {
  const [sectionStart, setSectionStart] = useState(0);
  const [note, setNote] = useState("");
  const [generating, setGenerating] = useState(false);
  const [suggestion, setSuggestion] = useState(null);
  const [error, setError] = useState("");
  const active = useRef(null);
  useEffect(() => () => active.current?.abort(), []);

  async function generate() {
    const controller = new AbortController();
    active.current = controller;
    setGenerating(true);
    setError("");
    setSuggestion(null);
    try {
      const result = await request("/api/knowledge/context/suggest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: document.name, previousContent: document.content, note,
          sectionStart: sectionStart || null,
        }),
      }, controller.signal);
      setSuggestion(result);
    } catch (reason) {
      if (!(reason instanceof RequestAborted)) {
        setError(reason.message);
        onError(reason);
      }
    } finally {
      if (!controller.signal.aborted) setGenerating(false);
    }
  }

  async function publish() {
    setError("");
    const failure = await onPublish(suggestion.proposalContent);
    if (failure) setError(failure);
  }

  const sections = [
    { value: 0, label: "整份文档" },
    ...(document.sections ?? []).map(({ title, startLine }) => ({
      value: startLine,
      label: `${title} · 第 ${startLine} 行`,
    })),
  ];
  const proposal = suggestion?.proposalContent;
  return (
    <Modal open title="检查并提出修改建议" width={760} onCancel={onClose}
      closable={!publishing} keyboard={!publishing} maskClosable={!publishing}
      footer={[
        <Button key="close" onClick={onClose} disabled={publishing}>关闭</Button>,
        proposal && <Button key="edit" onClick={() => onEdit(proposal)} disabled={publishing}>在文档中编辑</Button>,
        proposal && <Button key="publish" type="primary" onClick={publish}
          loading={publishing} disabled={publishing}>确认并保存</Button>,
      ]}>
      <Flex vertical gap={12}>
        {error && <Alert type="error" showIcon title={error} />}
        <Flex gap={8} align="center" wrap>
          <Typography.Text code>{document.name}</Typography.Text>
          <Select aria-label="检查范围" options={sections} value={sectionStart}
            style={{ flex: "1 1 180px" }} disabled={generating || publishing}
            onChange={(value) => { setSectionStart(value); setSuggestion(null); }} />
        </Flex>
        <label htmlFor="business-context-change">变更说明或相关资料</label>
        <Input.TextArea id="business-context-change" rows={4} value={note}
          placeholder="可粘贴已确认的业务变更、需求摘录或代码调查结果。"
          disabled={generating || publishing}
          onChange={(event) => { setNote(event.target.value); setSuggestion(null); }} />
        <Flex justify="end">
          <Button onClick={generate} loading={generating} disabled={!note.trim() || generating || publishing}>
            生成建议
          </Button>
        </Flex>
        {suggestion && (
          <Flex vertical gap={10}>
            <Alert type={suggestion.action === "needs_confirmation" ? "warning" : "info"}
              showIcon title={ACTIONS[suggestion.action]} description={suggestion.reason} />
            <Flex gap={12} wrap>
              <SuggestionText label="当前原文" text={suggestion.original} />
              {proposal && <SuggestionText label="建议正文" text={suggestion.replacement} />}
            </Flex>
            <SuggestionList label="依据" items={suggestion.basis} />
            <SuggestionList label="待业务确认" items={suggestion.pendingQuestions} />
          </Flex>
        )}
      </Flex>
    </Modal>
  );
}

function SuggestionText({ label, text }) {
  return <label style={{ flex: "1 1 260px" }}>
    <Typography.Text strong>{label}</Typography.Text>
    <Input.TextArea readOnly rows={7} value={text} style={{ fontFamily: "var(--font-code)" }} />
  </label>;
}

function SuggestionList({ label, items }) {
  if (!items.length) return null;
  return <div><Typography.Text strong>{label}</Typography.Text>
    <ul style={{ margin: "6px 0 0", paddingInlineStart: 20 }}>
      {items.map((item, index) => <li key={index}>{item}</li>)}
    </ul>
  </div>;
}

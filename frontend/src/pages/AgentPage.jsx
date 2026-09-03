import React, { useEffect, useRef } from "react";
import { ArrowUp, Check, Copy, Stop, ThumbsDown, ThumbsUp } from "@phosphor-icons/react";
import { Alert, Avatar, Button, Empty, Flex, Input, Spin, Tag, Tooltip, Typography } from "antd";
import { request } from "../lib/api.js";

const EXAMPLES = [
  "提款申请之前需要完成哪些业务步骤？",
  "中台发起提款前具体校验哪些条件？",
  "H5 发起提款申请后经过哪些应用？",
];

export function AgentPage(props) {
  const { result, turns = [], activeTurnId } = props;
  const activeTurn = turns.find((turn) => turn.id === activeTurnId);
  const loading = activeTurn?.status === "loading";

  return (
    <div className="agent-shell">
      <header className="agent-topbar">
        <div>
          <Typography.Text strong>{props.workspace?.project || "CodeAgent"}</Typography.Text>
          <span className="topbar-sep">·</span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Claude Code 对话
          </Typography.Text>
        </div>
        <div className="runtime-indicator">
          <span className={`runtime-dot ${loading ? "busy" : ""}`} />
          {loading ? "正在调查" : result?.runtime || "CLAUDE_CODE"}
        </div>
      </header>
      <div className="chat-column">
        <div className="chat-scroll">
          {turns.length === 0 ? <Welcome submit={props.submit} workspace={props.workspace} /> : (
            <div className="turn-list">
              {turns.map((turn) => (
                <TurnBlock key={turn.id} turn={turn} submit={props.submit} />
              ))}
            </div>
          )}
        </div>
        <div className="composer-stick">
          {props.error && <Alert type="error" showIcon message={props.error} closable style={{ marginBottom: 10 }} />}
          <Composer {...props} loading={loading} />
        </div>
      </div>
    </div>
  );
}

function Welcome({ submit, workspace }) {
  return (
    <div className="welcome">
      <Avatar shape="square" size={54} className="agent-avatar">{"{}"}</Avatar>
      <Typography.Title level={3} style={{ marginTop: 22, marginBottom: 8, letterSpacing: "-.02em" }}>
        今天想弄清楚什么？
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ maxWidth: 510, marginInline: "auto", lineHeight: 1.85, fontSize: 13.5 }}>
        Claude Code 会在当前工作区自行搜索业务基线、需求原文和最新源码，再用正常对话回答。{workspace?.counts?.repositories ? `当前已连接 ${workspace.counts.repositories} 个仓库。` : ""}
      </Typography.Paragraph>
      <Flex gap={10} justify="center" wrap="wrap" style={{ marginTop: 24 }}>
        {EXAMPLES.map((item) => <Button key={item} shape="round" onClick={() => submit(item)}>{item}</Button>)}
      </Flex>
    </div>
  );
}

function TurnBlock({ turn, submit }) {
  const result = turn.result || {};
  const events = result.events || turn.detail?.events || turn.events || [];
  return (
    <div className="turn-block">
      <div className="user-row"><span className="user-bubble">{turn.question}</span></div>
      <div className="answer-row">
        <Avatar shape="square" size={30} className="agent-avatar small">{"{}"}</Avatar>
        <div className="answer-copy">
          <Flex align="center" gap={8} style={{ marginBottom: 10 }}>
            <Typography.Text strong style={{ fontSize: 13 }}>Claude Code</Typography.Text>
            {turn.status === "success" && <Tag color="success">已完成</Tag>}
            {turn.status === "loading" && <Tag color="processing">调查中</Tag>}
            {turn.status === "error" && <Tag color="error">失败</Tag>}
          </Flex>
          {turn.status === "loading" && <RuntimeEvents events={events} live />}
          {turn.status === "error" && <Alert type="error" showIcon message="分析失败" description={turn.error || "Claude Code 未完成回答"} />}
          {turn.status === "success" && (
            <>
              <AnswerText answer={result.answer ?? turn.detail?.answer ?? ""} />
              <RuntimeEvents events={events} />
              <Feedback runId={result.runId || turn.detail?.runId || turn.id} answer={result.answer ?? turn.detail?.answer ?? ""} />
            </>
          )}
          {turn.status === "loading" && events.length === 0 && <div className="runtime-empty"><Spin size="small" /> 正在启动 Claude Code…</div>}
        </div>
      </div>
    </div>
  );
}

function AnswerText({ answer }) {
  const value = typeof answer === "string" ? answer : JSON.stringify(answer, null, 2);
  if (!value) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有返回文本回答" />;
  return <div className="answer-text">{value}</div>;
}

function RuntimeEvents({ events = [], live = false }) {
  if (!events.length) return null;
  const visible = events.slice(-24);
  return (
    <div className={`runtime-events ${live ? "live" : ""}`}>
      <div className="runtime-events-head">
        <Typography.Text type="secondary">{live ? "Claude Code 正在调查" : "调查过程"}</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>{events.length} 个事件</Typography.Text>
      </div>
      {visible.map((event, index) => <RuntimeEvent key={`${event.sequence || index}-${event.eventType || event.type}`} event={event} />)}
    </div>
  );
}

function RuntimeEvent({ event }) {
  const type = String(event.eventType || event.event_type || event.type || "message").toLowerCase();
  const payload = event.payload ?? event;
  const title = ({ tool_use: "调用读取工具", tool_result: "读取结果", assistant: "Claude Code", result: "完成回答", system: "会话初始化", error: "运行错误", user: "工具消息" })[type] || type;
  const detail = eventDetail(payload);
  return (
    <details className="runtime-event">
      <summary><span className={`event-dot ${type}`} /><span>{title}</span>{detail && <small>{detail}</small>}</summary>
      <pre>{JSON.stringify(payload, null, 2)}</pre>
    </details>
  );
}

function eventDetail(payload) {
  if (!payload || typeof payload !== "object") return String(payload || "");
  const candidates = [payload.name, payload.path, payload.file_path, payload.input?.file_path, payload.input?.path, payload.result];
  const value = candidates.find((item) => typeof item === "string" && item.trim());
  return value ? value.replace(/\s+/g, " ").slice(0, 140) : "";
}

function Composer({ question, setQuestion, submit, stopQuery, status, newConversation, loading }) {
  const inputRef = useRef(null);
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const tag = document.activeElement?.tagName;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
      event.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const send = () => submit();
  return (
    <div className="composer">
      <Input.TextArea
        ref={inputRef}
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        onPressEnter={(event) => { if (!event.shiftKey) { event.preventDefault(); send(); } }}
        autoSize={{ minRows: 1, maxRows: 6 }}
        placeholder="询问业务流程、代码逻辑或跨应用调用链…"
        variant="borderless"
      />
      <Flex justify="space-between" align="center" style={{ paddingTop: 7 }}>
        <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>Enter 发送 · Shift + Enter 换行</Typography.Text>
        <Flex gap={6}>
          {loading ? <Button type="text" danger icon={<Stop size={16} weight="fill" />} onClick={stopQuery}>停止</Button> : (
            <Button type="primary" shape="circle" icon={<ArrowUp size={17} weight="bold" />} onClick={send} disabled={!question?.trim()} aria-label="发送" />
          )}
          {status !== "idle" && !loading && <Button type="text" onClick={newConversation}>新对话</Button>}
        </Flex>
      </Flex>
    </div>
  );
}

function Feedback({ runId, answer }) {
  const [sent, setSent] = React.useState("");
  if (!runId) return null;
  async function send(rating) {
    try {
      await request(`/api/query/${runId}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rating }) });
      setSent(rating);
    } catch { /* feedback is optional and should not obscure the answer */ }
  }
  if (sent) return <div className="feedback-sent"><Check size={14} /> 感谢反馈</div>;
  return (
    <Flex gap={4} align="center" className="feedback">
      <Typography.Text type="secondary" style={{ fontSize: 11 }}>回答有帮助吗？</Typography.Text>
      <Tooltip title="有帮助"><Button type="text" size="small" icon={<ThumbsUp size={14} />} onClick={() => send("HELPFUL")} /></Tooltip>
      <Tooltip title="需要改进"><Button type="text" size="small" icon={<ThumbsDown size={14} />} onClick={() => send("NOT_HELPFUL")} /></Tooltip>
      <Tooltip title="复制回答"><Button type="text" size="small" icon={<Copy size={14} />} onClick={() => navigator.clipboard?.writeText(typeof answer === "string" ? answer : JSON.stringify(answer || "")) } /></Tooltip>
    </Flex>
  );
}

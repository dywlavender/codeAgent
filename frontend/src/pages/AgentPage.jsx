import React, { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, CaretRight, Check, Code, Copy, FileText, FolderOpen, MagnifyingGlass, SidebarSimple, Stop, ThumbsDown, ThumbsUp, WarningCircle } from "@phosphor-icons/react";
import { Alert, Button, Flex, Input, Tooltip } from "antd";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { request } from "../lib/api.js";
import { buildProgress, toolTarget, toolTitle } from "../lib/progress.js";

const EXAMPLES = ["梳理一个业务功能的入口和调用流程", "解释这段代码为什么这样校验", "结合业务基线，定位需求对应的实现"];

export function AgentPage(props) {
  const { turns = [], status } = props;
  const loading = status === "loading";
  const scrollRef = useRef(null);
  const bottomRef = useRef(null);
  const followRef = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setAtBottom(entry.isIntersecting), { root: scrollRef.current, threshold: 0 });
    if (bottomRef.current) observer.observe(bottomRef.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (followRef.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [turns]);
  function jumpToBottom() {
    followRef.current = true;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }
  return (
    <section className="agent-shell">
      <header className="agent-topbar">
        <Button type="text" icon={<SidebarSimple size={19} />} onClick={props.toggleSidebar} aria-label="切换侧栏" />
        <span className="project-title">{props.workspace?.project || "Code Atlas"}</span>
        <span className="workspace-caption">{props.workspace?.counts?.repositories || 0} 个仓库</span>
        <span className="runtime-indicator">{loading ? "处理中" : "Claude Code"}</span>
      </header>
      <div className="chat-column">
        <div className="chat-scroll" ref={scrollRef} onScroll={(event) => {
          const node = event.currentTarget;
          followRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
        }}>
          {!turns.length ? <Welcome submit={props.submit} workspace={props.workspace} /> : (
            <div className="turn-list">{turns.map((turn) => <TurnBlock key={turn.id} turn={turn} />)}</div>
          )}
          <div ref={bottomRef} className="scroll-bottom" />
        </div>
        <div className="composer-stick">
          {!atBottom && turns.length > 0 && <Button className="jump-bottom" shape="circle" icon={<ArrowDown size={16} />} aria-label="回到底部" onClick={jumpToBottom} />}
          {props.error && <Alert type="error" showIcon title={props.error} />}
          <Composer {...props} loading={loading} />
          <div className="composer-note">基于当前工作区的业务知识与代码回答，请核对关键结论。</div>
        </div>
      </div>
    </section>
  );
}

function Welcome({ submit, workspace }) {
  return <div className="welcome">
    <Code size={32} weight="bold" className="welcome-mark" />
    <h1>从一个问题开始</h1><p>一起读懂业务，找到代码里的答案。</p>
    <div className="welcome-context"><FolderOpen size={15} /> {workspace?.project || "当前工作区"}<span>{workspace?.counts?.repositories || 0} 个仓库</span></div>
    <div className="suggestions">{EXAMPLES.map((item) => <button key={item} onClick={() => submit(item)}>{item}<ArrowUp size={14} /></button>)}</div>
  </div>;
}

export function TurnBlock({ turn }) {
  const result = turn.result || turn.detail || {};
  const events = result.events || turn.events || [];
  const progress = useMemo(() => buildProgress(events), [events]);
  const live = turn.status === "loading";
  const answer = ["success", "cancelled"].includes(turn.status) ? result.answer : progress.text;
  return <article className="turn-block">
    <div className="user-row"><div className="user-bubble">{turn.question}</div></div>
    <div className="answer-row"><div className="answer-copy">
      <Investigation progress={progress} live={live} status={turn.status} />
      {answer && <AnswerText answer={answer} />}
      {turn.status === "error" && <Alert type="error" showIcon title="分析未完成" description={turn.error || result.error || "请稍后重试"} />}
      {turn.status === "stopped" && <p className="stopped-note">已停止接收。后台仍在运行，完成后可从左侧历史查看。</p>}
      {turn.status === "cancelled" && <p className="stopped-note">已停止生成，以上为停止前收到的内容。</p>}
      {turn.status === "disconnected" && <p className="stopped-note">进度连接已断开，后台任务状态待确认。</p>}
      {turn.status === "success" && <Feedback runId={result.runId || turn.id} answer={answer} />}
    </div></div>
  </article>;
}

function AnswerText({ answer }) {
  return <div className="answer-text"><Markdown remarkPlugins={[remarkGfm]} components={{
    pre: CodeBlock,
    a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
  }}>{typeof answer === "string" ? answer : JSON.stringify(answer, null, 2)}</Markdown></div>;
}

function CodeBlock({ children }) {
  const [copied, setCopied] = useState(false);
  const code = React.Children.toArray(children)[0];
  const language = code?.props?.className?.replace("language-", "") || "代码";
  return <div className="code-block"><div className="code-toolbar"><span>{language}</span><Button type="text" size="small" onClick={async () => {
    try { await navigator.clipboard.writeText(String(code?.props?.children || "")); setCopied(true); } catch { setCopied(false); }
  }} icon={copied ? <Check size={13} /> : <Copy size={13} />}>{copied ? "已复制" : "复制"}</Button></div><pre>{children}</pre></div>;
}

export function Investigation({ progress, live, status }) {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => { if (!live) setExpanded(false); }, [live]);
  const { tools, label } = progress;
  const completed = tools.filter((tool) => tool.status === "completed").length;
  const heading = live ? label : status === "success" ? (tools.length ? `已完成调查 · ${tools.length} 个工具步骤` : "已完成回答") : status === "cancelled" ? "已停止" : status === "stopped" ? "后台运行中" : "调查中断";
  return <div className={`investigation ${live ? "live" : ""}`}>
    <button className="investigation-toggle" onClick={() => setExpanded(!expanded)} aria-label={heading} aria-expanded={expanded}>
      {live ? <span className="activity-pulse" /> : status === "success" ? <Check size={14} /> : <WarningCircle size={14} />}
      <span role={live ? "status" : undefined}>{heading}</span>
      {tools.length > 0 && live && <small>{completed}/{tools.length}</small>}
      <CaretRight size={12} className={expanded ? "expanded" : ""} />
    </button>
    {expanded && <div className="investigation-steps">
      {!tools.length && <p className="step-empty">{live ? "正在准备和分析，尚未调用工具。" : "本轮没有工具调用记录。"}</p>}
      {tools.map((tool) => <ToolStep key={tool.id} tool={tool} live={live} />)}
    </div>}
  </div>;
}

function ToolStep({ tool, live }) {
  const Icon = tool.name === "Read" ? FileText : MagnifyingGlass;
  const running = tool.status === "running" && live;
  const status = running ? "进行中" : tool.status === "completed" ? "已完成" : tool.status === "error" ? "失败" : "未确认完成";
  return <details className="tool-step">
    <summary><Icon size={15} /><span>{toolTitle(tool)}</span><code title={toolTarget(tool)}>{toolTarget(tool) || "准备参数…"}</code><small className={tool.status === "error" ? "tool-error" : ""}>{status}</small><CaretRight size={12} /></summary>
    <div className="tool-detail">
      {Object.entries(tool.input || {}).map(([key, value]) => <div className="tool-argument" key={key}><span>{key}</span><code>{typeof value === "string" ? value : JSON.stringify(value)}</code></div>)}
      {tool.output && <pre>{tool.output}</pre>}
      {!tool.output && <p>{running ? "等待工具返回结果…" : "没有可展示的结果文本。"}</p>}
    </div>
  </details>;
}

function Composer({ question, setQuestion, submit, stopQuery, loading, cancelling }) {
  const inputRef = useRef(null);
  const composing = useRef(false);
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName) || document.activeElement?.isContentEditable) return;
      event.preventDefault(); inputRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return <div className="composer">
    <Input.TextArea ref={inputRef} value={question} aria-label="输入问题" onChange={(event) => setQuestion(event.target.value)}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
      onPressEnter={(event) => { if (!event.shiftKey && !composing.current && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); if (!loading) submit(); } }}
      autoSize={{ minRows: 2, maxRows: 8 }} placeholder="询问业务，或描述你想查找的代码…" variant="borderless" />
    <div className="composer-toolbar"><span><Code size={15} /> Claude Code <small>只读工作区</small></span>
      <Flex align="center" gap={12}><small className="keyboard-hint">Enter 发送</small>{loading ?
        <Tooltip title={cancelling ? "正在停止后台任务" : "停止生成"}><Button className="send-button" shape="circle" loading={cancelling} disabled={cancelling} icon={<Stop size={15} weight="fill" />} onClick={stopQuery} aria-label={cancelling ? "正在停止" : "停止生成"} /></Tooltip> :
        <Button className="send-button" type="primary" shape="circle" icon={<ArrowUp size={18} weight="bold" />} onClick={() => submit()} disabled={!question?.trim()} aria-label="发送" />
      }</Flex>
    </div>
  </div>;
}

function Feedback({ runId, answer }) {
  const [sent, setSent] = useState("");
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  async function send(rating) {
    try {
      await request(`/api/query/${runId}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rating }) });
      setSent(rating); setError("");
    } catch { setError("反馈未保存，请重试"); }
  }
  return <Flex gap={2} align="center" className="feedback">
    <Tooltip title={copied ? "已复制" : "复制回答"}><Button type="text" size="small" aria-label="复制回答" icon={copied ? <Check size={15} /> : <Copy size={15} />} onClick={async () => {
      try { await navigator.clipboard.writeText(answer || ""); setCopied(true); } catch { setError("复制失败，请手动选择文本"); }
    }} /></Tooltip>
    <Tooltip title="有帮助"><Button type="text" size="small" aria-label="有帮助" disabled={Boolean(sent)} icon={<ThumbsUp size={15} weight={sent === "HELPFUL" ? "fill" : "regular"} />} onClick={() => send("HELPFUL")} /></Tooltip>
    <Tooltip title="需要改进"><Button type="text" size="small" aria-label="需要改进" disabled={Boolean(sent)} icon={<ThumbsDown size={15} weight={sent === "NOT_HELPFUL" ? "fill" : "regular"} />} onClick={() => send("NOT_HELPFUL")} /></Tooltip>
    {sent && <small>感谢反馈</small>}{error && <small role="alert">{error}</small>}
  </Flex>;
}

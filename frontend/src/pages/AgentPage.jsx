import React, { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, CaretDown, CaretRight, Check, Code, Copy, FileText, FolderOpen, MagnifyingGlass, SidebarSimple, Stop, ThumbsDown, ThumbsUp, WarningCircle } from "@phosphor-icons/react";
import { Alert, Button, Checkbox, Flex, Input, Popover, Segmented, Tag, Tooltip } from "antd";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { request } from "../lib/api.js";
import { buildProgress, fileEvidence, toolTarget, toolTitle } from "../lib/progress.js";

const EXAMPLES = ["梳理一个业务功能的入口和调用流程", "解释这段代码为什么这样校验", "结合业务补充知识，定位需求对应的实现"];
const MODE_LABEL = { none: "无主干", backbone: "有主干", ast: "AST" };

export function AgentPage(props) {
  const { turns = [], status, projectId } = props;
  const loading = status === "loading";
  const empty = turns.length === 0;
  const scrollRef = useRef(null);
  const bottomRef = useRef(null);
  const followRef = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  const [astInfo, setAstInfo] = useState(null);
  useEffect(() => { setAstInfo(null); request("/api/ast").then(setAstInfo).catch(() => {}); }, [projectId]);
  useEffect(() => {
    if (empty) return undefined;
    const observer = new IntersectionObserver(([entry]) => setAtBottom(entry.isIntersecting), { root: scrollRef.current, threshold: 0 });
    if (bottomRef.current) observer.observe(bottomRef.current);
    return () => observer.disconnect();
  }, [empty]);
  useEffect(() => {
    if (followRef.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [turns]);
  function jumpToBottom() {
    followRef.current = true;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }

  if (empty) {
    return (
      <section className="agent-shell">
        <header className="agent-topbar">
          <Button type="text" icon={<SidebarSimple size={19} />} onClick={props.toggleSidebar} aria-label="切换侧栏" />
          <span className="project-title">{props.workspace?.project || "Code Atlas"}</span>
          <span className="workspace-caption">{props.workspace?.counts?.repositories || 0} 个仓库</span>
          <span className="runtime-indicator">{loading ? "处理中" : "Claude Code"}</span>
        </header>
        <div className="home-shell">
          <div className="home-inner">
            <h1 className="home-title">今天想弄清楚什么？</h1>
            <p className="home-sub">基于工作区里的业务补充知识、需求原文和代码仓库一起找答案。</p>
            {props.error && <Alert type="error" showIcon title={props.error} style={{ marginBottom: 12 }} />}
            {props.modeNotice && <div className="mode-notice">{props.modeNotice}</div>}
            <Composer {...props} loading={loading} home astInfo={astInfo} />
            <div className="home-chips">
              {EXAMPLES.map((item) => <button key={item} onClick={() => props.submit(item)}>{item}</button>)}
            </div>
            <div className="composer-note">基于当前工作区的业务知识与代码回答，请核对关键结论。</div>
          </div>
        </div>
      </section>
    );
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
          <div className="turn-list">{turns.map((turn) => <TurnBlock key={turn.id} turn={turn} />)}</div>
          <div ref={bottomRef} className="scroll-bottom" />
        </div>
        <div className="composer-stick">
          {!atBottom && <Button className="jump-bottom" shape="circle" icon={<ArrowDown size={16} />} aria-label="回到底部" onClick={jumpToBottom} />}
          {props.error && <Alert type="error" showIcon title={props.error} />}
          {props.modeNotice && <div className="mode-notice">{props.modeNotice}</div>}
          <Composer {...props} loading={loading} astInfo={astInfo} />
        </div>
      </div>
    </section>
  );
}

export function TurnBlock({ turn }) {
  const result = turn.result || turn.detail || {};
  const events = result.events || turn.events || [];
  const progress = useMemo(() => buildProgress(events), [events]);
  const files = useMemo(() => fileEvidence(progress.tools), [progress.tools]);
  const live = turn.status === "loading";
  const answer = ["success", "cancelled"].includes(turn.status) ? result.answer : progress.text;
  return <article className="turn-block">
    <div className="user-row"><div className="user-bubble"><span className="turn-mode">{MODE_LABEL[result.mode || turn.mode || "backbone"] || "有主干"}</span>{(result.astVersionId || turn.astVersionId) && <span className="turn-version">{result.astVersionId || turn.astVersionId}</span>}{turn.question}</div></div>
    <div className="answer-row"><div className="answer-copy">
      <Investigation progress={progress} files={files} live={live} status={turn.status} />
      {!live && files.length > 0 && <FileCards files={files} />}
      {answer && <div className="answer-report"><AnswerText answer={answer} /></div>}
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

export function Investigation({ progress, files = [], live, status }) {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => { if (!live) setExpanded(false); }, [live]);
  const { tools, label } = progress;
  const searches = tools.filter((tool) => tool.input?.pattern).length;
  const heading = live ? label
    : status === "success" ? (tools.length ? `已调查 ${tools.length} 步 · 读取 ${files.length} 个文件${searches ? ` · 搜索 ${searches} 次` : ""}` : "已完成回答")
    : status === "cancelled" ? "已停止" : status === "stopped" ? "后台运行中" : "调查中断";
  return <div className={`investigation ${live ? "live" : ""}`}>
    <button className="investigation-toggle" onClick={() => setExpanded(!expanded)} aria-label={heading} aria-expanded={expanded}>
      {live ? <span className="activity-pulse" /> : status === "success" ? <Check size={13} weight="bold" /> : <WarningCircle size={13} />}
      <span role={live ? "status" : undefined}>{heading}</span>
      <CaretRight size={11} className={expanded ? "expanded" : ""} />
    </button>
    {expanded && <div className="investigation-steps">
      {!tools.length && <p className="step-empty">{live ? "正在准备和分析，尚未调用工具。" : "本轮没有工具调用记录。"}</p>}
      {tools.map((tool) => <ToolStep key={tool.id} tool={tool} live={live} />)}
    </div>}
  </div>;
}

function FileCards({ files }) {
  return <div className="file-cards">
    {files.slice(0, 6).map((file) => <FileCard key={file.path} file={file} />)}
    {files.length > 6 && <span className="file-more">等 {files.length} 个文件</span>}
  </div>;
}

function FileCard({ file }) {
  const [open, setOpen] = useState(false);
  const segments = file.path.split("/").filter(Boolean);
  const name = segments.at(-1);
  const dir = segments.slice(-2, -1)[0] || "";
  return <div className={`file-card ${open ? "open" : ""}`}>
    <button type="button" className="file-card-head" onClick={() => setOpen(!open)} aria-expanded={open}>
      <FileText size={14} />
      <code>{name}</code>
      {dir && <small>{dir}/</small>}
      <CaretRight size={11} className={open ? "expanded" : ""} />
    </button>
    {open && <pre className="file-card-body">{file.snippet || "没有读取到的内容片段。"}</pre>}
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

// Codex 式环境选择器：多选系统/工程收窄本轮调查范围；不选 = 全部资料。
function ContextChip({ workspace, scope, setScope }) {
  const sources = workspace?.sources || [];
  const applications = workspace?.applications || [];
  const repositories = workspace?.repositories || [];
  const repos = sources.filter((item) => item.kind === "repository");
  const docs = sources.filter((item) => item.kind !== "repository");

  // 系统分组：来自项目拓扑（application → system → repository）
  const systems = [];
  for (const app of applications) {
    if (!app.systemId) continue;
    let group = systems.find((item) => item.systemId === app.systemId);
    if (!group) {
      group = { systemId: app.systemId, systemName: app.systemName || app.systemId, apps: [] };
      systems.push(group);
    }
    group.apps.push(app);
  }

  const selectedSystems = scope?.systemIds || [];
  const repoDisplayName = (id) => repositories.find((item) => item.id === id)?.displayName || id;

  function apply(nextSystems) {
    setScope(nextSystems.length ? { systemIds: nextSystems, repositoryIds: [] } : null);
  }
  function toggleSystem(systemId) {
    apply(selectedSystems.includes(systemId)
      ? selectedSystems.filter((item) => item !== systemId)
      : [...selectedSystems, systemId]);
  }

  const repoCount = workspace?.counts?.repositories ?? repos.length;
  const scopedNames = selectedSystems.map((id) => systems.find((item) => item.systemId === id)?.systemName || id);
  const chipLabel = scopedNames.length
    ? (scopedNames.length <= 2 ? scopedNames.join(" + ") : `${scopedNames.length} 个系统`)
    : (workspace?.project || "当前工作区");

  const content = <div className="ctx-menu">
    {systems.length > 0 && <>
      <div className="ctx-group">调查范围</div>
      <div className="ctx-option" role="checkbox" aria-checked={selectedSystems.length === 0}
        onClick={() => apply([])}>
        <Checkbox checked={selectedSystems.length === 0} onClick={(event) => event.preventDefault()} tabIndex={-1} />
        <span className="ctx-option-name">全部资料</span>
        <small>不限定系统</small>
      </div>
      {systems.map((group) => (
        <div className="ctx-option" role="checkbox" aria-checked={selectedSystems.includes(group.systemId)}
          key={group.systemId} onClick={() => toggleSystem(group.systemId)}>
          <Checkbox checked={selectedSystems.includes(group.systemId)} onClick={(event) => event.preventDefault()} tabIndex={-1} />
          <span className="ctx-option-name">{group.systemName}</span>
          <small>{group.apps.map((app) => repoDisplayName(app.repositoryId)).join("、")}</small>
        </div>
      ))}
      <div className="ctx-note">限定后 Agent 只授权读取所选系统涉及的工程目录。</div>
    </>}
    <div className="ctx-group">代码仓库 · {repos.length}</div>
    {repos.map((item) => <div className="ctx-row" key={item.id}><FolderOpen size={13} />{item.name}<small>{item.status === "READABLE" ? "可读" : "异常"}</small></div>)}
    {docs.length > 0 && <div className="ctx-group">资料目录</div>}
    {docs.map((item) => <div className="ctx-row" key={item.id}><FileText size={13} />{item.name}<small>{item.status === "READABLE" ? "可读" : "异常"}</small></div>)}
  </div>;

  return <Popover trigger={["hover", "click"]} placement="topLeft" title="工作区资料与调查范围" content={content}>
    <button type="button" className={`ctx-chip${scopedNames.length ? " scoped" : ""}`} aria-label="选择调查范围">
      <FolderOpen size={14} />
      <span>{chipLabel}</span>
      {scopedNames.length ? <Tag color="processing" bordered={false} style={{ margin: 0, fontSize: 10 }}>限定</Tag>
        : <small>{repoCount} 仓库</small>}
      <CaretDown size={10} />
    </button>
  </Popover>;
}

function Composer({ question, setQuestion, submit, stopQuery, loading, cancelling, workspace, home, scope, setScope, mode, setMode, astInfo }) {
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
  return <div className={`composer${home ? " home" : ""}`}>
    <Input.TextArea ref={inputRef} value={question} aria-label="输入问题" onChange={(event) => setQuestion(event.target.value)}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
      onPressEnter={(event) => { if (!event.shiftKey && !composing.current && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); if (!loading) submit(); } }}
      autoSize={{ minRows: 2, maxRows: 8 }} placeholder="询问业务，或描述你想查找的代码…" variant="borderless" />
    <div className="composer-toolbar">
      <Flex align="center" gap={8}>
        <ContextChip workspace={workspace} scope={scope} setScope={setScope} />
        <span className="composer-mode"><Code size={14} /> Claude Code</span>
        <Segmented
          size="small"
          className="mode-selector"
          value={mode || "backbone"}
          onChange={(value) => setMode?.(value)}
          options={[
            { value: "none", label: "无主干" },
            { value: "backbone", label: "有主干" },
            { value: "ast", label: "AST" },
          ]}
        />
        <small className="mode-version">
          {mode === "ast"
            ? ["available", "stale"].includes(astInfo?.status) ? `AST ${astInfo.currentVersionId}${astInfo.status === "stale" ? " · 待更新" : ""}` : "AST 未生成"
            : mode === "none" ? "仅项目资料" : "业务补充知识当前资料"}
        </small>
        <Tag bordered={false} color="default" style={{ margin: 0, fontSize: 10.5 }}>只读</Tag>
      </Flex>
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

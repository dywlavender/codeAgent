import React, { useEffect, useRef, useState } from "react";
import {
  BookOpen, ChatCircleDots, Check, Flask, Graph, Lock, MinusCircle, Plus, ShieldCheck, SidebarSimple, Trash, WarningCircle,
} from "@phosphor-icons/react";
import {
  Avatar, Badge, Button, Flex, Input, Layout, Menu, Modal, Popconfirm, Select, Typography,
} from "antd";
import { RequestAborted, activeProjectId, request, streamQuery } from "./lib/api.js";
import { formatRelative } from "./lib/format.js";
import { isActiveRun, mergeConversations, RUN_STATUS_LABEL, turnFromRun, watchRun } from "./lib/query-state.js";
import { AgentPage } from "./pages/AgentPage.jsx";
import { LibraryPage } from "./pages/LibraryPage.jsx";
import { GraphPage } from "./pages/GraphPage.jsx";
import { KnowledgeAdminPage } from "./pages/KnowledgeAdminPage.jsx";
import { EvaluationPage } from "./pages/EvaluationPage.jsx";
import { AstPage } from "./pages/AstPage.jsx";

const { Sider, Content } = Layout;

const PAGE_IDS = ["agent", "library", "graph", "evaluation", "ast", "admin"];

function pageFromHash() {
  const id = window.location.hash.replace(/^#\/?/, "");
  return PAGE_IDS.includes(id) ? id : "agent";
}

function setProjectInUrl(projectId) {
  const url = new URL(window.location.href);
  if (projectId) url.searchParams.set("projectId", projectId);
  else url.searchParams.delete("projectId");
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

export default function App() {
  const [page, setPage] = useState(pageFromHash);
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(() => activeProjectId());
  const [projectModalOpen, setProjectModalOpen] = useState(false);
  const [projectSaving, setProjectSaving] = useState(false);
  const [projectForm, setProjectForm] = useState({ configPath: "", name: "", dataRoot: "" });
  const [workspace, setWorkspace] = useState(null);
  const [runs, setRuns] = useState([]);
  const [result, setResult] = useState(null);
  const [runDetail, setRunDetail] = useState(null);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [activeTurnId, setActiveTurnId] = useState(null);
  const [adminUnlocked, setAdminUnlocked] = useState(() => {
    const savedProject = activeProjectId();
    return Boolean(sessionStorage.getItem(savedProject ? `knowledgeAdminToken:${savedProject}` : "knowledgeAdminToken"));
  });
  const [lockOpen, setLockOpen] = useState(false);
  const [lockToken, setLockToken] = useState("");
  const [scope, setScope] = useState(null);
  const [mode, setMode] = useState(() => {
    const savedProject = activeProjectId();
    return sessionStorage.getItem(`queryMode:${savedProject || "default"}`)
      || sessionStorage.getItem("queryMode") || "backbone";
  });
  const [modeNotice, setModeNotice] = useState("");
  const queryAbortRef = useRef(null);
  const [cancelling, setCancelling] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 768);
  const restoreRef = useRef(0);
  const historyRequestRef = useRef(0);
  const [nextCursor, setNextCursor] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  const projectStorageKey = (name, id = projectId) => `${name}:${id || "default"}`;

  useEffect(() => {
    if (conversationId && projectId) sessionStorage.setItem(projectStorageKey("queryConversationId"), conversationId);
  }, [conversationId, projectId]);

  useEffect(() => () => queryAbortRef.current?.abort(), []);

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function navigate(id) {
    if (window.location.hash !== `#/${id}`) window.location.hash = `#/${id}`;
    else setPage(id);
    if (window.innerWidth < 768) setSidebarOpen(false);
  }

  const refreshRuns = async (cursor = null) => {
    const requestId = ++historyRequestRef.current;
    setHistoryLoading(true);
    try {
      const data = await request(`/api/conversations?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
      if (requestId !== historyRequestRef.current) return;
      setRuns((current) => cursor ? mergeConversations(current, data.items || []) : data.items || []);
      setNextCursor(data.nextCursor || null);
    } finally {
      if (requestId === historyRequestRef.current) setHistoryLoading(false);
    }
  };

  useEffect(() => {
    request("/api/projects")
      .then((data) => {
        const items = data.items || [];
        setProjects(items);
        const saved = activeProjectId();
        const selected = items.find((item) => item.id === saved) || items[0];
        if (selected) {
          sessionStorage.setItem("activeProjectId", selected.id);
          setProjectInUrl(selected.id);
          setProjectId(selected.id);
          setAdminUnlocked(Boolean(sessionStorage.getItem(`knowledgeAdminToken:${selected.id}`)));
        }
      })
      .catch((reason) => setError(reason.message));
  }, []);

  useEffect(() => {
    if (!projectId) return undefined;
    Promise.all([request("/api/workspace"), refreshRuns()])
      .then(([space]) => {
        setWorkspace(space);
        const saved = sessionStorage.getItem(projectStorageKey("queryConversationId"));
        if (saved && pageFromHash() === "agent" && restoreRef.current === 0 && !queryAbortRef.current) doRestore({ conversationId: saved });
      })
      .catch((reason) => setError(reason.message));
    return undefined;
  }, [projectId]);

  async function submit(nextQuestion = question) {
    const normalized = nextQuestion.trim();
    if (!normalized || status === "loading" || queryAbortRef.current) return;
    const turnId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setQuestion("");
    setTurns((current) => [...current, { id: turnId, question: normalized, mode, status: "loading", events: [] }]);
    setActiveTurnId(turnId);
    setResult(null);
    setRunDetail(null);
    setStatus("loading");
    setError("");

    const controller = new AbortController();
    setCancelling(false);
    queryAbortRef.current = controller;
    try {
      const data = await streamQuery("/api/query/stream", {
        question: normalized,
        conversationId,
        scope: scope || undefined,
        mode,
      }, {
        signal: controller.signal,
        onRun: (value) => {
          if (queryAbortRef.current !== controller) return;
          controller.runId = value.runId;
          setConversationId(value.conversationId);
          setTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, mode: value.mode || mode, astVersionId: value.astVersionId } : turn));
          if (controller.cancelRequested) requestCancellation(controller);
        },
        onEvent: (event) => queryAbortRef.current === controller && setTurns((current) => current.map((turn) => (
          turn.id === turnId ? { ...turn, events: [...(turn.events || []), event] } : turn
        ))),
      });
      if (queryAbortRef.current !== controller) return;
      const detail = data;
      setResult(data);
      setRunDetail(detail);
      setConversationId(data.conversationId || detail.conversationId || conversationId);
      setTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, status: data.status === "cancelled" ? "cancelled" : "success", result: data, detail, events: data.events || turn.events || [] } : turn));
      setStatus("success");
      await refreshRuns().catch(() => {});
    } catch (reason) {
      if (queryAbortRef.current !== controller) return;
      if (reason instanceof RequestAborted || reason?.name === "AbortError") {
        setTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, status: "stopped" } : turn));
        setError("");
        setStatus("idle");
        return;
      }
      // Query failures belong to this turn, not the independent operation banner.
      setTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, status: "error", error: reason.message } : turn));
      setStatus("error");
    } finally {
      if (queryAbortRef.current === controller) {
        queryAbortRef.current = null;
        setCancelling(false);
      }
    }
  }

  function stopQuery() {
    const controller = queryAbortRef.current;
    if (!controller) return;
    controller.cancelRequested = true;
    setCancelling(true);
    requestCancellation(controller);
  }

  async function requestCancellation(controller) {
    if (!controller.runId || controller.cancelSent) return;
    controller.cancelSent = true;
    try {
      await request(`/api/query/${controller.runId}/cancel`, { method: "POST" });
      if (queryAbortRef.current === controller) setError("");
      // Keep SSE connected until the runtime confirms termination and saves it.
    } catch (reason) {
      if (queryAbortRef.current !== controller) return;
      controller.cancelSent = false;
      controller.cancelRequested = false;
      setCancelling(false);
      setError(`停止失败：${reason.message}，可以重试。`);
    }
  }

  async function openRun(run) {
    try {
      setRunDetail(await request(`/api/query/${run.id}`));
    } catch (reason) {
      setError(reason.message);
    }
  }

  async function deleteConversation(run) {
    try {
      await request(`/api/conversations/${run.conversationId}`, { method: "DELETE" });
      if (run.conversationId === conversationId) newConversation();
      await refreshRuns().catch(() => {});
    } catch (reason) {
      setError(`删除失败：${reason.message}`);
    }
  }

  async function doRestore(run) {
    if (!run || queryAbortRef.current) return;
    const restoreId = ++restoreRef.current;
    setError("");
    setStatus("loading");
    navigate("agent");
    try {
      const history = run.conversationId
        ? (await request(`/api/conversations/${run.conversationId}`)).items
        : [await request(`/api/query/${run.id}`)];
      if (restoreId !== restoreRef.current) return;
      const detail = history.at(-1);
      if (!detail) throw new Error("会话没有可恢复的消息");
      setRunDetail(detail);
      setQuestion("");
      const restoredResult = {
        runId: detail.runId || detail.id,
        conversationId: detail.conversationId,
        runtime: detail.runtime,
        sessionId: detail.sessionId,
        status: detail.status,
        answer: detail.answer || "",
        events: detail.events || [],
        mode: detail.mode,
        modeLabel: detail.modeLabel,
        astVersionId: detail.astVersionId,
      };
      setResult(restoredResult);
      setTurns(history.map(turnFromRun));
      setConversationId(detail.conversationId || null);
      setActiveTurnId(detail.id);
      setScope(detail.scope || null);
      setMode(detail.mode || "backbone");
      sessionStorage.setItem(projectStorageKey("queryMode"), detail.mode || "backbone");
      if (isActiveRun(detail)) {
        const controller = new AbortController();
        controller.runId = detail.runId || detail.id;
        queryAbortRef.current = controller;
        setCancelling(detail.status === "cancelling");
        setStatus("loading");
        await monitorRun(controller);
      } else setStatus(detail.status === "failed" ? "error" : "success");
    } catch (reason) {
      if (restoreId !== restoreRef.current) return;
      setError(reason.message);
      setStatus("error");
    }
  }

  async function monitorRun(controller) {
    try {
      await watchRun(controller.runId, {
        signal: controller.signal,
        getRun: (id, signal) => request(`/api/query/${id}`, {}, signal),
        onUpdate: (run) => {
          if (queryAbortRef.current !== controller) return;
          setTurns((current) => current.map((turn) => turn.id === controller.runId ? turnFromRun(run) : turn));
          setResult(run);
          setRunDetail(run);
          if (!isActiveRun(run)) {
            setStatus(run.status === "failed" ? "error" : "success");
            setError("");
          } else if (run.status === "cancelling") setCancelling(true);
        },
      });
      await refreshRuns().catch(() => {});
    } catch (reason) {
      if (controller.signal.aborted || queryAbortRef.current !== controller) return;
      setError(`任务进度连接中断，请重新打开当前会话恢复。${reason.message}`);
      setTurns((current) => current.map((turn) => turn.id === controller.runId ? { ...turn, status: "disconnected" } : turn));
      setStatus("error");
    } finally {
      if (queryAbortRef.current === controller) {
        queryAbortRef.current = null;
        setCancelling(false);
      }
    }
  }

  function newConversation() {
    if (queryAbortRef.current) return;
    restoreRef.current += 1;
    queryAbortRef.current?.abort();
    queryAbortRef.current = null;
    setTurns([]);
    setResult(null);
    setRunDetail(null);
    setQuestion("");
    setConversationId(null);
    setScope(null);
    sessionStorage.removeItem(projectStorageKey("queryConversationId"));
    setError("");
    setStatus("idle");
    setActiveTurnId(null);
    navigate("agent");
  }

  function changeMode(nextMode) {
    if (!nextMode || nextMode === mode || queryAbortRef.current || status === "loading") return;
    newConversation();
    setMode(nextMode);
    sessionStorage.setItem(projectStorageKey("queryMode"), nextMode);
    const label = nextMode === "none" ? "无主干" : nextMode === "ast" ? "AST" : "有主干";
    setModeNotice(`已切换到${label}模式，已开始新对话。`);
    window.setTimeout(() => setModeNotice(""), 4200);
  }

  function selectTurn(turn) {
    setActiveTurnId(turn.id);
    setResult(turn.result || null);
    setRunDetail(turn.detail || null);
  }

  function switchProject(nextProjectId) {
    if (!nextProjectId || nextProjectId === projectId || status === "loading" || queryAbortRef.current) return;
    restoreRef.current += 1;
    setProjectId(nextProjectId);
    sessionStorage.setItem("activeProjectId", nextProjectId);
    setProjectInUrl(nextProjectId);
    setAdminUnlocked(Boolean(sessionStorage.getItem(`knowledgeAdminToken:${nextProjectId}`)));
    setWorkspace(null);
    setRuns([]);
    setNextCursor(null);
    setTurns([]);
    setResult(null);
    setRunDetail(null);
    setQuestion("");
    setConversationId(null);
    setScope(null);
    setActiveTurnId(null);
    setError("");
    setStatus("idle");
    setMode(sessionStorage.getItem(projectStorageKey("queryMode", nextProjectId)) || "backbone");
    navigate("agent");
  }

  async function registerProject() {
    const configPath = projectForm.configPath.trim();
    if (!configPath || projectSaving) return;
    setProjectSaving(true);
    try {
      const data = await request("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          configPath,
          name: projectForm.name.trim() || undefined,
          dataRoot: projectForm.dataRoot.trim() || undefined,
        }),
      });
      const selected = data.project;
      setProjects(data.projects || []);
      setProjectModalOpen(false);
      setProjectForm({ configPath: "", name: "", dataRoot: "" });
      if (selected?.id) switchProject(selected.id);
    } catch (reason) {
      setError(`登记工程失败：${reason.message}`);
    } finally {
      setProjectSaving(false);
    }
  }

  const adminRequired = Boolean(workspace?.adminAuthRequired);
  const showAdminZone = !adminRequired || adminUnlocked;

  async function unlockAdmin() {
    const token = lockToken.trim();
    if (!token) return;
    sessionStorage.setItem(`knowledgeAdminToken:${projectId || "default"}`, token);
    setAdminUnlocked(true);
    setLockOpen(false);
    setLockToken("");
  }

  const activeTurn = turns.find((turn) => turn.id === activeTurnId);
  const activeRunId = activeTurn ? (activeTurn.result?.runId || activeTurn.id) : null;
  const recentConversations = runs;
  // 单项目部署时不显示项目标签，避免每张卡片重复同一名字；出现多项目后自动点亮。
  const currentWorkspaceId = workspace?.workspace?.id;
  const multiProject = new Set(runs.map((run) => run.workspaceId).filter(Boolean)).size > 1;
  // 范围标签：显示所选系统名，加上不属于这些系统的直接指定工程。
  const applications = workspace?.applications || [];
  const scopeText = (value) => {
    if (!value) return "";
    const covered = new Set(applications.filter((item) => value.systemIds?.includes(item.systemId)).map((item) => item.repositoryId));
    const names = (value.systemIds || []).map((id) => applications.find((item) => item.systemId === id)?.systemName || id);
    const direct = (value.repositoryIds || []).filter((id) => !covered.has(id))
      .map((id) => workspace?.repositories?.find((item) => item.id === id)?.displayName || id);
    return [...names, ...direct].join(" + ");
  };

  return (
    <Layout className="app-layout">
      {sidebarOpen && <button className="sidebar-scrim" aria-label="关闭侧栏" onClick={() => setSidebarOpen(false)} />}
      <Sider width={240} collapsedWidth={0} collapsed={!sidebarOpen} trigger={null} className="app-sidebar">
        <div className="side-brand">
          <Avatar shape="square" size={30} style={{ background: "#211E1A", fontWeight: 700, fontFamily: "monospace", fontSize: 12, borderRadius: 8, color: "#F4F2ED" }}>{"{}"}</Avatar>
          <div>
            <Typography.Text strong style={{ fontSize: 13.5, display: "block", lineHeight: 1.3 }}>Code Atlas</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>业务与代码</Typography.Text>
          </div>
          <Button type="text" className="side-collapse" icon={<SidebarSimple size={18} />} onClick={() => setSidebarOpen(false)} aria-label="收起侧栏" />
        </div>
        <div className="project-switcher">
          <Typography.Text type="secondary" className="zone-label">当前工程</Typography.Text>
          <Flex gap={6}>
            <Select
              aria-label="选择工程"
              value={projectId || undefined}
              placeholder="选择工程"
              loading={!projects.length && !workspace}
              options={projects.map((item) => ({ label: item.name || item.id, value: item.id }))}
              onChange={switchProject}
              disabled={status === "loading" || Boolean(queryAbortRef.current)}
              style={{ width: "100%" }}
              size="small"
            />
            <Button type="text" size="small" icon={<Plus size={15} />} aria-label="登记工程" onClick={() => setProjectModalOpen(true)} />
          </Flex>
        </div>
        <Button
          block
          icon={<Plus size={14} weight="bold" />}
          onClick={newConversation}
          disabled={status === "loading"}
          style={{ margin: "2px 14px 8px", width: "calc(100% - 28px)", borderRadius: 10, fontWeight: 550, boxShadow: "0 1px 2px rgba(30,28,20,.05)" }}
        >
          开始新对话
        </Button>
        <Menu
          mode="inline"
          inlineIndent={16}
          selectedKeys={[page]}
          onClick={({ key }) => navigate(key)}
          items={[
            { key: "agent", icon: <ChatCircleDots size={16.5} />, label: "问答" },
            { key: "library", icon: <BookOpen size={16.5} />, label: "项目资料" },
            { key: "graph", icon: <Graph size={16.5} />, label: "知识图谱" },
            { key: "evaluation", icon: <Flask size={16.5} />, label: "效果验证" },
            { key: "ast", icon: <Graph size={16.5} />, label: "AST 管理" },
            ...(showAdminZone ? [{
              type: "group",
              label: "管理",
              children: [
                { key: "admin", icon: <ShieldCheck size={16.5} />, label: <span>业务知识维护</span> },
              ],
            }] : []),
          ]}
        />
        <div className="recent-block">
          <div className="zone-label">最近会话<Button type="text" size="small" disabled={historyLoading} onClick={() => refreshRuns().catch((reason) => setError(reason.message))}>刷新</Button></div>
          {runs.length === 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12, padding: "4px 18px", display: "block", lineHeight: 1.7 }}>
              提交第一个问题后，分析历史会出现在这里。
            </Typography.Text>
          )}
          {recentConversations.map((run) => {
            const running = isActiveRun(run);
            const statusKey = running ? "running" : run.status;
            const showProject = multiProject && run.workspaceId && run.workspaceId !== currentWorkspaceId;
            return (
              <div
                key={run.id}
                role="button"
                tabIndex={0}
                className={`task-card ${run.conversationId === conversationId || run.id === activeRunId ? "on" : ""}`}
                title={run.question}
                onClick={() => { if (status !== "loading") doRestore(run); }}
                onKeyDown={(event) => { if ((event.key === "Enter" || event.key === " ") && status !== "loading") { event.preventDefault(); doRestore(run); } }}
              >
                <span className={`task-state ${statusKey || "unknown"}`} aria-hidden>
                  {running ? <span className="activity-pulse" />
                    : run.status === "failed" ? <WarningCircle size={12} weight="fill" />
                    : run.status === "cancelled" ? <MinusCircle size={12} />
                    : <Check size={11} weight="bold" />}
                </span>
                <span className="task-copy">
                  <span className="task-q">{run.question}</span>
                  <small className="task-meta">
                    {RUN_STATUS_LABEL[run.status] || run.status || "未知状态"} · {formatRelative(run.startedAt || run.created_at)}
                    {run.modeLabel && ` · ${run.modeLabel}`}
                    {run.astVersionId && ` · ${run.astVersionId}`}
                    {scopeText(run.scope) && ` · ${scopeText(run.scope)}`}
                    {showProject && ` · ${run.workspaceId}`}
                  </small>
                </span>
                <Popconfirm
                  title="删除这条会话？"
                  description="将删除该会话的全部问答记录。"
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={(event) => { event?.stopPropagation(); deleteConversation(run); }}
                  onCancel={(event) => event?.stopPropagation()}
                >
                  <Button
                    className="task-delete"
                    type="text"
                    size="small"
                    aria-label="删除会话"
                    icon={<Trash size={13} />}
                    disabled={running}
                    onClick={(event) => event.stopPropagation()}
                  />
                </Popconfirm>
              </div>
            );
          })}
          {nextCursor && <Button type="text" block loading={historyLoading} disabled={historyLoading} onClick={() => refreshRuns(nextCursor).catch((reason) => setError(reason.message))}>加载更多会话</Button>}
        </div>
        {!showAdminZone && (
          <button className="admin-entry" onClick={() => setLockOpen(true)}>
            <Lock size={14} /> 管理员入口
          </button>
        )}
        {showAdminZone && adminRequired && (
          <div className="admin-entry unlocked">
            <ShieldCheck size={14} /> 管理员会话
            <Badge status="success" style={{ marginLeft: "auto" }} />
          </div>
        )}
      </Sider>

      <Content style={{ minWidth: 0 }}>
        {page !== "agent" && !sidebarOpen && <Button className="reopen-sidebar" icon={<SidebarSimple size={18} />} onClick={() => setSidebarOpen(true)}>导航</Button>}
        {page === "agent" && (
          <AgentPage
            projectId={projectId}
            workspace={workspace}
            runs={runs}
            question={question}
            setQuestion={setQuestion}
            submit={submit}
            stopQuery={stopQuery}
            cancelling={cancelling}
            status={status}
            error={error}
            result={result}
            runDetail={runDetail}
            turns={turns}
            activeTurnId={activeTurnId}
            selectTurn={selectTurn}
            newConversation={newConversation}
            toggleSidebar={() => setSidebarOpen((open) => !open)}
            scope={scope}
            setScope={setScope}
            mode={mode}
            setMode={changeMode}
            modeNotice={modeNotice}
          />
        )}
        {page === "library" && <LibraryPage workspace={workspace} projectId={projectId} />}
        {page === "graph" && <GraphPage workspace={workspace} projectId={projectId} />}
        {page === "evaluation" && <EvaluationPage projectId={projectId} onRequireUnlock={() => setLockOpen(true)} />}
        {page === "ast" && <AstPage projectId={projectId} onRequireUnlock={() => setLockOpen(true)} />}
        {page === "admin" && <KnowledgeAdminPage projectId={projectId} onRequireUnlock={() => setLockOpen(true)} />}
      </Content>

      <Modal
        open={lockOpen}
        title={<Flex align="center" gap={8}><Lock size={16} /> 管理员验证</Flex>}
        okText="解锁"
        cancelText="取消"
        onCancel={() => { setLockOpen(false); setLockToken(""); }}
        onOk={unlockAdmin}
        okButtonProps={{ disabled: !lockToken.trim() }}
        width={400}
      >
        <Flex vertical gap={10} style={{ paddingTop: 6 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
            输入管理员口令后，侧栏会出现知识治理入口。凭证只保存在当前浏览器会话中。
          </Typography.Text>
          <Input.Password
            value={lockToken}
            onChange={(event) => setLockToken(event.target.value)}
            placeholder="管理员口令"
            autoComplete="current-password"
            onPressEnter={unlockAdmin}
          />
        </Flex>
      </Modal>

      <Modal
        open={projectModalOpen}
        title="登记工程"
        okText="登记并切换"
        cancelText="取消"
        confirmLoading={projectSaving}
        okButtonProps={{ disabled: !projectForm.configPath.trim() }}
        onCancel={() => { if (!projectSaving) setProjectModalOpen(false); }}
        onOk={registerProject}
        width={470}
      >
        <Flex vertical gap={10} style={{ paddingTop: 6 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
            配置路径由服务端读取。登记只创建独立工程数据目录，不自动同步仓库、生成主干或生成 AST。
          </Typography.Text>
          <Input
            value={projectForm.configPath}
            onChange={(event) => setProjectForm((current) => ({ ...current, configPath: event.target.value }))}
            placeholder="服务器上的 project.config.json 路径"
          />
          <Input
            value={projectForm.name}
            onChange={(event) => setProjectForm((current) => ({ ...current, name: event.target.value }))}
            placeholder="展示名称（可选）"
          />
          <Input
            value={projectForm.dataRoot}
            onChange={(event) => setProjectForm((current) => ({ ...current, dataRoot: event.target.value }))}
            placeholder="工程数据目录（可选；已有历史库可显式填写）"
          />
        </Flex>
      </Modal>

    </Layout>
  );
}

import React, { useEffect, useRef, useState } from "react";
import {
  BookOpen, ChatCircleDots, Graph, Lock, Plus, ShieldCheck, SidebarSimple,
} from "@phosphor-icons/react";
import {
  Avatar, Badge, Button, Flex, Input, Layout, Menu, Modal, Typography,
} from "antd";
import { RequestAborted, request, streamQuery } from "./lib/api.js";
import { formatTime } from "./lib/format.js";
import { isActiveRun, mergeConversations, turnFromRun, watchRun } from "./lib/query-state.js";
import { AgentPage } from "./pages/AgentPage.jsx";
import { LibraryPage } from "./pages/LibraryPage.jsx";
import { GraphPage } from "./pages/GraphPage.jsx";
import { KnowledgeAdminPage } from "./pages/KnowledgeAdminPage.jsx";

const { Sider, Content } = Layout;

const PAGE_IDS = ["agent", "library", "graph", "admin"];

function pageFromHash() {
  const id = window.location.hash.replace(/^#\/?/, "");
  return PAGE_IDS.includes(id) ? id : "agent";
}

export default function App() {
  const [page, setPage] = useState(pageFromHash);
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
  const [adminUnlocked, setAdminUnlocked] = useState(() => Boolean(sessionStorage.getItem("knowledgeAdminToken")));
  const [lockOpen, setLockOpen] = useState(false);
  const [lockToken, setLockToken] = useState("");
  const queryAbortRef = useRef(null);
  const [cancelling, setCancelling] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 768);
  const restoreRef = useRef(0);
  const historyRequestRef = useRef(0);
  const [nextCursor, setNextCursor] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  useEffect(() => {
    if (conversationId) sessionStorage.setItem("queryConversationId", conversationId);
  }, [conversationId]);

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
    Promise.all([request("/api/workspace"), refreshRuns()])
      .then(([space]) => {
        setWorkspace(space);
        const saved = sessionStorage.getItem("queryConversationId");
        if (saved && pageFromHash() === "agent" && restoreRef.current === 0 && !queryAbortRef.current) doRestore({ conversationId: saved });
      })
      .catch((reason) => setError(reason.message));
  }, []);

  async function submit(nextQuestion = question) {
    const normalized = nextQuestion.trim();
    if (!normalized || status === "loading" || queryAbortRef.current) return;
    const turnId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setQuestion("");
    setTurns((current) => [...current, { id: turnId, question: normalized, status: "loading", events: [] }]);
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
      }, {
        signal: controller.signal,
        onRun: (value) => {
          if (queryAbortRef.current !== controller) return;
          controller.runId = value.runId;
          setConversationId(value.conversationId);
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
      };
      setResult(restoredResult);
      setTurns(history.map(turnFromRun));
      setConversationId(detail.conversationId || null);
      setActiveTurnId(detail.id);
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
    sessionStorage.removeItem("queryConversationId");
    setError("");
    setStatus("idle");
    setActiveTurnId(null);
    navigate("agent");
  }

  function selectTurn(turn) {
    setActiveTurnId(turn.id);
    setResult(turn.result || null);
    setRunDetail(turn.detail || null);
  }

  const adminRequired = Boolean(workspace?.adminAuthRequired);
  const showAdminZone = !adminRequired || adminUnlocked;

  async function unlockAdmin() {
    const token = lockToken.trim();
    if (!token) return;
    sessionStorage.setItem("knowledgeAdminToken", token);
    setAdminUnlocked(true);
    setLockOpen(false);
    setLockToken("");
  }

  const activeTurn = turns.find((turn) => turn.id === activeTurnId);
  const activeRunId = activeTurn ? (activeTurn.result?.runId || activeTurn.id) : null;
  const recentConversations = runs;

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
            { key: "library", icon: <BookOpen size={16.5} />, label: "浏览知识库" },
            { key: "graph", icon: <Graph size={16.5} />, label: "知识图谱" },
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
          {recentConversations.map((run) => (
            <button key={run.id} disabled={status === "loading"} className={`recent-item ${run.conversationId === conversationId || run.id === activeRunId ? "on" : ""}`} title={run.question} onClick={() => doRestore(run)}>
          <span className="rcopy">
            <span className="rq">{run.question}</span>
            <small>{formatTime(run.startedAt || run.created_at)}</small>
              </span>
            </button>
          ))}
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
          />
        )}
        {page === "library" && <LibraryPage workspace={workspace} />}
        {page === "graph" && <GraphPage workspace={workspace} />}
        {page === "admin" && <KnowledgeAdminPage onRequireUnlock={() => setLockOpen(true)} />}
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

    </Layout>
  );
}

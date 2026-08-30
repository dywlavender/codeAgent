import React, { useEffect, useRef, useState } from "react";
import {
  BookOpen, ChatCircleDots, Graph, Lock, Plus, ShieldCheck,
} from "@phosphor-icons/react";
import {
  Avatar, Badge, Button, Flex, Input, Layout, Menu, Modal, Typography,
} from "antd";
import { RequestAborted, request } from "./lib/api.js";
import { buildQueryHistory, formatTime, intentLabel } from "./lib/format.js";
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

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function navigate(id) {
    if (window.location.hash !== `#/${id}`) window.location.hash = `#/${id}`;
    else setPage(id);
  }

  const refreshRuns = async () => {
    const data = await request("/api/runs?limit=30");
    setRuns(data.items || []);
  };

  useEffect(() => {
    Promise.all([request("/api/workspace"), request("/api/runs?limit=30")])
      .then(([space, history]) => {
        setWorkspace(space);
        setRuns(history.items || []);
      })
      .catch((reason) => setError(reason.message));
  }, []);

  async function submit(nextQuestion = question) {
    const normalized = nextQuestion.trim();
    if (!normalized || status === "loading") return;
    const turnId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const history = buildQueryHistory(turns);
    setQuestion("");
    setTurns((current) => [...current, { id: turnId, question: normalized, status: "loading" }]);
    setActiveTurnId(turnId);
    setResult(null);
    setRunDetail(null);
    setStatus("loading");
    setError("");

    const controller = new AbortController();
    queryAbortRef.current = controller;
    try {
      const data = await request("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: normalized, conversationId, history }),
      }, controller.signal);
      const detail = await request(`/api/query/${data.runId}`);
      setResult(data);
      setRunDetail(detail);
      setConversationId(data.conversationId || detail.conversationId || conversationId);
      setTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, status: "success", result: data, detail } : turn));
      setStatus("success");
      await refreshRuns();
    } catch (reason) {
      if (reason instanceof RequestAborted || reason?.name === "AbortError") {
        setTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, status: "error", error: "本次查询已被手动停止。" } : turn));
        setError("");
        setStatus("idle");
        return;
      }
      setError(reason.message);
      setTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, status: "error", error: reason.message } : turn));
      setStatus("error");
    } finally {
      queryAbortRef.current = null;
    }
  }

  function stopQuery() {
    queryAbortRef.current?.abort();
  }

  async function openRun(run) {
    try {
      setRunDetail(await request(`/api/query/${run.id}`));
    } catch (reason) {
      setError(reason.message);
    }
  }

  async function doRestore(run) {
    if (!run) return;
    setError("");
    setStatus("loading");
    navigate("agent");
    try {
      const detail = await request(`/api/query/${run.id}`);
      setRunDetail(detail);
      setQuestion("");
      const evidenceList = Array.isArray(detail.evidence) ? detail.evidence : [];
      const restoredResult = {
        runId: detail.id, status: detail.status, intent: detail.intent,
        evidenceStatus: detail.evidence_status, iterations: detail.iterations,
        answer: detail.answer || null, evidence: evidenceList,
        mappingSuggestions: detail.mappingSuggestions || [],
        answerMode: detail.answerMode || detail.answer_mode || detail.state?.answerMode || detail.state?.answer_mode,
        resolvedQuestion: detail.resolvedQuestion || detail.resolved_question || detail.state?.resolvedQuestion || detail.state?.resolved_question || detail.state?.search_terms?.join(" "),
        suggestedFollowUps: detail.suggestedFollowUps || detail.suggested_follow_ups || detail.answer?.suggestedFollowUps || [],
        entities: detail.entities || detail.resolvedEntities || detail.state?.entities || detail.state?.resolved_entities || [],
        metrics: { sourceCoverage: [...new Set(evidenceList.map((item) => item && item.sourceType).filter(Boolean))] },
      };
      setResult(restoredResult);
      setTurns([{ id: detail.id, question: detail.question, status: "success", result: restoredResult, detail }]);
      setConversationId(detail.conversationId || null);
      setActiveTurnId(detail.id);
      setStatus("success");
    } catch (reason) {
      setError(reason.message);
      setStatus("error");
    }
  }

  function newConversation() {
    queryAbortRef.current?.abort();
    setTurns([]);
    setResult(null);
    setRunDetail(null);
    setQuestion("");
    setConversationId(null);
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
    setPage((current) => current === "admin" ? current : current);
  }

  const sideBg = "#f1f0eb";

  return (
    <Layout style={{ minHeight: "100dvh", background: "#f7f7f4" }}>
      <Sider width={248} style={{ background: sideBg, borderRight: "1px solid #e3e2dc", position: "sticky", top: 0, height: "100dvh", overflow: "auto" }}>
        <div className="side-brand">
          <Avatar shape="square" size={30} style={{ background: "#141413", fontWeight: 700, fontFamily: "monospace", fontSize: 12, borderRadius: 8 }}>{"{}"}</Avatar>
          <div>
            <Typography.Text strong style={{ fontSize: 13.5, display: "block", lineHeight: 1.3 }}>Code Atlas</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 10.5 }}>证据优先的业务知识库</Typography.Text>
          </div>
        </div>
        <Button
          block
          icon={<Plus size={14} weight="bold" />}
          onClick={newConversation}
          style={{ margin: "2px 14px 8px", width: "calc(100% - 28px)", borderRadius: 10, fontWeight: 550, boxShadow: "0 1px 2px rgba(30,28,20,.05)" }}
        >
          开始新分析
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
          <div className="zone-label">最近分析</div>
          {runs.length === 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12, padding: "4px 18px", display: "block", lineHeight: 1.7 }}>
              提交第一个问题后，分析历史会出现在这里。
            </Typography.Text>
          )}
          {runs.slice(0, 12).map((run) => (
            <button key={run.id} className={`recent-item ${run.question === turns[0]?.question ? "on" : ""}`} title={run.question} onClick={() => doRestore(run)}>
              <span className={`rstate ${run.evidence_status?.toLowerCase() || ""}`} />
              <span className="rcopy">
                <span className="rq">{run.question}</span>
                <small>{intentLabel(run.intent)} · {formatTime(run.created_at)}</small>
              </span>
            </button>
          ))}
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
        {page === "agent" && (
          <AgentPage
            workspace={workspace}
            runs={runs}
            question={question}
            setQuestion={setQuestion}
            submit={submit}
            stopQuery={stopQuery}
            status={status}
            error={error}
            result={result}
            runDetail={runDetail}
            turns={turns}
            activeTurnId={activeTurnId}
            selectTurn={selectTurn}
            newConversation={newConversation}
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

function pendingCount() {
  return 0;
}

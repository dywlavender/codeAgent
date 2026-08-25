import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowLeft, ArrowRight, Books, BracketsCurly, CaretDown, CaretRight,
  ChatCircleDots, Check, CheckCircle, CircleNotch, ClockCounterClockwise,
  Code, Copy, Database, FileCode, FileText, GitBranch, MagnifyingGlass, Moon,
  Paperclip, Plus, Question, Rows, SidebarSimple, Sparkle, Sun, ThumbsDown,
  ThumbsUp, Warning, X,
} from "@phosphor-icons/react";
import "./design-tokens.css";
import "./styles.css";

const NAV = [
  ["agent", "问答 Agent", ChatCircleDots],
  ["runs", "运行记录", ClockCounterClockwise],
  ["knowledge-admin", "知识治理", CheckCircle],
  ["code", "Code Fact", BracketsCurly],
  ["business", "功能知识", Database],
  ["requirements", "Requirements", FileText],
];

const EXAMPLES = [
  "提款的时候为什么要校验 repayType？",
  "repayType 字段在哪里生成、读取和校验？",
  "申请阶段和提款阶段之间是什么关系？",
];

async function request(path, options) {
  const adminToken = sessionStorage.getItem("knowledgeAdminToken");
  const headers = { ...(options?.headers || {}) };
  if (adminToken && path.startsWith("/api/knowledge-admin/")) headers.Authorization = `Bearer ${adminToken}`;
  const response = await fetch(path, { ...(options || {}), headers });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401 && path.startsWith("/api/knowledge-admin/")) {
    sessionStorage.removeItem("knowledgeAdminToken");
  }
  if (!response.ok) throw new Error(body.error || `请求失败 (${response.status})`);
  return body;
}

function App() {
  const [page, setPage] = useState("agent");
  const [theme, setTheme] = useState(() => localStorage.getItem("theme") || "light");
  const [workspace, setWorkspace] = useState(null);
  const [runs, setRuns] = useState([]);
  const [result, setResult] = useState(null);
  const [runDetail, setRunDetail] = useState(null);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [mobilePane, setMobilePane] = useState("answer");
  const [railOpen, setRailOpen] = useState(true);
  const [activeTurnId, setActiveTurnId] = useState(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);

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
    setMobilePane("answer");
    try {
      const data = await request("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: normalized, conversationId, history }),
      });
      const detail = await request(`/api/query/${data.runId}`);
      setResult(data);
      setRunDetail(detail);
      setConversationId(data.conversationId || detail.conversationId || conversationId);
      setTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, status: "success", result: data, detail } : turn));
      setStatus("success");
      await refreshRuns();
    } catch (reason) {
      setError(reason.message);
      setTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, status: "error", error: reason.message } : turn));
      setStatus("error");
    }
  }

  async function openRun(run) {
    setPage("runs");
    setError("");
    try {
      setRunDetail(await request(`/api/query/${run.id}`));
    } catch (reason) {
      setError(reason.message);
    }
  }

  async function restoreRun(run) {
    setError("");
    setStatus("loading");
    try {
      const detail = await request(`/api/query/${run.id}`);
      setRunDetail(detail);
      setQuestion("");
      const restoredResult = {
        runId: detail.id, status: detail.status, intent: detail.intent,
        evidenceStatus: detail.evidence_status, iterations: detail.iterations,
        answer: detail.answer, evidence: detail.evidence,
        answerMode: detail.answerMode || detail.answer_mode || detail.state?.answerMode || detail.state?.answer_mode,
        resolvedQuestion: detail.resolvedQuestion || detail.resolved_question || detail.state?.resolvedQuestion || detail.state?.resolved_question || detail.state?.search_terms?.join(" "),
        suggestedFollowUps: detail.suggestedFollowUps || detail.suggested_follow_ups || detail.answer?.suggestedFollowUps || [],
        entities: detail.entities || detail.resolvedEntities || detail.state?.entities || detail.state?.resolved_entities || [],
        metrics: { sourceCoverage: [...new Set(detail.evidence.map((item) => item.sourceType))] },
      };
      setResult(restoredResult);
      setTurns([{ id: detail.id, question: detail.question, status: "success", result: restoredResult, detail }]);
      setConversationId(detail.conversationId || null);
      setActiveTurnId(detail.id);
      setMobilePane("answer");
      setStatus("success");
    } catch (reason) {
      setError(reason.message);
      setStatus("error");
    }
  }

  function newConversation() {
    setTurns([]);
    setResult(null);
    setRunDetail(null);
    setQuestion("");
    setConversationId(null);
    setError("");
    setStatus("idle");
    setActiveTurnId(null);
    setPage("agent");
  }

  function selectTurn(turn) {
    setActiveTurnId(turn.id);
    setResult(turn.result || null);
    setRunDetail(turn.detail || null);
  }

  return (
    <div className="app-shell">
      <aside className="nav-rail" aria-label="主导航">
        <button className="brand" onClick={() => setPage("agent")} aria-label="返回 Agent 工作台">
          <span className="brand-mark">CA</span>
          <span className="brand-copy"><strong>Code Atlas</strong><small>Evidence workspace</small></span>
        </button>
        <nav>
          {NAV.map(([id, label, Icon]) => <React.Fragment key={id}>
            {id === "knowledge-admin" && <span className="nav-divider" aria-hidden="true" />}
            <button className={page === id ? "active" : ""} onClick={() => setPage(id)} title={label} aria-current={page === id ? "page" : undefined}>
              <Icon size={19} weight={page === id ? "fill" : "regular"} />
              <span>{label}</span>
            </button>
          </React.Fragment>)}
        </nav>
        <button className="theme-toggle" onClick={() => setTheme(theme === "light" ? "dark" : "light")} title="切换主题">
          {theme === "light" ? <Moon size={20} /> : <Sun size={20} />}
          <span>主题</span>
        </button>
      </aside>

      <main className="product">
        <Topbar workspace={workspace} page={page} railOpen={railOpen} setRailOpen={setRailOpen} />
        {page === "agent" ? (
          <Workbench
            {...{ railOpen, runs, question, setQuestion, submit, status, error, result, runDetail, turns, activeTurnId, selectTurn, mobilePane, setMobilePane, openRun: restoreRun, newConversation }}
          />
        ) : page === "runs" ? (
          <RunsPage runs={runs} selected={runDetail} openRun={openRun} />
        ) : page === "knowledge-admin" ? (
          <KnowledgeAdminPage workspace={workspace} />
        ) : (
          <LibraryPage page={page} workspace={workspace} />
        )}
      </main>
    </div>
  );
}

const ADMIN_TABS = [
  ["pending", "待处理"],
  ["functions", "功能知识"],
  ["proposals", "变更审核"],
];

function KnowledgeAdminPage({ workspace }) {
  const [tab, setTab] = useState("pending");
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [comment, setComment] = useState("");
  const [generateOpen, setGenerateOpen] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState("");
  const [generateForm, setGenerateForm] = useState({ sourceType: "ADMIN_NOTE", sourceId: "", content: "", targetFunctionId: "" });
  const [adminToken, setAdminToken] = useState(() => sessionStorage.getItem("knowledgeAdminToken") || "");
  const pendingSelection = useRef(null);

  const load = async (nextTab = tab, nextQuery = query) => {
    setLoading(true);
    setError("");
    setSelected(null);
    try {
      const data = await request(`/api/knowledge-admin/${nextTab}?q=${encodeURIComponent(nextQuery)}`);
      const nextItems = adminItems(data, nextTab);
      setItems(nextItems);
      if (nextTab === "pending" && pendingSelection.current) {
        const generated = pendingSelection.current;
        pendingSelection.current = null;
        const id = adminId(generated);
        setSelected(nextItems.find((item) => adminId(item) === id) || generated);
        if (id) {
          try {
            const detail = await request(`/api/knowledge-admin/proposals/${encodeURIComponent(id)}`);
            setSelected(detail.item || detail.proposal || detail);
          } catch { /* generated proposal is enough to render */ }
        }
      }
    } catch (reason) {
      setItems([]);
      setError(reason.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (workspace?.adminAuthRequired && !sessionStorage.getItem("knowledgeAdminToken")) {
      setLoading(false);
      return;
    }
    load(tab, "");
  }, [tab, workspace?.adminAuthRequired]);

  function unlockAdmin(event) {
    event.preventDefault();
    if (!adminToken.trim()) return;
    sessionStorage.setItem("knowledgeAdminToken", adminToken.trim());
    load(tab, "");
  }

  async function selectItem(item) {
    setSelected(item);
    setComment("");
    if (tab === "functions") return;
    const id = adminId(item);
    if (!id) return;
    try {
      const detail = await request(`/api/knowledge-admin/proposals/${encodeURIComponent(id)}`);
      setSelected(detail.item || detail.proposal || detail);
    } catch {
      // List responses may already contain a complete proposal. Keep it usable.
    }
  }

  async function review(action) {
    const id = adminId(selected);
    if (!id || submitting) return;
    setSubmitting(action);
    setError("");
    try {
      await request(`/api/knowledge-admin/proposals/${encodeURIComponent(id)}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, comment: comment.trim() || undefined }),
      });
      setComment("");
      await load(tab, query);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setSubmitting("");
    }
  }

  async function generateProposal(event) {
    event.preventDefault();
    if (!generateForm.sourceId.trim() || !generateForm.content.trim() || generating) return;
    setGenerating(true);
    setGenerateError("");
    try {
      const data = await request("/api/knowledge-admin/proposals/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sourceType: generateForm.sourceType,
          sourceId: generateForm.sourceId.trim(),
          content: generateForm.content.trim(),
          targetFunctionId: generateForm.targetFunctionId.trim() || undefined,
        }),
      });
      pendingSelection.current = data.proposal || data.item || data;
      setGenerateForm({ sourceType: "ADMIN_NOTE", sourceId: "", content: "", targetFunctionId: "" });
      setGenerateOpen(false);
      setQuery("");
      if (tab === "pending") await load("pending", "");
      else setTab("pending");
    } catch (reason) {
      setGenerateError(reason.message);
    } finally {
      setGenerating(false);
    }
  }

  const description = tab === "pending"
    ? "集中处理代码、需求、文档和用户反馈触发的知识变化。"
    : tab === "functions"
      ? "按业务功能查看已发布知识，详细代码和原文作为证据按需展开。"
      : "对比知识变更前后内容，基于证据接受、驳回或暂缓提案。";

  return (
    <div className="management-page admin-page">
      <PageHeading title="知识治理" description="知识更新 Agent 负责发现变化并生成提案；管理员负责审核，问答 Agent 只读取已发布知识。" />
      {workspace?.adminAuthRequired && !sessionStorage.getItem("knowledgeAdminToken") ? (
        <form className="admin-unlock" onSubmit={unlockAdmin}>
          <div><strong>需要管理员凭证</strong><span>凭证只保存在当前浏览器会话中。</span></div>
          <input type="password" value={adminToken} onChange={(event) => setAdminToken(event.target.value)} placeholder="管理员凭证" autoComplete="current-password" />
          <button type="submit" disabled={!adminToken.trim()}>进入知识治理</button>
        </form>
      ) : <>
      <div className="admin-tabs-wrap">
        <div className="admin-tabs" role="tablist">{ADMIN_TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}</div>
        <button className="generate-trigger" onClick={() => { setGenerateOpen(!generateOpen); setGenerateError(""); }} aria-expanded={generateOpen}><Plus size={15} /> 发起知识更新</button>
      </div>
      {generateOpen && <KnowledgeUpdateForm form={generateForm} setForm={setGenerateForm} onSubmit={generateProposal} onCancel={() => setGenerateOpen(false)} submitting={generating} error={generateError} />}
      <div className="admin-toolbar">
        <div><strong>{ADMIN_TABS.find(([id]) => id === tab)?.[1]}</strong><span>{description}</span></div>
        <div className="admin-search"><MagnifyingGlass size={17} /><input aria-label="搜索知识治理内容" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => event.key === "Enter" && load()} placeholder="搜索功能、场景或变更内容" /><button onClick={() => load()}>搜索</button></div>
      </div>
      {error && <div className="admin-error"><Notice tone="danger" title="操作未完成" text={error} /></div>}
      {loading ? <AdminLoading /> : tab === "functions" ? (
        <FunctionKnowledgeView items={items} selected={selected} onSelect={selectItem} />
      ) : (
        <ProposalReviewView items={items} selected={selected} onSelect={selectItem} onReview={review} submitting={submitting} comment={comment} setComment={setComment} tab={tab} />
      )}
      </>}
    </div>
  );
}

const UPDATE_SOURCES = [
  ["ADMIN_NOTE", "管理员说明"],
  ["REQUIREMENT", "需求"],
  ["DOCUMENT", "文档"],
  ["USER_FEEDBACK", "用户反馈"],
  ["CODE_CHANGE", "代码变化"],
];

function KnowledgeUpdateForm({ form, setForm, onSubmit, onCancel, submitting, error }) {
  const update = (field) => (event) => setForm((current) => ({ ...current, [field]: event.target.value }));
  const sourceHint = ({
    CODE_CHANGE: "填写仓库 ID、变化记录 ID 或已索引文件路径",
    REQUIREMENT: "填写已导入的 Requirement ID",
    DOCUMENT: "填写文档名称或版本标识",
    USER_FEEDBACK: "填写反馈单号或问题标识",
    ADMIN_NOTE: "填写本次人工补充的稳定标识",
  })[form.sourceType];
  return <form className="knowledge-update-form" onSubmit={onSubmit}>
    <div className="update-form-heading"><div><strong>发起知识更新</strong><span>更新 Agent 会检索代码与已有知识，生成待审核提案，不会直接修改已发布内容。</span></div><button type="button" onClick={onCancel} aria-label="关闭发起知识更新表单"><X size={17} /></button></div>
    <div className="update-form-grid">
      <label><span>来源类型</span><select value={form.sourceType} onChange={update("sourceType")}>{UPDATE_SOURCES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label><span>来源标识</span><input required value={form.sourceId} onChange={update("sourceId")} placeholder={sourceHint} /></label>
      <label><span>目标功能 <small>可选</small></span><input value={form.targetFunctionId} onChange={update("targetFunctionId")} placeholder="已有功能 ID 或名称" /></label>
      <label className="update-content"><span>需要分析的内容</span><textarea required rows="4" value={form.content} onChange={update("content")} placeholder="说明新增或变化的业务事实。入口、调用链和代码证据由 Agent 自动检索。" /></label>
    </div>
    {error && <p className="form-error"><Warning size={15} /> {error}</p>}
    <div className="update-form-actions"><button type="button" onClick={onCancel}>取消</button><button className="primary" type="submit" disabled={submitting || !form.sourceId.trim() || !form.content.trim()}>{submitting ? <CircleNotch className="spin" size={16} /> : <Sparkle size={16} />} 生成更新提案</button></div>
  </form>;
}

function ProposalReviewView({ items, selected, onSelect, onReview, submitting, comment, setComment, tab }) {
  if (!items.length) return <EmptyAdmin title={tab === "pending" ? "当前没有待处理事项" : "没有匹配的变更提案"} text="代码事实会自动更新；只有可能改变业务含义的变化才会进入人工审核。" />;
  return <div className="governance-layout">
    <section className="governance-list" aria-label="知识变更列表">
      {items.map((item, index) => {
        const id = adminId(item) || index;
        return <button key={id} className={adminId(selected) === adminId(item) ? "active" : ""} onClick={() => onSelect(item)}>
          <span className={`proposal-kind ${proposalTone(item)}`}><GitBranch size={17} /></span>
          <span className="proposal-copy"><strong>{adminTitle(item)}</strong><small>{adminSummary(item)}</small><span>{sourceText(item)} · {formatTime(item.createdAt || item.created_at)}</span></span>
          <AdminStatus status={item.reviewStatus || item.review_status || item.status} />
        </button>;
      })}
    </section>
    <section className="proposal-inspector">
      {selected ? <ProposalDetail item={selected} comment={comment} setComment={setComment} onReview={onReview} submitting={submitting} /> : <EmptyAdmin title="选择一条变更" text="右侧会展示新旧知识差异、来源证据和影响范围。" compact />}
    </section>
  </div>;
}

function ProposalDetail({ item, comment, setComment, onReview, submitting }) {
  const before = item.before ?? item.previousContent ?? item.previous_content ?? item.diff?.before;
  const after = item.after ?? item.proposedContent ?? item.proposed_content ?? item.diff?.after;
  const evidence = item.evidence || item.evidences || item.sources || [];
  const impact = item.affectedFunctions || item.affected_functions || item.impact || [];
  const status = String(item.reviewStatus || item.review_status || item.status || "PENDING").toUpperCase();
  const actionable = ["PENDING", "PENDING_REVIEW", "PROPOSED", "DEFERRED", "CHANGES_REQUESTED"].includes(status);
  return <div className="proposal-detail">
    <header><div><small>{adminId(item)}</small><h2>{adminTitle(item)}</h2><p>{item.reason || item.summary || item.description || "知识更新 Agent 发现了可能影响业务含义的变化。"}</p></div><AdminStatus status={status} /></header>
    <section><h3>知识差异</h3><div className="diff-grid">
      <div className="diff-card before"><span>当前已发布</span><p>{contentText(before, "当前没有对应的已发布内容，可能是新增知识。")}</p></div>
      <div className="diff-card after"><span>建议更新为</span><p>{contentText(after, "提案没有提供可展示的结构化差异。")}</p></div>
    </div></section>
    <section><h3>来源证据</h3>{asList(evidence).length ? <div className="proposal-evidence">{asList(evidence).map((entry, index) => <article key={entry.id || entry.evidenceId || index}><span>{sourceLabel(entry.sourceType || entry.source_type || entry.type || "CODE")}</span><strong>{entry.title || entry.symbol || entry.path || entry.locator || entry.id || `证据 ${index + 1}`}</strong><p>{entry.content || entry.excerpt || entry.summary || entry.location || "已关联结构化证据引用"}</p></article>)}</div> : <EmptyInline text="该提案尚未返回可展示的证据。没有证据时不建议接受。" />}</section>
    <section><h3>影响范围</h3>{asList(impact).length ? <div className="impact-tags">{asList(impact).map((entry, index) => <span key={typeof entry === "string" ? entry : entry.id || index}>{typeof entry === "string" ? entry : entry.name || entry.title || entry.id}</span>)}</div> : <EmptyInline text="未发现其他功能受到影响。" />}</section>
    {actionable && <footer className="review-actions"><label htmlFor="review-comment">审核说明 <span>可选</span></label><textarea id="review-comment" rows="2" value={comment} onChange={(event) => setComment(event.target.value)} placeholder="补充接受、驳回或暂缓的原因" /><div><button className="defer" disabled={Boolean(submitting)} onClick={() => onReview("DEFER")}>暂缓</button><button className="reject" disabled={Boolean(submitting)} onClick={() => onReview("REJECT")}><X size={16} /> 驳回</button><button className="accept" disabled={Boolean(submitting)} onClick={() => onReview("ACCEPT")}>{submitting === "ACCEPT" ? <CircleNotch className="spin" size={16} /> : <Check size={16} />} 接受并发布</button></div></footer>}
  </div>;
}

function FunctionKnowledgeView({ items, selected, onSelect }) {
  if (!items.length) return <EmptyAdmin title="还没有已发布的功能知识" text="审核通过首个知识提案后，功能、场景、规则和证据会在这里汇总。" />;
  return <div className="governance-layout functions-layout">
    <section className="governance-list function-list">{items.map((item, index) => <button key={adminId(item) || index} className={adminId(selected) === adminId(item) ? "active" : ""} onClick={() => onSelect(item)}><span className="proposal-kind published"><Database size={17} /></span><span className="proposal-copy"><strong>{adminTitle(item)}</strong><small>{item.domain || item.businessDomain || item.business_domain || "未分配业务域"}</small><span>{asList(item.scenarios).length} 个场景 · {asList(item.rules).length} 条规则</span></span><CaretRight size={16} /></button>)}</section>
    <section className="proposal-inspector">{selected ? <FunctionDetail item={selected} /> : <EmptyAdmin title="选择一个业务功能" text="查看已发布摘要、入口、场景、规则和证据覆盖情况。" compact />}</section>
  </div>;
}

function FunctionDetail({ item }) {
  const entries = asList(item.entries || item.functionEntries || item.function_entries);
  const scenarios = asList(item.scenarios);
  const rules = asList(item.rules || item.businessRules || item.business_rules);
  const impacts = asList(item.dataImpacts || item.data_impacts);
  return <div className="proposal-detail function-detail"><header><div><small>{adminId(item)} · {item.version ? `V${item.version}` : "当前版本"}</small><h2>{adminTitle(item)}</h2><p>{item.summary || item.description || "暂无功能摘要。"}</p></div><AdminStatus status={item.status || "PUBLISHED"} /></header>
    <section><h3>功能入口</h3><KnowledgeChips items={entries} empty="暂未关联 Controller、消息消费者或批任务入口。" /></section>
    <section><h3>业务场景</h3><KnowledgeLines items={scenarios} empty="暂未沉淀业务场景。" /></section>
    <section><h3>业务规则</h3><KnowledgeLines items={rules} empty="暂未沉淀已确认业务规则。" /></section>
    <section><h3>数据影响</h3><KnowledgeLines items={impacts} empty="暂未沉淀业务级数据影响。" /></section>
    <section><h3>证据覆盖</h3><p className="coverage-copy">{item.evidenceCount ?? item.evidence_count ?? 0} 条有效证据 · 最近更新 {formatTime(item.updatedAt || item.updated_at) || "未知"}</p></section>
  </div>;
}

function KnowledgeChips({ items, empty }) { return items.length ? <div className="impact-tags">{items.map((item, index) => <span key={typeof item === "string" ? item : item.id || index}>{typeof item === "string" ? item : [item.type || item.entry_type, item.name || item.label || item.symbol || item.path || item.locator].filter(Boolean).join(" · ")}</span>)}</div> : <EmptyInline text={empty} />; }
function KnowledgeLines({ items, empty }) { return items.length ? <div className="knowledge-lines">{items.map((item, index) => <p key={typeof item === "string" ? item : item.id || index}>{typeof item === "string" ? item : item.statement || item.name || item.title || [item.object_name, item.operation, item.before_state && item.after_state ? `${item.before_state} → ${item.after_state}` : ""].filter(Boolean).join(" · ") || JSON.stringify(item)}</p>)}</div> : <EmptyInline text={empty} />; }
function EmptyAdmin({ title, text, compact = false }) { return <div className={`empty-admin ${compact ? "compact" : ""}`}><Rows size={27} /><h2>{title}</h2><p>{text}</p></div>; }
function AdminLoading() { return <div className="admin-loading"><div className="skeleton block" /><div className="skeleton block" /></div>; }
function AdminStatus({ status }) { const value = String(status || "PENDING").toUpperCase(); return <span className={`admin-status ${value.toLowerCase()}`}>{({ PENDING: "待审核", PENDING_REVIEW: "待审核", PROPOSED: "待审核", APPROVED: "已接受", ACCEPTED: "已接受", PUBLISHED: "已发布", REJECTED: "已驳回", DEFERRED: "已暂缓", CHANGES_REQUESTED: "待修改" })[value] || value}</span>; }
function adminItems(data, tab) { if (Array.isArray(data)) return data; return data?.items || data?.data || data?.[tab] || (tab === "proposals" ? data?.proposals : tab === "functions" ? data?.functions : data?.pending) || []; }
function adminId(item) { return item?.id || item?.proposalId || item?.proposal_id || item?.functionId || item?.function_id || ""; }
function adminTitle(item) { return item?.title || item?.functionName || item?.function_name || item?.targetName || item?.target_name || item?.name || "未命名知识变更"; }
function adminSummary(item) { return item?.summary || item?.reason || item?.changeType || item?.change_type || "等待管理员确认业务含义"; }
function sourceText(item) { return item?.triggerType || item?.trigger_type || item?.sourceType || item?.source_type || "知识更新 Agent"; }
function proposalTone(item) { const type = String(item?.changeType || item?.change_type || item?.triggerType || "").toUpperCase(); return type.includes("DELETE") || type.includes("STALE") ? "danger" : type.includes("UPDATE") || type.includes("MODIFY") ? "warning" : "new"; }
function asList(value) { return Array.isArray(value) ? value : value ? [value] : []; }
function contentText(value, fallback) { if (value === undefined || value === null || value === "") return fallback; if (typeof value === "string") return value; return value.statement || value.summary || value.content || JSON.stringify(value, null, 2); }

function Topbar({ workspace, page, railOpen, setRailOpen }) {
  const title = NAV.find(([id]) => id === page)?.[1] || "Agent";
  return (
    <header className="topbar">
      <div className="topbar-left">
        {page === "agent" && (
          <button className="icon-button" onClick={() => setRailOpen(!railOpen)} aria-label="切换会话栏">
            <SidebarSimple size={19} />
          </button>
        )}
        <span className="breadcrumb">{title}</span>
        <span className="slash">/</span>
        <strong>{workspace?.project || "载入项目中"}</strong>
      </div>
      <div className="workspace-health">
        <span className="status-mark" aria-hidden="true" />
        <span>知识源已连接</span>
        <span className="health-count">{workspace ? `${workspace.counts.facts} facts` : "..."}</span>
      </div>
    </header>
  );
}

function Workbench(props) {
  const { railOpen, runs, result, turns, activeTurnId, mobilePane, setMobilePane } = props;
  const activeTurn = turns.find((turn) => turn.id === activeTurnId);
  return (
    <div className={`workbench ${railOpen ? "with-history" : "without-history"}`}>
      <HistoryRail runs={runs} openRun={props.openRun} newConversation={props.newConversation} hidden={!railOpen} />
      <section className={`answer-pane ${mobilePane === "answer" ? "mobile-active" : ""}`}>
        <MobileTabs active={mobilePane} setActive={setMobilePane} counts={groupEvidence(result?.evidence)} />
        <div className="conversation-scroll" aria-live="polite">
          {turns.length ? <Conversation turns={turns} activeTurnId={activeTurnId} selectTurn={props.selectTurn} submit={props.submit} /> : <Welcome submit={props.submit} />}
        </div>
        <QuestionComposer {...props} />
      </section>
      <aside className={`evidence-pane ${mobilePane === "evidence" ? "mobile-active" : ""}`}>
        <MobileTabs active={mobilePane} setActive={setMobilePane} counts={groupEvidence(result?.evidence)} />
        <EvidencePanel result={result} loading={activeTurn?.status === "loading"} />
      </aside>
    </div>
  );
}

function HistoryRail({ runs, openRun, newConversation, hidden }) {
  if (hidden) return null;
  return (
    <aside className="history-rail">
      <div className="rail-title">
        <span>最近分析</span>
        <button aria-label="新建分析" onClick={newConversation}><Plus size={16} /></button>
      </div>
      <div className="history-list">
        {runs.length ? runs.map((run) => (
          <button key={run.id} onClick={() => openRun(run)}>
            <span className={`run-state ${run.evidence_status?.toLowerCase()}`} />
            <span className="history-copy">
              <strong>{run.question}</strong>
              <small>{intentLabel(run.intent)} · {formatTime(run.created_at)}</small>
            </span>
          </button>
        )) : <p className="muted empty-copy">提交第一个问题后，分析历史会出现在这里。</p>}
      </div>
    </aside>
  );
}

function QuestionComposer({ question, setQuestion, submit, status, error }) {
  return (
    <div className="chat-composer-wrap">
      <div className={`chat-composer ${error ? "has-error" : ""}`}>
        <textarea
          id="question"
          rows="1"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder="继续追问，或输入新的业务问题..."
        />
        <div className="composer-tools">
          <button className="composer-icon" type="button" aria-label="添加附件" title="附件能力暂未开放"><Paperclip size={18} /></button>
          <span className="source-scope"><Database size={15} /> 全部知识源 <CaretDown size={13} /></span>
          <button className="send-button" aria-label="发送问题" onClick={() => submit()} disabled={status === "loading" || !question.trim()}>
            {status === "loading" ? <CircleNotch className="spin" size={18} /> : <ArrowRight size={18} weight="bold" />}
          </button>
        </div>
      </div>
      {error && <p className="form-error"><Warning size={15} /> {error}</p>}
      <p className="composer-help">Enter 发送　Shift + Enter 换行</p>
    </div>
  );
}

function Welcome({ submit }) {
  return (
    <div className="welcome">
      <div className="welcome-mark"><GitBranch size={27} /></div>
      <h1>从证据开始分析</h1>
      <p>问题会同时检索代码事实、人工确认的业务知识和需求依据。没有证据的部分会明确留空。</p>
      <div className="example-list">
        {EXAMPLES.map((item) => <button key={item} onClick={() => submit(item)}><Question size={17} /> {item}<ArrowRight size={15} /></button>)}
      </div>
    </div>
  );
}

function Conversation({ turns, activeTurnId, selectTurn, submit }) {
  const endRef = useRef(null);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" }); }, [turns]);
  return <div className="conversation">{turns.map((turn) => (
    <div className={`conversation-turn ${activeTurnId === turn.id ? "active" : ""}`} key={turn.id} onClick={() => selectTurn(turn)}>
      <div className="user-message">
        <span className="message-author">你</span>
        <div className="user-bubble">{turn.question}</div>
      </div>
      <div className="agent-message">
        <div className="agent-avatar"><Sparkle size={17} weight="fill" /></div>
        <div className="agent-content">
          <strong className="message-author">Agent</strong>
          {turn.status === "loading" ? <AnswerLoading /> : turn.status === "error" ? (
            <Notice tone="danger" title="分析失败" text={turn.error} />
          ) : <><Answer result={turn.result} submit={submit} /><MessageActions detail={turn.detail} /></>}
        </div>
      </div>
    </div>
  ))}<span ref={endRef} /></div>;
}

function MessageActions({ detail }) {
  const [runOpen, setRunOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  async function sendFeedback(rating) {
    if (!detail?.id || feedback) return;
    try {
      await request(`/api/query/${encodeURIComponent(detail.id)}/feedback`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating }),
      });
      setFeedback(rating);
    } catch { /* feedback must not interrupt the answer */ }
  }
  async function copyAnswer() {
    const text = detail?.answer?.conclusion || "";
    if (text && navigator.clipboard) await navigator.clipboard.writeText(text);
  }
  return <div className="message-footer">
    <div className="message-actions">
      <button aria-label="复制回答" title="复制回答" onClick={copyAnswer}><Copy size={15} /></button>
      <button aria-label="回答有帮助" title="有帮助" className={feedback === "HELPFUL" ? "selected" : ""} onClick={() => sendFeedback("HELPFUL")}><ThumbsUp size={15} /></button>
      <button aria-label="回答没有帮助" title="没有帮助" className={feedback === "NOT_HELPFUL" ? "selected" : ""} onClick={() => sendFeedback("NOT_HELPFUL")}><ThumbsDown size={15} /></button>
      {detail && <button className="run-action" onClick={() => setRunOpen(!runOpen)} aria-expanded={runOpen}><GitBranch size={15} /> 查看运行轨迹 <CaretDown size={13} className={runOpen ? "rotate" : ""} /></button>}
    </div>
    {runOpen && detail && <RunTimeline detail={detail} />}
  </div>;
}

function Answer({ result, submit }) {
  const { answer } = result;
  const answerMode = result.answerMode || result.answer_mode || answer.answerMode || answer.answer_mode;
  const resolvedQuestion = result.resolvedQuestion || result.resolved_question || answer.resolvedQuestion || answer.resolved_question;
  const suggestedFollowUps = result.suggestedFollowUps || result.suggested_follow_ups || answer.suggestedFollowUps || answer.suggested_follow_ups || [];
  const codeFacts = answer.facts.filter((item) => item.sourceType === "CODE");
  const businessRules = answer.facts.filter((item) => item.sourceType === "BUSINESS");
  const requirementRules = answer.facts.filter((item) => item.sourceType === "REQUIREMENT");
  return (
    <article className="answer-document chat-answer">
      <div className="answer-meta">
        <StatusBadge status={result.evidenceStatus} />
        <span>{intentLabel(result.intent)}</span>
        {answerMode && <span>{answerModeLabel(answerMode)}</span>}
        <span>{result.iterations} 轮证据扩展</span>
      </div>
      {resolvedQuestion && <div className="resolved-question"><span>本轮理解</span><p>{resolvedQuestion}</p></div>}
      <AnswerSection title="结论" className="conclusion">
        <p>{answer.conclusion}</p>
      </AnswerSection>
      <AnswerSection title="业务流程">
        {answer.businessFlow?.length ? (
          <div className="flow-list">{answer.businessFlow.map((item, index) => <FlowStep key={index} item={item} index={index} />)}</div>
        ) : <EmptyInline text="现有证据未形成可确认的完整业务链路。" />}
      </AnswerSection>
      <AnswerSection title="已确认事实">
        {(businessRules.length || codeFacts.length || requirementRules.length) ? <FactList facts={[...codeFacts, ...businessRules, ...requirementRules]} /> : <EmptyInline text="当前没有已确认事实。" />}
      </AnswerSection>
      {answer.inferences?.length > 0 && <AnswerSection title="推断"><div className="compact-points">{answer.inferences.map((item, index) => <p key={index}>{item.statement || item}</p>)}</div></AnswerSection>}
      <AnswerSection title="待确认" className={(answer.conflicts.length || answer.unknowns.length) ? "attention" : ""}>
        {answer.conflicts.map((item, index) => <Notice key={`c-${index}`} tone="danger" title="证据冲突" text={item.reason || "不同来源给出了不一致的结论。"} />)}
        {answer.unknowns.map((item, index) => <Notice key={`u-${index}`} tone="warning" title="未确认" text={item} />)}
        {!answer.conflicts.length && !answer.unknowns.length && <Notice tone="success" title="证据闭合" text="当前回答没有遗留的证据缺口。" />}
      </AnswerSection>
      {suggestedFollowUps.length > 0 && <div className="suggested-followups">
        <span>继续追问</span>
        <div>{suggestedFollowUps.map((item, index) => {
          const text = typeof item === "string" ? item : item.question || item.text || item.label;
          return text ? <button key={`${text}-${index}`} onClick={(event) => { event.stopPropagation(); submit(text); }}>{text}<ArrowRight size={13} /></button> : null;
        })}</div>
      </div>}
    </article>
  );
}

function AnswerSection({ title, children, className = "" }) {
  return <section className={`answer-section ${className}`}><h2>{title}</h2><div>{children}</div></section>;
}

function FactList({ facts }) {
  return <div className="fact-list">{facts.map((fact, index) => (
    <div className="fact" key={`${fact.statement}-${index}`}>
      <span className={`source-bar ${fact.sourceType.toLowerCase()}`} />
      <div><p>{fact.statement}</p><small>{fact.sourceType} · {fact.evidenceIds.join(", ")}</small></div>
    </div>
  ))}</div>;
}

function FlowStep({ item, index }) {
  const text = item.statement || item.step || String(item);
  return <div className="flow-step"><span>{index + 1}</span><p>{text}</p></div>;
}

function Notice({ tone, title, text }) {
  const Icon = tone === "success" ? CheckCircle : tone === "danger" ? X : Warning;
  return <div className={`notice ${tone}`}><Icon size={18} weight="bold" /><div><strong>{title}</strong><p>{text}</p></div></div>;
}

function EmptyInline({ text }) { return <p className="empty-inline">{text}</p>; }

function EvidencePanel({ result, loading }) {
  const grouped = groupEvidence(result?.evidence);
  const [tab, setTab] = useState("CODE");
  const [expanded, setExpanded] = useState(null);
  const current = grouped[tab] || [];
  return (
    <div className="evidence-panel">
      <div className="evidence-heading">
        <div><h2>Evidence</h2><p>结论所依据的原始证据</p></div>
        {result && <span className="evidence-total">{result.evidence.length}</span>}
      </div>
      <div className="evidence-tabs" role="tablist">
        {["CODE", "BUSINESS", "REQUIREMENT"].map((name) => (
          <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)} role="tab">
            {sourceLabel(name)} <span>{grouped[name].length}</span>
          </button>
        ))}
      </div>
      <div className="evidence-list">
        {loading ? <EvidenceLoading /> : !result ? <EvidenceEmpty /> : current.length ? current.map((item) => (
          <EvidenceCard key={item.evidenceId} item={item} open={expanded === item.evidenceId} toggle={() => setExpanded(expanded === item.evidenceId ? null : item.evidenceId)} />
        )) : <EvidenceEmpty text={`本次回答没有使用${sourceLabel(tab)}证据。`} />}
      </div>
    </div>
  );
}

function EvidenceCard({ item, open, toggle }) {
  const location = formatLocation(item.location);
  const Icon = item.sourceType === "CODE" ? FileCode : item.sourceType === "BUSINESS" ? Database : FileText;
  return (
    <article className={`evidence-card ${open ? "open" : ""}`}>
      <button className="evidence-summary" onClick={toggle} aria-expanded={open}>
        <span className={`evidence-icon ${item.sourceType.toLowerCase()}`}><Icon size={17} /></span>
        <span className="evidence-copy"><strong>{evidenceTitle(item)}</strong><small>{location || item.sourceVersion}</small></span>
        {open ? <CaretDown size={15} /> : <CaretRight size={15} />}
      </button>
      {open && <div className="evidence-body">
        <div className="evidence-properties"><span>{item.status}</span><span>{item.evidenceId}</span></div>
        <pre>{item.content || "该 Evidence 仅包含结构化引用。"}</pre>
      </div>}
    </article>
  );
}

function RunDock({ detail }) {
  const [open, setOpen] = useState(false);
  const steps = useMemo(() => normalizeSteps(detail), [detail]);
  return (
    <section className={`run-dock ${open ? "open" : ""}`}>
      <button className="run-dock-trigger" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span><GitBranch size={17} /> Agent 过程</span>
        <span className="run-summary">{steps.length} 步 · {detail.iterations} 轮 · {detail.evidence_status}</span>
        <CaretDown size={16} className={open ? "rotate" : ""} />
      </button>
      {open && <div className="run-timeline">{steps.map((step, index) => <RunStep key={step.id || index} step={step} last={index === steps.length - 1} />)}</div>}
    </section>
  );
}

function RunTimeline({ detail }) {
  const steps = useMemo(() => normalizeSteps(detail), [detail]);
  return <div className="run-timeline inline-run">{steps.map((step, index) => <RunStep key={step.id || index} step={step} last={index === steps.length - 1} />)}</div>;
}

function RunStep({ step, last }) {
  return (
    <div className="run-step">
      <div className="step-axis"><Check size={12} weight="bold" />{!last && <span />}</div>
      <div className="step-content">
        <div><strong>{stepLabel(step.step_name)}</strong><small>{step.duration_ms?.toFixed?.(1) || step.duration_ms || 0} ms</small></div>
        <p>{stepSummary(step)}</p>
        {step.tools?.length > 0 && <div className="tool-calls">{step.tools.map((tool) => <span key={tool.id}>{tool.tool_name} <b>{tool.result_count}</b></span>)}</div>}
      </div>
    </div>
  );
}

function MobileTabs({ active, setActive, counts }) {
  return <div className="mobile-tabs"><button className={active === "answer" ? "active" : ""} onClick={() => setActive("answer")}>回答</button><button className={active === "evidence" ? "active" : ""} onClick={() => setActive("evidence")}>Evidence <span>{Object.values(counts).flat().length}</span></button></div>;
}

function RunsPage({ runs, selected, openRun }) {
  return (
    <div className="management-page runs-page">
      <PageHeading title="Agent Runs" description="查看步骤、工具调用、证据缺口与运行结果。这里不展示模型隐藏推理。" />
      <div className="runs-layout">
        <div className="run-table">
          {runs.length ? runs.map((run) => <button key={run.id} className={selected?.id === run.id ? "active" : ""} onClick={() => openRun(run)}>
            <StatusBadge status={run.evidence_status} />
            <span className="table-question"><strong>{run.question}</strong><small>{intentLabel(run.intent)} · {formatTime(run.created_at)}</small></span>
            <span>{run.iterations} 轮</span><CaretRight size={16} />
          </button>) : <EmptyLibrary title="还没有 Agent Run" text="从 Agent 工作台提交问题后，可以在这里回放运行过程。" />}
        </div>
        <div className="run-inspector">{selected ? <><div className="inspector-title"><small>{selected.id}</small><h2>{selected.question}</h2></div><div className="run-timeline expanded">{normalizeSteps(selected).map((step, index, all) => <RunStep key={step.id || index} step={step} last={index === all.length - 1} />)}</div></> : <EmptyLibrary title="选择一个 Run" text="左侧列表会保留问题、状态和意图。" />}</div>
      </div>
    </div>
  );
}

function LibraryPage({ page, workspace }) {
  const config = {
    code: { title: "Code Explorer", description: "通过 Symbol、字段活动和表列事实定位代码证据。", endpoint: "/api/code/search?q=repayType", icon: Code, count: workspace?.counts.symbols, noun: "Symbols" },
    business: { title: "功能知识", description: "查看已发布的业务功能、场景、规则与代码入口。", endpoint: "/api/functions?q=", icon: Database, count: workspace?.counts.businessKnowledge, noun: "Functions" },
    requirements: { title: "Requirements", description: "默认阅读 Digest、业务规则和关联，原文只在需要时打开。", endpoint: "/api/requirements?q=", icon: Books, count: workspace?.counts.requirements, noun: "Requirements" },
  }[page];
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState(page === "code" ? "repayType" : "");

  useEffect(() => {
    setLoading(true);
    request(config.endpoint).then((data) => setItems(data.items || [])).catch((reason) => setError(reason.message)).finally(() => setLoading(false));
  }, [page]);

  async function search() {
    const base = config.endpoint.split("?")[0];
    setLoading(true); setError("");
    try { setItems((await request(`${base}?q=${encodeURIComponent(query)}`)).items || []); }
    catch (reason) { setError(reason.message); }
    finally { setLoading(false); }
  }

  const Icon = config.icon;
  return (
    <div className="management-page library-page">
      <PageHeading title={config.title} description={config.description} />
      <div className="library-toolbar">
        <div className="search-box"><MagnifyingGlass size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => event.key === "Enter" && search()} aria-label={`搜索 ${config.title}`} placeholder={`搜索 ${config.title}`} /><button onClick={search}>搜索</button></div>
        <div className="library-count"><Icon size={19} /><strong>{config.count ?? "..."}</strong><span>{config.noun}</span></div>
      </div>
      {error ? <Notice tone="danger" title="加载失败" text={error} /> : loading ? <LibraryLoading /> : items.length ? <div className="library-results">{items.map((item, index) => <LibraryRow key={item.id || index} item={item} page={page} />)}</div> : <EmptyLibrary title="没有匹配结果" text="换一个字段、流程名称或业务对象再试。" />}
    </div>
  );
}

function LibraryRow({ item, page }) {
  const title = item.qualified_name || item.title || item.id;
  const subtitle = item.summary || item.statement || `${item.status || "ACTIVE"} · ${item.current_version ? `Version ${item.current_version}` : item.kind || ""}`;
  return <button className="library-row"><span className={`library-glyph ${page}`}>{page === "code" ? <FileCode size={19} /> : page === "business" ? <Database size={19} /> : <FileText size={19} />}</span><span><strong>{title}</strong><small>{subtitle}</small></span><CaretRight size={17} /></button>;
}

function PageHeading({ title, description }) { return <div className="page-heading"><div><h1>{title}</h1><p>{description}</p></div></div>; }
function EmptyLibrary({ title, text }) { return <div className="empty-library"><Rows size={28} /><h2>{title}</h2><p>{text}</p></div>; }

function AnswerLoading() { return <div className="answer-loading"><div className="skeleton wide" /><div className="skeleton line" /><div className="skeleton line short" /><div className="skeleton block" /><div className="skeleton block small" /></div>; }
function EvidenceLoading() { return <div className="evidence-loading">{[1, 2, 3].map((item) => <div className="skeleton evidence-skeleton" key={item} />)}</div>; }
function LibraryLoading() { return <div className="library-loading">{[1, 2, 3, 4].map((item) => <div className="skeleton row-skeleton" key={item} />)}</div>; }
function EvidenceEmpty({ text = "提交问题后，代码、业务知识和需求证据会在这里分组展示。" }) { return <div className="evidence-empty"><Sparkle size={25} /><p>{text}</p></div>; }

function StatusBadge({ status }) {
  const value = status || "INSUFFICIENT";
  const Icon = value === "SUFFICIENT" ? CheckCircle : value === "CONFLICT" ? Warning : Question;
  return <span className={`status-badge ${value.toLowerCase()}`}><Icon size={14} weight="fill" /> {statusLabel(value)}</span>;
}

function groupEvidence(evidence = []) {
  return evidence.reduce((groups, item) => {
    (groups[item.sourceType] ||= []).push(item);
    return groups;
  }, { CODE: [], BUSINESS: [], REQUIREMENT: [] });
}

function buildQueryHistory(turns) {
  return turns
    .filter((turn) => turn.status === "success" && turn.result)
    .slice(-4)
    .map((turn) => ({
      question: turn.question,
      resolvedQuestion: turn.result.resolvedQuestion || turn.result.resolved_question || turn.result.answer?.resolvedQuestion || turn.result.answer?.resolved_question || turn.question,
      entities: turn.result.entities || turn.result.resolvedEntities || turn.result.resolved_entities || turn.result.answer?.entities || stateEntities(turn.detail?.state),
    }));
}

function stateEntities(state = {}) {
  return [...new Set([
    ...(state.business_objects || state.businessObjects || []),
    ...(state.processes || []),
    ...(state.systems || []),
    ...(state.field_hints || state.fieldHints || []),
    ...(state.table_hints || state.tableHints || []),
    ...(state.code_hints || state.codeHints || []),
  ])].slice(0, 24);
}

function answerModeLabel(value) {
  const labels = {
    MODEL: "模型归纳",
    DIRECT: "直接回答",
    CONTEXTUAL: "上下文回答",
    CLARIFICATION: "需要澄清",
    EVIDENCE_ONLY: "证据回答",
    FALLBACK: "规则归纳",
  };
  return labels[String(value).toUpperCase()] || String(value);
}

function normalizeSteps(detail) {
  if (!detail?.steps) return [];
  return detail.steps.map((step) => ({ ...step, tools: detail.toolCalls?.filter((tool) => tool.step_id === step.id) || [] }));
}

function stepSummary(step) {
  try {
    const output = JSON.parse(step.output_summary_json || "{}");
    if (output.gaps !== undefined) return `${output.gaps} 个证据缺口`;
    if (output.calls !== undefined) return `${output.calls} 次工具调用`;
    if (output.count !== undefined) return `读取 ${output.count} 条证据`;
    if (output.intent) return `识别为 ${intentLabel(output.intent)}`;
    if (output.facts !== undefined) return `${output.facts} 条确定事实`;
  } catch { /* malformed historical summary */ }
  return step.evidence_count ? `累计 ${step.evidence_count} 条 Evidence` : `第 ${step.iteration} 轮`;
}

function evidenceTitle(item) {
  if (item.sourceType === "CODE") return item.symbol || item.sourceId;
  if (item.sourceType === "BUSINESS") return `${item.sourceId} 业务事实`;
  return `${item.sourceId} 需求证据`;
}
function formatLocation(value = {}) { return value.file ? `${value.file}:${value.startLine || ""}` : value.chunkId || value.knowledgeId || ""; }
function sourceLabel(name) { return ({ CODE: "代码", BUSINESS: "业务知识", REQUIREMENT: "需求" })[name] || name; }
function statusLabel(name) { return ({ SUFFICIENT: "证据充分", INSUFFICIENT: "证据不足", CONFLICT: "存在冲突" })[name] || name; }
function intentLabel(name) { return ({ BUSINESS_LOGIC: "业务逻辑", DATA_TRACE: "数据流转", RULE_REASON: "规则原因", CROSS_PROCESS: "跨流程关系" })[name] || name; }
function stepLabel(name) { return ({ UNDERSTAND: "理解问题", INITIAL_SEARCH: "三源初搜", INITIAL_SEARCH_TOOLS: "调用初搜工具", LOAD_SUMMARY: "加载摘要", EVALUATE: "评估证据", PLAN_EXPANSION: "规划补证据", EXPAND_EVIDENCE: "扩展证据", EXPAND_EVIDENCE_TOOLS: "调用扩展工具", READ_RAW_EVIDENCE: "读取原始证据", BUILD_ANSWER: "生成回答" })[name] || name; }
function formatTime(value) { if (!value) return ""; return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }

createRoot(document.getElementById("root")).render(<React.StrictMode><App /></React.StrictMode>);

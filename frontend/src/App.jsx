/* 应用壳：装配工程、管理员、问答状态，渲染侧栏、页面和全局弹窗。
 *
 * 页面内部状态由各自 feature 维护；这里只保留跨页面共享的当前页面、当前工程、
 * 工作区快照和会话历史，以及唯一的错误横幅。
 */

import React, { useEffect, useRef, useState } from "react";
import { Layout } from "antd";
import { request } from "./lib/api.js";
import { mergeConversations } from "./lib/query-state.js";
import { scopeLabel } from "./lib/scope.js";
import { AdminLockModal, ProjectRegisterModal } from "./app/AdminModals.jsx";
import { AppSidebar } from "./app/AppSidebar.jsx";
import { AppTopbar } from "./app/AppTopbar.jsx";
import { pageFromHash } from "./app/routes.js";
import { useAdminAccess } from "./app/useAdminAccess.js";
import { useProjects } from "./app/useProjects.js";
import { AgentPage } from "./features/query/AgentPage.jsx";
import { useQueryRun } from "./features/query/useQueryRun.js";
import { AstPage } from "./features/ast/AstPage.jsx";
import { EvaluationPage } from "./features/evaluation/EvaluationPage.jsx";
import { GraphPage } from "./features/graph/GraphPage.jsx";
import { KnowledgeAdminPage } from "./features/knowledge-admin/KnowledgeAdminPage.jsx";
import { LibraryPage } from "./features/library/LibraryPage.jsx";
import { SettingsPage } from "./features/settings/SettingsPage.jsx";

const { Content } = Layout;

export default function App() {
  const [page, setPage] = useState(pageFromHash);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 768);
  const [workspace, setWorkspace] = useState(null);
  const [error, setError] = useState("");
  const [runs, setRuns] = useState([]);
  const [nextCursor, setNextCursor] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const historyRequestRef = useRef(0);
  const adminRequired = Boolean(workspace?.adminAuthRequired);

  function navigate(id) {
    if (window.location.hash !== `#/${id}`) window.location.hash = `#/${id}`;
    else setPage(id);
    if (window.innerWidth < 768) setSidebarOpen(false);
  }

  async function refreshRuns(cursor = null) {
    const requestId = ++historyRequestRef.current;
    setHistoryLoading(true);
    try {
      const data = await request(`/api/conversations?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
      if (requestId !== historyRequestRef.current) return;
      setRuns((current) => (cursor ? mergeConversations(current, data.items || []) : data.items || []));
      setNextCursor(data.nextCursor || null);
    } finally {
      if (requestId === historyRequestRef.current) setHistoryLoading(false);
    }
  }

  function handleProjectChange(nextProjectId, { reset }) {
    admin.syncForProject(nextProjectId);
    if (!reset) return;
    setWorkspace(null);
    setRuns([]);
    setNextCursor(null);
    query.resetForProject(nextProjectId);
    navigate("agent");
  }

  const projects = useProjects({
    setError,
    isBusy: () => query.busy,
    onProjectChange: handleProjectChange,
  });

  const query = useQueryRun({
    projectId: projects.projectId,
    navigate,
    error,
    setError,
    onHistoryChanged: refreshRuns,
  });

  const admin = useAdminAccess({ projectId: projects.projectId, adminRequired });

  async function deleteConversation(run) {
    try {
      await request(`/api/conversations/${run.conversationId}`, { method: "DELETE" });
      if (run.conversationId === query.conversationId) query.newConversation();
      await refreshRuns().catch(() => {});
    } catch (reason) {
      setError(`删除失败：${reason.message}`);
    }
  }

  useEffect(() => {
    if (!projects.projectId) return undefined;
    Promise.all([request("/api/workspace"), refreshRuns()])
      .then(([space]) => {
        setWorkspace(space);
        const saved = sessionStorage.getItem(`queryConversationId:${projects.projectId}`);
        if (saved && pageFromHash() === "agent" && query.canAutoRestore()) {
          query.restore({ conversationId: saved });
        }
      })
      .catch((reason) => setError(reason.message));
    return undefined;
  }, [projects.projectId]);

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const syncSidebarForViewport = () => {
      if (window.innerWidth < 768) setSidebarOpen(false);
    };
    syncSidebarForViewport();
    window.addEventListener("resize", syncSidebarForViewport);
    return () => window.removeEventListener("resize", syncSidebarForViewport);
  }, []);

  const applications = workspace?.applications || [];
  const repositories = workspace?.repositories || [];
  // 单项目部署时不显示项目标签，避免每张卡片重复同一名字；出现多项目后自动点亮。
  const multiProject = new Set(runs.map((run) => run.workspaceId).filter(Boolean)).size > 1;
  const activeTurn = query.turns.find((turn) => turn.id === query.activeTurnId);
  const activeRunId = activeTurn ? (activeTurn.result?.runId || activeTurn.id) : null;

  return (
    <Layout className="app-layout">
      <AppSidebar
        page={page}
        navigate={navigate}
        sidebarOpen={sidebarOpen}
        setSidebarOpen={setSidebarOpen}
        projects={projects.projects}
        projectId={projects.projectId}
        projectsLoading={!projects.projects.length && !workspace}
        switchProject={projects.switchProject}
        onOpenProjectModal={projects.openModal}
        onNewConversation={query.newConversation}
        busy={query.busy}
        admin={{
          required: adminRequired,
          zoneVisible: admin.zoneVisible,
          openLock: () => admin.setOpen(true),
        }}
        history={{
          runs,
          nextCursor,
          loading: historyLoading,
          multiProject,
          currentWorkspaceId: workspace?.workspace?.id,
          scopeLabel: (value) => scopeLabel(value, applications, repositories),
          refresh: () => refreshRuns().catch((reason) => setError(reason.message)),
          loadMore: () => refreshRuns(nextCursor).catch((reason) => setError(reason.message)),
          remove: deleteConversation,
        }}
        query={{
          conversationId: query.conversationId,
          activeRunId,
          status: query.status,
          onOpen: query.restore,
        }}
      />

      <Content className="app-content" style={{ minWidth: 0 }}>
        {page === "agent" && (
          <AgentPage
            projectId={projects.projectId}
            workspace={workspace}
            runs={runs}
            question={query.question}
            setQuestion={query.setQuestion}
            submit={query.submit}
            stopQuery={query.stop}
            cancelling={query.cancelling}
            status={query.status}
            error={error}
            result={query.result}
            runDetail={query.runDetail}
            turns={query.turns}
            activeTurnId={query.activeTurnId}
            selectTurn={query.selectTurn}
            newConversation={query.newConversation}
            toggleSidebar={() => setSidebarOpen((open) => !open)}
            scope={query.scope}
            setScope={query.setScope}
            models={query.models}
            defaultModel={query.defaultModel}
            model={query.model}
            setModel={query.setModel}
          />
        )}
        {page !== "agent" && <AppTopbar page={page} toggleSidebar={() => setSidebarOpen((open) => !open)} />}
        {page === "library" && <LibraryPage workspace={workspace} projectId={projects.projectId} />}
        {page === "graph" && <GraphPage workspace={workspace} projectId={projects.projectId} />}
        {page === "evaluation" && <EvaluationPage projectId={projects.projectId} onRequireUnlock={() => admin.setOpen(true)} />}
        {page === "ast" && <AstPage projectId={projects.projectId} onRequireUnlock={() => admin.setOpen(true)} />}
        {page === "admin" && <KnowledgeAdminPage projectId={projects.projectId} onRequireUnlock={() => admin.setOpen(true)} />}
        {page === "settings" && (
          <SettingsPage
            workspace={workspace}
            projectId={projects.projectId}
            adminRequired={adminRequired}
            adminUnlocked={admin.unlocked}
            onOpenProjectModal={projects.openModal}
            onRequireUnlock={() => admin.setOpen(true)}
            onNavigate={navigate}
          />
        )}
      </Content>

      <AdminLockModal admin={admin} />
      <ProjectRegisterModal
        project={{
          open: projects.open,
          saving: projects.saving,
          form: projects.form,
          submit: projects.register,
          close: projects.closeModal,
          update: projects.updateForm,
        }}
      />
    </Layout>
  );
}

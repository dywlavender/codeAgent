/* 一轮问答的完整生命周期：发起、流式接收、取消、监控、恢复和历史回看。
 *
 * 状态只在这里维护，App 与页面组件通过返回值读写；这样切换工程、恢复历史
 * 和取消任务都不会散落在多个组件里。
 */

import { useEffect, useRef, useState } from "react";
import { RequestAborted, request, streamQuery } from "../../lib/api.js";
import { isActiveRun, turnFromRun, watchRun } from "../../lib/query-state.js";

const QUERY_MODE = "combined";

function storageKey(name, projectId) {
  return `${name}:${projectId || "default"}`;
}

export function useQueryRun({ projectId, navigate, error, setError, onHistoryChanged }) {
  const [turns, setTurns] = useState([]);
  const [result, setResult] = useState(null);
  const [runDetail, setRunDetail] = useState(null);
  const [question, setQuestion] = useState("");
  const [conversationId, setConversationId] = useState(null);
  const [status, setStatus] = useState("idle");
  const [activeTurnId, setActiveTurnId] = useState(null);
  const [scope, setScope] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const [models, setModels] = useState([]);
  const [defaultModel, setDefaultModel] = useState("");
  const [model, setModelState] = useState("");
  const queryAbortRef = useRef(null);
  const restoreRef = useRef(0);

  useEffect(() => {
    if (!projectId) return undefined;
    let cancelled = false;
    request("/api/pi/models")
      .then((data) => {
        if (cancelled) return;
        const items = Array.isArray(data.items) ? data.items : [];
        setModels(items);
        setDefaultModel(data.defaultModel || "");
        const stored = sessionStorage.getItem(storageKey("queryModel", projectId));
        const selected = [stored, data.configuredModel]
          .find((value) => value && items.some((item) => item.id === value));
        setModelState(selected || "");
      })
      .catch(() => {
        if (cancelled) return;
        setModels([]);
        setDefaultModel("");
      });
    return () => { cancelled = true; };
  }, [projectId]);

  useEffect(() => {
    if (conversationId && projectId) {
      sessionStorage.setItem(storageKey("queryConversationId", projectId), conversationId);
    }
  }, [conversationId, projectId]);

  useEffect(() => () => queryAbortRef.current?.abort(), []);

  function updateTurn(turnId, patch) {
    setTurns((current) => current.map((turn) => (
      turn.id === turnId ? { ...turn, ...(typeof patch === "function" ? patch(turn) : patch) } : turn
    )));
  }

  async function submit(nextQuestion = question) {
    const normalized = nextQuestion.trim();
    if (!normalized || status === "loading" || queryAbortRef.current) return;
    const turnId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setQuestion("");
    setTurns((current) => [...current, { id: turnId, question: normalized, mode: QUERY_MODE, status: "loading", events: [] }]);
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
        mode: QUERY_MODE,
        model: model || undefined,
      }, {
        signal: controller.signal,
        onRun: (value) => {
          if (queryAbortRef.current !== controller) return;
          controller.runId = value.runId;
          setConversationId(value.conversationId);
          updateTurn(turnId, { runId: value.runId, mode: value.mode || QUERY_MODE, astVersionId: value.astVersionId });
          if (controller.cancelRequested) requestCancellation(controller);
        },
        onEvent: (event) => queryAbortRef.current === controller
          && updateTurn(turnId, (turn) => ({ events: [...(turn.events || []), event] })),
      });
      if (queryAbortRef.current !== controller) return;
      const detail = data;
      setResult(data);
      setRunDetail(detail);
      setConversationId(data.conversationId || detail.conversationId || conversationId);
      updateTurn(turnId, (turn) => ({
        status: ["cancelled", "incomplete"].includes(data.status) ? data.status : "success",
        result: data,
        detail,
        events: data.events || turn.events || [],
      }));
      setStatus("success");
      await onHistoryChanged().catch(() => {});
    } catch (reason) {
      if (queryAbortRef.current !== controller) return;
      if (reason instanceof RequestAborted || reason?.name === "AbortError") {
        updateTurn(turnId, { status: "stopped" });
        setError("");
        setStatus("idle");
        return;
      }
      // Query failures belong to this turn, not the independent operation banner.
      updateTurn(turnId, { status: "error", error: reason.message });
      setStatus("error");
    } finally {
      if (queryAbortRef.current === controller) {
        queryAbortRef.current = null;
        setCancelling(false);
      }
    }
  }

  function stop() {
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

  function setModel(next) {
    const value = next || "";
    setModelState(value);
    if (projectId) {
      if (value) sessionStorage.setItem(storageKey("queryModel", projectId), value);
      else sessionStorage.removeItem(storageKey("queryModel", projectId));
    }
  }

  function clear() {
    setTurns([]);
    setResult(null);
    setRunDetail(null);
    setQuestion("");
    setConversationId(null);
    setScope(null);
    setActiveTurnId(null);
    setError("");
    setStatus("idle");
  }

  function newConversation() {
    if (queryAbortRef.current) return;
    restoreRef.current += 1;
    queryAbortRef.current?.abort();
    queryAbortRef.current = null;
    clear();
    sessionStorage.removeItem(storageKey("queryConversationId", projectId));
    navigate("agent");
  }

  function selectTurn(turn) {
    setActiveTurnId(turn.id);
    setResult(turn.result || null);
    setRunDetail(turn.detail || null);
  }

  /** 切换工程时清空会话并放弃在途请求，主问答始终组合使用可用资料。 */
  function resetForProject() {
    restoreRef.current += 1;
    queryAbortRef.current?.abort();
    queryAbortRef.current = null;
    clear();
    setCancelling(false);
    setModels([]);
    setDefaultModel("");
    setModelState("");
  }

  async function restore(run) {
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
          setTurns((current) => current.map((turn) => (turn.id === controller.runId ? turnFromRun(run) : turn)));
          setResult(run);
          setRunDetail(run);
          if (!isActiveRun(run)) {
            setStatus(run.status === "failed" ? "error" : "success");
            setError("");
          } else if (run.status === "cancelling") setCancelling(true);
        },
      });
      await onHistoryChanged().catch(() => {});
    } catch (reason) {
      if (controller.signal.aborted || queryAbortRef.current !== controller) return;
      setError(`任务进度连接中断，请重新打开当前会话恢复。${reason.message}`);
      setTurns((current) => current.map((turn) => (
        turn.id === controller.runId ? { ...turn, status: "disconnected" } : turn
      )));
      setStatus("error");
    } finally {
      if (queryAbortRef.current === controller) {
        queryAbortRef.current = null;
        setCancelling(false);
      }
    }
  }

  return {
    turns, result, runDetail, question, setQuestion, conversationId, status, error, setError,
    activeTurnId, scope, setScope, cancelling, models, defaultModel, model, setModel,
    /** 运行中或正在取消时，禁止切换工程和发起新对话。 */
    busy: status === "loading" || Boolean(queryAbortRef.current),
    canAutoRestore: () => restoreRef.current === 0 && !queryAbortRef.current,
    submit, stop, newConversation, selectTurn, restore, clear, resetForProject,
  };
}

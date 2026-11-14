/* 侧栏的会话历史：分页、恢复、删除。
 *
 * history 与 query 两个包由 App 装配，避免这里再直接触达请求层。
 */

import React from "react";
import { Check, MinusCircle, Trash, WarningCircle } from "@phosphor-icons/react";
import { Button, Popconfirm, Typography } from "antd";
import { formatRelative } from "../../lib/format.js";
import { isActiveRun, RUN_STATUS_LABEL } from "../../lib/query-state.js";

export function RecentConversations({ history, query }) {
  const {
    runs, nextCursor, loading, multiProject, currentWorkspaceId, scopeLabel,
    refresh, loadMore, remove,
  } = history;
  const { conversationId, activeRunId, status, onOpen } = query;

  return (
    <div className="recent-block">
      <div className="zone-label">
        最近会话
        <Button type="text" size="small" disabled={loading} onClick={refresh}>刷新</Button>
      </div>
      {runs.length === 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 12, padding: "4px 18px", display: "block", lineHeight: 1.7 }}>
          提交第一个问题后，分析历史会出现在这里。
        </Typography.Text>
      )}
      {runs.map((run) => {
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
            onClick={() => { if (status !== "loading") onOpen(run); }}
            onKeyDown={(event) => {
              if ((event.key === "Enter" || event.key === " ") && status !== "loading") {
                event.preventDefault();
                onOpen(run);
              }
            }}
          >
            <span className={`task-state ${statusKey || "unknown"}`} aria-hidden>
              {running ? <span className="activity-pulse" />
                : run.status === "failed" ? <WarningCircle size={12} weight="fill" />
                : run.status === "cancelled" ? <MinusCircle size={12} />
                : run.status === "incomplete" ? <WarningCircle size={12} />
                : <Check size={11} weight="bold" />}
            </span>
            <span className="task-copy">
              <span className="task-q">{run.question}</span>
              <small className="task-meta">
                {RUN_STATUS_LABEL[run.status] || run.status || "未知状态"} · {formatRelative(run.startedAt || run.created_at)}
                {run.modeLabel && ` · ${run.modeLabel}`}
                {run.astVersionId && ` · ${run.astVersionId}`}
                {scopeLabel(run.scope) && ` · ${scopeLabel(run.scope)}`}
                {showProject && ` · ${run.workspaceId}`}
              </small>
            </span>
            <Popconfirm
              title="删除这条会话？"
              description="将删除该会话的全部问答记录。"
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={(event) => { event?.stopPropagation(); remove(run); }}
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
      {nextCursor && (
        <Button type="text" block loading={loading} disabled={loading} onClick={loadMore}>
          加载更多会话
        </Button>
      )}
    </div>
  );
}

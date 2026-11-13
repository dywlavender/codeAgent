/* 管理员凭证只存在于当前浏览器会话，按工程分别保存。 */

import { useState } from "react";

function storageKey(projectId) {
  return projectId ? `knowledgeAdminToken:${projectId}` : "knowledgeAdminToken";
}

export function readAdminToken(projectId) {
  return sessionStorage.getItem(storageKey(projectId));
}

export function useAdminAccess({ projectId, adminRequired }) {
  const [unlocked, setUnlocked] = useState(() => Boolean(readAdminToken(projectId)));
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");

  function syncForProject(nextProjectId) {
    setUnlocked(Boolean(readAdminToken(nextProjectId)));
  }

  function unlock() {
    const value = token.trim();
    if (!value) return;
    sessionStorage.setItem(storageKey(projectId), value);
    setUnlocked(true);
    setOpen(false);
    setToken("");
  }

  function close() {
    setOpen(false);
    setToken("");
  }

  return {
    unlocked,
    open,
    token,
    setToken,
    setOpen,
    unlock,
    close,
    syncForProject,
    /** 未启用管理员口令时，知识治理入口始终可见。 */
    zoneVisible: !adminRequired || unlocked,
  };
}

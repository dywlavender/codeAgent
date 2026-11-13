/* 工程登记与切换。工程切换会重置工作区、会话历史和问答状态，由 App 回调处理。 */

import { useEffect, useState } from "react";
import { activeProjectId, request } from "../lib/api.js";
import { setProjectInUrl } from "./routes.js";

const EMPTY_FORM = { configPath: "", name: "", dataRoot: "" };

export function useProjects({ setError, isBusy, onProjectChange }) {
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(() => activeProjectId());
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);

  function apply(nextProjectId) {
    sessionStorage.setItem("activeProjectId", nextProjectId);
    setProjectInUrl(nextProjectId);
    setProjectId(nextProjectId);
  }

  useEffect(() => {
    request("/api/projects")
      .then((data) => {
        const items = data.items || [];
        setProjects(items);
        const saved = activeProjectId();
        const selected = items.find((item) => item.id === saved) || items[0];
        if (selected) {
          apply(selected.id);
          onProjectChange(selected.id, { reset: false });
        }
      })
      .catch((reason) => setError(reason.message));
  }, []);

  function switchProject(nextProjectId) {
    if (!nextProjectId || nextProjectId === projectId || isBusy()) return;
    apply(nextProjectId);
    onProjectChange(nextProjectId, { reset: true });
  }

  async function register() {
    const configPath = form.configPath.trim();
    if (!configPath || saving) return;
    setSaving(true);
    try {
      const data = await request("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          configPath,
          name: form.name.trim() || undefined,
          dataRoot: form.dataRoot.trim() || undefined,
        }),
      });
      setProjects(data.projects || []);
      setOpen(false);
      setForm(EMPTY_FORM);
      if (data.project?.id) switchProject(data.project.id);
    } catch (reason) {
      setError(`登记工程失败：${reason.message}`);
    } finally {
      setSaving(false);
    }
  }

  return {
    projects, projectId, open, saving, form,
    openModal: () => setOpen(true),
    closeModal: () => { if (!saving) setOpen(false); },
    updateForm: (field, value) => setForm((current) => ({ ...current, [field]: value })),
    switchProject, register,
  };
}

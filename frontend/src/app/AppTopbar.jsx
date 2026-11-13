/* 非对话页面共用的顶栏，只承载页面标题和侧栏开关。 */

import React from "react";
import { SidebarSimple } from "@phosphor-icons/react";
import { Button } from "antd";
import { PAGE_LABELS } from "./routes.js";

export function AppTopbar({ page, toggleSidebar }) {
  return (
    <header className="page-topbar">
      <Button type="text" icon={<SidebarSimple size={18} />} onClick={toggleSidebar} aria-label="切换侧栏" />
      <span className="page-topbar-title">{PAGE_LABELS[page] || "Code Atlas"}</span>
    </header>
  );
}

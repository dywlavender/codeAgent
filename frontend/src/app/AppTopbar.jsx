/* 非对话页面共用的顶栏：侧栏开关 + 面包屑（知识中心五页带分组路径）。 */

import React from "react";
import { SidebarSimple } from "@phosphor-icons/react";
import { Button } from "antd";
import { PAGE_LABELS, pageGroup } from "./routes.js";

export function AppTopbar({ page, toggleSidebar }) {
  const group = pageGroup(page);
  return (
    <header className="page-topbar">
      <Button type="text" icon={<SidebarSimple size={18} />} onClick={toggleSidebar} aria-label="切换侧栏" />
      <span className="page-topbar-title">
        {group ? <>Code Atlas <span className="topbar-crumb-sep">/</span> {group} <span className="topbar-crumb-sep">/</span> {PAGE_LABELS[page]}</>
          : PAGE_LABELS[page] || "Code Atlas"}
      </span>
    </header>
  );
}

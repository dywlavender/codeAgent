/* 侧栏：品牌、工程切换、页面导航、会话历史和管理员入口。
 *
 * 只管展示与导航，不直接发起业务请求；数据与动作由 App 通过 history/query 传入。
 */

import React from "react";
import {
  BookOpen, ChatCircleDots, Flask, Gear, Graph, Lock, Plus, ShieldCheck, SidebarSimple,
} from "@phosphor-icons/react";
import { Avatar, Badge, Button, Flex, Layout, Menu, Select, Typography } from "antd";
import { RecentConversations } from "../features/query/RecentConversations.jsx";

const { Sider } = Layout;

export function AppSidebar({
  page, navigate, sidebarOpen, setSidebarOpen,
  projects, projectId, switchProject, onOpenProjectModal, onNewConversation, busy, projectsLoading,
  admin, history, query,
}) {
  return (
    <>
      {sidebarOpen && <button className="sidebar-scrim" aria-label="关闭侧栏" onClick={() => setSidebarOpen(false)} />}
      <Sider width={224} collapsedWidth={0} collapsed={!sidebarOpen} trigger={null} className="app-sidebar">
        <div className="side-brand">
          <Avatar shape="square" size={30} style={{ background: "var(--color-text)", fontWeight: 700, fontFamily: "monospace", fontSize: 12, borderRadius: 8, color: "var(--color-canvas)" }}>{"{}"}</Avatar>
          <div>
            <Typography.Text strong style={{ fontSize: 13.5, display: "block", lineHeight: 1.3 }}>Code Atlas</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>代码调查工作台</Typography.Text>
          </div>
          <Button type="text" className="side-collapse" icon={<SidebarSimple size={18} />} onClick={() => setSidebarOpen(false)} aria-label="收起侧栏" />
        </div>
        <div className="project-switcher">
          <Typography.Text type="secondary" className="zone-label">当前工程</Typography.Text>
          <Flex gap={6}>
            <Select
              aria-label="选择工程"
              value={projectId || undefined}
              placeholder="选择工程"
              loading={projectsLoading}
              options={projects.map((item) => ({ label: item.name || item.id, value: item.id }))}
              onChange={switchProject}
              disabled={busy}
              style={{ width: "100%" }}
              size="small"
            />
            <Button type="text" size="small" icon={<Plus size={15} />} aria-label="登记工程" onClick={onOpenProjectModal} />
          </Flex>
        </div>
        <Button
          block
          icon={<Plus size={14} weight="bold" />}
          onClick={onNewConversation}
          disabled={query.status === "loading"}
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
            {
              type: "group",
              label: "知识中心",
              children: [
                { key: "library", icon: <BookOpen size={16.5} />, label: "知识总览" },
                ...(admin.zoneVisible ? [{ key: "guides", icon: <BookOpen size={16.5} />, label: "系统导航" }] : []),
                { key: "graph", icon: <Graph size={16.5} />, label: "知识图谱" },
                { key: "ast", icon: <Graph size={16.5} />, label: "AST 结构" },
                ...(admin.zoneVisible ? [{ key: "admin", icon: <ShieldCheck size={16.5} />, label: "业务知识" }] : []),
              ],
            },
            {
              type: "group",
              label: "其他",
              children: [
                { key: "agent", icon: <ChatCircleDots size={16.5} />, label: "代码问答" },
                { key: "evaluation", icon: <Flask size={16.5} />, label: "效果验证" },
                { key: "settings", icon: <Gear size={16.5} />, label: "工程设置" },
              ],
            },
          ]}
        />
        <RecentConversations history={history} query={query} />
        {!admin.zoneVisible && (
          <button className="admin-entry" onClick={admin.openLock}>
            <Lock size={14} /> 管理员入口
          </button>
        )}
        {admin.zoneVisible && admin.required && (
          <div className="admin-entry unlocked">
            <ShieldCheck size={14} /> 管理员会话
            <Badge status="success" style={{ marginLeft: "auto" }} />
          </div>
        )}
      </Sider>
    </>
  );
}

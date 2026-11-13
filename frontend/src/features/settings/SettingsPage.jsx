import React from "react";
import { FolderOpen, Lock, ShieldCheck } from "@phosphor-icons/react";
import { Button, Card, Flex, Tag, Typography } from "antd";

export function SettingsPage({
  workspace,
  projectId,
  adminRequired,
  adminUnlocked,
  onOpenProjectModal,
  onRequireUnlock,
  onNavigate,
}) {
  const repositories = workspace?.repositories || [];
  const applications = workspace?.applications || [];
  const sourceById = new Map((workspace?.sources || []).map((item) => [item.id, item]));
  const repositoryStatus = (item) => item.status
    || sourceById.get(item.id)?.status
    || sourceById.get(`repository:${item.id}`)?.status
    || "UNKNOWN";
  const readable = repositories.filter((item) => repositoryStatus(item) === "READABLE").length;

  return (
    <div className="page-wrap settings-page">
      <div className="settings-inner">
        <div className="page-heading compact-heading">
          <div>
            <span className="page-kicker">工程边界</span>
            <h1>工程设置</h1>
            <p>工程登记、资料目录和管理员会话在这里确认。切换工程会同时切换问答历史与资料版本。</p>
          </div>
          <Button onClick={onOpenProjectModal}>登记工程</Button>
        </div>

        <div className="settings-grid">
          <Card className="panel-card settings-card">
            <div className="settings-card-title"><FolderOpen size={18} /><h2>当前工程</h2></div>
            <Typography.Text className="settings-value">{workspace?.project || projectId || "尚未选择工程"}</Typography.Text>
            <Typography.Paragraph className="settings-help">
              工程登记只创建独立边界和数据目录，不会自动同步源码、导入业务知识或生成 AST。
            </Typography.Paragraph>
            <Button onClick={onOpenProjectModal}>切换或登记工程</Button>
          </Card>

          <Card className="panel-card settings-card">
            <div className="settings-card-title"><ShieldCheck size={18} /><h2>管理员会话</h2></div>
            <div className="settings-status">
              <Tag color={!adminRequired || adminUnlocked ? "success" : "default"}>
                {!adminRequired ? "无需口令" : adminUnlocked ? "已解锁" : "未解锁"}
              </Tag>
              <span>{adminRequired ? "当前浏览器会话有效" : "服务端未要求管理员口令"}</span>
            </div>
            {adminRequired && !adminUnlocked && <Button icon={<Lock size={14} />} onClick={onRequireUnlock}>验证管理员口令</Button>}
            {adminUnlocked && <Button onClick={() => onNavigate("admin")}>打开知识维护</Button>}
          </Card>
        </div>

        <Card className="panel-card settings-repositories">
          <div className="settings-card-title"><FolderOpen size={18} /><h2>资料目录</h2><span className="settings-count">{readable}/{repositories.length} 个仓库可读</span></div>
          <div className="settings-repo-list">
            {repositories.length ? repositories.map((item) => (
              <div className="settings-repo-row" key={item.id}>
                <div><b>{item.displayName || item.name || item.id}</b><small>{item.id}</small></div>
                <Tag color={repositoryStatus(item) === "READABLE" ? "success" : "warning"}>{repositoryStatus(item) === "READABLE" ? "可读" : repositoryStatus(item) === "UNKNOWN" ? "状态未知" : repositoryStatus(item)}</Tag>
              </div>
            )) : <Typography.Text type="secondary">当前工程没有可展示的仓库。</Typography.Text>}
          </div>
          <div className="settings-footnote">资料来源的路径、授权和异常处理请在“项目资料”中查看。AST 生成始终由用户手动触发。</div>
        </Card>

        <div className="settings-summary">
          <span>系统 {applications.length}</span>
          <span>仓库 {repositories.length}</span>
          <span>工程 ID <code>{projectId || "-"}</code></span>
        </div>
      </div>
    </div>
  );
}

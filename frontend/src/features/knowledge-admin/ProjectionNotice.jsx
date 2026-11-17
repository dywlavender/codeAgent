import React from "react";
import { Alert, Button } from "antd";

export const PROJECTION_STATUS = {
  empty: ["暂无文档", "default"],
  not_imported: ["未导入", "default"],
  synced: ["已同步", "success"],
  stale: ["待更新", "warning"],
  failed: ["生成失败", "error"],
};

export function projectionParser(value) {
  if (!value) return "";
  return value === "markdown" ? "Markdown 规则解析" : "Claude Code";
}

export function ProjectionNotice({ state, onOpen }) {
  const states = {
    not_imported: ["info", "业务文档尚未导入结构化视图"],
    stale: ["warning", "业务文档已变更，结构化视图待更新"],
    failed: ["error", "上次结构化提取失败"],
  };
  if (!states[state?.status]) return null;
  const [type, title] = states[state.status];
  return <Alert type={type} showIcon title={title} description={state.error}
    style={{ marginBottom: 12 }}
    action={onOpen && <Button size="small" onClick={onOpen}>查看维护入口</Button>} />;
}

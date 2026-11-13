/* 两个全局弹窗：管理员验证与工程登记。 */

import React from "react";
import { Lock } from "@phosphor-icons/react";
import { Flex, Input, Modal, Typography } from "antd";

export function AdminLockModal({ admin }) {
  return (
    <Modal
      open={admin.open}
      title={<Flex align="center" gap={8}><Lock size={16} /> 管理员验证</Flex>}
      okText="解锁"
      cancelText="取消"
      onCancel={admin.close}
      onOk={admin.unlock}
      okButtonProps={{ disabled: !admin.token.trim() }}
      width={400}
    >
      <Flex vertical gap={10} style={{ paddingTop: 6 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
          输入管理员口令后，侧栏会出现知识治理入口。凭证只保存在当前浏览器会话中。
        </Typography.Text>
        <Input.Password
          value={admin.token}
          onChange={(event) => admin.setToken(event.target.value)}
          placeholder="管理员口令"
          autoComplete="current-password"
          onPressEnter={admin.unlock}
        />
      </Flex>
    </Modal>
  );
}

export function ProjectRegisterModal({ project }) {
  return (
    <Modal
      open={project.open}
      title="登记工程"
      okText="登记并切换"
      cancelText="取消"
      confirmLoading={project.saving}
      okButtonProps={{ disabled: !project.form.configPath.trim() }}
      onCancel={project.close}
      onOk={project.submit}
      width={470}
    >
      <Flex vertical gap={10} style={{ paddingTop: 6 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
          配置路径由服务端读取。登记只创建独立工程数据目录，不自动同步仓库、导入业务补充知识或生成 AST。
        </Typography.Text>
        <Input
          value={project.form.configPath}
          onChange={(event) => project.update("configPath", event.target.value)}
          placeholder="服务器上的 project.config.json 路径"
        />
        <Input
          value={project.form.name}
          onChange={(event) => project.update("name", event.target.value)}
          placeholder="展示名称（可选）"
        />
        <Input
          value={project.form.dataRoot}
          onChange={(event) => project.update("dataRoot", event.target.value)}
          placeholder="工程数据目录（可选；已有历史库可显式填写）"
        />
      </Flex>
    </Modal>
  );
}

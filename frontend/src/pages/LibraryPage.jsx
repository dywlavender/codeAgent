import React, { useEffect, useState } from "react";
import { Alert, Button, Empty, Flex, Input, Segmented, Skeleton, Tag, Typography } from "antd";
import { request } from "../lib/api.js";

const TYPES = { repository: "代码仓库", baseline: "业务基线", requirements: "需求原文" };
const STATUS = {
  READABLE: ["green", "目录可读"], EMPTY: ["orange", "空目录"],
  MISSING: ["red", "目录不存在"], UNREADABLE: ["red", "目录无法读取"],
};
const PURPOSE = {
  repository: "用于核实当前实现、方法逻辑和跨仓库调用关系。",
  baseline: "用于理解业务含义、系统职责和调查入口。",
  requirements: "用于查阅需求原文，与当前代码实现对照。",
};

export function LibraryPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [type, setType] = useState("all");
  const [query, setQuery] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    request("/api/workspace").then((value) => { if (active) setData(value); })
      .catch((reason) => { if (active) setError(reason.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [revision]);
  const sources = data?.sources || [];
  const visible = sources.filter((item) => (type === "all" || item.kind === type)
    && (item.name + " " + item.path).toLowerCase().includes(query.trim().toLowerCase()));
  return (
    <div className="page-wrap" style={{ overflowY: "auto" }}>
      <div style={{ width: "100%", maxWidth: 1180, margin: "0 auto" }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>项目资料</Typography.Title>
        <Typography.Paragraph type="secondary">
          查看 Agent 的资料来源。问答直接读取这些目录中的文件，不依赖代码符号索引或结构化知识条目。
        </Typography.Paragraph>
        <Flex gap={12} wrap="wrap" align="center" style={{ marginBottom: 20 }}>
          <Segmented value={type} onChange={setType} options={[
            { value: "all", label: "全部" }, ...Object.entries(TYPES).map(([value, label]) => ({ value, label })),
          ]} />
          <Input aria-label="筛选资料名称或路径" placeholder="筛选名称或路径" allowClear
            value={query} onChange={(event) => setQuery(event.target.value)} style={{ maxWidth: 320 }} />
          <Button loading={loading} onClick={() => setRevision((value) => value + 1)}>刷新状态</Button>
        </Flex>
        {error && <Alert type="error" showIcon title="无法读取资料状态" description={error} />}
        {loading ? <Skeleton active paragraph={{ rows: 6 }} /> : !error && <>
          <Typography.Paragraph type="secondary">
            {data?.project} · {sources.length} 个来源。刷新只检查本地状态，不会拉取代码或导入知识。
          </Typography.Paragraph>
          {!visible.length ? <Empty description={sources.length ? "没有匹配的资料" : "尚未配置资料，请联系管理员配置项目来源。"} />
            : visible.map((item) => {
              const [color, label] = STATUS[item.status] || ["default", "状态未知"];
              return <section key={item.id} style={{ padding: "20px 0", borderTop: "1px solid #ebeae4" }}>
                <Flex gap={8} align="center" wrap="wrap">
                  <Typography.Text strong>{item.name}</Typography.Text>
                  <Typography.Text type="secondary">{TYPES[item.kind]}</Typography.Text>
                  <Tag color={color}>{label}</Tag>
                  <Tag color={item.authorizationConfigured ? "default" : "orange"}>
                    {item.authorizationConfigured ? "已配置目录授权" : "未配置目录授权"}
                  </Tag>
                </Flex>
                <Typography.Paragraph copyable style={{ margin: "10px 0", overflowWrap: "anywhere" }}>
                  {item.path}
                </Typography.Paragraph>
                <Typography.Text type="secondary">{PURPOSE[item.kind]}</Typography.Text>
                {item.status !== "READABLE" && <Typography.Paragraph style={{ margin: "8px 0 0" }}>
                  {item.status === "EMPTY" ? "目录中尚无资料，请补充文件后再使用。" : "请管理员检查项目配置、仓库同步结果或目录读取权限。"}
                </Typography.Paragraph>}
              </section>;
            })}
          <Typography.Paragraph type="secondary" style={{ marginTop: 24 }}>
            目录状态由后端检查；授权状态表示启动参数配置，不代表已经通过模型工具访问验证。
            结构化知识请在管理员的“业务知识维护”中查看。
          </Typography.Paragraph>
        </>}
      </div>
    </div>
  );
}

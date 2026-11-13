import React, { useEffect, useState } from "react";
import { Alert, Button, Drawer, Empty, Flex, Input, Segmented, Skeleton, Tag, Typography } from "antd";
import { request } from "../../lib/api.js";

const TYPES = {
  repository: "代码仓库", business_context: "业务补充知识", code_map: "自动代码地图",
  baseline: "旧业务基线", requirements: "需求原文",
};
const STATUS = {
  READABLE: ["green", "目录可读"], EMPTY: ["orange", "空目录"],
  MISSING: ["red", "目录不存在"], UNREADABLE: ["red", "目录无法读取"],
};
const PURPOSE = {
  repository: "用于核实当前实现、方法逻辑和跨仓库调用关系。",
  business_context: "只补充源码难以可靠推断的项目特有语义、边界和术语。",
  code_map: "用于定位目录、文件和符号，不作为业务结论或实现证据。",
  baseline: "旧工程兼容目录，迁移后请维护业务补充知识。",
  requirements: "用于查阅需求原文，与当前代码实现对照。",
};

export function LibraryPage({ projectId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [type, setType] = useState("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    request("/api/workspace").then((value) => { if (active) setData(value); })
      .catch((reason) => { if (active) setError(reason.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [revision, projectId]);
  const sources = data?.sources || [];
  const visible = sources.filter((item) => (type === "all" || item.kind === type)
    && (item.name + " " + item.path).toLowerCase().includes(query.trim().toLowerCase()));
  return (
    <div className="page-wrap" style={{ overflowY: "auto" }}>
      <div style={{ width: "100%", maxWidth: 1180, margin: "0 auto" }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>项目资料</Typography.Title>
        <Typography.Paragraph type="secondary">
          查看 Agent 的资料来源。问答按模式读取这些目录中的文件；自动代码地图只负责定位，当前实现仍以源码为准。
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
              return <button className="library-source-row" key={item.id} onClick={() => setSelected(item)}>
                <span className="library-source-copy"><b>{item.name}</b><small>{TYPES[item.kind]}</small></span>
                <span className="library-source-status"><Tag color={color}>{label}</Tag><small>{item.authorizationConfigured ? "可用" : "需授权"}</small></span>
              </button>;
            })}
          <Typography.Paragraph type="secondary" style={{ marginTop: 24 }}>
            目录状态由后端检查；授权状态表示启动参数配置，不代表已经通过模型工具访问验证。
            业务补充知识的结构化维护请在管理员的“业务补充知识维护”中查看。
          </Typography.Paragraph>
        </>}
      </div>
      <Drawer open={Boolean(selected)} title={selected?.name} onClose={() => setSelected(null)} width={420}>
        {selected && <div className="library-source-detail">
          <Flex gap={8} wrap="wrap" align="center"><Tag>{TYPES[selected.kind]}</Tag><Tag color={STATUS[selected.status]?.[0] || "default"}>{STATUS[selected.status]?.[1] || "状态未知"}</Tag></Flex>
          <dl><dt>资料用途</dt><dd>{PURPOSE[selected.kind]}</dd><dt>目录路径</dt><dd className="path-value">{selected.path}</dd><dt>目录授权</dt><dd>{selected.authorizationConfigured ? "已配置" : "未配置"}</dd></dl>
          {selected.status !== "READABLE" && <Alert type="warning" showIcon message={selected.status === "EMPTY" ? "目录中尚无资料" : "目录当前不可用"} description="请检查项目配置、仓库同步结果或目录读取权限。" />}
        </div>}
      </Drawer>
    </div>
  );
}

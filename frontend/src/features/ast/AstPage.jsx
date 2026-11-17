import React, { useEffect, useState } from "react";
import { Alert, Button, Card, Empty, Flex, Progress, Skeleton, Tabs, Tag } from "antd";
import { ArrowClockwise, Code } from "@phosphor-icons/react";
import { request } from "../../lib/api.js";
import { AstGraph } from "./AstGraph.jsx";

export const AST_STATUS = {
  missing: ["未生成", "default"],
  generating: ["生成中", "processing"],
  available: ["可用", "success"],
  stale: ["待更新", "warning"],
  failed: ["生成失败", "error"],
  unsupported: ["需重新生成", "warning"],
};

export function AstPage({ projectId, onRequireUnlock }) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [generating, setGenerating] = useState(false);
  const [tab, setTab] = useState("index");

  async function load(signal) {
    const value = await request("/api/ast", {}, signal);
    if (!signal?.aborted) setStatus(value);
  }

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).catch((reason) => {
      if (!controller.signal.aborted) setError(reason.message);
    });
    return () => controller.abort();
  }, [projectId]);

  useEffect(() => {
    if (status?.status !== "generating") return undefined;
    const controller = new AbortController();
    const poll = setInterval(() => load(controller.signal).catch((reason) => {
      if (!controller.signal.aborted) setError(reason.message);
    }), 1800);
    return () => { controller.abort(); clearInterval(poll); };
  }, [status?.status, projectId]);

  async function refresh() {
    setError("");
    try {
      await load();
    } catch (reason) {
      setError(reason.message);
    }
  }

  async function generate() {
    setGenerating(true);
    setError("");
    try {
      await request("/api/ast/generate", { method: "POST", body: "{}" });
      await load();
    } catch (reason) {
      if (reason.status === 401) onRequireUnlock?.();
      setError(reason.message);
    } finally {
      setGenerating(false);
    }
  }

  const current = status?.current;
  const [label, color] = AST_STATUS[status?.status] || AST_STATUS.missing;
  const graph = status?.status === "available"
    ? <AstGraph key={status.currentVersionId} versionId={status.currentVersionId}
        repositories={current?.sourceRepositories || []} onInvalidated={refresh} />
    : <Empty description="生成可用的结构索引后即可查询符号及相邻关系。"
        image={Empty.PRESENTED_IMAGE_SIMPLE}>
        <Button onClick={() => setTab("index")}>查看索引状态</Button>
      </Empty>;

  return <div className="page-wrap ast-page" style={{ overflowY: "auto" }}>
    <Flex justify="space-between" align="center" wrap gap={8} style={{ margin: "0 auto" }}>
      <div className="page-heading compact-heading">
        <div><h1>AST 结构</h1><p>按符号查找调用、继承与实现，点击源码位置核对实际代码。</p></div>
      </div>
      <Flex gap={8}>
        <Button icon={<ArrowClockwise size={15} />} onClick={refresh}>刷新状态</Button>
        <Button type="primary" icon={<Code size={15} />} onClick={generate}
          loading={generating || status?.status === "generating"}>
          {status?.currentVersionId ? "重新生成" : "生成 AST"}
        </Button>
      </Flex>
    </Flex>
    {error && <Alert type="error" showIcon title={error} style={{ margin: "12px auto" }} />}
    <Tabs style={{ margin: "0 auto" }} activeKey={tab} onChange={setTab} items={[
      { key: "index", label: "索引管理", children: status
        ? <div className="ast-grid">
            <Card className="panel-card ast-status-card">
              <Flex align="center" gap={8} className="section-title"><h2>当前索引</h2><Tag color={color}>{label}</Tag></Flex>
              {status.status === "generating" && <Progress percent={status.progress || 0} status="active" />}
              {status.error && <Alert type={status.status === "failed" ? "error" : "warning"}
                showIcon title={status.error} />}
              {status.status === "missing" && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未生成结构索引" />}
              {current && <IndexFacts current={current} />}
              {status.status === "stale" && <Alert style={{ marginTop: 12 }} type="warning" showIcon
                title="源码已变化，请重新生成索引后查询关系。" />}
            </Card>
            <Card className="panel-card ast-scope-card">
              <div className="section-title"><h2>覆盖仓库</h2></div>
              <div className="repo-list">{current?.sourceRepositories?.length
                ? current.sourceRepositories.map((repo) => <div className="repo-row" key={repo.id}>
                    <Code size={14} /><span>{repo.id}</span><small title={repo.path}>{repo.path}</small>
                  </div>)
                : <span className="muted-text">生成后显示所用源码仓库</span>}</div>
              <p className="panel-help">Java 使用 tree-sitter。Web 符号和调用线索来自现有模式解析器。</p>
            </Card>
          </div>
        : <Skeleton active /> },
      { key: "graph", label: "代码关系图", children: graph },
    ]} />
  </div>;
}

function IndexFacts({ current }) {
  const stats = current.statistics || {};
  const fields = [
    ["版本", current.versionId], ["生成时间", current.generatedAt && new Date(current.generatedAt).toLocaleString("zh-CN")],
    ["代码文件", stats.fileCount], ["符号", stats.symbolCount],
    ["类及类型", stats.typeCount], ["方法及函数", stats.callableCount],
    ["关系索引条目", stats.relationCount],
  ];
  return <div className="ast-facts">{fields.map(([name, value]) => <div key={name}><span>{name}</span><b>{value ?? "—"}</b></div>)}</div>;
}

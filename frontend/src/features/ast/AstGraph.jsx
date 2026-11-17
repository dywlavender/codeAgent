import React, { useEffect, useRef, useState } from "react";
import { Alert, Button, Drawer, Empty, Flex, Input, Select, Skeleton } from "antd";
import { request } from "../../lib/api.js";
import { CallChainCanvas } from "../graph/CallChainView.jsx";

const RELATIONS = ["CALLS", "INHERITS", "IMPLEMENTS"];
const BOTH = ["incoming", "outgoing"];
const CLUE = { ambiguous: "歧义", unresolved: "未解析", raw: "原始调用" };

const graphNode = (symbol, column) => ({
  id: symbol.symbolId, label: symbol.qualifiedName, displayName: symbol.name,
  subtitle: symbol.qualifiedName, type: symbol.kind, state: "current",
  application: symbol.repositoryId, column,
  location: { path: symbol.path, start: symbol.lineStart },
});

function expandGraph(previous, node, groups) {
  const nodes = new Map((previous?.nodes || [node]).map((item) => [item.id, item]));
  const edges = new Map((previous?.edges || []).map((edge) => [edge.id, edge]));
  const clues = new Map((previous?.clues || []).map((edge) => [edge.relationId, edge]));
  for (const [direction, result] of groups) {
    for (const edge of result.relations) {
      const adjacent = direction === "incoming" ? edge.from : edge.to;
      if (!nodes.has(adjacent.symbolId)) {
        nodes.set(adjacent.symbolId, graphNode(adjacent, node.column + (direction === "incoming" ? -1 : 1)));
      }
      edges.set(edge.relationId, {
        id: edge.relationId, source: edge.from.symbolId, target: edge.to.symbolId,
        label: edge.relationType, kind: edge.relationType, state: "current",
      });
    }
    for (const edge of [...result.unresolved, ...result.rawCalls]) clues.set(edge.relationId, edge);
  }
  return {
    rootId: previous?.rootId || node.id, nodes: [...nodes.values()], edges: [...edges.values()],
    clues: [...clues.values()], truncated: previous?.truncated || groups.some(([, result]) => result.truncated),
  };
}

export function AstGraph({ versionId, repositories, onInvalidated }) {
  const [repositoryId, setRepositoryId] = useState("");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);
  const [relationType, setRelationType] = useState("");
  const [graph, setGraph] = useState(null);
  const [focused, setFocused] = useState(null);
  const [source, setSource] = useState(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(null);
  useEffect(() => () => active.current?.abort(), []);

  function load(path, params, signal) {
    const search = new URLSearchParams({ versionId, ...params });
    if (repositoryId) search.set("repositoryId", repositoryId);
    return request(`/api/ast/symbols${path}?${search}`, {}, signal);
  }

  async function run(work) {
    const controller = new AbortController();
    active.current?.abort();
    active.current = controller;
    setPending(true);
    setError("");
    try {
      await work(controller.signal);
    } catch (reason) {
      if (controller.signal.aborted) return;
      setError(reason.message);
      if (reason.status === 409) {
        setGraph(null);
        setResult(null);
        onInvalidated?.();
      }
    } finally {
      if (!controller.signal.aborted) setPending(false);
    }
  }

  function search() {
    if (query.trim()) run(async (signal) => {
      const data = await load("", { query: query.trim() }, signal);
      if (!signal.aborted) setResult(data);
    });
  }

  function explore(node, directions, type = relationType, reset = false) {
    run(async (signal) => {
      const path = `/${encodeURIComponent(node.id)}/relations`;
      const groups = await Promise.all(directions.map(async (direction) => [
        direction, await load(path, { direction, ...(type ? { relationType: type } : {}) }, signal),
      ]));
      if (!signal.aborted) {
        setGraph((previous) => expandGraph(reset ? null : previous, node, groups));
        setFocused(node.id);
      }
    });
  }

  function openSource(symbol, relationId) {
    run(async (signal) => {
      const data = await load(`/${encodeURIComponent(symbol)}/source`, relationId ? { relationId } : {}, signal);
      if (!signal.aborted) setSource(data);
    });
  }

  const node = graph?.nodes.find((item) => item.id === focused);
  const clues = graph?.clues.filter((edge) => edge.from.symbolId === focused) || [];
  return <div className="call-chain-view">
    <Flex wrap gap={8}>
      <Select aria-label="仓库筛选" style={{ minWidth: 165 }} value={repositoryId} disabled={pending}
        options={[{ value: "", label: "全部仓库" }, ...repositories.map((repo) => ({ value: repo.id, label: repo.id }))]}
        onChange={(value) => { setRepositoryId(value); setGraph(null); setResult(null); setError(""); }} />
      <Input.Search aria-label="符号名称" placeholder="类名、方法名或全限定名" value={query} enterButton="搜索"
        style={{ flex: "1 1 260px" }} loading={pending} onSearch={search}
        onChange={(event) => setQuery(event.target.value)} />
      <Select aria-label="关系类型" style={{ minWidth: 145 }} value={relationType} disabled={pending}
        options={[{ value: "", label: "全部关系" }, ...RELATIONS.map((x) => ({ value: x, label: x }))]}
        onChange={(value) => {
          setRelationType(value);
          if (node) explore({ ...node, column: 0 }, BOTH, value, true);
        }} />
    </Flex>
    {error && <Alert type="error" showIcon title={error} />}
    {result && <Select aria-label="搜索结果" placeholder="选择一个符号查看关系"
      disabled={pending} options={result.symbols.map((item) => ({
        value: item.symbolId, label: `${item.qualifiedName} · ${item.repositoryId}:${item.lineStart}`,
      }))}
      notFoundContent="未找到符号" onChange={(id) => {
        const symbol = result.symbols.find((item) => item.symbolId === id);
        explore(graphNode(symbol, 0), BOTH, relationType, true);
      }} />}
    {node && <Flex gap={8} wrap align="center">
      <code>{node.label}</code>
      <Button disabled={pending} onClick={() => explore(node, ["incoming"])}>展开上游</Button>
      <Button disabled={pending} onClick={() => explore(node, ["outgoing"])}>展开下游</Button>
      <Button disabled={pending} onClick={() => openSource(node.id)}>查看源码</Button>
    </Flex>}
    {graph
      ? <CallChainCanvas chain={graph} graphLabel="AST 代码关系图" selection={{ id: focused }}
          onSelect={(item) => {
            if (pending) return;
            if (item.kind === "node") setFocused(item.id);
            else {
              const edge = graph.edges.find((value) => value.id === item.id);
              openSource(edge.source, edge.id);
            }
          }} />
      : pending ? <Skeleton active /> : <Empty description="搜索并选择一个符号开始查看关系" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
    {graph?.truncated && <p className="call-chain-caption">部分关系已按单次结果上限截断。</p>}
    {clues.length > 0 && <details className="call-chain-version">
      <summary>未连线的调用和关系线索（{clues.length}）</summary>
      <Flex gap={4} wrap>{clues.map((edge) => <Button size="small" key={edge.relationId}
        disabled={pending} onClick={() => openSource(edge.from.symbolId, edge.relationId)}>
        {CLUE[edge.status]} · {edge.targetName} · {edge.location.lineStart}
      </Button>)}</Flex>
    </details>}
    <Drawer open={Boolean(source)} onClose={() => setSource(null)} title="源码位置" size={640}>
      {source && <section className="call-chain-source">
        <div className="call-chain-source-path">{source.path}:{source.focusLine}</div>
        <pre><code>{source.content.split("\n").map((line, i) => {
          const number = source.lineStart + i;
          return number <= source.lineEnd
            ? <React.Fragment key={number}>{String(number).padStart(4)}  {line}{"\n"}</React.Fragment>
            : null;
        })}</code></pre>
        {source.truncated && <p>源码片段已截断。</p>}
      </section>}
    </Drawer>
  </div>;
}

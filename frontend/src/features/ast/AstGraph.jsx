import React, { useEffect, useRef, useState } from "react";
import { Button, Empty, Flex, Input, Select, Skeleton, Splitter } from "antd";
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

/* 未解析/原始调用以虚线“线索”节点进入画布：点击仍打开调用处源码，但不推断目标实现。 */
function clueNode(node, edge) {
  return {
    id: `clue:${edge.relationId}`,
    label: edge.targetName || "未知目标", displayName: edge.targetName,
    subtitle: CLUE[edge.status] || "线索", type: "UNRESOLVED", state: "unresolved",
    application: edge.from.repositoryId, column: node.column + 1,
    location: { path: edge.location?.path || edge.from.path, start: edge.location?.lineStart ?? edge.from.lineStart },
  };
}

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
    for (const edge of [...result.unresolved, ...result.rawCalls]) {
      clues.set(edge.relationId, edge);
      if (edge.from.symbolId && !nodes.has(`clue:${edge.relationId}`)) {
        nodes.set(`clue:${edge.relationId}`, clueNode(node, edge));
        edges.set(`clue:${edge.relationId}`, {
          id: `clue:${edge.relationId}`, source: edge.from.symbolId, target: `clue:${edge.relationId}`,
          label: CLUE[edge.status] || "线索", kind: "UNRESOLVED", state: "unresolved",
          relationId: edge.relationId,
        });
      }
    }
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
  const clueCount = graph?.clues.length || 0;
  return <Splitter className="ast-explorer">
    <Splitter.Panel defaultSize="23%" min="18%" max="34%">
      <div className="ast-explorer-side">
        <Select aria-label="仓库筛选" style={{ width: "100%" }} value={repositoryId} disabled={pending}
          options={[{ value: "", label: "全部仓库" }, ...repositories.map((repo) => ({ value: repo.id, label: repo.id }))]}
          onChange={(value) => { setRepositoryId(value); setGraph(null); setResult(null); setError(""); }} />
        <Input.Search aria-label="符号名称" placeholder="类名、方法名或全限定名" value={query} enterButton="搜索"
          loading={pending} onSearch={search}
          onChange={(event) => setQuery(event.target.value)} />
        <Select aria-label="关系类型" style={{ width: "100%" }} value={relationType} disabled={pending}
          options={[{ value: "", label: "全部关系" }, ...RELATIONS.map((x) => ({ value: x, label: x }))]}
          onChange={(value) => {
            setRelationType(value);
            if (node && !node.id.startsWith("clue:")) explore({ ...node, column: 0 }, BOTH, value, true);
          }} />
        <div className="ast-symbol-list" aria-label="搜索结果">
          {result?.symbols?.length
            ? result.symbols.map((item) => <button key={item.symbolId}
                aria-label={`${item.qualifiedName} · ${item.repositoryId}:${item.lineStart}`}
                className={`r-row ast-symbol-row ${item.symbolId === node?.id ? "on" : ""}`}
                onClick={() => explore(graphNode(item, 0), BOTH, relationType, true)}>
                <span className="r-glyph">⌘</span>
                <span className="r-copy"><b>{item.qualifiedName}</b><small>{item.repositoryId}:{item.lineStart}</small></span>
              </button>)
            : result ? <p className="ast-side-hint">未找到匹配符号</p>
              : <p className="ast-side-hint">搜索符号开始查看关系</p>}
        </div>
        {error && <p className="ast-side-error">{error}</p>}
      </div>
    </Splitter.Panel>
    <Splitter.Panel>
      <div className="ast-explorer-main">
        {node && !node.id.startsWith("clue:") && <Flex gap={8} wrap align="center" className="ast-explorer-toolbar">
          <code>{node.label}</code>
          <Button disabled={pending} onClick={() => explore(node, ["incoming"])}>展开上游</Button>
          <Button disabled={pending} onClick={() => explore(node, ["outgoing"])}>展开下游</Button>
          <Button disabled={pending} onClick={() => openSource(node.id)}>查看源码</Button>
        </Flex>}
        {graph
          ? <CallChainCanvas chain={graph} graphLabel="AST 代码关系图" selection={{ id: focused }}
              onSelect={(item) => {
                if (pending) return;
                if (item.kind === "node") {
                  if (item.id.startsWith("clue:")) {
                    const clueEdge = graph.edges.find((value) => value.id === item.id);
                    if (clueEdge) openSource(clueEdge.source, clueEdge.relationId);
                    return;
                  }
                  setFocused(item.id);
                } else {
                  const edge = graph.edges.find((value) => value.id === item.id);
                  if (edge) openSource(edge.source, edge.relationId || edge.id);
                }
              }} />
          : pending ? <Skeleton active /> : <Empty description="搜索并选择符号查看关系" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
        {clueCount > 0 && <p className="call-chain-caption">虚线为未解析调用线索（{clueCount}）</p>}
        {graph?.truncated && <p className="call-chain-caption">部分关系已截断</p>}
      </div>
    </Splitter.Panel>
    <Splitter.Panel defaultSize="24%" min="18%" max="34%">
      <div className="detail-scroll ast-source-pane" aria-label="源码位置">
        {source ? <section className="call-chain-source ast-source-inline">
          <div className="call-chain-source-path">{source.path}:{source.focusLine}</div>
          <pre><code>{source.content.split("\n").map((line, i) => {
            const number = source.lineStart + i;
            return number <= source.lineEnd
              ? <React.Fragment key={number}>{String(number).padStart(4)}  {line}{"\n"}</React.Fragment>
              : null;
          })}</code></pre>
          {source.truncated && <p>片段已截断</p>}
        </section> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="点击节点或连线查看源码" />}
      </div>
    </Splitter.Panel>
  </Splitter>;
}

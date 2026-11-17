import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { Alert, Button, Drawer, Empty, Select, Skeleton } from "antd";
import { ArrowClockwise, ArrowsOut, Minus, Plus } from "@phosphor-icons/react";
import { request } from "../../lib/api.js";
import { CARD, callChainLayout, edgeCurve } from "./call-chain-layout.js";
import "./call-chain.css";

const TYPES = { ENDPOINT: "接口", METHOD: "服务方法", HTTP_CLIENT: "HTTP 客户端", UNRESOLVED: "待解析" };
const scoped = (id, path) => `/api/projects/${encodeURIComponent(id)}${path}`;

export function CallChainView({ projectId }) {
  const [catalog, setCatalog] = useState(null);
  const [contract, setContract] = useState(null);
  const [chain, setChain] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [selection, setSelection] = useState(null);
  const [evidence, setEvidence] = useState(null);
  const [evidenceError, setEvidenceError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setSelection(null); setChain(null);
    request(scoped(projectId, "/call-chains"), {}, controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      setCatalog(data);
      setContract((current) => data.items.some((item) => item.id === current) ? current
        : (data.items.find((item) => item.httpCount && item.method === "GET") || data.items.find((item) => item.httpCount) || data.items[0])?.id || null);
      setLoading(false);
    }).catch((reason) => { if (!controller.signal.aborted) { setError(reason.message); setLoading(false); } });
    return () => controller.abort();
  }, [projectId, refresh]);

  useEffect(() => {
    if (!contract || !catalog?.indexVersion) return undefined;
    const controller = new AbortController();
    setChain(null); setError(""); setSelection(null);
    const params = new URLSearchParams({ version: catalog.indexVersion });
    request(scoped(projectId, `/call-chains/${encodeURIComponent(contract)}?${params}`), {}, controller.signal)
      .then((data) => { if (!controller.signal.aborted) setChain(data); }).catch((reason) => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [projectId, contract, catalog]);

  useEffect(() => {
    if (!selection || !chain) return undefined;
    const controller = new AbortController();
    setEvidence(null); setEvidenceError("");
    const params = new URLSearchParams({ version: chain.indexVersion, [selection.kind]: selection.id });
    request(scoped(projectId, `/call-chains/${encodeURIComponent(contract)}/evidence?${params}`), {}, controller.signal)
      .then((data) => { if (!controller.signal.aborted) setEvidence(data); }).catch((reason) => { if (!controller.signal.aborted) setEvidenceError(reason.message); });
    return () => controller.abort();
  }, [projectId, contract, chain, selection]);

  const options = useMemo(() => {
    const groups = new Map();
    for (const item of catalog?.items || []) {
      if (!groups.has(item.application)) groups.set(item.application, []);
      groups.get(item.application).push({ value: item.id, label: `${item.method} ${item.path}`,
        searchText: `${item.method} ${item.path} ${item.symbol} ${item.application}`.toLocaleLowerCase() });
    }
    return [...groups].map(([label, items]) => ({ label, options: items }));
  }, [catalog]);

  const selectedNode = selection?.kind === "node" ? chain?.nodes.find((n) => n.id === selection.id) : null;
  const selectedEdge = selection?.kind === "edge" ? chain?.edges.find((e) => e.id === selection.id) : null;
  const stale = chain?.nodes.some((node) => node.state === "stale" || node.state === "unavailable")
    || chain?.edges.some((edge) => ["stale", "unavailable"].includes(edge.state));

  return <div className="call-chain-view">
    <div className="call-chain-toolbar">
      <Select aria-label="选择入口接口" placeholder="选择入口接口" value={contract} options={options}
        showSearch filterOption={(input, option) => (option.searchText || "").includes(input.toLocaleLowerCase())}
        onChange={setContract} loading={loading} className="call-chain-select" popupMatchSelectWidth={false} />
      <Button icon={<ArrowClockwise size={16} />} onClick={() => setRefresh((n) => n + 1)}>刷新</Button>
    </div>
    {error && <Alert type="error" showIcon title={error} />}
    {stale && <Alert type="warning" showIcon title="相关源码已变化或不可读取，当前图展示索引时的连接，请更新索引。" />}
    {chain?.truncated && <Alert type="info" title="当前仅展示前 70 个方法节点。" />}
    {error ? null : loading ? <Skeleton active paragraph={{ rows: 8 }} /> : catalog?.status === "missing"
      ? <Empty description="尚未准备调用链索引，请先准备当前工程的契约索引，再刷新。" />
      : !catalog?.items.length ? <Empty description="当前索引没有可展示的接口。" />
        : !chain ? !error && <Skeleton active paragraph={{ rows: 7 }} />
          : <CallChainCanvas chain={chain} selection={selection} onSelect={setSelection} />}
    {chain && <p className="call-chain-caption">静态结构导航 · 覆盖已索引的返回依赖调用 · 不代表完整运行调用链 · 点击节点或连线查看源码</p>}
    <Drawer open={Boolean(selection)} onClose={() => setSelection(null)} title={selectedNode?.label || selectedEdge?.label || "源码依据"}
      size={560} mask={false} className="call-chain-drawer">
      {selectedNode && <div className="call-chain-inspector-title"><span>{selectedNode.application}</span><code>{selectedNode.route}</code></div>}
      {selectedNode?.contractId && selectedNode.contractId !== contract && <Button onClick={() => { setContract(selectedNode.contractId); setSelection(null); }}>从此接口继续查看</Button>}
      {selectedEdge && <p className="call-chain-inspector-title">{chain.nodes.find((n) => n.id === selectedEdge.source)?.label} → {chain.nodes.find((n) => n.id === selectedEdge.target)?.label}</p>}
      {selectedNode?.reason && <Alert type="info" title={selectedNode.label} description="索引未能确认目标连接，图中未推断目标服务。" />}
      {evidenceError ? <Alert type="error" title={evidenceError} /> : !evidence ? <Skeleton active /> : <>
        {evidence.blocks.some((b) => b.state !== "current") && <Alert type="warning" title="下面为索引时的源码片段，当前文件已变化或不可读取。" />}
        {!evidence.blocks.length && <Empty description="对应源码不在当前工程资料范围或无法读取。" />}
        {evidence.blocks.map((block, i) => <section className="call-chain-source" key={`${block.path}:${block.start}:${i}`}>
          <div className="call-chain-source-path">{block.path}:{block.start}–{block.end}</div>
          <pre><code>{block.content}</code></pre>
          {block.truncated && <p>片段已按行数上限截断。</p>}
        </section>)}
        {evidence.omittedBlocks > 0 && <p>另有 {evidence.omittedBlocks} 段依据未展示。</p>}
        <details className="call-chain-version"><summary>索引版本</summary><code>{evidence.indexVersion}</code></details>
      </>}
    </Drawer>
  </div>;
}

export function CallChainCanvas({ chain, selection, onSelect, graphLabel = "接口调用链", typeLabels = TYPES }) {
  const layout = useMemo(() => callChainLayout(chain.nodes, chain.edges, chain.rootId), [chain]);
  const [view, setView] = useState({ x: 0, y: 0, w: layout.width, h: layout.height });
  const svg = useRef(null), drag = useRef(null);
  const marker = useId().replaceAll(":", "");
  const nodes = useMemo(() => Object.fromEntries(chain.nodes.map((node) => [node.id, node])), [chain]);
  const fit = () => setView({ x: 0, y: 0, w: layout.width, h: layout.height });
  useEffect(fit, [layout]);

  function zoom(factor) {
    setView((v) => {
      const w = Math.min(layout.width * 2, Math.max(layout.width / 4, v.w * factor));
      const h = w * layout.height / layout.width;
      return { x: v.x + (v.w - w) / 2, y: v.y + (v.h - h) / 2, w, h };
    });
  }
  function down(event) {
    if (event.target.closest("[data-item]")) return;
    drag.current = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function move(event) {
    if (!drag.current || !svg.current) return;
    const rect = svg.current.getBoundingClientRect();
    const scale = Math.max(view.w / rect.width, view.h / rect.height);
    setView((v) => ({ ...v, x: drag.current.vx - (event.clientX - drag.current.x) * scale,
      y: drag.current.vy - (event.clientY - drag.current.y) * scale }));
  }
  function activate(event, value) {
    if (event.type === "keydown" && !["Enter", " "].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation(); onSelect(value);
  }
  return <div className="call-chain-canvas">
    <div className="call-chain-controls">
      <Button size="small" aria-label="放大调用链" icon={<Plus size={16} />} onClick={() => zoom(.8)} />
      <Button size="small" aria-label="缩小调用链" icon={<Minus size={16} />} onClick={() => zoom(1.25)} />
      <Button size="small" aria-label="适应画布" icon={<ArrowsOut size={16} />} onClick={fit}>适应</Button>
    </div>
    <svg ref={svg} viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`} role="group" aria-label={graphLabel}
      onPointerDown={down} onPointerMove={move} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
      <defs><marker id={`${marker}-arrow`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
      {chain.edges.map((edge) => {
        const a = layout.positions[edge.source], b = layout.positions[edge.target];
        if (!a || !b) return null;
        const path = edgeCurve(a, b), active = selection?.id === edge.id;
        return <g key={edge.id} data-item="edge" className={`call-chain-edge ${edge.kind === "HTTP_RESPONSE" ? "http" : ""} ${edge.state !== "current" ? "uncertain" : ""} ${active ? "selected" : ""}`}
          role="button" tabIndex={0} aria-label={`${edge.label}：${nodes[edge.source]?.label} → ${nodes[edge.target]?.label}`}
          onClick={(e) => activate(e, { kind: "edge", id: edge.id })} onKeyDown={(e) => activate(e, { kind: "edge", id: edge.id })}>
          <path d={path} markerEnd={`url(#${marker}-arrow)`} /><path d={path} className="call-chain-edge-hit" />
          <text x={(a.x + CARD.width + b.x) / 2} y={(a.y + b.y) / 2 + CARD.height / 2 - 12} textAnchor="middle">{edge.label}</text>
        </g>;
      })}
      {chain.nodes.map((node) => {
        const p = layout.positions[node.id];
        const divider = node.label.lastIndexOf(".");
        const owner = node.displayName ?? (divider > 0 ? node.label.slice(0, divider) : node.label);
        const method = node.subtitle ?? (divider > 0 ? node.label.slice(divider + 1) + "()" : "");
        const typeLabel = typeLabels[node.type] || node.type;
        const route = node.route || "";
        const routeBreak = route.lastIndexOf("/", 32);
        const cut = routeBreak > 8 ? routeBreak : 32;
        const routeLines = route.length > 32 ? [route.slice(0, cut), route.slice(cut)] : [route];
        return <g key={node.id} data-item="node" transform={`translate(${p.x} ${p.y})`}
          className={`call-chain-node ${node.type.toLowerCase()} ${node.state !== "current" ? "uncertain" : ""} ${selection?.id === node.id ? "selected" : ""}`}
          tabIndex={0} role="button" aria-label={`${typeLabel} ${node.label}`}
          onClick={(e) => activate(e, { kind: "node", id: node.id })} onKeyDown={(e) => activate(e, { kind: "node", id: node.id })}>
          <rect width={CARD.width} height={CARD.height} rx="12" />
          <text x="18" y="25" className="call-chain-node-type">{typeLabel}{node.state === "stale" ? " · 待更新" : node.state === "unavailable" ? " · 不可读取" : node.state === "unresolved" ? " · 线索" : ""}</text>
          <text x="18" y="53" className="call-chain-node-owner">{owner.length > 26 ? owner.slice(0, 25) + "…" : owner}</text>
          <text x="18" y="77" className="call-chain-node-method">{method.length > 30 ? method.slice(0, 29) + "…" : method}</text>
          {routeLines.map((line, i) => <text key={i} x="18" y={101 + i * 15} className="call-chain-node-route">{line.length > 32 ? line.slice(0, 31) + "…" : line}</text>)}
          <text x="18" y="139" className="call-chain-node-app">{node.application}</text>
          <title>{node.label}{node.route ? `\n${node.route}` : ""}{"\n"}{node.location.path}:{node.location.start}</title>
        </g>;
      })}
    </svg>
    <span className="call-chain-pan-hint">拖拽平移 · 使用右上角缩放</span>
  </div>;
}

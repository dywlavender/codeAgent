import React, { useEffect, useMemo, useRef, useState } from "react";
import { MagnifyingGlass } from "@phosphor-icons/react";
import {
  Button, Card, Empty, Flex, Input, Segmented, Skeleton, Splitter, Tag, Typography,
} from "antd";
import { request } from "../lib/api.js";

const TYPE_STYLE = {
  FUNCTION: { glyph: "功", color: "#1a7f4e", soft: "#e7f4ee" },
  PROJECT: { glyph: "工", color: "#0b6a63", soft: "#e6f2f1" },
  ENTRY_ANCHOR: { glyph: "入", color: "#8d5a1f", soft: "#fbf1e4" },
  CODE: { glyph: "{}", color: "#57606a", soft: "#f0f2f4" },
  TABLE: { glyph: "表", color: "#9a6700", soft: "#fcf3e3" },
  TAG: { glyph: "#", color: "#8d8d88", soft: "#f4f4f2" },
  BUSINESS: { glyph: "业", color: "#0b6a63", soft: "#e6f2f1" },
  SYSTEM: { glyph: "系", color: "#0b6a63", soft: "#e6f2f1" },
  BUSINESS_TERM: { glyph: "词", color: "#7c4d9b", soft: "#f3ebf7" },
  CAPABILITY: { glyph: "能", color: "#1a7f4e", soft: "#e7f4ee" },
  FLOW: { glyph: "流", color: "#0969da", soft: "#eaf2fb" },
  RULE: { glyph: "规", color: "#9a6700", soft: "#fcf3e3" },
};
const TYPE_OPTIONS = [
  { label: "全部", value: "" },
  { label: "系统", value: "SYSTEM", countKey: "systems" },
  { label: "术语", value: "BUSINESS_TERM", countKey: "terms" },
  { label: "能力", value: "CAPABILITY", countKey: "capabilities" },
  { label: "流程", value: "FLOW", countKey: "flows" },
  { label: "规则", value: "RULE", countKey: "rules" },
  { label: "入口", value: "ENTRY_ANCHOR", countKey: "entryAnchors" },
  { label: "代码", value: "CODE", countKey: "code" },
];

export function GraphPage({ workspace }) {
  const [query, setQuery] = useState("");
  const [nodeType, setNodeType] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState(null);

  async function load(nextQuery = query, nextType = nodeType) {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (nextQuery.trim()) params.set("q", nextQuery.trim());
      if (nextType) params.set("type", nextType);
      const data = await request(`/api/knowledge-graph?${params.toString()}`);
      setData(data);
      setSelectedId(null);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load("", ""); }, []);

  const nodes = data?.nodes || [];
  const edges = data?.edges || [];
  const nodeById = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes]);
  const selected = selectedId ? nodeById[selectedId] : null;
  const neighbors = useMemo(() => {
    if (!selectedId) return [];
    return edges
      .filter((e) => e.source === selectedId || e.target === selectedId)
      .map((e) => ({
        relation: e.label || e.relation,
        direction: e.source === selectedId ? "→" : "←",
        node: nodeById[e.source === selectedId ? e.target : e.source],
        evidenceIds: e.evidenceIds || [],
      }))
      .filter((item) => item.node);
  }, [edges, nodeById, selectedId]);

  const counts = data?.allCounts || {};
  const filterOptions = TYPE_OPTIONS.map((opt) => ({
    ...opt,
    label: opt.countKey && counts[opt.countKey] !== undefined
      ? `${opt.label} ${counts[opt.countKey]}`
      : opt.label,
  }));

  return (
    <div className="page-wrap">
      <div style={{ maxWidth: 1280, margin: "0 auto", width: "100%" }}>
        <Flex gap={12} align="center" wrap="wrap" style={{ marginBottom: 14 }}>
          <Typography.Title level={4} style={{ margin: 0, letterSpacing: "-.02em", marginRight: 8 }}>知识图谱</Typography.Title>
          <Segmented options={filterOptions} value={nodeType} onChange={(v) => { setNodeType(v); load(query, v); }} />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onPressEnter={() => load()}
            placeholder="搜索业务术语、关系或代码符号…"
            prefix={<MagnifyingGlass size={15} style={{ color: "#a3a29c" }} />}
            style={{ width: 300 }}
            allowClear
          />
          <Button onClick={() => load()} ghost>查询</Button>
          <Typography.Text type="secondary" style={{ marginLeft: "auto", fontSize: 11.5 }}>
            {data ? `${nodes.length} 节点 · ${edges.length} 关系` : ""}
            {workspace?.project ? ` · ${workspace.project}` : ""}
          </Typography.Text>
        </Flex>
      </div>

      {error ? (
        <Empty description={error} style={{ marginTop: 100 }} />
      ) : loading ? (
        <Skeleton active paragraph={{ rows: 12 }} style={{ maxWidth: 1280, margin: "40px auto" }} />
      ) : (
        <Splitter style={{ flex: 1, minHeight: 0, maxWidth: 1280, margin: "0 auto", width: "100%", height: "calc(100dvh - 200px)" }}>
          <Splitter.Panel defaultSize="24%" min="16%" max="36%">
            <div className="result-list">
              {nodes.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span style={{ fontSize: 12 }}>没有匹配的节点。<br/>导入业务基线后，业务关系和调查入口会出现在这里。</span>} style={{ marginTop: 90 }} />
              ) : nodes.map((node) => (
                <GraphRow key={node.id} node={node} active={node.id === selectedId} onSelect={() => setSelectedId(node.id)} />
              ))}
            </div>
          </Splitter.Panel>

          <Splitter.Panel>
            <GraphCanvas
              nodes={nodes}
              edges={edges}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          </Splitter.Panel>

          <Splitter.Panel defaultSize="24%" min="18%" max="36%">
            <div className="detail-scroll">
              {!selected ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span style={{ fontSize: 12.5 }}>点击节点查看详情与相邻知识</span>} style={{ marginTop: 110 }} />
              ) : (
                <NodeInspector node={selected} neighbors={neighbors} onSelect={setSelectedId} />
              )}
            </div>
          </Splitter.Panel>
        </Splitter>
      )}
    </div>
  );
}

function GraphRow({ node, active, onSelect }) {
  const style = TYPE_STYLE[node.type] || TYPE_STYLE.TAG;
  return (
    <button className={`r-row ${active ? "on" : ""}`} onClick={onSelect}>
      <span className="r-glyph" style={{ color: style.color, borderColor: `${style.color}55`, background: style.soft }}>{style.glyph}</span>
      <span className="r-copy">
        <b>{node.label}</b>
        <small>{node.subtitle || node.typeLabel}</small>
      </span>
      {(node.evidenceCount ?? 0) > 0 && <Tag bordered={false} style={{ fontSize: 10.5 }}>{node.evidenceCount} 证</Tag>}
    </button>
  );
}

/* ===== 画布：确定性环形布局 + 滚轮缩放 + 拖拽平移 ===== */
const VB = { w: 1000, h: 720 };

/* 画布上的代码节点用短名（类.方法），全限定名放进悬浮提示，避免标签互相压盖 */
function canvasLabel(node) {
  const label = String(node.label || "");
  if ((node.type === "CODE" || node.type === "MYBATIS_STATEMENT") && label.includes(".")) {
    const parts = label.split(".");
    return parts.slice(-2).join(".");
  }
  return label;
}

function GraphCanvas({ nodes, edges, selectedId, onSelect }) {
  const svgRef = useRef(null);
  const [view, setView] = useState({ x: 0, y: 0, w: VB.w, h: VB.h });
  const dragRef = useRef(null);

  const positions = useMemo(() => {
    const map = {};
    const cx = VB.w / 2, cy = VB.h / 2;
    // 按度数分层：连接多的放内圈，其余按类型分组排外圈
    const degree = {};
    edges.forEach((e) => {
      degree[e.source] = (degree[e.source] || 0) + 1;
      degree[e.target] = (degree[e.target] || 0) + 1;
    });
    const sorted = [...nodes].sort((a, b) => (degree[b.id] || 0) - (degree[a.id] || 0));
    const n = sorted.length || 1;
    sorted.forEach((node, index) => {
      const d = degree[node.id] || 0;
      let angle, radius;
      if (n === 1) { map[node.id] = { x: cx, y: cy }; return; }
      if (index === 0 && d >= 3 && n > 4) {
        angle = 0; radius = 0;
      } else {
        const ring = d >= 3 ? 1 : 2;
        const sameRing = sorted.filter((m, i) => i < index && (degree[m.id] || 0) >= 3 === (ring === 1)).length;
        angle = (sameRing * 137.508 * Math.PI) / 180;
        radius = ring === 1 ? Math.min(cx, cy) * 0.42 : Math.min(cx, cy) * (0.62 + 0.3 * ((index % 3) / 3));
      }
      map[node.id] = {
        x: cx + radius * Math.cos(angle),
        y: cy + radius * Math.sin(angle) * 0.86,
      };
    });
    return map;
  }, [nodes, edges]);

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (event) => {
      event.preventDefault();
      const factor = event.deltaY > 0 ? 1.12 : 1 / 1.12;
      setView((v) => {
        const nw = Math.min(Math.max(v.w * factor, VB.w * 0.25), VB.w * 3);
        const nh = nw * (VB.h / VB.w);
        const rect = el.getBoundingClientRect();
        const px = v.x + ((event.clientX - rect.left) / rect.width) * v.w;
        const py = v.y + ((event.clientY - rect.top) / rect.height) * v.h;
        const ratio = nw / v.w;
        return { w: nw, h: nh, x: px - (px - v.x) * ratio, y: py - (py - v.y) * ratio };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  function onPointerDown(event) {
    if (event.target.closest("[data-node]")) return;
    dragRef.current = { sx: event.clientX, sy: event.clientY, vx: view.x, vy: view.y };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function onPointerMove(event) {
    if (!dragRef.current || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const scaleX = view.w / rect.width;
    const scaleY = view.h / rect.height;
    setView((v) => ({
      ...v,
      x: dragRef.current.vx - (event.clientX - dragRef.current.sx) * scaleX,
      y: dragRef.current.vy - (event.clientY - dragRef.current.sy) * scaleY,
    }));
  }
  function onPointerUp() { dragRef.current = null; }

  const degreeOf = useMemo(() => {
    const d = {};
    edges.forEach((e) => { d[e.source] = (d[e.source] || 0) + 1; d[e.target] = (d[e.target] || 0) + 1; });
    return d;
  }, [edges]);

  return (
    <div className="graph-canvas-wrap" onDoubleClick={() => setView({ x: 0, y: 0, w: VB.w, h: VB.h })}>
      {nodes.length === 0 ? null : (
        <svg
          ref={svgRef}
          className="graph-svg"
          viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          role="img"
          aria-label="知识图谱关系视图"
        >
          {edges.map((edge) => {
            const a = positions[edge.source];
            const b = positions[edge.target];
            if (!a || !b) return null;
            const active = selectedId && (edge.source === selectedId || edge.target === selectedId);
            return (
              <line key={edge.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                className={active ? "g-edge on" : "g-edge"}>
                <title>{`${edge.label || edge.relation}${edge.status === "SUGGESTED" ? "（建议关联）" : ""}`}</title>
              </line>
            );
          })}
          {nodes.map((node) => {
            const p = positions[node.id];
            if (!p) return null;
            const style = TYPE_STYLE[node.type] || TYPE_STYLE.TAG;
            const isSel = node.id === selectedId;
            const r = 9 + Math.min((degreeOf[node.id] || 0) * 2.2, 13) + (isSel ? 3 : 0);
            return (
              <g key={node.id} data-node={node.id} className={`g-node ${isSel ? "sel" : ""}`}
                onClick={(event) => { event.stopPropagation(); onSelect(isSel ? null : node.id); }}>
                <circle cx={p.x} cy={p.y} r={r} fill={style.soft} stroke={style.color} strokeWidth={isSel ? 2.4 : 1.4} />
                <text x={p.x} y={p.y + 3.5} textAnchor="middle" className="g-glyph" fill={style.color}>{style.glyph}</text>
                <text x={p.x} y={p.y + r + 13} textAnchor="middle" className={isSel ? "g-label sel" : "g-label"}>{canvasLabel(node)}</text>
                <title>{`${node.typeLabel} · ${node.label}${node.statusLabel ? `\n${node.statusLabel}` : ""}${node.description ? `\n${String(node.description).slice(0, 160)}` : ""}`}</title>
              </g>
            );
          })}
        </svg>
      )}
      {nodes.length > 0 && (
        <div className="graph-hint">滚轮缩放 · 拖拽平移 · 双击复位</div>
      )}
    </div>
  );
}

function NodeInspector({ node, neighbors, onSelect }) {
  const style = TYPE_STYLE[node.type] || TYPE_STYLE.TAG;
  return (
    <Card size="small" styles={{ body: { padding: "16px 18px" } }}>
      <Flex gap={8} align="center" style={{ marginBottom: 6 }}>
        <span className="r-glyph" style={{ color: style.color, borderColor: `${style.color}55`, background: style.soft, width: 24, height: 24, fontSize: 10 }}>{style.glyph}</span>
        <Tag bordered={false}>{node.typeLabel}</Tag>
        {node.statusLabel && <Tag bordered={false} color="default">{node.statusLabel}</Tag>}
        {node.version != null && <Tag bordered={false}>V{node.version}</Tag>}
      </Flex>
      <Typography.Title level={5} style={{ marginTop: 2, marginBottom: 4, wordBreak: "break-all" }}>{node.label}</Typography.Title>
      {node.subtitle && <Typography.Paragraph type="secondary" style={{ fontSize: 11.5, fontFamily: "monospace", marginBottom: 8 }}>{node.subtitle}</Typography.Paragraph>}
      {node.description && (
        node.type === "CODE"
          ? <pre className="ev-pre" style={{ maxHeight: 140 }}>{node.description}</pre>
          : <Typography.Paragraph style={{ fontSize: 12.5, lineHeight: 1.75 }}>{node.description}</Typography.Paragraph>
      )}
      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
        Evidence {node.evidenceCount ?? 0} 条{node.sourceId ? ` · ID ${node.sourceId}` : ""}
      </Typography.Text>

      <Typography.Text type="secondary" className="doc-label" style={{ display: "block", margin: "16px 0 8px" }}>
        相邻知识 · {neighbors.length}
      </Typography.Text>
      {neighbors.length === 0 ? (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>该节点暂无已确认的关系。</Typography.Text>
      ) : (
        <Flex vertical gap={6}>
          {neighbors.map((item, index) => (
            <button key={`${item.node.id}-${index}`} className="adj-row" onClick={() => onSelect(item.node.id)}>
              <span className="adj-relation">{item.direction} {item.relation}</span>
              <span className="adj-name">{TYPE_STYLE[item.node.type]?.glyph} {item.node.label}</span>
            </button>
          ))}
        </Flex>
      )}
    </Card>
  );
}

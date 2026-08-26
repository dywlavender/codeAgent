import React, { useEffect, useState } from "react";
import { MagnifyingGlass } from "@phosphor-icons/react";
import {
  Button, Card, Descriptions, Empty, Flex, Input, Segmented, Skeleton,
  Splitter, Tag, Typography,
} from "antd";
import { request } from "../lib/api.js";

const TYPE_CONFIG = {
  代码: {
    endpoint: "/api/code/search?q=",
    placeholder: "搜索类、方法或字段，如 repayType",
    empty: "换一个 Symbol 或字段名再试。",
    countKey: "symbols",
    noun: "Symbols",
  },
  功能知识: {
    endpoint: "/api/functions?q=",
    placeholder: "搜索功能名称或业务域",
    empty: "还没有已发布的功能知识。审核通过首个提案后会出现在这里。",
    countKey: "businessKnowledge",
    noun: "Functions",
  },
  需求: {
    endpoint: "/api/requirements?q=",
    placeholder: "搜索需求编号或标题",
    empty: "没有匹配的需求。可通过 CLI 导入 .docx / .md / .txt。",
    countKey: "requirements",
    noun: "Requirements",
  },
};

export function LibraryPage({ workspace }) {
  const [type, setType] = useState("代码");
  const config = TYPE_CONFIG[type];
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("repayType");
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    setSelectedId(null);
    setDetail(null);
    request(config.endpoint).then((data) => {
      if (alive) setItems(data.items || []);
    }).catch((reason) => {
      if (alive) setError(reason.message);
    }).finally(() => {
      if (alive) setLoading(false);
    });
    return () => { alive = false; };
  }, [type]);

  async function search(nextQuery = query) {
    const base = config.endpoint.split("?")[0];
    setLoading(true);
    setError("");
    setSelectedId(null);
    setDetail(null);
    try {
      setItems((await request(`${base}?q=${encodeURIComponent(nextQuery)}`)).items || []);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setLoading(false);
    }
  }

  async function select(item) {
    if (item.id === selectedId) { setSelectedId(null); setDetail(null); return; }
    setSelectedId(item.id);
    setDetail(null);
    if (type !== "代码") { setDetail({ item }); return; }
    setDetailLoading(true);
    try {
      const data = await request(`/api/code/symbol/${encodeURIComponent(item.id)}`);
      setDetail(data);
    } catch (reason) {
      setDetail({ item, error: reason.message });
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <div className="page-wrap">
      <div style={{ maxWidth: 1180, margin: "0 auto", width: "100%" }}>
        <Typography.Title level={4} style={{ marginTop: 0, letterSpacing: "-.02em" }}>浏览知识库</Typography.Title>
        <Flex gap={10} align="center" style={{ marginBottom: 14 }} wrap="wrap">
          <Segmented
            value={type}
            onChange={(value) => setType(value)}
            options={Object.keys(TYPE_CONFIG)}
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onPressEnter={() => search()}
            placeholder={config.placeholder}
            prefix={<MagnifyingGlass size={15} style={{ color: "#a3a29c" }} />}
            style={{ width: 320 }}
            allowClear
          />
          <Button type="primary" onClick={() => search()} ghost>搜索</Button>
          <span style={{ marginLeft: "auto", fontVariantNumeric: "tabular-nums" }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {workspace?.counts?.[config.countKey] ?? "…"} {config.noun}
            </Typography.Text>
          </span>
        </Flex>
      </div>

      <Splitter style={{ flex: 1, minHeight: 0, maxWidth: 1180, margin: "0 auto", width: "100%", height: "calc(100dvh - 210px)" }}>
        <Splitter.Panel defaultSize="42%" min="28%" max="60%">
          <div className="result-list">
            {error ? (
              <Empty description={error} style={{ marginTop: 80 }} />
            ) : loading ? (
              <div style={{ padding: 16 }}><Skeleton active paragraph={{ rows: 8 }} /></div>
            ) : items.length === 0 ? (
              <Empty description={<span style={{ fontSize: 12.5 }}>{config.empty}</span>} style={{ marginTop: 80 }} />
            ) : items.map((item) => (
              <ResultRow key={item.id} item={item} type={type} active={item.id === selectedId} onSelect={() => select(item)} />
            ))}
          </div>
        </Splitter.Panel>
        <Splitter.Panel>
          <div className="detail-scroll">
            {!selectedId ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span style={{ fontSize: 12.5 }}>在左侧选择一个条目查看详情</span>} style={{ marginTop: 120 }} />
            ) : detailLoading && type === "代码" ? (
              <Skeleton active paragraph={{ rows: 6 }} style={{ padding: 20 }} />
            ) : detail ? (
              type === "代码"
                ? <CodeDetail item={items.find((i) => i.id === selectedId)} detail={detail} />
                : <KnowledgeDetail type={type} item={detail.item || selectedRaw(items, selectedId)} />
            ) : null}
          </div>
        </Splitter.Panel>
      </Splitter>
    </div>
  );
}

function selectedRaw(items, id) {
  return items.find((item) => item.id === id);
}

function rowTitle(item, type) {
  return item.qualified_name || item.title || item.id;
}

function rowSubtitle(item, type) {
  if (type === "代码") {
    const facts = (item.summary || "").split(" ").filter(Boolean).length;
    return `${item.kind || "SYMBOL"} · ${facts} 条事实`;
  }
  if (type === "功能知识") {
    return `${item.statement || ""}`;
  }
  return `${item.status || ""}${item.current_version ? ` · V${item.current_version}` : ""}`;
}

function statusColor(status) {
  const value = String(status || "").toUpperCase();
  if (["PUBLISHED", "ACTIVE"].includes(value)) return "green";
  if (value === "DRAFT") return "gold";
  return "default";
}

function ResultRow({ item, type, active, onSelect }) {
  return (
    <button className={`r-row ${active ? "on" : ""}`} onClick={onSelect}>
      <span className={`r-glyph ${type === "代码" ? "code" : type === "功能知识" ? "biz" : "req"}`}>
        {type === "代码" ? "{ }" : type === "功能知识" ? "知" : "需"}
      </span>
      <span className="r-copy">
        <b>{rowTitle(item, type)}</b>
        <small>{rowSubtitle(item, type)}</small>
      </span>
      {item.status && <Tag color={statusColor(item.status)} style={{ fontSize: 10.5 }}>{item.status === "PUBLISHED" ? "已发布" : item.status}</Tag>}
    </button>
  );
}

function factTypeLabel(type) {
  return ({ CALL: "调用", READ: "读取", WRITE: "写入", GENERATE: "生成", CHECK: "校验" })[type] || type;
}

function CodeDetail({ item, detail }) {
  const relations = detail?.relations || [];
  const grouped = relations.reduce((acc, row) => {
    (acc[row.fact_type || "FACT"] ||= []).push(row);
    return acc;
  }, {});
  return (
    <Card styles={{ body: { padding: "18px 22px" } }}>
      <Descriptions size="small" column={1} bordered={false} style={{ marginBottom: 4 }}
        items={[
          { key: "kind", label: "类型", children: item?.kind || "SYMBOL" },
          { key: "name", label: "限定名", children: <Typography.Text code copyable style={{ fontSize: 12 }}>{item?.qualified_name}</Typography.Text> },
          ...(detail?.path ? [{ key: "loc", label: "源码位置", children: <Typography.Text style={{ fontSize: 12 }}>{detail.path}:{detail.line_start}-{detail.line_end}</Typography.Text> }] : []),
        ]}
      />
      {Object.entries(grouped).map(([factType, rows]) => (
        <div key={factType} style={{ marginTop: 14 }}>
          <Typography.Text type="secondary" style={{ fontSize: 11, letterSpacing: ".05em", display: "block", marginBottom: 7 }}>
            {factTypeLabel(factType).toUpperCase()} · {factTypeLabel(factType)} · {rows.length}
          </Typography.Text>
          <Flex gap={6} wrap="wrap">
            {rows.map((row, index) => (
              <Tag key={`${row.evidence_id}-${index}`} bordered={false}>
                {row.subject}{row.target ? ` → ${row.target}` : ""}
              </Tag>
            ))}
          </Flex>
        </div>
      ))}
      <Divider_ />
      {detail?.error ? (
        <Typography.Text type="warning">无法读取源码：{detail.error}</Typography.Text>
      ) : detail?.content ? (
        <>
          <Typography.Text type="secondary" style={{ fontSize: 11, letterSpacing: ".05em", display: "block", marginBottom: 7 }}>源码</Typography.Text>
          <pre className="ev-pre drawer">{detail.content}</pre>
        </>
      ) : null}
    </Card>
  );
}

function KnowledgeDetail({ type, item }) {
  if (!item) return null;
  const fields = type === "功能知识"
    ? [
      { key: "st", label: "摘要", children: item.statement || "—" },
      { key: "ver", label: "版本", children: item.version != null ? `V${item.version}` : "—" },
      { key: "kt", label: "知识类型", children: item.knowledge_type || "—" },
      { key: "ev", label: "证据", children: item.evidence_id || "—" },
    ]
    : [
      { key: "ver", label: "当前版本", children: item.current_version != null ? `V${item.current_version}` : "—" },
      { key: "up", label: "更新时间", children: item.updated_at ? new Date(item.updated_at).toLocaleDateString("zh-CN") : "—" },
    ];
  return (
    <Card styles={{ body: { padding: "18px 22px" } }}>
      <Typography.Title level={5} style={{ marginTop: 0 }}>{item.title || item.id}</Typography.Title>
      <Descriptions size="small" column={1} items={fields} />
    </Card>
  );
}

function Divider_() {
  return <div style={{ borderTop: "1px dashed #ebeae4", margin: "16px 0" }} />;
}

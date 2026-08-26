import React, { useEffect, useRef, useState } from "react";
import {
  Check, Lightning, Minus, Plus, X,
} from "@phosphor-icons/react";
import {
  Alert, Button, Card, Flex, Form, Input, Modal, Select, Skeleton, Space,
  Splitter, Tabs, Tag, Typography,
} from "antd";
import { request } from "../lib/api.js";
import { formatTime } from "../lib/format.js";

const UPDATE_SOURCES = [
  ["ADMIN_NOTE", "管理员说明", "填写本次人工补充的稳定标识"],
  ["REQUIREMENT", "需求", "填写已导入的 Requirement ID"],
  ["DOCUMENT", "文档", "填写文档名称或版本标识"],
  ["USER_FEEDBACK", "用户反馈", "填写反馈单号或问题标识"],
  ["CODE_CHANGE", "代码变化", "填写仓库 ID、变化记录 ID 或已索引文件路径"],
];

export function KnowledgeAdminPage({ onRequireUnlock }) {
  const [items, setItems] = useState([]);
  const [tab, setTab] = useState("pending");
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState("");
  const [comment, setComment] = useState("");
  const [generateOpen, setGenerateOpen] = useState(false);
  const [form] = Form.useForm();
  const pendingSelection = useRef(null);

  async function load(nextTab = tab) {
    setLoading(true);
    setError("");
    try {
      const data = await request(`/api/knowledge-admin/${nextTab}?q=`);
      const nextItems = data?.items || [];
      setItems(nextItems);
      if (nextTab === "pending" && pendingSelection.current) {
        const generated = pendingSelection.current;
        pendingSelection.current = null;
        const id = adminId(generated);
        setSelectedId(id);
        if (id) {
          try {
            const got = await request(`/api/knowledge-admin/proposals/${encodeURIComponent(id)}`);
            setDetail(got.item || got.proposal || got);
          } catch {
            setDetail(generated);
          }
        }
      }
    } catch (reason) {
      setItems([]);
      setError(reason.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(tab);
  }, [tab]);

  async function selectItem(item) {
    setSelectedId(adminId(item));
    setComment("");
    if (tab === "functions") return;
    try {
      const got = await request(`/api/knowledge-admin/proposals/${encodeURIComponent(adminId(item))}`);
      setDetail(got.item || got.proposal || got);
    } catch {
      setDetail(item);
    }
  }

  async function review(action) {
    if (!selectedId || submitting) return;
    setSubmitting(action);
    setError("");
    try {
      await request(`/api/knowledge-admin/proposals/${encodeURIComponent(selectedId)}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, comment: comment.trim() || undefined }),
      });
      setComment("");
      await load(tab);
    } catch (reason) {
      setError(reason.message);
      if (String(reason.message).includes("凭证") || String(reason.message).includes("credential")) onRequireUnlock?.();
    } finally {
      setSubmitting("");
    }
  }

  async function generateProposal(values) {
    try {
      const data = await request("/api/knowledge-admin/proposals/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sourceType: values.sourceType,
          sourceId: values.sourceId.trim(),
          content: values.content.trim(),
          targetFunctionId: values.targetFunctionId?.trim() || undefined,
        }),
      });
      pendingSelection.current = data.proposal || data.item || data;
      form.resetFields();
      setGenerateOpen(false);
      if (tab === "pending") await load("pending");
      else setTab("pending");
    } catch (reason) {
      setError(reason.message);
    }
  }

  const selected = items.find((item) => adminId(item) === selectedId) || detail;

  return (
    <div className="page-wrap">
      <div style={{ maxWidth: 1180, margin: "0 auto", width: "100%", display: "flex", alignItems: "center" }}>
        <Tabs
          activeKey={tab}
          onChange={setTab}
          style={{ flex: 1 }}
          items={[
            { key: "pending", label: "待处理" },
            { key: "functions", label: "功能知识" },
            { key: "proposals", label: "变更审核" },
          ]}
        />
        <Button type="primary" icon={<Lightning size={14} weight="fill" />} onClick={() => setGenerateOpen(true)} style={{ marginBottom: 16 }}>
          发起知识更新
        </Button>
      </div>

      {error && (
        <div style={{ maxWidth: 1180, margin: "0 auto 12px", width: "100%" }}>
          <Alert type="error" showIcon title="操作未完成" description={error} closable onClose={() => setError("")} />
        </div>
      )}

      <Splitter style={{ maxWidth: 1180, margin: "0 auto", width: "100%", height: "calc(100dvh - 230px)" }}>
        <Splitter.Panel defaultSize="38%" min="26%" max="55%">
          <div className="result-list">
            {loading ? (
              <Skeleton active paragraph={{ rows: 8 }} style={{ padding: 16 }} />
            ) : items.length === 0 ? (
              <Typography.Text type="secondary" style={{ display: "block", textAlign: "center", marginTop: 90, fontSize: 12.5, lineHeight: 1.8, paddingInline: 30 }}>
                {tab === "pending" ? "当前没有待处理事项。代码事实会自动更新；只有可能改变业务含义的变化才会进入人工审核。" : "没有匹配内容。"}
              </Typography.Text>
            ) : items.map((item, index) => (
              <GovRow
                key={adminId(item) || index}
                item={item}
                tab={tab}
                active={adminId(item) === selectedId}
                onSelect={() => selectItem(item)}
              />
            ))}
          </div>
        </Splitter.Panel>
        <Splitter.Panel>
          <div className="detail-scroll">
            {!selected ? (
              <Typography.Text type="secondary" style={{ display: "block", textAlign: "center", marginTop: 120, fontSize: 12.5 }}>
                在左侧选择一条{tab === "functions" ? "功能知识" : "变更"}查看详情。
              </Typography.Text>
            ) : tab === "functions" ? (
              <FunctionDetailCard item={selected} />
            ) : (
              <ProposalDetailCard item={selected} submitting={submitting} comment={comment} setComment={setComment} onReview={review} />
            )}
          </div>
        </Splitter.Panel>
      </Splitter>

      <Modal
        open={generateOpen}
        title="发起知识更新"
        okText="生成更新提案"
        cancelText="取消"
        onCancel={() => setGenerateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={false}
        width={560}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12.5, marginBottom: 14 }}>
          更新 Agent 会检索代码与已有知识，生成待审核提案，不会直接修改已发布内容。
        </Typography.Paragraph>
        <Form form={form} layout="vertical" onFinish={generateProposal} initialValues={{ sourceType: "ADMIN_NOTE" }}>
          <Flex gap={10}>
            <Form.Item name="sourceType" label="来源类型" style={{ width: 170 }} rules={[{ required: true }]}>
              <Select options={UPDATE_SOURCES.map(([value, label]) => ({ value, label }))} />
            </Form.Item>
            <Form.Item
              noStyle
              shouldUpdate={(prev, next) => prev.sourceType !== next.sourceType}
            >
              {({ getFieldValue }) => {
                const hint = UPDATE_SOURCES.find(([value]) => value === getFieldValue("sourceType"))?.[2];
                return (
                  <Form.Item name="sourceId" label="来源标识" style={{ flex: 1 }} rules={[{ required: true, message: "请填写来源标识" }]}>
                    <Input placeholder={hint} />
                  </Form.Item>
                );
              }}
            </Form.Item>
          </Flex>
          <Form.Item name="targetFunctionId" label={<span>目标功能 <Typography.Text type="secondary" style={{ fontWeight: 400 }}>可选</Typography.Text></span>}>
            <Input placeholder="已有功能 ID 或名称" />
          </Form.Item>
          <Form.Item name="content" label="需要分析的内容" rules={[{ required: true, message: "请描述新增或变化的业务事实" }]}>
            <Input.TextArea rows={4} placeholder="说明新增或变化的业务事实。入口、调用链和代码证据由 Agent 自动检索。" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

function adminId(item) {
  return item?.id || item?.proposalId || item?.proposal_id || "";
}

function govTitle(item, tab) {
  return item?.title || item?.functionName || item?.function_name || item?.targetName || item?.name || (tab === "functions" ? "未命名功能" : "未命名知识变更");
}

function statusTag(status) {
  const value = String(status || "").toUpperCase();
  const map = {
    PENDING: ["gold", "待审核"], PENDING_REVIEW: ["gold", "待审核"], PROPOSED: ["gold", "待审核"],
    APPROVED: ["green", "已接受"], ACCEPTED: ["green", "已接受"], PUBLISHED: ["green", "已发布"],
    REJECTED: ["red", "已驳回"], DEFERRED: ["default", "已暂缓"], CHANGES_REQUESTED: ["orange", "待修改"],
    ACTIVE: ["green", "生效中"],
  };
  const [color, label] = map[value] || ["default", value || "—"];
  return <Tag color={color} style={{ fontSize: 10.5 }}>{label}</Tag>;
}

function GovRow({ item, tab, active, onSelect }) {
  return (
    <button className={`r-row ${active ? "on" : ""}`} onClick={onSelect}>
      <span className={`r-glyph ${tab === "functions" ? "biz" : "warn"}`}>{tab === "functions" ? "知" : "!"}</span>
      <span className="r-copy">
        <b>{govTitle(item, tab)}</b>
        <small>{item.summary || item.reason || item.changeType || item.domain || "等待确认业务含义"}</small>
        <small style={{ color: "#a3a29a" }}>{item.triggerType || item.sourceType || "知识更新 Agent"} · {formatTime(item.createdAt || item.created_at)}</small>
      </span>
      {statusTag(item.reviewStatus || item.review_status || item.status)}
    </button>
  );
}

function ProposalDetailCard({ item, submitting, comment, setComment, onReview }) {
  const before = item.before ?? item.previousContent ?? item.previous_content ?? item.diff?.before;
  const after = item.after ?? item.proposedContent ?? item.proposed_content ?? item.diff?.after;
  const evidence = item.evidence || item.evidences || item.sources || [];
  const impact = item.affectedFunctions || item.affected_functions || item.impact || [];
  const status = String(item.reviewStatus || item.review_status || item.status || "PENDING").toUpperCase();
  const actionable = ["PENDING", "PENDING_REVIEW", "PROPOSED", "DEFERRED", "CHANGES_REQUESTED"].includes(status);

  const evList = Array.isArray(evidence) ? evidence : evidence ? [evidence] : [];

  return (
    <Card styles={{ body: { padding: "20px 24px" } }}>
      <Flex justify="space-between" align="flex-start" gap={16} style={{ paddingBottom: 14, borderBottom: "1px solid #ebeae4" }}>
        <div style={{ minWidth: 0 }}>
          <Typography.Text type="secondary" code style={{ fontSize: 11 }}>{adminId(item)}</Typography.Text>
          <Typography.Title level={5} style={{ margin: "6px 0 4px" }}>{govTitle(item)}</Typography.Title>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 12.5, lineHeight: 1.7 }}>
            {item.reason || item.summary || item.description || "知识更新 Agent 发现了可能影响业务含义的变化。"}
          </Typography.Paragraph>
        </div>
        {statusTag(item.status)}
      </Flex>

      <SectionLabel text="知识差异" />
      <div className="diff-grid">
        <div className="diff-card before"><span>当前已发布</span><p>{contentText(before, "当前没有对应的已发布内容，可能是新增知识。")}</p></div>
        <div className="diff-card after"><span>建议更新为</span><p>{contentText(after, "提案没有提供可展示的结构化差异。")}</p></div>
      </div>

      <SectionLabel text="来源证据" />
      {evList.length ? (
        <Space direction="vertical" size={6} style={{ width: "100%" }}>
          {evList.map((entry, index) => (
            <div key={entry.id || index} className="ev-line">
              <Tag bordered={false} color={(entry.sourceType || entry.type || "CODE").toUpperCase() === "REQUIREMENT" ? "gold" : "blue"} style={{ fontSize: 10 }}>
                {(entry.sourceType || entry.type || "CODE").toUpperCase()}
              </Tag>
              <b>{entry.title || entry.symbol || entry.path || entry.id || `证据 ${index + 1}`}</b>
              <p>{entry.content || entry.excerpt || entry.summary || entry.location || "已关联结构化证据引用"}</p>
            </div>
          ))}
        </Space>
      ) : (
        <Alert type="warning" showIcon title="该提案尚未返回可展示的证据，不建议直接接受。" />
      )}

      <SectionLabel text="影响范围" />
      <Flex gap={6} wrap="wrap">
        {(Array.isArray(impact) ? impact : impact ? [impact] : []).map((entry, index) => (
          <Tag key={typeof entry === "string" ? entry : index} bordered={false}>{typeof entry === "string" ? entry : entry.name || entry.title || entry.id}</Tag>
        ))}
        {(!impact || impact.length === 0) && <Typography.Text type="secondary" style={{ fontSize: 12 }}>未发现其他功能受到影响。</Typography.Text>}
      </Flex>

      {actionable && (
        <>
          <SectionLabel text="审核操作" />
          <Input.TextArea
            rows={2}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="补充接受、驳回或暂缓的原因（可选）"
            style={{ marginBottom: 10 }}
          />
          <Flex justify="flex-end" gap={8}>
            <Button icon={<Minus size={13} />} disabled={Boolean(submitting)} onClick={() => onReview("DEFER")}>暂缓</Button>
            <Button danger icon={<X size={13.5} weight="bold" />} disabled={Boolean(submitting)} onClick={() => onReview("REJECT")}>驳回</Button>
            <Button type="primary" icon={<Check size={13.5} weight="bold" />} loading={submitting === "ACCEPT"} onClick={() => onReview("ACCEPT")}>接受并发布</Button>
          </Flex>
        </>
      )}
    </Card>
  );
}

function FunctionDetailCard({ item }) {
  const entries = asList(item.entries || item.functionEntries || item.function_entries);
  const scenarios = asList(item.scenarios);
  const rules = asList(item.rules || item.businessRules || item.business_rules);
  const impacts = asList(item.dataImpacts || item.data_impacts);
  return (
    <Card styles={{ body: { padding: "20px 24px" } }}>
      <Flex justify="space-between" align="flex-start">
        <div>
          <Typography.Text type="secondary" code style={{ fontSize: 11 }}>{adminId(item)} · {item.version ? `V${item.version}` : "当前版本"}</Typography.Text>
          <Typography.Title level={5} style={{ margin: "6px 0 4px" }}>{govTitle(item, "functions")}</Typography.Title>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 12.5 }}>{item.summary || item.description || "暂无功能摘要。"}</Typography.Paragraph>
        </div>
        {statusTag(item.status || "PUBLISHED")}
      </Flex>
      <SectionLabel text="功能入口" />
      <Flex gap={6} wrap="wrap">{entries.length ? entries.map((it, i) => <Tag key={i} bordered={false}>{typeof it === "string" ? it : [it.type || it.entry_type, it.name].filter(Boolean).join(" · ")}</Tag>) : <EmptyInline />}</Flex>
      <SectionLabel text="业务场景" />
      <Lines items={scenarios} />
      <SectionLabel text="业务规则" />
      <Lines items={rules} />
      <SectionLabel text="数据影响" />
      <Lines items={impacts} />
      <SectionLabel text="证据覆盖" />
      <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>{item.evidenceCount ?? item.evidence_count ?? 0} 条有效证据 · 最近更新 {formatTime(item.updatedAt || item.updated_at) || "未知"}</Typography.Text>
    </Card>
  );
}

function asList(value) { return Array.isArray(value) ? value : value ? [value] : []; }

function Lines({ items }) {
  if (!asList(items).length) return <EmptyInline />;
  return (
    <Space direction="vertical" size={5} style={{ width: "100%" }}>
      {asList(items).map((item, index) => (
        <div key={index} className="knowledge-line">{typeof item === "string" ? item : item.statement || item.name || JSON.stringify(item)}</div>
      ))}
    </Space>
  );
}

function EmptyInline() {
  return <Typography.Text type="secondary" style={{ fontSize: 12 }}>暂未沉淀。</Typography.Text>;
}

function SectionLabel({ text }) {
  return (
    <Typography.Text type="secondary" style={{ fontSize: 11, letterSpacing: ".06em", display: "block", margin: "16px 0 8px" }}>
      {text}
    </Typography.Text>
  );
}

function contentText(value, fallback) {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "string") return value;
  return value.statement || value.summary || value.content || JSON.stringify(value, null, 2);
}

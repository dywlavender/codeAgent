import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp, Copy, Lightning, Stop, ThumbsDown, ThumbsUp,
} from "@phosphor-icons/react";
import {
  Alert, Avatar, Button, Card, Collapse, Divider, Drawer, Empty, Flex,
  Input, Segmented, Skeleton, Splitter, Tag, Timeline, Tooltip, Typography,
} from "antd";
import { request } from "../lib/api.js";
import {
  answerModeLabel, formatLocation, groupEvidence,
  intentLabel, normalizeSteps, sourceLabel, statusLabel, stepLabel, stepSummary,
} from "../lib/format.js";

const EXAMPLES = [
  "提款的时候为什么要校验 repayType？",
  "repayType 字段在哪里生成、读取和校验？",
  "申请阶段和提款阶段之间是什么关系？",
];

const SOURCE_COLOR = { CODE: "#6e7781", BUSINESS: "#1a7f4e", REQUIREMENT: "#9a6700" };
const SOURCE_HEX = { CODE: "default", BUSINESS: "default", REQUIREMENT: "default" };

export function AgentPage(props) {
  const { result, turns, activeTurnId } = props;
  const [viewMode, setViewMode] = useState("阅读");
  const [drawerEv, setDrawerEv] = useState(null);
  const loading = turns.find((turn) => turn.id === activeTurnId)?.status === "loading";

  return (
    <div style={{ height: "calc(100dvh)", display: "flex", flexDirection: "column", background: "#fff" }}>
      <header className="agent-topbar">
        <Typography.Text strong>{props.workspace?.project || "载入项目中"}</Typography.Text>
        <span className="topbar-sep">·</span>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          知识源已连接{props.workspace?.counts ? ` · ${props.workspace.counts.facts} facts` : ""}
          </Typography.Text>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10, alignItems: "center" }}>
          {result && <StatusTag status={result.evidenceStatus} />}
          {result && <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>{result.iterations} 轮证据扩展</Typography.Text>}
        </div>
      </header>

      <Splitter style={{ flex: 1, minHeight: 0 }}>
        <Splitter.Panel defaultSize="62%" min="40%" max="80%">
          <ChatColumn {...props} viewMode={viewMode} setViewMode={setViewMode} setDrawerEv={setDrawerEv} loading={loading} />
        </Splitter.Panel>
        <Splitter.Panel
          key={viewMode}
          collapsible
          defaultSize={viewMode === "对照" ? "38%" : "0%"}
          min="0%"
          max="60%"
        >
          <EvidencePane result={result} loading={loading} mode={viewMode} />
        </Splitter.Panel>
      </Splitter>

      <Drawer
        open={Boolean(drawerEv)}
        onClose={() => setDrawerEv(null)}
        width={480}
        title={drawerEv ? (
          <Flex gap={8} align="center">
            <Tag color={SOURCE_HEX[drawerEv.sourceType]} style={{ marginInlineEnd: 0 }}>{drawerEv.evidenceId}</Tag>
            <span style={{ fontWeight: 600 }}>{evidenceTitle(drawerEv)}</span>
          </Flex>
        ) : null}
      >
        {drawerEv && <EvidenceDetail item={drawerEv} />}
      </Drawer>
    </div>
  );
}

function StatusTag({ status }) {
  if (!status) return null;
  const map = {
    SUFFICIENT: { color: "green", label: "证据充分" },
    INSUFFICIENT: { color: "orange", label: "证据不足" },
    CONFLICT: { color: "red", label: "存在冲突" },
  };
  const conf = map[status] || { color: "default", label: statusLabel(status) };
  return <Tag color={conf.color} style={{ marginInlineEnd: 0 }}>{conf.label}</Tag>;
}

function ChatColumn({ turns, question, setQuestion, submit, stopQuery, status, error, viewMode, setViewMode, setDrawerEv, newConversation }) {
  const composerRef = useRef(null);
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const el = document.activeElement;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      event.preventDefault();
      composerRef.current?.focus({ cursor: "end" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="chat-column">
      <div className="chat-scroll">
        {turns.length === 0 ? (
          <Welcome submit={submit} />
        ) : (
          <div className="turn-list">
            {turns.map((turn) => (
              <TurnBlock key={turn.id} turn={turn} submit={submit} viewMode={viewMode} setViewMode={setViewMode} setDrawerEv={setDrawerEv} />
            ))}
          </div>
        )}
      </div>
      <div className="composer-stick">
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 10 }} closable />}
        <Composer
          composerRef={composerRef}
          question={question}
          setQuestion={setQuestion}
          submit={submit}
          stopQuery={stopQuery}
          status={status}
          hasTurns={turns.length > 0}
          onNew={newConversation}
        />
      </div>
    </div>
  );
}

function Welcome({ submit }) {
  return (
    <div className="welcome">
      <Avatar shape="square" size={54} style={{ background: "#141413", fontSize: 20, fontWeight: 700, fontFamily: "monospace", borderRadius: 14 }}>{"{}"}</Avatar>
      <Typography.Title level={3} style={{ marginTop: 22, marginBottom: 8, letterSpacing: "-.02em" }}>
        今天想弄清楚什么？
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ maxWidth: 430, marginInline: "auto", lineHeight: 1.85, fontSize: 13.5 }}>
        我会同时检索代码事实、已发布的业务知识和需求原文。每个结论都带证据编号，没有证据的部分会明确留空。
      </Typography.Paragraph>
      <Flex gap={10} justify="center" wrap="wrap" style={{ marginTop: 24 }}>
        {EXAMPLES.map((item) => (
          <Button key={item} shape="round" size="middle" onClick={() => submit(item)} style={{ borderColor: "#deddd5" }}>
            {item}
          </Button>
        ))}
      </Flex>
    </div>
  );
}

function TurnBlock({ turn, submit, viewMode, setViewMode, setDrawerEv }) {
  return (
    <div className="turn-block">
      <div className="user-row"><span className="user-bubble">{turn.question}</span></div>
      <div className="answer-row">
        <Avatar shape="square" size={28} style={{ background: "#141413", fontFamily: "monospace", fontWeight: 700, fontSize: 11, flexShrink: 0, borderRadius: 9 }}>{"{}"}</Avatar>
        <div style={{ minWidth: 0, flex: 1 }}>
          <Flex align="center" gap={8} style={{ marginBottom: 12 }}>
            <Typography.Text strong style={{ fontSize: 13 }}>Code Atlas</Typography.Text>
            {turn.status === "success" && (
              <>
                <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
                  {intentLabel(turn.result.intent)} · {answerModeLabel(turn.result.answerMode || turn.result.answer_mode) || ""}
                </Typography.Text>
                <Segmented
                  size="small"
                  value={viewMode}
                  onChange={setViewMode}
                  options={["阅读", "对照"]}
                  style={{ marginLeft: "auto" }}
                />
              </>
            )}
          </Flex>
          {turn.status === "loading" && <AnswerSkeleton />}
          {turn.status === "error" && <Alert type="error" showIcon message="分析失败" description={turn.error} />}
          {turn.status === "success" && <AnswerDocument result={turn.result} detail={turn.detail} submit={submit} viewMode={viewMode} setDrawerEv={setDrawerEv} />}
        </div>
      </div>
    </div>
  );
}

function AnswerSkeleton() {
  return <Skeleton active paragraph={{ rows: 6 }} />;
}

function AnswerDocument({ result, detail, submit, viewMode, setDrawerEv }) {
  const { answer } = result;
  const evidenceById = useMemo(() => {
    const map = {};
    (result.evidence || []).forEach((item) => { map[item.evidenceId] = item; });
    return map;
  }, [result]);
  const suggestedFollowUps = result.suggestedFollowUps || result.suggested_follow_ups || answer.suggestedFollowUps || [];
  const resolvedQuestion = result.resolvedQuestion || result.resolved_question;

  const citedIds = collectCitedIds(answer);
  const codeFacts = answer.facts.filter((f) => f.sourceType === "CODE");
  const businessFacts = answer.facts.filter((f) => f.sourceType === "BUSINESS");
  const requirementFacts = answer.facts.filter((f) => f.sourceType === "REQUIREMENT");
  const allFacts = [...codeFacts, ...businessFacts, ...requirementFacts];

  const readView = (
    <div className="doc">
      <DocSection label="结论">
        <Typography.Paragraph style={{ fontSize: 15.5, lineHeight: 1.85, marginBottom: citedIds.length ? 10 : 0 }}>
          {answer.conclusion}
        </Typography.Paragraph>
        {citedIds.length > 0 && (
          <Flex gap={6} wrap="wrap">
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>引用：</Typography.Text>
            {citedIds.map((id) => (
              <EvTag key={id} id={id} item={evidenceById[id]} onOpen={() => evidenceById[id] && setDrawerEv(evidenceById[id])} />
            ))}
          </Flex>
        )}
      </DocSection>

      <DocSection label="业务流程">
        {answer.businessFlow?.length ? (
          <Timeline
            items={answer.businessFlow.map((item, index) => ({
              color: "green",
              children: <span style={{ lineHeight: 1.75 }}>{item.statement || item.step || String(item)}</span>,
              key: index,
            }))}
          />
        ) : <EmptyText text="现有证据未形成可确认的完整业务链路。" />}
      </DocSection>

      <DocSection label={`已确认事实${allFacts.length ? ` · ${allFacts.length}` : ""}`}>
        {allFacts.length ? allFacts.map((fact, index) => (
          <FactRow key={`${fact.statement}-${index}`} fact={fact} evidenceById={evidenceById} setDrawerEv={setDrawerEv} />
        )) : <EmptyText text="当前没有已确认事实。" />}
      </DocSection>

      {(answer.inferences?.length > 0) && (
        <DocSection label="推断">
          <ul className="infer-list">
            {answer.inferences.map((item, index) => <li key={index}>{item.statement || item}</li>)}
          </ul>
        </DocSection>
      )}

      <DocSection label="待确认" last>
        {answer.conflicts.map((item, index) => (
          <Alert key={`c-${index}`} type="error" showIcon message="证据冲突" description={item.reason || "不同来源给出了不一致的结论。"} style={{ marginBottom: 8 }} />
        ))}
        {answer.unknowns.map((item, index) => (
          <Alert key={`u-${index}`} type="warning" showIcon message="未确认" description={item} style={{ marginBottom: 8 }} />
        ))}
        {!answer.conflicts.length && !answer.unknowns.length && (
          <Alert type="success" message="证据闭合" description="当前回答没有遗留的证据缺口。" />
        )}
      </DocSection>
    </div>
  );

  const compareView = (
    <CompareView result={result} evidenceById={evidenceById} setDrawerEv={setDrawerEv} />
  );

  return (
    <div>
      {resolvedQuestion && (
        <div className="resolved-q"><span>本轮理解</span><p>{resolvedQuestion}</p></div>
      )}
      {viewMode === "阅读" ? readView : compareView}
      {detail && (
        <Collapse
          ghost
          size="small"
          items={[{
            key: "run",
            label: <span style={{ fontSize: 12, color: "#75746b" }}>运行轨迹 · {normalizeSteps(detail).length} 步</span>,
            children: <RunSteps detail={detail} />,
          }]}
          style={{ marginTop: 4 }}
        />
      )}
      <Divider style={{ margin: "10px 0 12px" }} />
      <Flex gap={6} wrap="wrap" align="center">
        <CopyButton detail={detail} />
        <FeedbackButtons detail={detail} />
        {viewMode === "阅读"
          ? <Button size="small" type="text" icon={<Lightning size={13} />} onClick={() => setViewMode("对照")} style={{ color: "#75746b", marginLeft: "auto" }}>证据对照 ⚖</Button>
          : <Button size="small" type="text" onClick={() => setViewMode("阅读")} style={{ color: "#75746b", marginLeft: "auto" }}>← 返回阅读</Button>}
      </Flex>
      {suggestedFollowUps.length > 0 && (
        <Flex gap={8} wrap="wrap" style={{ marginTop: 12 }}>
          {suggestedFollowUps.slice(0, 3).map((item, index) => {
            const text = typeof item === "string" ? item : item.question || item.text || item.label;
            return text
              ? <Button key={`${text}-${index}`} shape="round" size="small" onClick={() => submit(text)}>{text} ›</Button>
              : null;
          })}
        </Flex>
      )}
    </div>
  );
}

function CompareView({ result, evidenceById, setDrawerEv }) {
  const grouped = groupEvidence(result.evidence);
  const { answer } = result;
  return (
    <Card size="small" styles={{ body: { padding: "14px 18px" } }} title={<Typography.Text strong style={{ fontSize: 13 }}>结论与链路（引用已对齐到右侧）</Typography.Text>}>
      <Typography.Paragraph style={{ fontSize: 14, lineHeight: 1.85 }}>{answer.conclusion}</Typography.Paragraph>
      {answer.businessFlow?.length > 0 && (
        <Timeline
          style={{ marginTop: 8 }}
          items={answer.businessFlow.map((item, index) => ({ color: "green", children: item.statement || item.step || String(item), key: index }))}
        />
      )}
      {[["CODE", "代码证据"], ["BUSINESS", "业务知识"], ["REQUIREMENT", "需求依据"]].map(([type, label]) => (
        grouped[type]?.length > 0 && (
          <div key={type} style={{ marginTop: 16 }}>
            <Typography.Text type="secondary" style={{ fontSize: 11, letterSpacing: ".05em", display: "block", marginBottom: 8 }}>
              {label.toUpperCase()} · {label} · {grouped[type].length} 条
            </Typography.Text>
            <Flex vertical gap={8}>
              {grouped[type].map((item) => (
                <Card
                  key={item.evidenceId}
                  size="small"
                  hoverable
                  onClick={() => setDrawerEv(item)}
                  styles={{ body: { padding: "9px 13px" } }}
                  title={
                    <Flex gap={8} align="center">
                      <Tag color={SOURCE_HEX[item.sourceType]} style={{ marginInlineEnd: 0 }}>{item.evidenceId}</Tag>
                      <Typography.Text style={{ fontSize: 12, fontFamily: "monospace" }}>{evidenceTitle(item)}</Typography.Text>
                    </Flex>
                  }
                  extra={<Typography.Text type="secondary" style={{ fontSize: 10.5 }}>{formatLocation(item.location)}</Typography.Text>}
                >
                  <pre className="ev-pre">{(item.content || "该 Evidence 仅包含结构化引用。").slice(0, 220)}</pre>
                </Card>
              ))}
            </Flex>
          </div>
        )
      ))}
    </Card>
  );
}

function FactRow({ fact, evidenceById, setDrawerEv }) {
  const linked = (fact.evidenceIds || []).map((id) => evidenceById[id]).filter(Boolean);
  const primary = linked[0];
  return (
    <div className="fact-row">
      <Flex gap={10} align="flex-start">
        <span className="fact-dot" style={{ background: SOURCE_COLOR[fact.sourceType] || "#a3a29a" }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ lineHeight: 1.75 }}>{fact.statement}</div>
          <Flex gap={8} align="center" style={{ marginTop: 4 }} wrap="wrap">
            <Tag bordered={false} style={{ fontSize: 10.5, lineHeight: "17px", marginInlineEnd: 0, background: "#f7f7f5", color: "#6f6f69" }}>
              {sourceLabel(fact.sourceType)}
            </Tag>
            {(fact.evidenceIds || []).map((id) => (
              <EvTag key={id} id={id} item={evidenceById[id]} small onOpen={() => evidenceById[id] && setDrawerEv(evidenceById[id])} />
            ))}
          </Flex>
          {primary && primary.content && (
            <details className="inline-ev">
              <summary>展开原文{primary.location?.file ? ` · ${formatLocation(primary.location)}` : ""}</summary>
              <pre className="ev-pre">{primary.content}</pre>
            </details>
          )}
        </div>
      </Flex>
    </div>
  );
}

function EvTag({ id, item, onOpen, small }) {
  return (
    <Tooltip title={item ? "点击查看证据详情" : "该证据仅在历史记录中"}>
      <Tag
        color="default"
        style={{ cursor: item ? "pointer" : "default", fontSize: small ? 10.5 : 11, marginInlineEnd: 0, borderRadius: 99, fontFamily: "monospace", borderColor: item ? "#cfd3cb" : "#ececeb", color: item ? "#57606a" : "#a3a29c" }}
        onClick={onOpen}
      >
        {id}
      </Tag>
    </Tooltip>
  );
}

function DocSection({ label, children, last }) {
  return (
    <section className={`doc-section ${last ? "last" : ""}`}>
      <Typography.Text type="secondary" className="doc-label">{label}</Typography.Text>
      <div>{children}</div>
    </section>
  );
}

function EmptyText({ text }) {
  return <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>{text}</Typography.Text>;
}

function EvidenceDetail({ item }) {
  return (
    <div>
      <Flex gap={6} wrap="wrap" style={{ marginBottom: 12 }}>
        <Tag color={SOURCE_HEX[item.sourceType]}>{sourceLabel(item.sourceType)}</Tag>
        {item.status && <Tag>{statusLabel(item.status) || item.status}</Tag>}
        {item.location?.file && <Tag>{formatLocation(item.location)}</Tag>}
      </Flex>
      <pre className="ev-pre drawer">{item.content || "该 Evidence 仅包含结构化引用，详细内容见原始来源。"}</pre>
    </div>
  );
}

function EvidencePane({ result, loading, mode }) {
  const grouped = groupEvidence(result?.evidence);
  const total = result?.evidence?.length ?? 0;
  const isEmpty = !result || total === 0;
  return (
    <div className={`evidence-pane ${mode === "对照" ? "open" : ""}`}>
      <div className="pane-head">
        <Typography.Text strong style={{ fontSize: 12.5 }}>Evidence</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>{total} 条 · 结论所依据的原始证据</Typography.Text>
      </div>
      <div className="pane-body">
        {loading ? <Skeleton active paragraph={{ rows: 8 }} style={{ padding: 16 }} /> : isEmpty ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span style={{ fontSize: 12 }}>提交问题后，三源证据会在这里分组展示。<br/>也可以在回答中切换「对照」模式。</span>} style={{ marginTop: 60 }} />
        ) : (
          ["CODE", "BUSINESS", "REQUIREMENT"].map((type) => grouped[type]?.length > 0 && (
            <div key={type} className="ev-group">
              <div className="ev-group-label">
                {sourceLabel(type)} <span>{grouped[type].length}</span>
              </div>
              {grouped[type].map((item) => (
                <Card key={item.evidenceId} size="small" className="ev-mini-card" styles={{ body: { padding: "8px 12px" } }}>
                  <Flex gap={7} align="center">
                    <Tag color={SOURCE_HEX[item.sourceType]} style={{ marginInlineEnd: 0, fontSize: 10, fontFamily: "monospace" }}>{item.evidenceId}</Tag>
                    <Typography.Text ellipsis style={{ flex: 1, fontSize: 11.5, fontFamily: "monospace" }}>{evidenceTitle(item)}</Typography.Text>
                  </Flex>
                  <Typography.Paragraph ellipsis={{ rows: 2 }} type="secondary" style={{ fontSize: 10.5, marginTop: 6, marginBottom: 0 }}>
                    {(item.content || "仅包含结构化引用。").slice(0, 120)}
                  </Typography.Paragraph>
                </Card>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function RunSteps({ detail }) {
  const steps = normalizeSteps(detail);
  return (
    <Timeline
      style={{ padding: "6px 0 0 6px" }}
      items={steps.map((step, index) => ({
        key: step.id || index,
        color: "green",
        children: (
          <div style={{ paddingBottom: 2 }}>
            <Flex gap={8} align="baseline">
              <Typography.Text strong style={{ fontSize: 12 }}>{stepLabel(step.step_name)}</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 10, fontFamily: "monospace" }}>{step.duration_ms?.toFixed?.(1) || step.duration_ms || 0} ms</Typography.Text>
            </Flex>
            <Typography.Paragraph type="secondary" style={{ fontSize: 11, margin: "2px 0 0" }}>{stepSummary(step)}</Typography.Paragraph>
            {step.tools?.length > 0 && (
              <Flex gap={4} wrap="wrap" style={{ marginTop: 5 }}>
                {step.tools.map((tool) => (
                  <Tag key={tool.id} bordered={false} style={{ fontSize: 10, fontFamily: "monospace" }}>{tool.tool_name} {tool.result_count}</Tag>
                ))}
              </Flex>
            )}
          </div>
        ),
      }))}
    />
  );
}

function CopyButton({ detail }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    const text = detail?.answer?.conclusion || "";
    if (text && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    }
  }
  return (
    <Button size="small" type="text" icon={<Copy size={13} />} onClick={copy} style={{ color: "#75746b", fontSize: 12 }}>
      {copied ? "已复制" : "复制结论"}
    </Button>
  );
}

function FeedbackButtons({ detail }) {
  const [feedback, setFeedback] = useState("");
  async function send(rating) {
    if (!detail?.id || feedback) return;
    try {
      await request(`/api/query/${encodeURIComponent(detail.id)}/feedback`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating }),
      });
      setFeedback(rating);
    } catch { /* feedback must not interrupt the answer */ }
  }
  return (
    <Flex gap={2}>
      <Tooltip title="有帮助">
        <Button size="small" type="text" icon={<ThumbsUp size={13.5} />} onClick={() => send("HELPFUL")}
          style={feedback === "HELPFUL" ? { color: "#141413" } : { color: "#75746b" }} />
      </Tooltip>
      <Tooltip title="没有帮助">
        <Button size="small" type="text" icon={<ThumbsDown size={13.5} />} onClick={() => send("NOT_HELPFUL")}
          style={feedback === "NOT_HELPFUL" ? { color: "#cf222e" } : { color: "#75746b" }} />
      </Tooltip>
    </Flex>
  );
}

function Composer({ composerRef, question, setQuestion, submit, stopQuery, status }) {
  const loading = status === "loading";
  return (
    <div className="composer">
      <Input.TextArea
        ref={composerRef}
        variant="borderless"
        autoSize={{ minRows: 1, maxRows: 8 }}
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder="输入业务问题…"
        style={{ fontSize: 14 }}
      />
      <Flex align="center" style={{ marginTop: 6 }}>
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          检索代码 · 业务知识 · 需求三源证据　按 / 聚焦
        </Typography.Text>
        {loading
          ? <Button shape="circle" icon={<Stop size={13} weight="bold" />} onClick={stopQuery} danger style={{ marginLeft: "auto" }} />
          : <Button shape="circle" type="primary" icon={<ArrowUp size={15} weight="bold" />} disabled={!question.trim()} onClick={() => submit()} style={{ marginLeft: "auto" }} />}
      </Flex>
    </div>
  );
}

function collectCitedIds(answer) {
  const ids = [];
  const pushAll = (list) => (list || []).forEach((id) => { if (!ids.includes(id)) ids.push(id); });
  pushAll(answer.conclusion?.match(/EV-[A-Za-z0-9-]+/g));
  (answer.facts || []).forEach((fact) => pushAll(fact.evidenceIds));
  return ids.slice(0, 8);
}

function evidenceTitle(item) {
  if (!item) return "";
  if (item.sourceType === "CODE") return item.symbol || item.sourceId;
  if (item.sourceType === "BUSINESS") return `${item.sourceId} 业务事实`;
  return `${item.sourceId} 需求证据`;
}

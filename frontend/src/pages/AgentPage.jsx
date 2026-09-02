import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp, Copy, Lightning, Stop, ThumbsDown, ThumbsUp,
} from "@phosphor-icons/react";
import {
  Alert, Avatar, Button, Card, Collapse, Divider, Drawer, Empty, Flex,
  Input, Segmented, Skeleton, Splitter, Tag, Timeline, Tooltip, Typography,
} from "antd";
import { request } from "../lib/api.js";
import { palette, sourceMeta } from "../theme.js";
import {
  answerModeLabel, answerTypeLabel, formatLocation, groupEvidence,
  intentLabel, normalizeSteps, sourceLabel, statusLabel, stepLabel, stepSummary,
  synthesisSkippedReasonLabel,
} from "../lib/format.js";

const EXAMPLES = [
  "提款的时候为什么要校验 repayType？",
  "repayType 字段在哪里生成、读取和校验？",
  "申请阶段和提款阶段之间是什么关系？",
];

const READ_SIZES = ["100%", "0%"];
const COMPARE_SIZES = ["62%", "38%"];

export function AgentPage(props) {
  const { result, turns, activeTurnId } = props;
  const [viewMode, setViewMode] = useState("阅读");
  const [paneSizes, setPaneSizes] = useState(READ_SIZES);
  const [drawerEv, setDrawerEv] = useState(null);
  const loading = turns.find((turn) => turn.id === activeTurnId)?.status === "loading";

  useEffect(() => {
    // 受控尺寸：antd Splitter 的 defaultSize 在重渲染时不可靠，切视图必须显式赋值
    setPaneSizes(viewMode === "对照" ? COMPARE_SIZES : READ_SIZES);
  }, [viewMode]);

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
          {result && <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
            {String(result.answerMode || "").toUpperCase() === "MODEL_AGENT"
              ? `${result.metrics?.sourceReadCount || result.sourceReferences?.length || 0} 次源码阅读`
              : `${result.iterations} 轮证据扩展`}
          </Typography.Text>}
        </div>
      </header>

      <Splitter onResize={setPaneSizes} style={{ flex: 1, minHeight: 0, background: "#fff" }}>
        <Splitter.Panel size={paneSizes[0]} min="40%" max="100%">
          <ChatColumn {...props} viewMode={viewMode} setViewMode={setViewMode} setDrawerEv={setDrawerEv} loading={loading} />
        </Splitter.Panel>
        <Splitter.Panel size={paneSizes[1]} min="0%" max="60%">
          <EvidencePane result={result} loading={loading} mode={viewMode} setDrawerEv={setDrawerEv} />
        </Splitter.Panel>
      </Splitter>

      <Drawer
        open={Boolean(drawerEv)}
        onClose={() => setDrawerEv(null)}
        width={480}
        title={drawerEv ? (
          <Flex gap={8} align="center">
            <EvTag id={drawerEv.evidenceId} item={drawerEv} />
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
  const scrollRef = useRef(null);
  const stickRef = useRef(true);

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

  const lastTurnId = turns.length ? turns[turns.length - 1].id : null;

  useEffect(() => {
    // 新一轮提问（或恢复历史）时始终滚到最新消息
    const el = scrollRef.current;
    if (el) {
      stickRef.current = true;
      el.scrollTop = el.scrollHeight;
    }
  }, [lastTurnId]);

  useEffect(() => {
    // 回答加载等高度变化只在用户本就停在底部时跟随
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [turns]);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90;
  }

  return (
    <div className="chat-column">
      <div className="chat-scroll" ref={scrollRef} onScroll={handleScroll}>
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
        {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 10 }} closable />}
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
      <Avatar shape="square" size={54} style={{ background: "#211E1A", fontSize: 20, fontWeight: 700, fontFamily: "monospace", borderRadius: 14, color: "#F4F2ED" }}>{"{}"}</Avatar>
      <Typography.Title level={3} style={{ marginTop: 22, marginBottom: 8, letterSpacing: "-.02em" }}>
        今天想弄清楚什么？
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ maxWidth: 430, marginInline: "auto", lineHeight: 1.85, fontSize: 13.5 }}>
        我会先用业务知识和代码索引定位，再实际读取源码。回答中的代码结论都会带本次源码引用；没有读到的部分会明确留空。
      </Typography.Paragraph>
      <Flex gap={10} justify="center" wrap="wrap" style={{ marginTop: 24 }}>
        {EXAMPLES.map((item) => (
          <Button key={item} shape="round" size="middle" onClick={() => submit(item)}>
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
        <Avatar shape="square" size={28} style={{ background: "#211E1A", fontFamily: "monospace", fontWeight: 700, fontSize: 11, flexShrink: 0, borderRadius: 9, color: "#F4F2ED" }}>{"{}"}</Avatar>
        <div style={{ minWidth: 0, flex: 1 }}>
          <Flex align="center" gap={8} style={{ marginBottom: 12 }}>
            <Typography.Text strong style={{ fontSize: 13 }}>Code Atlas</Typography.Text>
            {turn.status === "success" && (
              <>
                <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
                  {[
                    intentLabel(turn.result.intent),
                    answerTypeLabel(turn.result.answerType || turn.result.answer_type || turn.result.answer?.answerType),
                    answerModeLabel(turn.result.answerMode || turn.result.answer_mode),
                  ].filter(Boolean).join(" · ")}
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
          {turn.status === "error" && <Alert type="error" showIcon title="分析失败" description={turn.error} />}
          {turn.status === "success" && (
            <AnswerDocument
              result={turn.result}
              detail={turn.detail}
              submit={submit}
              viewMode={viewMode}
              setViewMode={setViewMode}
              setDrawerEv={setDrawerEv}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function AnswerSkeleton() {
  return <Skeleton active paragraph={{ rows: 6 }} />;
}

function AnswerDocument({ result, detail, submit, viewMode, setViewMode, setDrawerEv }) {
  const answer = result.answer || {};
  const factsList = Array.isArray(answer.facts) ? answer.facts : [];
  const conflictList = Array.isArray(answer.conflicts) ? answer.conflicts : [];
  const unknownList = Array.isArray(answer.unknowns) ? answer.unknowns : [];
  const flowList = Array.isArray(answer.businessFlow) ? answer.businessFlow : [];
  const technicalFlowList = Array.isArray(answer.technicalFlow) ? answer.technicalFlow : [];
  const inferenceList = Array.isArray(answer.inferences) ? answer.inferences : [];
  const answerType = String(
    result.answerType || result.answer_type || answer.answerType || answer.answer_type
      || detail?.answerType || detail?.answer_type || detail?.state?.answerType || detail?.state?.answer_type
      || (conflictList.length ? "CONFLICT" : factsList.length ? (unknownList.length ? "PARTIAL" : "FULL") : "UNKNOWN")
  ).toUpperCase();
  const synthesisSkippedReason = result.synthesisSkippedReason || result.synthesis_skipped_reason
    || answer.synthesisSkippedReason || answer.synthesis_skipped_reason
    || detail?.synthesisSkippedReason || detail?.synthesis_skipped_reason;
  const evidenceById = useMemo(() => {
    const map = {};
    (Array.isArray(result.evidence) ? result.evidence : []).forEach((item) => { if (item && item.evidenceId) map[item.evidenceId] = item; });
    return map;
  }, [result]);
  // SYM-xxx 是内部符号 ID；用证据里的限定名换算成可读短名
  const symbolNameById = useMemo(() => {
    const map = {};
    (Array.isArray(result.evidence) ? result.evidence : []).forEach((item) => {
      if (item?.sourceType === "CODE" && item.sourceId && item.symbol) map[item.sourceId] = item.symbol;
    });
    return map;
  }, [result]);
  const humanize = useMemo(() => {
    const cache = {};
    return (text) => {
      const raw = String(text ?? "");
      if (!/SYM-[A-Za-z0-9]+/.test(raw) && !/[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+){2,}/.test(raw)) return raw;
      if (cache[raw] === undefined) {
        cache[raw] = raw
          // 内部符号 ID 换成证据里的可读限定名
          .replace(/SYM-[A-Za-z0-9]+/g, (id) => {
            const full = symbolNameById[id];
            if (!full) return id;
            return String(full).split(".").slice(-2).join(".");
          })
          // 全限定类名缩短为「类.成员」，完整路径在证据卡片里可见
          .replace(/[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+){2,}/g, (full) => {
            const parts = full.split(".");
            return parts.slice(-2).join(".");
          });
      }
      return cache[raw];
    };
  }, [symbolNameById]);
  const suggestedFollowUps = result.suggestedFollowUps || result.suggested_follow_ups || answer.suggestedFollowUps || [];
  const resolvedQuestion = result.resolvedQuestion || result.resolved_question;

  const citedIds = collectCitedIds(answer);
  const codeFacts = factsList.filter((f) => f.sourceType === "CODE");
  const businessFacts = factsList.filter((f) => f.sourceType === "BUSINESS");
  const requirementFacts = factsList.filter((f) => f.sourceType === "REQUIREMENT");
  const allFacts = [...codeFacts, ...businessFacts, ...requirementFacts];

  if (!result.answer) {
    return <Alert type="warning" showIcon title="该记录没有可展示的回答数据" description="可能是早期版本或未完成的查询，仅保留了运行元信息。" />;
  }

  const readView = (
    <div className="doc">
      <DocSection label="结论">
        <Typography.Paragraph style={{ fontSize: 15.5, lineHeight: 1.85, marginBottom: citedIds.length ? 10 : 0 }}>
          {humanize(answer.conclusion)}
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
        {flowList.length ? (
          <Timeline
            items={flowList.map((item, index) => ({
              color: "green",
              children: <span style={{ lineHeight: 1.75 }}>{item.statement || item.step || String(item)}</span>,
              key: index,
            }))}
          />
        ) : <EmptyText text="现有证据未形成可确认的完整业务链路。" />}
      </DocSection>

      {technicalFlowList.length > 0 && (
        <DocSection label="技术调用链">
          <Timeline
            items={technicalFlowList.map((item, index) => ({
              color: "blue",
              children: <span style={{ lineHeight: 1.75 }}>{humanize(item.statement || item.step || String(item))}</span>,
              key: index,
            }))}
          />
        </DocSection>
      )}

      <DocSection label={`${answerType === "PARTIAL" ? "已确认" : "已确认事实"}${allFacts.length ? ` · ${allFacts.length}` : ""}`}>
        {allFacts.length ? allFacts.map((fact, index) => (
          <FactRow key={`${fact.statement}-${index}`} fact={fact} humanize={humanize} evidenceById={evidenceById} setDrawerEv={setDrawerEv} />
        )) : <EmptyText text="当前没有已确认事实。" />}
      </DocSection>

      {inferenceList.length > 0 && (
        <DocSection label="推断">
          <ul className="infer-list">
            {inferenceList.map((item, index) => <li key={index}>{humanize(item.statement || item)}</li>)}
          </ul>
        </DocSection>
      )}

      <DocSection label={answerType === "PARTIAL" || answerType === "UNKNOWN" ? "未确认" : "待确认"} last>
        {conflictList.map((item, index) => (
          <Alert key={`c-${index}`} type="error" showIcon title="证据冲突" description={item.reason || "不同来源给出了不一致的结论。"} style={{ marginBottom: 8 }} />
        ))}
        {unknownList.map((item, index) => (
          <Alert key={`u-${index}`} type="warning" showIcon title="未确认" description={item} style={{ marginBottom: 8 }} />
        ))}
        {!conflictList.length && !unknownList.length && (
          <Alert type="success" title="证据闭合" description="当前回答没有遗留的证据缺口。" />
        )}
      </DocSection>
    </div>
  );

  const compareView = (
    <CompareView result={result} evidenceById={evidenceById} setDrawerEv={setDrawerEv} humanize={humanize} />
  );

  return (
    <div>
      <AnswerTypeBanner answerType={answerType} reason={synthesisSkippedReason} />
      {resolvedQuestion && (
        <div className="resolved-q"><span>检索词</span><p>{resolvedQuestion}</p></div>
      )}
      {viewMode === "阅读" ? readView : compareView}
      {detail && (
        <Collapse
          ghost
          size="small"
          items={[{
            key: "run",
            label: <span style={{ fontSize: 12, color: "#7E786E" }}>运行轨迹 · {normalizeSteps(detail).length} 步</span>,
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
          ? <Button size="small" type="text" icon={<Lightning size={13} />} onClick={() => setViewMode("对照")} style={{ color: palette.brand, marginLeft: "auto" }}>证据对照</Button>
          : <Button size="small" type="text" onClick={() => setViewMode("阅读")} style={{ color: "#7E786E", marginLeft: "auto" }}>← 返回阅读</Button>}
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

function AnswerTypeBanner({ answerType, reason }) {
  const reasonText = synthesisSkippedReasonLabel(reason);
  if (answerType === "FULL") return null;
  if (answerType === "PARTIAL") {
    return (
      <Alert
        type="warning"
        showIcon
        title="当前结论基于部分证据"
        description={`下面分别列出已确认和未确认内容${reasonText ? `（回答模型未调用：${reasonText}）` : "。"}`}
        style={{ margin: "14px 18px 0" }}
      />
    );
  }
  if (answerType === "CONFLICT") {
    return (
      <Alert
        type="error"
        showIcon
        title="发现证据冲突"
        description={`不同来源给出了不一致的结论，当前不能合并为单一答案${reasonText ? `（${reasonText}）` : "。"}`}
        style={{ margin: "14px 18px 0" }}
      />
    );
  }
  return (
    <Alert
      type="warning"
      showIcon
      title="当前证据不足，无法确认"
      description={reasonText || "当前没有足够的已验证证据支持确定结论。"}
      style={{ margin: "14px 18px 0" }}
    />
  );
}

function CompareView({ result, evidenceById, setDrawerEv, humanize }) {
  const grouped = groupEvidence(result && result.evidence);
  const answer = (result && result.answer) || {};
  const flowList = Array.isArray(answer.businessFlow) ? answer.businessFlow : [];
  const technicalFlowList = Array.isArray(answer.technicalFlow) ? answer.technicalFlow : [];
  return (
    <Card size="small" styles={{ body: { padding: "14px 18px" } }} title={<Typography.Text strong style={{ fontSize: 13 }}>结论与链路（引用已对齐到证据卡片）</Typography.Text>}>
      <Typography.Paragraph style={{ fontSize: 14, lineHeight: 1.85 }}>{humanize(answer.conclusion)}</Typography.Paragraph>
      {flowList.length > 0 && (
        <Timeline
          style={{ marginTop: 8 }}
          items={flowList.map((item, index) => ({ color: "green", children: item.statement || item.step || String(item), key: index }))}
        />
      )}
      {technicalFlowList.length > 0 && (
        <Timeline
          style={{ marginTop: 8 }}
          items={technicalFlowList.map((item, index) => ({ color: "blue", children: humanize(item.statement || item.step || String(item)), key: `technical-${index}` }))}
        />
      )}
      {[["CODE", "代码证据"], ["BUSINESS", "业务知识"], ["REQUIREMENT", "需求依据"]].map(([type, label]) => (
        grouped[type]?.length > 0 && (
          <div key={type} style={{ marginTop: 16 }}>
            <div className={`ev-group-label ${type.toLowerCase()}`}>
              {label} <span>· {grouped[type].length} 条</span>
            </div>
            <Flex vertical gap={8}>
              {grouped[type].map((item) => {
                const meta = sourceMeta(type);
                return (
                  <Card
                    key={item.evidenceId}
                    size="small"
                    hoverable
                    onClick={() => setDrawerEv(item)}
                    style={{ borderLeft: `3px solid ${meta.color}` }}
                    styles={{ body: { padding: "9px 13px" } }}
                    title={
                      <Flex gap={8} align="center" style={{ minWidth: 0 }}>
                        <EvTag id={item.evidenceId} item={item} />
                        <Typography.Text ellipsis style={{ flex: 1, minWidth: 0, fontSize: 12, fontFamily: "monospace" }}>
                          {evidenceTitle(item, { short: true })}
                        </Typography.Text>
                      </Flex>
                    }
                  >
                    <Typography.Text type="secondary" style={{ display: "block", fontSize: 10.5, fontFamily: "monospace", marginBottom: 6 }}>
                      {formatLocation(item.location)}
                    </Typography.Text>
                    <pre className="ev-pre">{(item.content || "该 Evidence 仅包含结构化引用。").slice(0, 220)}</pre>
                  </Card>
                );
              })}
            </Flex>
          </div>
        )
      ))}
    </Card>
  );
}

function FactRow({ fact, humanize, evidenceById, setDrawerEv }) {
  const linked = (fact.evidenceIds || []).map((id) => evidenceById[id]).filter(Boolean);
  const primary = linked[0];
  const meta = sourceMeta(fact.sourceType);
  const structured = fact.sourceType === "BUSINESS" ? splitStructuredInfo(fact.statement) : null;
  const statementText = structured ? structured.text : humanize(fact.statement);
  return (
    <div className="fact-row">
      <Flex gap={10} align="flex-start">
        <span className="fact-dot" style={{ background: meta.color }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ lineHeight: 1.75 }}>{statementText}</div>
          {structured && (
            <Flex gap={6} wrap="wrap" style={{ marginTop: 6 }}>
              {Object.entries(structured.attrs).map(([key, value]) => (
                <span key={key} className="fact-src" style={{ background: meta.soft, color: meta.color, fontFamily: "var(--mono)", fontSize: 10.5 }}>
                  {key} = {Array.isArray(value) ? value.join(", ") : String(value)}
                </span>
              ))}
            </Flex>
          )}
          <Flex gap={8} align="center" style={{ marginTop: 4 }} wrap="wrap">
            <span className="fact-src" style={{ background: meta.soft, color: meta.color }}>{sourceLabel(fact.sourceType)}</span>
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
  const meta = item ? sourceMeta(item.sourceType) : null;
  const className = item ? `ev-pill ${item.sourceType?.toLowerCase() || ""}` : "ev-pill missing";
  return (
    <Tooltip title={item ? "点击查看证据详情" : "该证据仅在历史记录中"}>
      <span
        className={className}
        style={{ fontSize: small ? 10 : 10.5, cursor: item ? "pointer" : "default" }}
        onClick={item && onOpen ? onOpen : undefined}
      >
        {id}
      </span>
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

/* “结构化信息：{...}”这类后端拼接的属性 JSON 拆出来单独渲染 */
function splitStructuredInfo(statement) {
  const match = String(statement || "").match(/^(.*?)(?:结构化信息|信息)[:：]\s*(\{[\s\S]*\})$/);
  if (!match) return null;
  let attrs;
  try { attrs = JSON.parse(match[2]); } catch { return null; }
  if (!attrs || typeof attrs !== "object" || Array.isArray(attrs)) return null;
  return { text: match[1].trim(), attrs };
}

function EvidenceDetail({ item }) {
  return (
    <div>
      <Flex gap={6} wrap="wrap" style={{ marginBottom: 12 }}>
        <span className={`fact-src`} style={{ background: sourceMeta(item.sourceType).soft, color: sourceMeta(item.sourceType).color }}>{sourceLabel(item.sourceType)}</span>
        {item.status && <Tag>{statusLabel(item.status) || item.status}</Tag>}
        {item.location?.file && <Tag>{formatLocation(item.location)}</Tag>}
      </Flex>
      <pre className="ev-pre drawer">{item.content || "该 Evidence 仅包含结构化引用，详细内容见原始来源。"}</pre>
    </div>
  );
}

function EvidencePane({ result, loading, mode, setDrawerEv }) {
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
              <div className={`ev-group-label ${type.toLowerCase()}`}>
                {sourceLabel(type)} <span>{grouped[type].length}</span>
              </div>
              {grouped[type].map((item) => {
                const meta = sourceMeta(type);
                return (
                  <Card
                    key={item.evidenceId}
                    size="small"
                    className="ev-mini-card"
                    style={{ borderLeft: `3px solid ${meta.color}`, marginBottom: 8 }}
                    styles={{ body: { padding: "8px 12px" } }}
                    onClick={() => setDrawerEv?.(item)}
                  >
                    <Flex gap={7} align="center">
                      <EvTag id={item.evidenceId} item={item} small />
                      <Typography.Text ellipsis style={{ flex: 1, minWidth: 0, fontSize: 11.5, fontFamily: "monospace" }}>{evidenceTitle(item, { short: true })}</Typography.Text>
                    </Flex>
                    <Typography.Paragraph ellipsis={{ rows: 2 }} type="secondary" style={{ fontSize: 10.5, marginTop: 6, marginBottom: 0 }}>
                      {(item.content || "仅包含结构化引用。").slice(0, 120)}
                    </Typography.Paragraph>
                  </Card>
                );
              })}
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
    <Button size="small" type="text" icon={<Copy size={13} />} onClick={copy} style={{ color: "#7E786E", fontSize: 12 }}>
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
          style={feedback === "HELPFUL" ? { color: palette.brand } : { color: "#7E786E" }} />
      </Tooltip>
      <Tooltip title="没有帮助">
        <Button size="small" type="text" icon={<ThumbsDown size={13.5} />} onClick={() => send("NOT_HELPFUL")}
          style={feedback === "NOT_HELPFUL" ? { color: palette.danger } : { color: "#7E786E" }} />
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
          检索业务上下文 · 代码索引 · 实际源码　按 / 聚焦
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
  pushAll(answer.conclusion?.match(/EV[D]?-[A-Za-z0-9-]+/g));
  (answer.facts || []).forEach((fact) => pushAll(fact.evidenceIds));
  return ids.slice(0, 8);
}

function shortSymbol(name) {
  const value = String(name || "");
  if (!value.includes(".")) return value;
  const parts = value.split(".");
  return parts.slice(-2).join(".");
}

function evidenceTitle(item, { short = false } = {}) {
  if (!item) return "";
  if (item.sourceType === "CODE") {
    const name = item.symbol || item.sourceId;
    return short ? shortSymbol(name) : name;
  }
  if (item.sourceType === "BUSINESS") return `${item.sourceId} 业务事实`;
  return `${item.sourceId} 需求证据`;
}

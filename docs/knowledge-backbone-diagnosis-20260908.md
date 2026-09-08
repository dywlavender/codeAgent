# 知识主干诊断验证（2026-09-08）

遵循 [知识主干整改方向](knowledge-backbone-direction.md)。本次实现用于区分总览定位收益、流程主干的额外收益与代码细节推理；不是收益证明。贷款业务源码和正常需求资料未修改。

## 改动

- 新对照 `diagnostic`：`code_only`（代码、README、需求）、`overview`（同样资料＋仅 project-overview.md）、`optional`（同样资料＋总览和按需流程文档）。总览组没有流程文档；缺少总览时拒绝启动该对照。
- 页面可选择 AB、诊断三组、AST 三组，按实际组数估算调用量；批次内可切换对比组查看答案、轨迹、评分和复核。
- CLI 和页面报告增加题型分层，以及完整主干相对仅总览的独立配对统计。增量比较不依赖无主干组成功，分母在各自结果中明确记录。
- 评分者发现标准与冻结源码矛盾时返回 `rubricInvalid`；该次评分不可用，不能把正确答案记零分，也不会为了格式重试再次调用模型。
- 修订标准重评创建独立目录，复制冻结源码和原答案，保存原评分；只有问题文字完全相同才能复用答案。旧批次和已导入旧题库均保留。
- 修复旧 `current/preloaded` 诊断分支计算了 preload 却未传给运行时的问题。

## 新题库

`validation-projects/complex-loan/evaluations/complex-loan-diagnostic-v3.json`：11 题。

- 原 8 题保持问题正文，分为 `code_detail` 和 `technical_flow`。
- 修订策略码为当前源码的 `mifos-standard-strategy`。
- 回调和陈旧结果题区分顺序可见、并发旧实体与模拟器可产生的状态，不预设事务注解或状态判断保证并发幂等。
- 新增 3 道 `business_investigation`：收款后仍处理中、扣款后可借额度未恢复、集团共享与渠道条件差异。题干不提供类名或枚举。
- 新题评分覆盖当前实现中的实际限制，不为主干胜出删减正常资料。不向主干写入题库答案。

已导入复杂贷款项目题库列表，名称为“融商贷诊断V3（修订标准＋业务现象）”。运行中的平台后端需重启才能加载新的评测接口代码。

## 执行

完整对照：

```bash
.venv/bin/python scripts/evaluate_backbone.py \
  --suite validation-projects/complex-loan/evaluations/complex-loan-diagnostic-v3.json \
  --comparison diagnostic --repeats 2 --judge
```

11题×3组×2轮＝66次答题，正常66次初评；格式不合法的初评最多重试一次。建议先选两题单轮检验流程，不据单轮宣称收益。

旧答案修订重评（必须使用新的输出目录）：

```bash
.venv/bin/python scripts/evaluate_backbone.py \
  --rejudge validation-projects/complex-loan/.data/evaluations/eval-20260906-223513 \
  --rubric-suite validation-projects/complex-loan/evaluations/complex-loan-diagnostic-v3.json \
  --cases v2-strategy-code v2-callback-twice v2-stale-failed \
  --output validation-projects/complex-loan/.data/evaluations/diagnosis-rejudge-new \
  --workers 2
```

`--prepare-only` 只准备输入，不调用答题或评分模型。已准备的重评目录可用 `--rejudge <新目录>` 开始评分，不再重复传 `--rubric-suite`。

## 调查过程复核

```bash
.venv/bin/python scripts/diagnose_backbone.py <批次目录>
```

生成：

- `investigation-traces.json`：完整工具轨迹，步骤从1开始。
- `investigation-review.json`：首次正确定位步骤、无关检索步骤、遗漏业务环节、证据和复核来源。
- `investigation-report.md`：总览注入、主干访问和复核结果。未复核为“待复核”，不能算作零遗漏。

编辑复核文件时填 `operatorType`（例如 user 或 assistant）、`evidence`，并按真实轨迹填写字段，然后重新运行导出命令。脚本保留原有标注，校验步骤范围。首次 Read 不自动等同首次正确定位，无结果搜索也不自动认定无关。

## 本次验证边界

本地单元/API回归及前端构建用于验证功能正确，不代表主干有效。真实模型重评和小规模试验曾被自动审批拦截：调用目的地被识别为外部 DeepSeek API，需用户明确授权发送相关源码、题目与历史答案；未获授权前只准备输入和运行本地测试。

## 后续授权与AST方案

用户随后明确要求继续验证并实现AST对照，已据此继续执行当前配置的外部模型调用。先前审批阻断为历史记录，不再作为本轮暂停理由。五组设计见[AST对照方案](ast-comparison-20260908.md)。

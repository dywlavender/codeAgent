# 原始评测输出

由 `scripts/evaluation/evaluate_pi.py run` 直接写出的批次数据，只作审计证据。

- `batch.json`：批次配置（题库、案例、模式、重复、模型、超时、关键文件清单）。
- `runs/<case>-<mode>-r<n>/`：`answer.md`、`result.json`、`trace.json`、`events.jsonl`、`review-template.json`；`review-template.json` 是新版空白模板（`verdict` 未填），已复核运行以 `review.json` 与 `checks.md` 为准。
- 批次汇总与逐项结论见上级目录的 `../summary.md`、`../summary.json` 和 `checks.md`；本目录保留原始输出。
- 文件包含评测时的绝对本地工作区路径；已核查不含 API Key 等凭据。
- 归档 Trace 保留生成时的旧字段（例如旧引用匹配数、旧 `modelSnapshotAvailable`）；离线报告已按新口径重新统计，不能把旧 Trace 字段当作当前核验结果。
- 质量结论以 `../README.md`、`../summary.md` 和 `../summary.json` 为准，本目录不单独下结论。

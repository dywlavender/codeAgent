# 原始评测输出

由 `scripts/evaluation/evaluate_pi.py run` 直接写出的批次数据，只作审计证据。

- `batch.json`：批次配置（题库、案例、模式、重复、模型、超时、关键文件清单）。
- `runs/<case>-<mode>-r<n>/`：`answer.md`、`result.json`、`trace.json`、`events.jsonl`、`review-template.json`。
- 文件包含评测时的绝对本地工作区路径；已核查不含 API Key 等凭据。
- 质量结论以 `../README.md`、`../summary.md` 和 `../summary.json` 为准，本目录不单独下结论。

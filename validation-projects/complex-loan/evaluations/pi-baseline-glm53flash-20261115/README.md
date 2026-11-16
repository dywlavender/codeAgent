# Pi 基线批次快照（2026-11-15/16）

状态：进行中。16 次计划运行已完成 7 次（native-frontend 4/4；native-lifecycle none 2/2、backbone 1/2）；其余 9 次待续跑。

- 模型：`zcode/glm-5.3-flash`；Pi 1.5.0；无 thinking；单次超时 900 秒。
- 组别：`none`（源码、README、需求）与 `backbone`（增加 business-context），每题每组 2 次。
- `summary.md` / `summary.json` 由 `scripts/evaluation/evaluate_pi.py report` 从已完成的 `result.json` 生成；原始 Trace、逐次 `result.json` 和 `review-template.json` 归档在 `raw-traces/`（`batch.json` 与 `runs/`），Pi 会话仍只在本地 `.data/`。
- 结论与 checks 逐题分析仍在编写；本快照只用于固定当前运行输出，不代表 16 次已完成。

关键文件清单：`../pi-quality-keys-20261115.json`（仅评测器使用）。

"""Export investigation evidence and summarize explicitly reviewed annotations."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from business_code_agent.evaluation.diagnosis import investigation_artifacts


def export_diagnosis(batch):
    batch = Path(batch)
    protocol = json.loads((batch / "protocol.json").read_text())
    results = json.loads((batch / "results.json").read_text())
    investigation_artifacts(batch, protocol, results)
    annotations = json.loads((batch / "investigation-review.json").read_text())
    lines = ["# 调查轨迹复核", "", "首次正确定位和无关检索需按题意复核；主干自动注入不计为 Read。", "",
             "| 答案 | 总览注入 | 主干正文访问 | 首次正确定位步骤 | 无关检索数 | 遗漏数 | 复核来源 |",
             "| --- | --- | ---: | --- | --- | --- | --- |"]
    for result in results:
        item = annotations.get(result["id"], {})
        reviewed = bool(item.get("operatorType") and item.get("evidence"))
        steps = len(result.get("toolTrace", []))
        first = item.get("firstCorrectLocationStep")
        unrelated = item.get("unrelatedSearchSteps")
        missing = item.get("missingKeySteps")
        if first is not None and (type(first) is not int or not 1 <= first <= steps):
            raise ValueError(f'{result["id"]}: 首次正确定位步骤超出轨迹范围')
        if unrelated is not None and (not isinstance(unrelated, list) or any(type(x) is not int or not 1 <= x <= steps for x in unrelated) or len(set(unrelated)) != len(unrelated)):
            raise ValueError(f'{result["id"]}: 无关检索步骤需为不重复的有效步骤编号')
        if missing is not None and (not isinstance(missing, list) or any(not isinstance(x, str) or not x.strip() for x in missing)):
            raise ValueError(f'{result["id"]}: 遗漏项需要具体业务环节文字')
        command_file = batch / "runs" / result["id"] / "command.json"
        command = json.loads(command_file.read_text()) if command_file.exists() else []
        # Derive from the frozen arm's overview and actual recorded command.
        overview = batch / "inputs/baselines" / result["arm"] / "project-overview.md"
        injected = bool(overview.is_file() and overview.read_text().strip() and
                        overview.read_text().strip() in "\n".join(command))
        lines.append(f'| {result["id"]} | {"是" if injected else "未确认"} | {result.get("baselineContentCalls", 0)} | '
                     f'{first if reviewed and first is not None else "待复核"} | '
                     f'{len(unrelated) if reviewed and unrelated is not None else "待复核"} | '
                     f'{len(missing) if reviewed and missing is not None else "待复核"} | '
                     f'{item.get("operatorType") if reviewed else "待复核"} |')
    target = batch / "investigation-report.md"
    target.write_text("\n".join(lines) + "\n")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    print(export_diagnosis(parser.parse_args().batch))

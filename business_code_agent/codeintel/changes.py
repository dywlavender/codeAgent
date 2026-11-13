"""Original-text Git diff ranges shared by explicit index builders."""

import re
from pathlib import Path
import subprocess
import tempfile


def diff_ranges(before, after):
    with tempfile.TemporaryDirectory(prefix="codeagent-diff-") as directory:
        a, b = Path(directory) / "before", Path(directory) / "after"
        a.write_text(before, encoding="utf-8")
        b.write_text(after, encoding="utf-8")
        result = subprocess.run(["git", "diff", "--no-index", "--no-ext-diff", "--unified=0", "--", str(a), str(b)],
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode not in (0, 1):
            raise ValueError("Git diff 执行失败，请确认 Git 可用")
        return [{"start": int(m[0]), "lines": int(m[1] or 1)}
                for m in re.findall(r"^@@ .*? \+(\d+)(?:,(\d+))? @@", result.stdout, re.M)]

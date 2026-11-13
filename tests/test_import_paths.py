"""Static import-path integrity checks for the backend package.

The production runtime uses optional dependencies (LangChain, tree-sitter, ONNX
Runtime). Importing every module in a unit test would fail on machines that only
installed the core dependencies, so these checks resolve import targets from the
filesystem instead of importing them.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PACKAGE_NAME = "business_code_agent"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPOSITORY_ROOT / PACKAGE_NAME


def _module_exists(base: Path, dotted: str) -> bool:
    parts = [part for part in dotted.split(".") if part]
    target = base.joinpath(*parts)
    return target.with_suffix(".py").is_file() or (target / "__init__.py").is_file()


def _sources():
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _broken_imports(kind: str) -> list[str]:
    broken = []
    for source in _sources():
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = node.module or ""
            if kind == "relative":
                if node.level == 0:
                    continue
                base = source.parent
                for _ in range(node.level - 1):
                    base = base.parent
                rendered = "." * node.level + target
            else:
                if node.level != 0 or not target.startswith(PACKAGE_NAME):
                    continue
                base = REPOSITORY_ROOT
                rendered = target
            if not _module_exists(base, target):
                relative_path = source.relative_to(REPOSITORY_ROOT)
                broken.append(f"{relative_path}:{node.lineno} -> {rendered}")
    return broken


class ImportPathTest(unittest.TestCase):
    def test_relative_import_targets_exist(self):
        broken = _broken_imports("relative")
        self.assertEqual([], broken, "相对 import 指向不存在的模块:\n" + "\n".join(broken))

    def test_absolute_package_import_targets_exist(self):
        broken = _broken_imports("absolute")
        self.assertEqual([], broken, "绝对 import 指向不存在的模块:\n" + "\n".join(broken))


if __name__ == "__main__":
    unittest.main()

"""Machine-checkable package boundary rules for the backend.

These mirror the dependency direction in docs/spec/architecture.md:
``api -> domain services -> storage``, with ``shared/`` usable from any layer
but depending on nothing else inside the project. Checks are static so they do
not require optional runtime dependencies to be installed.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PACKAGE = "business_code_agent"
ROOT = Path(__file__).resolve().parents[1] / PACKAGE
DOMAIN_PACKAGES = {
    "api", "query", "knowledge", "codeintel", "evaluation", "platform", "requirement",
}


def _import_targets(source: Path, tree: ast.Module) -> list[tuple[int, str]]:
    pkg_parts = [PACKAGE] + list(source.relative_to(ROOT).parts[:-1])
    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0:
                target = node.module or ""
            else:
                base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
            targets.append((node.lineno, target))
        elif isinstance(node, ast.Import):
            targets.extend((node.lineno, alias.name) for alias in node.names)
    return targets


def _belongs_to(target: str, package: str) -> bool:
    prefix = f"{PACKAGE}.{package}"
    return target == prefix or target.startswith(prefix + ".")


def _sources_under(*packages: str):
    for source in sorted(ROOT.rglob("*.py")):
        parts = source.relative_to(ROOT).parts
        if len(parts) > 1 and parts[0] in packages:
            yield source


class PackageBoundaryTest(unittest.TestCase):
    def test_no_cross_package_relative_imports(self):
        violations = []
        for source in sorted(ROOT.rglob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level == 0:
                    continue
                if node.level == 1 and "." not in (node.module or ""):
                    continue
                rel = source.relative_to(ROOT)
                violations.append(f"{rel}:{node.lineno} -> {'.' * node.level}{node.module or ''}")
        self.assertEqual([], violations, "跨包必须使用绝对 import:\n" + "\n".join(violations))

    def test_domain_packages_do_not_import_api(self):
        violations = []
        for source in _sources_under(*(DOMAIN_PACKAGES - {"api"})):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for lineno, target in _import_targets(source, tree):
                if _belongs_to(target, "api"):
                    violations.append(f"{source.relative_to(ROOT)}:{lineno} -> {target}")
        self.assertEqual([], violations, "领域包不得反向引用 api:\n" + "\n".join(violations))

    def test_api_does_not_depend_on_storage_directly(self):
        violations = []
        for source in _sources_under("api"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for lineno, target in _import_targets(source, tree):
                if _belongs_to(target, "storage"):
                    violations.append(f"{source.relative_to(ROOT)}:{lineno} -> {target}")
        self.assertEqual([], violations,
                         "HTTP 层的连接由 ProjectContext.open_db() 提供:\n" + "\n".join(violations))

    def test_broken_dependency_edges_stay_broken(self):
        forbidden = {
            "query": {"evaluation", "knowledge"},
            "codeintel": {"evaluation", "query", "requirement"},
        }
        violations = []
        for owner, blocked in forbidden.items():
            for source in _sources_under(owner):
                tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
                for lineno, target in _import_targets(source, tree):
                    if any(_belongs_to(target, package) for package in blocked):
                        violations.append(f"{source.relative_to(ROOT)}:{lineno} -> {target}")
        self.assertEqual([], violations,
                         "已断开的包级依赖不得回退:\n" + "\n".join(violations))

    def test_storage_and_shared_have_no_domain_dependencies(self):
        violations = []
        for source in _sources_under("storage", "shared"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for lineno, target in _import_targets(source, tree):
                if any(_belongs_to(target, package) for package in DOMAIN_PACKAGES):
                    violations.append(f"{source.relative_to(ROOT)}:{lineno} -> {target}")
        self.assertEqual([], violations, "storage/shared 不得依赖领域包:\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()

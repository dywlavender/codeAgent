import tempfile
import unittest
from pathlib import Path

from business_code_agent.evaluation.ast_docs import generate_ast_documents, _split, MAX_DOC_LINES, MAX_DOC_CHARS
from business_code_agent.evaluation.runner import _arm_documents
from business_code_agent.evaluation.harness import reference_usage
from business_code_agent.evaluation.diagnosis import diagnosis_summaries


class AstEvaluationTest(unittest.TestCase):
    def test_generated_overview_links_to_index_and_actual_method_endpoints(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "repo-0/src/main/java/demo/PayController.java"
            source.parent.mkdir(parents=True)
            source.write_text('''package demo;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/pay")
public class PayController {
 @PostMapping("/submit")
 public String submit() { return "ok"; }
}
''')
            test = root / "repo-0/src/test/java/demo/OnlyInTest.java"
            test.parent.mkdir(parents=True)
            test.write_text("package demo; public class OnlyInTest {}")
            docs = generate_ast_documents(root, [("payment-repo", "repo-0")])
            self.assertIn("payment-repo", docs["project-overview.md"])
            self.assertIn("ast/repo-0-index.md", docs["project-overview.md"])
            content = "\n".join(docs.values())
            self.assertIn("PayController", content)
            self.assertIn("submit(L", content)
            self.assertIn("POST /pay/submit", content)
            self.assertNotIn("OnlyInTest", content)

    def test_hybrid_retains_same_business_overview_not_flow_documents(self):
        business = {"project-overview.md": "business map", "flow.md": "secret flow"}
        ast = {"project-overview.md": "structure map", "ast/repo-0-index.md": "index"}
        result = _arm_documents("overview_ast", business, None, ast)
        self.assertTrue(result["project-overview.md"].startswith("business map"))
        self.assertEqual("structure map", result["ast-overview.md"])
        self.assertNotIn("flow.md", result)
        overview = _arm_documents("overview", business, None)["project-overview.md"]
        self.assertTrue(result["project-overview.md"].startswith(overview))
        self.assertIn("未提供总览中链接的人工流程主干文件", overview)
        self.assertNotIn("secret flow", str(result))
        self.assertEqual(ast, _arm_documents("ast", business, None, ast))

    def test_shards_split_between_types(self):
        docs = _split("map", "header\n", ["class\nmethod" for _ in range(300)])
        self.assertGreater(len(docs), 1)
        self.assertTrue(all(len(text.splitlines()) <= MAX_DOC_LINES for text in docs.values()))

        docs = _split("map", "header\n", ["x" * 1000 for _ in range(40)])
        self.assertTrue(all(len(text) <= MAX_DOC_CHARS for text in docs.values()))

    def test_usage_distinguishes_injection_read_and_listing(self):
        with tempfile.TemporaryDirectory() as folder:
            baseline = Path(folder) / "baseline"
            baseline.mkdir()
            (baseline / "project-overview.md").write_text("structure overview")
            trace = [{"name": "Glob", "status": "completed", "input": {"path": str(baseline / "ast")}},
                     {"name": "Read", "status": "completed", "input": {"file_path": str(baseline / "ast/repo-0-index.md")}}]
            alias = Path(folder) / "baseline-alias"
            alias.symlink_to(baseline, target_is_directory=True)
            result = reference_usage(trace, alias, ["--append-system-prompt", "structure overview"])
            self.assertTrue(result["overviewInjected"])
            self.assertEqual(1, result["astContentCalls"])

    def test_ast_increment_is_paired_against_same_overview(self):
        rows = [{"id": arm, "questionId": "q", "repeat": 1, "arm": arm,
                 "status": "completed", "elapsedSeconds": seconds}
                for arm, seconds in [("code_only", 15), ("overview", 12), ("overview_ast", 9)]]
        result = diagnosis_summaries(rows, {"q": {"checks": ["x"]}}, ["code_only", "overview", "overview_ast"])
        self.assertEqual("overview", result["astVsOverview"]["controlArm"])
        self.assertEqual(-3, result["astVsOverview"]["pairs"][0]["secondsDelta"])

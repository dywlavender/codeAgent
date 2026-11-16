import tempfile
import unittest
from pathlib import Path

from business_code_agent.query.navigation import build_navigation
from business_code_agent.query.system_guides import load_system_guides


class SystemGuideSelectionTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.config = {
            "systems": [{"id": "loan", "name": "贷款中台"},
                        {"id": "channel", "name": "渠道系统"}],
            "applications": [
                {"id": "loan-api", "systemId": "loan", "repositoryId": "shared", "sourceRoot": "api"},
                {"id": "loan-job", "systemId": "loan", "repositoryId": "jobs", "sourceRoot": "."},
                {"id": "channel-api", "systemId": "channel", "repositoryId": "shared", "sourceRoot": "channel"},
            ],
        }
        for path, text in {
            "loan/SYSTEM.md": "中台通用规则",
            "loan/applications/loan-job.md": "批任务规则",
            "channel/SYSTEM.md": "渠道通用规则",
        }.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

    def navigation(self, mounts, **scope):
        return build_navigation(self.config, mounts, "project", "工程", **scope)

    def test_explicit_system_filters_guides_even_with_a_shared_repository(self):
        navigation = self.navigation({"shared": "repos/shared", "jobs": "repos/jobs"},
                                     system_ids=["loan"])
        self.assertEqual(["repos/shared/api", "repos/jobs"],
                         [app["mount"] for app in navigation["applications"]])
        self.assertEqual(["loan/SYSTEM.md", "loan/applications/loan-job.md"],
                         [doc.relative_path for doc in load_system_guides(self.root, navigation)])

    def test_repository_selection_includes_related_system_and_only_selected_app(self):
        navigation = self.navigation({"jobs": "repos/jobs"})
        documents = load_system_guides(self.root, navigation)
        self.assertEqual(["中台通用规则", "批任务规则"], [doc.text for doc in documents])
        self.assertEqual((), load_system_guides(self.root / "not-installed", navigation))

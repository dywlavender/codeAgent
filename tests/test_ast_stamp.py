"""Source stamp comparability and interrupted-generation healing."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from business_code_agent.evaluation.ast_service import _LIVE_GENERATIONS, AstService
from business_code_agent.platform.ast_material import current_stamp, source_stamp


class SourceStampTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.cache = self.root / "stamp-cache.json"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "Main.java").write_text("class Main {}\n", encoding="utf-8")
        self.pairs = [("r1", self.repo)]

    def test_touch_does_not_flip_content_digest(self):
        first = source_stamp(self.pairs, cache_path=self.cache)
        stat = self.repo / "Main.java"
        times = (stat.stat().st_atime, stat.stat().st_mtime + 100)
        os.utime(stat, times)
        second = source_stamp(self.pairs, cache_path=self.cache)
        self.assertEqual(first["contentDigest"], second["contentDigest"])

    def test_content_change_flips_content_digest(self):
        first = source_stamp(self.pairs, cache_path=self.cache)
        (self.repo / "Main.java").write_text("class Main { int x = 1; }\n", encoding="utf-8")
        second = source_stamp(self.pairs, cache_path=self.cache)
        self.assertNotEqual(first["contentDigest"], second["contentDigest"])

    def test_legacy_stamp_still_mtime_based(self):
        legacy = source_stamp(self.pairs, content_digest=False)
        self.assertNotIn("contentDigest", legacy)
        self.assertEqual({"fileCount", "latestModifiedAt", "repositories"}, set(legacy))

    def test_current_stamp_matches_saved_format(self):
        saved_new = source_stamp(self.pairs, cache_path=self.cache)
        self.assertEqual(saved_new, current_stamp(saved_new, self.pairs, cache_path=self.cache))
        saved_legacy = source_stamp(self.pairs, content_digest=False)
        # A content change that keeps mtime does not mark legacy versions stale.
        stat = self.repo / "Main.java"
        before = stat.stat().st_mtime_ns
        (self.repo / "Main.java").write_text("class Main { int y = 2; }\n", encoding="utf-8")
        os.utime(stat, ns=(stat.stat().st_atime_ns, before))
        self.assertEqual(saved_legacy, current_stamp(saved_legacy, self.pairs))


class InterruptedGenerationHealTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "repo").mkdir()
        (self.root / "repo" / "Main.java").write_text("class Main {}\n", encoding="utf-8")
        self.config = self.root / "project.config.json"
        self.config.write_text(json.dumps({
            "project": {"id": "heal-test"},
            "repositories": [{"id": "core", "gitUrl": "unused", "localPath": "repo"}],
        }), encoding="utf-8")
        self.ast_root = self.root / "ast"

    def seed_generating(self):
        self.ast_root.mkdir(parents=True, exist_ok=True)
        (self.ast_root / "index.json").write_text(json.dumps({
            "status": "generating", "generationId": "ast-generation-x",
            "currentVersionId": None,
        }), encoding="utf-8")

    def test_dangling_generating_marked_failed_on_construction(self):
        self.seed_generating()
        service = AstService(project_config=self.config, data_root=self.ast_root)
        status = service.status()
        self.assertEqual("failed", status["status"])
        self.assertIn("服务重启中断", status["error"])

    def test_live_generation_is_not_healed(self):
        self.seed_generating()
        live = (self.ast_root.resolve(), "ast-generation-x")
        _LIVE_GENERATIONS.add(live)
        self.addCleanup(_LIVE_GENERATIONS.discard, live)
        service = AstService(project_config=self.config, data_root=self.ast_root)
        self.assertEqual("generating", service.status()["status"])


if __name__ == "__main__":
    unittest.main()

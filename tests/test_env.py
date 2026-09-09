from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from business_code_agent.env import EnvFileError, load_env_file


class EnvFileTest(unittest.TestCase):
    def test_loads_common_dotenv_entries_with_file_values_taking_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text(
                """# comment
export TEST_CODE_ATLAS_PLAIN=plain-value
TEST_CODE_ATLAS_DOUBLE=\"double value\"
TEST_CODE_ATLAS_SINGLE='single value'
TEST_CODE_ATLAS_EMPTY=
TEST_CODE_ATLAS_EXISTING=from-file
""",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"TEST_CODE_ATLAS_EXISTING": "from-process"}, clear=False):
                loaded = load_env_file(path)
                self.assertEqual("plain-value", os.environ["TEST_CODE_ATLAS_PLAIN"])
                self.assertEqual("double value", os.environ["TEST_CODE_ATLAS_DOUBLE"])
                self.assertEqual("single value", os.environ["TEST_CODE_ATLAS_SINGLE"])
                self.assertEqual("", os.environ["TEST_CODE_ATLAS_EMPTY"])
                self.assertEqual("from-file", os.environ["TEST_CODE_ATLAS_EXISTING"])
                self.assertIn("TEST_CODE_ATLAS_EXISTING", loaded)

    def test_missing_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual([], load_env_file(Path(folder) / ".env"))

    def test_invalid_entry_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("not a variable\n", encoding="utf-8")
            with self.assertRaises(EnvFileError):
                load_env_file(path)

if __name__ == "__main__":
    unittest.main()

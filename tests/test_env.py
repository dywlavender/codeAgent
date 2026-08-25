from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from business_code_agent.env import EnvFileError, load_env_file
from business_code_agent.knowledge_update.langchain_adapter import model_config_from_environment


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

    def test_model_configuration_is_composed_from_environment(self):
        config = model_config_from_environment({
            "BUSINESS_CODE_MODEL_ENABLED": "true",
            "BUSINESS_CODE_MODEL_PROVIDER": "openai",
            "BUSINESS_CODE_MODEL_NAME": "gpt-test",
            "BUSINESS_CODE_MODEL_API_KEY": "secret",
            "BUSINESS_CODE_MODEL_BASE_URL": "https://example.test/v1",
            "BUSINESS_CODE_MODEL_TEMPERATURE": "0.2",
            "BUSINESS_CODE_MODEL_TIMEOUT": "45",
            "BUSINESS_CODE_MODEL_MAX_RETRIES": "4",
        })
        self.assertEqual({
            "enabled": True,
            "provider": "openai",
            "name": "gpt-test",
            "apiKeyEnv": "BUSINESS_CODE_MODEL_API_KEY",
            "baseUrl": "https://example.test/v1",
            "temperature": 0.2,
            "timeout": 45.0,
            "maxRetries": 4,
        }, config)

    def test_model_environment_can_disable_legacy_json_configuration(self):
        config = model_config_from_environment({"BUSINESS_CODE_MODEL_ENABLED": "false"})
        self.assertEqual(False, config["enabled"])


if __name__ == "__main__":
    unittest.main()

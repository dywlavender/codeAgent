import io
import json
import unittest
from unittest.mock import patch

from business_code_agent.evaluation.context_runtime import CompatibleContextRuntime, configured_connection


class CompatibleContextTest(unittest.TestCase):
    def environment(self):
        return {"BUSINESS_CODE_MODEL_ENABLED": "true", "BUSINESS_CODE_MODEL_PROVIDER": "openai",
                "BUSINESS_CODE_MODEL_NAME": "test-qwen", "BUSINESS_CODE_MODEL_BASE_URL": "https://model.invalid/v1",
                "BUSINESS_CODE_MODEL_API_KEY": "example-secret", "BUSINESS_CODE_MODEL_TEMPERATURE": ".5"}

    def test_env_model_and_exact_frozen_prompt_are_sent_without_tools(self):
        runtime = CompatibleContextRuntime(environment=self.environment())
        response = io.BytesIO(json.dumps({"model": "reported-qwen", "choices": [{"message": {"content": "依据原文回答"}}],
                                         "usage": {"prompt_tokens": 12, "completion_tokens": 7}}).encode())
        with patch("business_code_agent.evaluation.context_runtime.urlopen", return_value=response) as http:
            result = runtime.ask("冻结问题和原文", workspace="unused")
        req = http.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual("test-qwen", payload["model"])
        self.assertEqual("冻结问题和原文", payload["messages"][1]["content"])
        self.assertNotIn("tools", payload)
        self.assertEqual(2, len(payload["messages"]))
        self.assertEqual(["reported-qwen"], result.usage["reportedModels"])
        self.assertEqual(12, result.usage["prompt_tokens"])
        self.assertNotIn("example-secret", str(runtime.build_command("q", workspace="unused")))

    def test_explicit_candidate_model_does_not_change_evidence(self):
        runtime = CompatibleContextRuntime(model="candidate-two", environment=self.environment())
        self.assertEqual("candidate-two", runtime.model)
        with patch.dict("os.environ", self.environment(), clear=True):
            setup = configured_connection()
        self.assertEqual("test-qwen", setup["model"])
        self.assertFalse(setup["spendLimitSupported"])
        self.assertNotIn("API_KEY", str(setup))
        self.assertNotIn("example-secret", str(setup))

    def test_empty_response_is_failed_instead_of_inventing_answer(self):
        runtime = CompatibleContextRuntime(environment=self.environment())
        with patch("business_code_agent.evaluation.context_runtime.urlopen", return_value=io.BytesIO(b'{"choices": []}')):
            with self.assertRaisesRegex(ValueError, "未返回"):
                runtime.ask("q", workspace="unused")


if __name__ == "__main__":
    unittest.main()

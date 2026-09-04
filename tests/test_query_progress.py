from __future__ import annotations

import unittest
from unittest.mock import patch

from business_code_agent.query_agent.progress import ProgressEvents


class ProgressTest(unittest.TestCase):
    def test_snapshot_without_thinking_replaces_streamed_text(self):
        progress = ProgressEvents()
        events = []
        def stream(event):
            events.extend(progress.feed({"type": "stream_event", "event": event}))
        stream({"type": "message_start", "message": {"id": "m"}})
        stream({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}})
        stream({"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": "先"}})
        stream({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "检查入口"}})
        stream({"type": "content_block_stop", "index": 1})
        events.extend(progress.feed({"type": "assistant", "message": {"id": "m", "content": [
            {"type": "text", "text": "先检查入口"},
        ]}}))
        text_events = [event["payload"] for event in events if event["eventType"] == "text"]
        self.assertEqual({"m:0"}, {event["id"] for event in text_events})
        self.assertEqual("先检查入口", "".join(event["text"] for event in text_events if event["mode"] == "append"))
        self.assertEqual("replace", text_events[-1]["mode"])
        self.assertEqual("先检查入口", text_events[-1]["text"])

    def test_text_ordinals_preserve_identical_blocks_and_reset_each_message(self):
        for retain_thinking in (False, True):
            with self.subTest(retain_thinking=retain_thinking):
                progress = ProgressEvents()
                for message_id in ("m1", "m2"):
                    progress.feed({"type": "stream_event", "event": {"type": "message_start", "message": {"id": message_id}}})
                    blocks = [{"type": "thinking"}, {"type": "text", "text": "检查"},
                              {"type": "tool_use", "id": message_id + "-tool", "name": "Read", "input": {}},
                              {"type": "text", "text": "检查"}]
                    streamed = []
                    for index, block in enumerate(blocks):
                        streamed += progress.feed({"type": "stream_event", "event": {
                            "type": "content_block_start", "index": index, "content_block": block,
                        }})
                    snapshot = progress.feed({"type": "assistant", "message": {
                        "id": message_id, "content": blocks if retain_thinking else blocks[1:],
                    }})
                    ids = [f"{message_id}:0", f"{message_id}:1"]
                    self.assertEqual(ids, [event["payload"]["id"] for event in streamed if event["eventType"] == "text"])
                    self.assertEqual(ids, [event["payload"]["id"] for event in snapshot if event["eventType"] == "text"])

    def test_streamed_tool_input_and_result_share_id(self):
        progress = ProgressEvents()
        def stream(event):
            return progress.feed({"type": "stream_event", "event": event})
        stream({"type": "message_start", "message": {"id": "m1"}})
        first = stream({"type": "content_block_start", "index": 0,
                        "content_block": {"type": "tool_use", "id": "read-1", "name": "Read", "input": {}}})
        self.assertEqual("running", first[0]["payload"]["status"])
        for chunk in ['{"file_path":', '"src/Service.java"}']:
            self.assertEqual([], stream({"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "input_json_delta", "partial_json": chunk}}))
        update = stream({"type": "content_block_stop", "index": 0})
        self.assertEqual("src/Service.java", update[0]["payload"]["input"]["file_path"])
        self.assertEqual([], progress.feed({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "read-1", "name": "Read", "input": {"file_path": "src/Service.java"}},
        ]}}))
        result = progress.feed({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "read-1", "content": [{"type": "text", "text": "source"}]},
        ]}})
        self.assertEqual("read-1", result[0]["payload"]["id"])
        self.assertEqual("completed", result[0]["payload"]["status"])
        self.assertEqual("source", result[0]["payload"]["output"])

    def test_text_is_batched_and_snapshot_replaces_same_block(self):
        with patch("business_code_agent.query_agent.progress.time.monotonic", return_value=0) as clock:
            progress = ProgressEvents()
            progress.feed({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m"}}})
            for chunk in ["先", "校验", "\n", "REPAYTYPE"]:
                self.assertEqual([], progress.feed({"type": "stream_event", "event": {
                    "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": chunk},
                }}))
            clock.return_value = 0.11
            event = progress.flush()[0]
            self.assertEqual({"id": "m:0", "text": "先校验\nREPAYTYPE", "mode": "append"}, event["payload"])
            snapshot = progress.feed({"type": "assistant", "message": {"id": "m", "content": [
                {"type": "text", "text": "先校验\nREPAYTYPE"},
            ]}})[0]
            self.assertEqual("m:0", snapshot["payload"]["id"])
            self.assertEqual("replace", snapshot["payload"]["mode"])

    def test_thinking_content_and_telemetry_are_not_forwarded(self):
        progress = ProgressEvents()
        first = progress.feed({"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 1})
        self.assertEqual("thinking", first[0]["payload"]["phase"])
        self.assertEqual([], progress.feed({"type": "stream_event", "event": {"type": "message_start"}}))
        self.assertEqual([], progress.feed({"type": "stream_event", "event": {
            "type": "content_block_start", "content_block": {"type": "thinking", "thinking": "private text"},
        }}))
        self.assertEqual([], progress.feed({"type": "stream_event", "event": {
            "type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "private text"},
        }}))
        self.assertEqual([], progress.feed({"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 2}))

    def test_tool_errors_and_final_failure_are_preserved(self):
        progress = ProgressEvents()
        progress.feed({"type": "tool_use", "id": "t", "name": "Grep", "input": {"pattern": "x"}})
        event = progress.feed({"type": "tool_result", "tool_use_id": "t", "is_error": True, "content": "missing"})[0]
        self.assertEqual("error", event["payload"]["status"])
        self.assertEqual("failed", progress.feed({"type": "result", "is_error": True})[0]["payload"]["phase"])


if __name__ == "__main__":
    unittest.main()

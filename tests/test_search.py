import asyncio
import unittest

from codex_gateway.codex_responses import (
    collect_codex_responses_native_response,
    collect_codex_responses_text_and_usage,
    convert_chat_completions_to_codex_responses,
)
from codex_gateway.config import Settings
from codex_gateway.openai_compat import ChatCompletionRequest, ChatMessage, responses_input_to_messages
from codex_gateway.server import _should_use_codex_backend


class SearchTests(unittest.TestCase):
    def test_search_is_enabled_by_default(self) -> None:
        self.assertIs(Settings.enable_search, True)

    def test_codex_backend_payload_includes_web_search_when_enabled(self) -> None:
        payload = convert_chat_completions_to_codex_responses(
            ChatCompletionRequest(
                model="gpt-5.5",
                messages=[ChatMessage(role="user", content="what changed today?")],
            ),
            model_name="gpt-5.5",
            force_stream=True,
            enable_search=True,
        )

        self.assertIn({"type": "web_search", "external_web_access": True}, payload["tools"])
        self.assertNotEqual(payload.get("tool_choice"), "none")
        self.assertIn("Sources section", payload["input"][0]["content"][0]["text"])

    def test_search_keeps_codex_backend_available(self) -> None:
        self.assertTrue(
            _should_use_codex_backend(
                provider="codex",
                use_codex_responses_api=True,
                enable_image_input=True,
                has_image_urls=True,
                has_file_inputs=True,
                stream=True,
            )
        )

    def test_native_responses_preserves_web_search_output_items(self) -> None:
        async def events():
            for evt in [
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {"id": "ws_1", "type": "web_search_call", "status": "in_progress"},
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {"id": "ws_1", "type": "web_search_call", "status": "completed"},
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 1,
                    "item": {"id": "msg_1", "type": "message", "status": "completed"},
                },
                {"type": "response.completed", "response": {"id": "resp_1", "object": "response"}},
            ]:
                yield evt

        response = asyncio.run(collect_codex_responses_native_response(events()))

        self.assertEqual(
            [item["type"] for item in response["output"]],
            ["web_search_call", "message"],
        )

    def test_codex_text_collector_keeps_completed_image_on_incomplete_stream(self) -> None:
        async def events():
            for evt in [
                {
                    "type": "response.output_item.done",
                    "item": {
                        "id": "img_1",
                        "type": "image_generation_call",
                        "result": "abc123",
                        "output_format": "png",
                        "size": "1024x1536",
                    },
                },
                {
                    "type": "gateway.upstream_incomplete",
                    "message": "codex responses stream ended before completion",
                },
            ]:
                yield evt

        text, usage, tool_calls, images = asyncio.run(collect_codex_responses_text_and_usage(events()))

        self.assertEqual(text, "")
        self.assertIsNone(usage)
        self.assertIsNone(tool_calls)
        self.assertEqual(images[0]["b64_json"], "abc123")

    def test_codex_text_collector_raises_on_incomplete_stream_without_result(self) -> None:
        async def events():
            yield {
                "type": "gateway.upstream_incomplete",
                "message": "codex responses stream ended before completion",
            }

        with self.assertRaisesRegex(RuntimeError, "ended before completion"):
            asyncio.run(collect_codex_responses_text_and_usage(events()))

    def test_native_response_synthesizes_web_search_url_annotations(self) -> None:
        async def events():
            for evt in [
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {"id": "ws_1", "type": "web_search_call", "status": "completed"},
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 1,
                    "item": {
                        "id": "msg_1",
                        "type": "message",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "annotations": [],
                                "text": "Source: https://openai.com/index/example.",
                            }
                        ],
                    },
                },
                {"type": "response.completed", "response": {"id": "resp_1", "object": "response"}},
            ]:
                yield evt

        response = asyncio.run(collect_codex_responses_native_response(events()))
        annotations = response["output"][1]["content"][0]["annotations"]

        self.assertEqual(
            annotations,
            [
                {
                    "type": "url_citation",
                    "start_index": 8,
                    "end_index": 40,
                    "url": "https://openai.com/index/example",
                    "title": "openai.com",
                }
            ],
        )

    def test_responses_function_call_output_is_forwarded_to_codex_backend(self) -> None:
        messages = responses_input_to_messages(
            [
                {"type": "message", "role": "user", "content": "search KB"},
                {
                    "type": "function_call",
                    "call_id": "call_123",
                    "name": "lookup_kb_items",
                    "arguments": "{\"query\":\"web search\"}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_123",
                    "output": "{\"items\":[{\"title\":\"Web Search\",\"url\":\"https://example.test\"}]}",
                },
            ]
        )

        self.assertEqual([msg.role for msg in messages], ["user", "assistant", "tool"])
        self.assertEqual(messages[1].model_extra["tool_calls"][0]["id"], "call_123")
        self.assertEqual(messages[2].model_extra["tool_call_id"], "call_123")

        payload = convert_chat_completions_to_codex_responses(
            ChatCompletionRequest(model="gpt-5.5", messages=messages),
            model_name="gpt-5.5",
            force_stream=True,
            allow_tools=True,
        )

        function_items = [
            item for item in payload["input"] if item.get("type") in {"function_call", "function_call_output"}
        ]
        self.assertEqual(
            function_items,
            [
                {
                    "type": "function_call",
                    "call_id": "call_123",
                    "name": "lookup_kb_items",
                    "arguments": "{\"query\":\"web search\"}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_123",
                    "output": "{\"items\":[{\"title\":\"Web Search\",\"url\":\"https://example.test\"}]}",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()

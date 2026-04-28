import asyncio
import json
import unittest

from fastapi.responses import JSONResponse
from starlette.requests import Request

from codex_gateway.anthropic_compat import (
    AnthropicCountTokensRequest,
    AnthropicMessage,
    AnthropicMessagesRequest,
    anthropic_messages_to_chat_request,
    estimate_anthropic_input_tokens,
    openai_chat_completion_to_anthropic_message,
    openai_stream_to_anthropic_events,
)
from codex_gateway import server


def _request(path: str = "/v1/messages") -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({"type": "http", "method": "POST", "path": path, "headers": []}, receive)


class AnthropicCompatTests(unittest.TestCase):
    def test_anthropic_messages_to_chat_request_preserves_tool_order_and_tools(self) -> None:
        req = AnthropicMessagesRequest(
            model="claude-sonnet-4-6",
            system=[{"type": "text", "text": "You are precise."}],
            tools=[
                {
                    "name": "shell",
                    "description": "Run a shell command",
                    "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
                }
            ],
            tool_choice={"type": "tool", "name": "shell"},
            messages=[
                AnthropicMessage(
                    role="assistant",
                    content=[
                        {"type": "text", "text": "I will inspect the repo."},
                        {"type": "tool_use", "id": "toolu_1", "name": "shell", "input": {"cmd": "pwd"}},
                    ],
                ),
                AnthropicMessage(
                    role="user",
                    content=[
                        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"},
                        {"type": "text", "text": "continue"},
                    ],
                ),
            ],
        )

        chat = anthropic_messages_to_chat_request(req)

        self.assertEqual([msg.role for msg in chat.messages], ["system", "assistant", "tool", "user"])
        self.assertEqual(chat.messages[0].content, "You are precise.")
        self.assertEqual(chat.messages[1].content, "I will inspect the repo.")
        self.assertEqual(chat.messages[1].model_extra["tool_calls"][0]["function"]["name"], "shell")
        self.assertEqual(chat.messages[2].model_extra["tool_call_id"], "toolu_1")
        self.assertEqual(chat.messages[3].content, "continue")
        self.assertEqual(chat.model_extra["tool_choice"]["function"]["name"], "shell")
        self.assertEqual(chat.model_extra["tools"][0]["function"]["name"], "shell")

    def test_openai_chat_completion_to_anthropic_message_maps_tool_calls(self) -> None:
        payload = openai_chat_completion_to_anthropic_message(
            {
                "id": "chatcmpl-demo",
                "object": "chat.completion",
                "model": "gpt-5.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Need a tool",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "shell", "arguments": "{\"cmd\":\"pwd\"}"},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
            }
        )

        self.assertEqual(payload["type"], "message")
        self.assertEqual(payload["stop_reason"], "tool_use")
        self.assertEqual(payload["usage"], {"input_tokens": 12, "output_tokens": 7})
        self.assertEqual(payload["content"][0], {"type": "text", "text": "Need a tool"})
        self.assertEqual(payload["content"][1]["type"], "tool_use")
        self.assertEqual(payload["content"][1]["name"], "shell")

    def test_estimate_anthropic_input_tokens_is_non_zero(self) -> None:
        req = AnthropicCountTokensRequest(
            model="claude-sonnet-4-6",
            messages=[AnthropicMessage(role="user", content="hello world")],
        )
        self.assertGreaterEqual(estimate_anthropic_input_tokens(req), 1)

    def test_openai_stream_to_anthropic_events_emits_message_protocol(self) -> None:
        async def source():
            yield (
                'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,'
                '"model":"gpt-5.5","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
            )
            yield (
                'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,'
                '"model":"gpt-5.5","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}\n\n'
            )
            yield (
                'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,'
                '"model":"gpt-5.5","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            )
            yield "data: [DONE]\n\n"

        async def collect():
            return [event async for event in openai_stream_to_anthropic_events(source(), model="claude-sonnet-4-6")]

        events = asyncio.run(collect())
        joined = "".join(events)

        self.assertIn("event: message_start", joined)
        self.assertIn("event: content_block_start", joined)
        self.assertIn("event: content_block_delta", joined)
        self.assertIn('"text": "ok"', joined)
        self.assertIn('"stop_reason": "end_turn"', joined)
        self.assertIn("event: message_stop", joined)


class AnthropicRouteTests(unittest.TestCase):
    def test_anthropic_messages_route_wraps_existing_chat_flow(self) -> None:
        req = AnthropicMessagesRequest(
            model="claude-sonnet-4-6",
            messages=[AnthropicMessage(role="user", content="say ok")],
        )

        async def fake_chat_completions(chat_req, request, authorization=None):
            self.assertEqual(chat_req.messages[0].role, "user")
            self.assertEqual(chat_req.messages[0].content, "say ok")
            return {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": "gpt-5.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            }

        original = server.chat_completions
        server.chat_completions = fake_chat_completions
        authorization = f"Bearer {server.settings.bearer_token}" if server.settings.bearer_token else None
        try:
            response = asyncio.run(server.anthropic_messages(req, _request(), authorization))
        finally:
            server.chat_completions = original

        self.assertIsInstance(response, dict)
        self.assertEqual(response["content"], [{"type": "text", "text": "ok"}])
        self.assertEqual(response["usage"], {"input_tokens": 3, "output_tokens": 1})

    def test_anthropic_messages_route_maps_openai_errors(self) -> None:
        req = AnthropicMessagesRequest(
            model="claude-sonnet-4-6",
            messages=[AnthropicMessage(role="user", content="say ok")],
        )

        async def fake_chat_completions(chat_req, request, authorization=None):
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "bad request", "type": "codex_gateway_error"}},
            )

        original = server.chat_completions
        server.chat_completions = fake_chat_completions
        authorization = f"Bearer {server.settings.bearer_token}" if server.settings.bearer_token else None
        try:
            response = asyncio.run(server.anthropic_messages(req, _request(), authorization))
        finally:
            server.chat_completions = original

        self.assertIsInstance(response, JSONResponse)
        body = json.loads(response.body.decode("utf-8"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(body["type"], "error")
        self.assertEqual(body["error"]["message"], "bad request")


if __name__ == "__main__":
    unittest.main()

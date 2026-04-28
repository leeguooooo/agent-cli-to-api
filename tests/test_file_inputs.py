import base64
import unittest

from codex_gateway.claude_oauth import _content_to_anthropic_blocks
from codex_gateway.codex_responses import convert_chat_completions_to_codex_responses
from codex_gateway.gemini_cloudcode import _messages_to_cloudcode_payload
from codex_gateway.openai_compat import (
    ChatCompletionRequest,
    ChatMessage,
    extract_file_inputs,
    responses_input_to_messages,
)


PDF_B64 = base64.b64encode(
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF\n"
).decode("ascii")


class FileInputTests(unittest.TestCase):
    def test_responses_input_file_is_normalized_for_chat_messages(self) -> None:
        messages = responses_input_to_messages(
            [
                {"type": "input_file", "filename": "label.pdf", "file_data": PDF_B64},
                {"type": "text", "text": "summarize"},
            ]
        )

        self.assertEqual(messages[0].role, "user")
        self.assertEqual(
            messages[0].content,
            [{"type": "file", "file": {"filename": "label.pdf", "file_data": PDF_B64}}],
        )
        self.assertEqual(messages[1].content, "summarize")

    def test_extract_file_inputs_finds_chat_style_file_parts(self) -> None:
        messages = [
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "check"},
                    {"type": "file", "file": {"filename": "label.pdf", "file_data": PDF_B64}},
                ],
            )
        ]

        self.assertEqual(
            extract_file_inputs(messages),
            [{"filename": "label.pdf", "file_data": PDF_B64}],
        )

    def test_codex_backend_converts_file_parts_to_input_file(self) -> None:
        req = ChatCompletionRequest(
            model="gpt-5.5",
            messages=[
                ChatMessage(
                    role="user",
                    content=[
                        {"type": "file", "file": {"filename": "label.pdf", "file_data": PDF_B64}},
                        {"type": "text", "text": "summarize"},
                    ],
                )
            ],
        )

        payload = convert_chat_completions_to_codex_responses(
            req,
            model_name="gpt-5.5",
            force_stream=False,
        )

        content = payload["input"][0]["content"]
        self.assertIn(
            {"type": "input_file", "filename": "label.pdf", "file_data": PDF_B64},
            content,
        )
        self.assertIn({"type": "input_text", "text": "summarize"}, content)

    def test_claude_oauth_maps_pdf_to_document_block(self) -> None:
        blocks = _content_to_anthropic_blocks(
            [
                {"type": "file", "file": {"filename": "label.pdf", "file_data": PDF_B64}},
                {"type": "text", "text": "check"},
            ]
        )

        self.assertEqual(blocks[0]["type"], "document")
        self.assertEqual(blocks[0]["source"]["media_type"], "application/pdf")
        self.assertEqual(blocks[0]["source"]["data"], PDF_B64)
        self.assertEqual(blocks[0]["title"], "label.pdf")

    def test_gemini_cloudcode_maps_pdf_to_inline_data(self) -> None:
        payload = _messages_to_cloudcode_payload(
            [
                ChatMessage(
                    role="user",
                    content=[
                        {"type": "file", "file": {"filename": "label.pdf", "file_data": PDF_B64}},
                        {"type": "text", "text": "check"},
                    ],
                )
            ],
            project_id="demo-project",
            model_name="gemini-3-flash-preview",
            reasoning_effort="low",
        )

        parts = payload["request"]["contents"][0]["parts"]
        self.assertIn({"text": "check"}, parts)
        self.assertIn(
            {"inlineData": {"mime_type": "application/pdf", "data": PDF_B64}},
            parts,
        )


if __name__ == "__main__":
    unittest.main()

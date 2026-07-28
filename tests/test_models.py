import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from codex_gateway import server


class ModelListTests(unittest.TestCase):
    def test_codex_provider_advertises_current_codex_models_by_default(self) -> None:
        settings = SimpleNamespace(
            bearer_token=None,
            provider="codex",
            default_model="gpt-5.6-sol",
            cursor_agent_model=None,
            claude_model=None,
            gemini_model=None,
            advertised_models=[],
            model_aliases={},
            allow_client_model_override=True,
        )

        with mock.patch.object(server, "settings", settings):
            result = asyncio.run(server.list_models())

        model_ids = [item["id"] for item in result["data"]]
        self.assertEqual(
            model_ids,
            ["default", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"],
        )

    def test_explicit_advertised_models_override_codex_defaults(self) -> None:
        settings = SimpleNamespace(
            bearer_token=None,
            provider="codex",
            default_model="gpt-5.6-sol",
            cursor_agent_model=None,
            claude_model=None,
            gemini_model=None,
            advertised_models=["custom-model"],
            model_aliases={},
            allow_client_model_override=True,
        )

        with mock.patch.object(server, "settings", settings):
            result = asyncio.run(server.list_models())

        self.assertEqual([item["id"] for item in result["data"]], ["custom-model"])


if __name__ == "__main__":
    unittest.main()

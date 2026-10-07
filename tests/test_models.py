import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from codex_gateway import codex_models, server
from codex_gateway.config import Settings


class ModelListTests(unittest.TestCase):
    def test_codex_provider_advertises_current_codex_models_by_default(self) -> None:
        settings = SimpleNamespace(
            bearer_token=None,
            provider="codex",
            default_model="gpt-6.1-sol",
            cursor_agent_model=None,
            claude_model=None,
            gemini_model=None,
            advertised_models=[],
            model_aliases={},
            allow_client_model_override=True,
            codex_cli_home=None,
        )
        live = ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol"]

        with mock.patch.object(server, "settings", settings), mock.patch.object(
            server, "available_codex_models", return_value=live
        ):
            result = asyncio.run(server.list_models())

        model_ids = [item["id"] for item in result["data"]]
        self.assertEqual(
            model_ids,
            ["default", "gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol"],
        )

    def test_explicit_advertised_models_override_codex_defaults(self) -> None:
        settings = SimpleNamespace(
            bearer_token=None,
            provider="codex",
            default_model="gpt-6.1-sol",
            cursor_agent_model=None,
            claude_model=None,
            gemini_model=None,
            advertised_models=["custom-model"],
            model_aliases={},
            allow_client_model_override=True,
            codex_cli_home=None,
        )

        with mock.patch.object(server, "settings", settings):
            result = asyncio.run(server.list_models())

        self.assertEqual([item["id"] for item in result["data"]], ["custom-model"])


class CodexModelDiscoveryTests(unittest.TestCase):
    def tearDown(self) -> None:
        codex_models._live_models = []
        codex_models._file_models = []
        codex_models._file_checked_at = 0.0

    def test_parse_orders_by_priority_and_skips_hidden(self) -> None:
        payload = {
            "models": [
                {"slug": "gpt-5.6-sol", "visibility": "list", "priority": 5},
                {"slug": "gpt-reserve", "visibility": "hide", "priority": 1},
                {"slug": "gpt-7-sol", "visibility": "list", "priority": 0},
            ]
        }
        self.assertEqual(codex_models.parse_models_payload(payload), ["gpt-7-sol", "gpt-5.6-sol"])

    def test_reads_newest_cli_cache_file(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as cli_home:
            for root, fetched_at, slug in (
                (home, "2026-01-01T00:00:00Z", "gpt-old"),
                (cli_home, "2026-10-01T00:00:00Z", "gpt-new"),
            ):
                path = Path(root) / ".codex" / "models_cache.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({"fetched_at": fetched_at, "models": [{"slug": slug, "priority": 1}]}))
            with mock.patch.object(Path, "home", return_value=Path(home)):
                self.assertEqual(codex_models.latest_codex_model(cli_home), "gpt-new")

    def test_live_list_wins_and_settings_follow_it(self) -> None:
        codex_models._live_models = ["gpt-7-sol", "gpt-6.1-sol"]
        self.assertEqual(Settings(codex_model="").default_model, "gpt-7-sol")
        self.assertEqual(Settings(codex_model="auto").default_model, "gpt-7-sol")
        self.assertEqual(Settings(codex_model="gpt-5.6-sol").default_model, "gpt-5.6-sol")


if __name__ == "__main__":
    unittest.main()

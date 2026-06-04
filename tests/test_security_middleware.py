import time
import unittest
from types import SimpleNamespace
from unittest import mock

from starlette.requests import Request

from codex_gateway import server


def _request(path: str, *, client: str = "127.0.0.1", headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers or [],
        "client": (client, 12345),
        "scheme": "http",
        "server": ("127.0.0.1", 8000),
    }
    return Request(scope, receive)


class SecurityMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        server._banned_ips.clear()

    def tearDown(self) -> None:
        server._banned_ips.clear()

    def test_client_ip_uses_forwarded_for_only_when_trusted(self) -> None:
        req = _request(
            "/wham/tasks/list",
            headers=[(b"x-forwarded-for", b"203.0.113.7, 10.0.0.1")],
        )

        with mock.patch.object(server, "settings", SimpleNamespace(trust_proxy_headers=False)):
            self.assertEqual(server._request_client_ip(req), "127.0.0.1")

        with mock.patch.object(server, "settings", SimpleNamespace(trust_proxy_headers=True)):
            self.assertEqual(server._request_client_ip(req), "203.0.113.7")

    def test_suspicious_path_bans_ip_until_expiry(self) -> None:
        now = time.time()
        with mock.patch.object(server, "settings", SimpleNamespace(ban_duration_seconds=60)):
            server._ban_ip("203.0.113.7", now)

        self.assertTrue(server._is_ip_banned("203.0.113.7", now + 10))
        self.assertFalse(server._is_ip_banned("203.0.113.7", now + 61))

    def test_suspicious_path_prefixes_match_high_confidence_scanner_routes(self) -> None:
        with mock.patch.object(
            server,
            "settings",
            SimpleNamespace(suspicious_path_prefixes=["/.env", "/.git/", "/wp-admin/"]),
        ):
            self.assertTrue(server._is_suspicious_path("/.env"))
            self.assertTrue(server._is_suspicious_path("/.git/config"))
            self.assertTrue(server._is_suspicious_path("/wp-admin/setup-config.php"))
            self.assertFalse(server._is_suspicious_path("/wham/tasks/list"))
            self.assertFalse(server._is_suspicious_path("/codex/remote/control/environments"))
            self.assertFalse(server._is_suspicious_path("/v1/chat/completions"))

    def test_request_header_audit_filters_status_and_path(self) -> None:
        settings = SimpleNamespace(
            audit_request_headers=True,
            audit_request_header_statuses=["403", "404"],
            audit_request_header_prefixes=["/aip/", "/wham/"],
        )
        with mock.patch.object(server, "settings", settings):
            self.assertTrue(server._should_audit_request_headers("/aip/connectors/x/logo", 404))
            self.assertTrue(server._should_audit_request_headers("/wham/tasks/list", 403))
            self.assertFalse(server._should_audit_request_headers("/v1/chat/completions", 404))
            self.assertFalse(server._should_audit_request_headers("/aip/connectors/x/logo", 200))

    def test_safe_header_value_redacts_sensitive_headers(self) -> None:
        with mock.patch.object(server, "settings", SimpleNamespace(audit_redact_headers=True)):
            self.assertEqual(server._safe_header_value("authorization", "Bearer secret"), "<redacted>")
            self.assertEqual(server._safe_header_value("cookie", "session=secret"), "<redacted>")
            self.assertEqual(server._safe_header_value("user-agent", "curl/8"), "curl/8")

    def test_safe_header_value_can_keep_sensitive_headers_for_audit(self) -> None:
        with mock.patch.object(server, "settings", SimpleNamespace(audit_redact_headers=False)):
            self.assertEqual(server._safe_header_value("authorization", "Bearer secret"), "Bearer secret")
            self.assertEqual(server._safe_header_value("cookie", "session=secret"), "session=secret")

    def test_request_header_audit_supports_wildcard_header_names(self) -> None:
        req = _request(
            "/aip/connectors/x/logo",
            headers=[(b"authorization", b"Bearer visible"), (b"x-custom-header", b"value")],
        )
        settings = SimpleNamespace(
            audit_request_headers=True,
            audit_request_header_statuses=["404"],
            audit_request_header_prefixes=["/aip/"],
            audit_request_header_names=["*"],
            audit_redact_headers=False,
        )
        with mock.patch.object(server, "settings", settings), mock.patch.object(server, "print") as mocked_print:
            server._audit_request_headers(req, "127.0.0.1", 404)

        rendered = str(mocked_print.call_args)
        self.assertIn("authorization", rendered)
        self.assertIn("Bearer visible", rendered)
        self.assertIn("x-custom-header", rendered)


if __name__ == "__main__":
    unittest.main()

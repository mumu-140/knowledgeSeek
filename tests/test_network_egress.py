from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from paperseek_core.network.egress import EgressRouter, load_proxy_profiles, redact_network_text
from paperseek_core.network.url_policy import EndpointPolicyError, validate_outbound_url


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.headers = {}
        self.text = ""


class NetworkRedactionTest(unittest.TestCase):
    def test_proxy_credentials_and_query_secrets_are_redacted(self):
        raw = "ProxyError via http://alice:s3cr3t@proxy.example:8080/path?api_key=abc123&token=xyz"
        safe = redact_network_text(raw)
        self.assertNotIn("alice", safe)
        self.assertNotIn("s3cr3t", safe)
        self.assertNotIn("abc123", safe)
        self.assertNotIn("xyz", safe)
        self.assertIn("proxy.example:8080", safe)
        self.assertIn("<redacted>", safe)


class URLPolicyTest(unittest.TestCase):
    def test_public_https_endpoint_is_allowed(self):
        endpoint = validate_outbound_url(
            "https://llm.example.com/v1",
            resolver=lambda host, port: ["93.184.216.34"],
        )
        self.assertEqual(endpoint.host, "llm.example.com")
        self.assertEqual(endpoint.url, "https://llm.example.com/v1")

    def test_private_and_metadata_addresses_are_rejected(self):
        addresses = ["127.0.0.1", "169.254.169.254", "10.1.2.3", "172.16.1.1", "192.168.1.2", "::1", "fc00::1"]
        for address in addresses:
            with self.subTest(address=address):
                with self.assertRaises(EndpointPolicyError):
                    validate_outbound_url("https://custom.example/v1", resolver=lambda host, port, address=address: [address])

    def test_url_credentials_and_plain_http_are_rejected(self):
        with self.assertRaises(EndpointPolicyError):
            validate_outbound_url("https://user:pass@example.com/v1", resolver=lambda host, port: ["93.184.216.34"])
        with self.assertRaises(EndpointPolicyError):
            validate_outbound_url("http://example.com/v1", resolver=lambda host, port: ["93.184.216.34"])
        with self.assertRaises(EndpointPolicyError):
            validate_outbound_url("https://example.com/v1?token=secret", resolver=lambda host, port: ["93.184.216.34"])

    def test_http_can_be_explicitly_enabled_by_server_policy(self):
        endpoint = validate_outbound_url(
            "http://example.com/v1",
            allow_http=True,
            resolver=lambda host, port: ["93.184.216.34"],
        )
        self.assertEqual(endpoint.port, 80)


class EgressRouterTest(unittest.TestCase):
    def _env(self):
        return {
            "PROXY_POOL": "main,backup",
            "PROXY_MAIN_URL": "http://proxy-main.example:8080",
            "PROXY_BACKUP_URL": "http://proxy-backup.example:8080",
            "PROXY_FAILURE_THRESHOLD": "99",
            "EGRESS_MAX_ATTEMPTS": "4",
        }

    def test_proxy_profiles_are_server_managed(self):
        profiles = load_proxy_profiles(self._env())
        self.assertEqual([item.id for item in profiles], ["main", "backup"])
        self.assertEqual(profiles[0].label, "main")

    def test_auto_fails_over_direct_then_multiple_proxies(self):
        calls = []

        def fake_get(url, **kwargs):
            proxies = kwargs.get("proxies") or {}
            route = proxies.get("https") or "direct"
            calls.append(route)
            if route == "direct":
                raise requests.ConnectionError("network unreachable")
            if "proxy-main" in route:
                return FakeResponse(503)
            return FakeResponse(200)

        router = EgressRouter(mode="auto", environ=self._env())
        with patch("paperseek_core.network.egress.requests.get", side_effect=fake_get):
            response = router.request("GET", "https://api.example.com/v1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(router.attempted_routes, ["direct", "main", "backup"])
        self.assertEqual(router.last_route, "backup")
        self.assertEqual(len(calls), 3)

    def test_auth_failure_does_not_trigger_proxy_failover(self):
        calls = []

        def fake_get(url, **kwargs):
            calls.append(kwargs.get("proxies"))
            return FakeResponse(401)

        router = EgressRouter(mode="auto", environ=self._env())
        with patch("paperseek_core.network.egress.requests.get", side_effect=fake_get):
            response = router.request("GET", "https://api.example.com/v1")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(router.attempted_routes, ["direct"])
        self.assertEqual(len(calls), 1)

    def test_connect_timeout_is_bounded_without_shortening_read_timeout(self):
        captured = {}

        def fake_get(url, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return FakeResponse(200)

        env = dict(self._env())
        env["EGRESS_CONNECT_TIMEOUT"] = "3"
        router = EgressRouter(mode="direct", environ=env)
        with patch("paperseek_core.network.egress.requests.get", side_effect=fake_get):
            response = router.request("GET", "https://api.example.com/v1", timeout=120)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["timeout"], (3.0, 120.0))

    def test_auto_uses_short_direct_probe_then_regular_proxy_connect_timeout(self):
        timeouts = []

        def fake_get(url, **kwargs):
            proxies = kwargs.get("proxies") or {}
            timeouts.append(kwargs.get("timeout"))
            if not proxies.get("https"):
                raise requests.ConnectionError("direct unavailable")
            return FakeResponse(200)

        env = dict(self._env())
        env["EGRESS_CONNECT_TIMEOUT"] = "8"
        env["EGRESS_AUTO_DIRECT_CONNECT_TIMEOUT"] = "2"
        router = EgressRouter(mode="auto", environ=env)
        with patch("paperseek_core.network.egress.requests.get", side_effect=fake_get):
            response = router.request("GET", "https://api.example.com/v1", timeout=120)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(router.attempted_routes, ["direct", "main"])
        self.assertEqual(timeouts, [(2.0, 120.0), (8.0, 120.0)])

    def test_explicit_proxy_selection_uses_only_named_profiles(self):
        router = EgressRouter(mode="proxy", proxy_ids=["backup"], environ=self._env())
        self.assertEqual([item.id for item in router._selected_profiles()], ["backup"])
        with self.assertRaises(ValueError):
            EgressRouter(mode="proxy", proxy_ids=["missing"], environ=self._env())


if __name__ == "__main__":
    unittest.main()

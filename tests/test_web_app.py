import re
import tempfile
from pathlib import Path
from paperseek.providers import PaperAuthor, PaperCitation, PaperIdentifiers, PaperLinks, PaperNames, PaperRecord, PaperSource
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from paperseek.web_app import app
from paperseek.web_app import DiagnosticRequest, SearchRequest, _config_from_payload
from tests.helpers import CONFIG_ENV_KEYS, temporary_env


SOURCE_IDS = ["openalex", "arxiv", "semanticscholar", "pubmed", "googlescholar", "paperhub", "crossref", "federated", "wos"]


class WebAppTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_sources_endpoint_returns_ordered_source_capabilities(self):
        response = self.client.get("/api/sources")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item["id"] for item in payload["sources"]], SOURCE_IDS)
        self.assertTrue(payload["sources"][0]["default"])
        self.assertEqual(payload["sources"][-1]["status"], "temporarily_unavailable")
        self.assertIn("discipline_fields", payload["sources"][0]["supported_parameters"])

    def test_disciplines_endpoint_returns_openalex_fields(self):
        response = self.client.get("/api/disciplines")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["disciplines"]), 26)
        self.assertEqual(payload["disciplines"][6]["id"], "17")
        self.assertEqual(payload["disciplines"][6]["label"], "Computer Science")
        self.assertEqual(payload["sources"]["openalex"]["mode"], "native")
        self.assertEqual(payload["sources"]["wos"]["label"], "Web of Science Category")
        self.assertEqual(payload["sources"]["googlescholar"]["mode"], "text")
        self.assertEqual(payload["sources"]["paperhub"]["mode"], "text")
        self.assertEqual(payload["sources"]["paperhub"]["options"], [])

    def test_diagnostics_accepts_ollama_without_api_key(self):
        response = self.client.post(
            "/api/diagnostics",
            json={
                "data_source": "openalex",
                "llm_provider": "ollama",
                "llm_api_type": "openai_chat",
                "llm_model": "qwen3:8b",
                "llm_base_url": "http://127.0.0.1:11434/v1",
                "target_min": 5,
                "target_max": 50,
                "max_iterations": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(payload["status"], ("pass", "warning"))
        self.assertNotEqual(payload["status"], "fail")

    def test_search_validation_reports_missing_question(self):
        response = self.client.post(
            "/api/search",
            json={
                "question": "",
                "data_source": "openalex",
                "llm_provider": "ollama",
                "llm_api_type": "openai_chat",
                "target_min": 5,
                "target_max": 50,
                "max_iterations": 5,
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("Research Question is required", response.json()["detail"])

    def test_search_rejects_invalid_target_range_before_llm_setup(self):
        response = self.client.post(
            "/api/search",
            json={
                "question": "open innovation",
                "data_source": "openalex",
                "llm_provider": "ollama",
                "llm_api_type": "openai_chat",
                "target_min": 20,
                "target_max": 5,
                "max_iterations": 5,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Target minimum cannot exceed", response.json()["detail"])

    def test_search_accepts_new_source_payload(self):
        class FakeAgent:
            def __init__(self, config, llm):
                self.config = config
                self.llm = llm

            def search(self, question, verbose=False, event_handler=None):
                return {
                    "question": question,
                    "source": self.config.data_source,
                    "final_query": "graph neural networks",
                    "db": self.config.data_source.upper(),
                    "field": "",
                    "total": 0,
                    "iterations": 1,
                    "history": [],
                    "citation_map": {},
                    "ranked": [],
                }

        with patch("paperseek.web_app.create_llm_client", return_value=object()), patch(
            "paperseek.web_app.PaperSeekAgent", FakeAgent
        ):
            response = self.client.post(
                "/api/search",
                json={
                    "question": "graph neural networks",
                    "data_source": "arxiv",
                    "llm_provider": "ollama",
                    "llm_api_type": "openai_chat",
                    "llm_model": "qwen3:8b",
                    "llm_base_url": "http://127.0.0.1:11434/v1",
                    "target_min": 0,
                    "target_max": 5,
                    "max_iterations": 1,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "arxiv")

    def test_config_from_payload_preserves_environment_credentials_when_form_keys_are_blank(self):
        with temporary_env({
            "DATA_SOURCE": "openalex",
            "OPENALEX_API_KEY": "oa-env-test",
            "LLM_PROVIDER": "deepseek",
            "LLM_API_TYPE": "openai_chat",
            "LLM_MODEL": "deepseek-test",
            "LLM_BASE_URL": "https://api.deepseek.com",
            "LLM_API_KEY": "sk-env-test",
        }, clear=CONFIG_ENV_KEYS):
            payload = SearchRequest(
                question="open innovation",
                data_source="openalex",
                openalex_api_key="",
                llm_api_key="",
                llm_provider="",
                llm_api_type="",
                llm_model=None,
                llm_base_url=None,
                discipline_fields=["Computer Science", "14"],
            )
            config = _config_from_payload(payload)
            self.assertEqual(config.openalex_api_key, "oa-env-test")
            self.assertEqual(config.llm_api_key, "sk-env-test")
            self.assertEqual(config.llm_provider, "deepseek")
            self.assertEqual(config.llm_model, "deepseek-test")
            self.assertEqual(config.discipline_fields, ("17", "14"))

    def test_config_from_payload_uses_text_hint_for_sources_without_native_filter(self):
        payload = SearchRequest(
            question="graph neural networks",
            data_source="paperhub",
            llm_provider="ollama",
            llm_api_type="openai_chat",
            discipline_fields=["Computer Science", "17"],
            search_field="human-computer interaction",
        )
        config = _config_from_payload(payload)
        self.assertEqual(config.data_source, "paperhub")
        self.assertEqual(config.discipline_fields, ())
        self.assertEqual(config.search_field, "human-computer interaction")

    def test_config_defaults_reports_configured_secrets_without_exposing_values(self):
        with temporary_env({
            "DATA_SOURCE": "openalex",
            "OPENALEX_API_KEY": "oa-env-test",
            "LLM_PROVIDER": "deepseek",
            "LLM_API_TYPE": "openai_chat",
            "LLM_MODEL": "deepseek-test",
            "LLM_BASE_URL": "https://api.deepseek.com",
            "LLM_API_KEY": "sk-env-test",
        }, clear=CONFIG_ENV_KEYS):
            response = self.client.get("/api/config/defaults")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["llm_provider"], "deepseek")
            self.assertTrue(payload["has_llm_api_key"])
            self.assertTrue(payload["has_openalex_api_key"])
            self.assertNotIn("sk-env-test", response.text)
            self.assertNotIn("oa-env-test", response.text)


    def test_sources_endpoint_includes_federated_source(self):
        response = self.client.get("/api/sources")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        sources_map = {item["id"]: item for item in payload["sources"]}
        self.assertIn("federated", sources_map)
        fed = sources_map["federated"]
        self.assertIn("federated_profile", fed.get("supported_parameters", []))
        self.assertIn("federated_max_per_source", fed.get("supported_parameters", []))

    def test_config_defaults_includes_federated_settings(self):
        response = self.client.get("/api/config/defaults")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("federated_profile", payload)
        self.assertIn("federated_max_per_source", payload)
        self.assertIn(payload["federated_profile"], ("biomed", "cs", "general"))
        self.assertGreaterEqual(payload["federated_max_per_source"], 1)

    def test_config_from_payload_with_federated_profiles(self):
        for profile in ("biomed", "cs", "general"):
            payload = SearchRequest(
                question="immune checkpoint inhibitors",
                data_source="federated",
                federated_profile=profile,
                federated_max_per_source=30,
            )
            config = _config_from_payload(payload)
            self.assertEqual(config.data_source, "federated")
            self.assertEqual(config.federated_profile, profile)
            self.assertEqual(config.federated_max_per_source, 30)

    def test_search_federated_successful_multi_source_and_provenance(self):
        record = PaperRecord(
            uid="mosaic:sample-1",
            title="CRISPR gene editing in mammalian cells",
            types=["article"],
            source=PaperSource(source_title="Nature", publish_year=2024),
            names=PaperNames(authors=[PaperAuthor(display_name="Jennifer Doudna")]),
            links=PaperLinks(record="https://nature.com/example", pdf="https://nature.com/example.pdf"),
            citations=[PaperCitation(db="MultiSource", count=120)],
            identifiers=PaperIdentifiers(doi="10.1038/sample1"),
            abstract="Study on gene editing techniques.",
            provider="federated",
            raw={
                "mosaic_sources": ["PubMed", "OpenAlex", "Crossref"],
                "mosaic_source": "PubMed",
            },
        )
        fake_stats = {
            "raw_total": 75,
            "unique": 60,
            "merged": 15,
            "per_source": {"PubMed": 25, "Europe PMC": 25, "Crossref": 25},
            "errors": {},
        }
        fake_steps = [
            {"step": "retrieval", "count": 60},
            {"step": "rrf", "count": 60},
            {"step": "embedding", "count": 60},
            {"step": "reranker", "count": 30, "skipped": True},
            {"step": "llm", "count": 10},
        ]

        class FakeFederatedAgent:
            def __init__(self, config, llm):
                self.config = config
                self.llm = llm

            def search(self, question, verbose=False, event_handler=None):
                return {
                    "question": question,
                    "source": "federated",
                    "final_query": "CRISPR gene editing",
                    "db": "FEDERATED",
                    "field": "",
                    "total": 60,
                    "iterations": 1,
                    "history": [],
                    "citation_map": {},
                    "federated_stats": fake_stats,
                    "ranking_steps": fake_steps,
                    "ranked": [
                        {
                            "document": record,
                            "score": 9.5,
                            "reasoning": "Direct match for CRISPR mechanisms.",
                            "retrieval_lanes": ["dense", "bm25"],
                        }
                    ],
                }

        with patch("paperseek.web_app.create_llm_client", return_value=object()), patch(
            "paperseek.web_app.PaperSeekAgent", FakeFederatedAgent
        ):
            response = self.client.post(
                "/api/search",
                json={
                    "question": "CRISPR gene editing",
                    "data_source": "federated",
                    "federated_profile": "biomed",
                    "federated_max_per_source": 25,
                    "llm_provider": "ollama",
                    "llm_api_type": "openai_chat",
                    "target_min": 5,
                    "target_max": 20,
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["source"], "federated")
        self.assertEqual(data["federated_stats"]["raw_total"], 75)
        self.assertEqual(data["federated_stats"]["unique"], 60)
        self.assertEqual(data["federated_stats"]["merged"], 15)
        self.assertEqual(data["federated_stats"]["per_source"]["PubMed"], 25)
        self.assertEqual(len(data["ranked"]), 1)
        paper = data["ranked"][0]
        self.assertEqual(paper["mosaic_sources"], ["PubMed", "OpenAlex", "Crossref"])
        self.assertEqual(paper["mosaic_source"], "PubMed")
        self.assertEqual(paper["relevance_reason"], "Direct match for CRISPR mechanisms.")
        self.assertEqual(data["ranking_steps"], fake_steps)

    def test_search_federated_partial_source_failure_and_429(self):
        fake_stats = {
            "raw_total": 45,
            "unique": 40,
            "merged": 5,
            "per_source": {"PubMed": 25, "Europe PMC": 20, "Semantic Scholar": 0, "bioRxiv": 0},
            "errors": {
                "Semantic Scholar": "Rate limited (429)",
                "bioRxiv": "No papers found",
            },
        }

        class FakePartialAgent:
            def __init__(self, config, llm):
                self.config = config
                self.llm = llm

            def search(self, question, verbose=False, event_handler=None):
                return {
                    "question": question,
                    "source": "federated",
                    "final_query": "cancer immunotherapy",
                    "db": "FEDERATED",
                    "field": "",
                    "total": 40,
                    "iterations": 1,
                    "history": [],
                    "citation_map": {},
                    "federated_stats": fake_stats,
                    "ranked": [],
                }

        with patch("paperseek.web_app.create_llm_client", return_value=object()), patch(
            "paperseek.web_app.PaperSeekAgent", FakePartialAgent
        ):
            response = self.client.post(
                "/api/search",
                json={
                    "question": "cancer immunotherapy",
                    "data_source": "federated",
                    "federated_profile": "biomed",
                    "llm_provider": "ollama",
                    "llm_api_type": "openai_chat",
                    "target_min": 0,
                    "target_max": 20,
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["federated_stats"]["errors"]["Semantic Scholar"], "Rate limited (429)")
        self.assertEqual(data["federated_stats"]["errors"]["bioRxiv"], "No papers found")
        self.assertEqual(data["federated_stats"]["per_source"]["Semantic Scholar"], 0)

    def test_search_federated_zero_result_source(self):
        fake_stats = {
            "raw_total": 10,
            "unique": 10,
            "merged": 0,
            "per_source": {"arXiv": 10, "DBLP": 0},
            "errors": {},
        }

        class FakeZeroSourceAgent:
            def __init__(self, config, llm):
                self.config = config
                self.llm = llm

            def search(self, question, verbose=False, event_handler=None):
                return {
                    "question": question,
                    "source": "federated",
                    "final_query": "quantum gravity",
                    "db": "FEDERATED",
                    "field": "",
                    "total": 10,
                    "iterations": 1,
                    "history": [],
                    "citation_map": {},
                    "federated_stats": fake_stats,
                    "ranked": [],
                }

        with patch("paperseek.web_app.create_llm_client", return_value=object()), patch(
            "paperseek.web_app.PaperSeekAgent", FakeZeroSourceAgent
        ):
            response = self.client.post(
                "/api/search",
                json={
                    "question": "quantum gravity",
                    "data_source": "federated",
                    "federated_profile": "cs",
                    "llm_provider": "ollama",
                    "llm_api_type": "openai_chat",
                    "target_min": 0,
                    "target_max": 10,
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["federated_stats"]["per_source"]["DBLP"], 0)
        self.assertEqual(data["federated_stats"]["unique"], 10)


    def test_llm_models_get_returns_presets(self):
        response = self.client.get("/api/llm/models?provider=deepseek")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["provider"], "deepseek")
        self.assertEqual(data["source"], "preset")
        self.assertIn("deepseek-chat", data["models"])
        self.assertIn("deepseek-reasoner", data["models"])

    def test_config_defaults_never_returns_secret_values(self):
        """/api/config/defaults must never echo API key values back to the client."""
        secrets = {
            "LLM_API_KEY": "sk-test",
            "OPENALEX_API_KEY": "oa-test-secret",
            "WOS_API_KEY": "wos-test-secret",
            "SEMANTIC_SCHOLAR_API_KEY": "ss-test-secret",
            "PUBMED_API_KEY": "pubmed-test-secret",
            "SERPER_API_KEY": "serper-test-secret",
        }
        with temporary_env(secrets, clear=CONFIG_ENV_KEYS):
            response = self.client.get("/api/config/defaults")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        for secret in secrets.values():
            self.assertNotIn(secret, response.text)
        # Secrets surface only as has_* booleans.
        self.assertTrue(payload["has_llm_api_key"])
        self.assertTrue(payload["has_openalex_api_key"])
        self.assertTrue(payload["has_wos_api_key"])
        for key in payload:
            self.assertFalse(key.endswith("_api_key") and not key.startswith("has_"),
                             f"raw secret field exposed: {key}")

    def test_config_save_endpoint_is_not_public(self):
        """POST /api/config/save was removed; it must not write server config."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            with temporary_env(
                {"PAPERSEEK_CONFIG_FILE": str(config_path)},
                clear=CONFIG_ENV_KEYS,
            ):
                response = self.client.post(
                    "/api/config/save",
                    json={"settings": {"LLM_PROVIDER": "deepseek", "LLM_MODEL": "deepseek-chat"}},
                )
                self.assertIn(response.status_code, (404, 405))
                self.assertFalse(config_path.exists())

    def test_remote_model_discovery_cannot_fetch_arbitrary_url(self):
        """POST /api/llm/models was removed; no user URL may reach requests.get."""
        import requests

        with patch("paperseek_core.llm.requests.get") as mock_get:
            response = self.client.post(
                "/api/llm/models",
                json={
                    "llm_provider": "custom",
                    "llm_base_url": "http://169.254.169.254/latest/meta-data/",
                    "llm_api_key": "sk-test",
                },
            )
            self.assertIn(response.status_code, (404, 405))
            mock_get.assert_not_called()
        # Preset listing stays available without any network egress.
        response = self.client.get("/api/llm/models?provider=openai")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "preset")

    # SSRF hardening: the outbound base URLs the server dials (LLM, embedding, reranker)
    # must come from trusted server env/config only. A client payload must never be able to
    # steer them at cloud metadata (169.254.169.254), localhost, or RFC1918 internal ranges.
    # _config_from_payload is the sole producer of the AgentConfig used for those outbound
    # requests, so proving the malicious value never lands on the config proves it never
    # becomes the runtime outbound URL.
    SSRF_URLS = (
        "http://127.0.0.1:9000/v1",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/internal",
        "http://172.16.0.5/v1",
        "http://192.168.1.1/admin",
    )

    def test_search_config_ignores_client_supplied_llm_base_url(self):
        """A client-supplied llm_base_url must not become the outbound LLM URL."""
        trusted = "https://api.deepseek.com"
        for url in self.SSRF_URLS:
            with self.subTest(url=url), temporary_env({
                "LLM_PROVIDER": "deepseek",
                "LLM_API_TYPE": "openai_chat",
                "LLM_MODEL": "deepseek-test",
                "LLM_BASE_URL": trusted,
                "LLM_API_KEY": "sk-test",
            }, clear=CONFIG_ENV_KEYS):
                # Provider unchanged: the trusted env base URL must be retained verbatim.
                payload = SearchRequest(question="q", llm_provider="", llm_base_url=url)
                config = _config_from_payload(payload)
                self.assertEqual(config.llm_base_url, trusted)
                self.assertNotEqual(config.llm_base_url, url)

    def test_search_config_uses_provider_default_not_client_base_url_on_switch(self):
        """Switching provider must fall back to the trusted provider default, never the client URL."""
        for url in self.SSRF_URLS:
            with self.subTest(url=url), temporary_env({
                "LLM_PROVIDER": "deepseek",
                "LLM_API_TYPE": "openai_chat",
                "LLM_BASE_URL": "https://api.deepseek.com",
            }, clear=CONFIG_ENV_KEYS):
                payload = SearchRequest(
                    question="q",
                    llm_provider="openai",
                    llm_api_type="openai_chat",
                    llm_base_url=url,
                )
                config = _config_from_payload(payload)
                self.assertNotEqual(config.llm_base_url, url)
                # It resolves to a server-known provider default, not anything client-supplied.
                self.assertTrue(config.llm_base_url.startswith("https://"))
                self.assertNotIn("127.0.0.1", config.llm_base_url)
                self.assertNotIn("169.254.169.254", config.llm_base_url)

    def test_search_config_ignores_client_supplied_retrieval_base_urls(self):
        """Client embedding/reranker base URLs must not become outbound URLs."""
        embed_trusted = "https://embeddings.internal.example/v1"
        rerank_trusted = "https://rerank.internal.example/v1"
        for url in self.SSRF_URLS:
            with self.subTest(url=url), temporary_env({
                "RETRIEVAL_EMBEDDING_BASE_URL": embed_trusted,
                "RETRIEVAL_RERANKER_BASE_URL": rerank_trusted,
            }, clear=CONFIG_ENV_KEYS + ("RETRIEVAL_EMBEDDING_BASE_URL", "RETRIEVAL_RERANKER_BASE_URL")):
                payload = SearchRequest(
                    question="q",
                    retrieval_embedding_base_url=url,
                    retrieval_reranker_base_url=url,
                )
                config = _config_from_payload(payload)
                self.assertEqual(config.retrieval_embedding_base_url, embed_trusted)
                self.assertEqual(config.retrieval_reranker_base_url, rerank_trusted)
                self.assertNotEqual(config.retrieval_embedding_base_url, url)
                self.assertNotEqual(config.retrieval_reranker_base_url, url)

    def test_retrieval_base_urls_stay_empty_when_env_unset(self):
        """With no server-side base URL configured, a client value must not fill the gap."""
        for url in self.SSRF_URLS:
            with self.subTest(url=url), temporary_env({}, clear=CONFIG_ENV_KEYS + (
                "RETRIEVAL_EMBEDDING_BASE_URL", "RETRIEVAL_RERANKER_BASE_URL",
            )):
                payload = SearchRequest(
                    question="q",
                    retrieval_embedding_base_url=url,
                    retrieval_reranker_base_url=url,
                )
                config = _config_from_payload(payload)
                self.assertEqual(config.retrieval_embedding_base_url, "")
                self.assertEqual(config.retrieval_reranker_base_url, "")

    def test_diagnostics_config_ignores_client_supplied_base_urls(self):
        """DiagnosticRequest must be hardened identically to SearchRequest."""
        embed_trusted = "https://embeddings.internal.example/v1"
        rerank_trusted = "https://rerank.internal.example/v1"
        llm_trusted = "https://api.deepseek.com"
        for url in self.SSRF_URLS:
            with self.subTest(url=url), temporary_env({
                "LLM_PROVIDER": "deepseek",
                "LLM_API_TYPE": "openai_chat",
                "LLM_MODEL": "deepseek-test",
                "LLM_BASE_URL": llm_trusted,
                "RETRIEVAL_EMBEDDING_BASE_URL": embed_trusted,
                "RETRIEVAL_RERANKER_BASE_URL": rerank_trusted,
            }, clear=CONFIG_ENV_KEYS + ("RETRIEVAL_EMBEDDING_BASE_URL", "RETRIEVAL_RERANKER_BASE_URL")):
                payload = DiagnosticRequest(
                    question="q",
                    llm_provider="",
                    llm_base_url=url,
                    retrieval_embedding_base_url=url,
                    retrieval_reranker_base_url=url,
                )
                config = _config_from_payload(payload)
                self.assertEqual(config.llm_base_url, llm_trusted)
                self.assertEqual(config.retrieval_embedding_base_url, embed_trusted)
                self.assertEqual(config.retrieval_reranker_base_url, rerank_trusted)
                for value in (config.llm_base_url, config.retrieval_embedding_base_url,
                              config.retrieval_reranker_base_url):
                    self.assertNotEqual(value, url)

    def test_browser_persistence_excludes_secret_fields(self):
        """app.js must persist only the allowlisted non-secret preference keys."""
        app_js = Path(__file__).resolve().parent.parent / "paperseek" / "static" / "app.js"
        source = app_js.read_text(encoding="utf-8")
        allowlist_match = re.search(r"PERSISTED_CONFIG_KEYS\s*=\s*\[(.*?)\]", source, re.DOTALL)
        self.assertIsNotNone(allowlist_match, "PERSISTED_CONFIG_KEYS allowlist missing from app.js")
        allowlist = re.findall(r'"([a-z0-9_]+)"', allowlist_match.group(1))
        self.assertTrue(allowlist, "PERSISTED_CONFIG_KEYS allowlist is empty")
        forbidden = {"llm_api_key", "llm_base_url", "wos_api_key", "openalex_api_key", "openalex_email",
                     "crossref_email", "semantic_scholar_api_key", "pubmed_api_key", "pubmed_email",
                     "pubmed_tool", "serper_api_key", "retrieval_embedding_api_key",
                     "retrieval_embedding_base_url", "retrieval_reranker_api_key",
                     "retrieval_reranker_base_url"}
        for key in allowlist:
            self.assertNotIn(key, forbidden, f"secret-ish field persisted in browser: {key}")
            self.assertFalse(
                "api_key" in key or "token" in key or "secret" in key or "password" in key,
                f"credential-like field persisted: {key}",
            )
        # saveUserConfigToLocal must route through the allowlist filter.
        self.assertIn("pickPersistableConfig(config)", source)

    def test_legacy_browser_secret_fields_are_not_restored(self):
        """restoreUserConfigFromLocal must purge legacy secret keys from stored state."""
        app_js = Path(__file__).resolve().parent.parent / "paperseek" / "static" / "app.js"
        source = app_js.read_text(encoding="utf-8")
        legacy_match = re.search(r"LEGACY_SENSITIVE_CONFIG_KEYS\s*=\s*\[(.*?)\]", source, re.DOTALL)
        self.assertIsNotNone(legacy_match, "LEGACY_SENSITIVE_CONFIG_KEYS missing from app.js")
        legacy_keys = re.findall(r'"([a-z0-9_]+)"', legacy_match.group(1))
        for required in ("llm_api_key", "wos_api_key", "serper_api_key", "retrieval_embedding_api_key"):
            self.assertIn(required, legacy_keys, f"legacy purge list missing {required}")
        # restore must strip unknown fields and never set secret inputs from storage.
        restore_match = re.search(
            r"function restoreUserConfigFromLocal\(\)\s*\{(.*?)\n\}", source, re.DOTALL
        )
        self.assertIsNotNone(restore_match, "restoreUserConfigFromLocal missing from app.js")
        restore_body = restore_match.group(1)
        self.assertIn("hadDisallowedFields", restore_body)
        for secret_input in ('setVal("llmApiKey"', 'setVal("wosApiKey"', 'setVal("serperApiKey"'):
            self.assertNotIn(secret_input, restore_body, f"restore writes secret input {secret_input}")


if __name__ == "__main__":
    unittest.main()

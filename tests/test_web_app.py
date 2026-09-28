import tempfile
from pathlib import Path
from paperseek.providers import PaperAuthor, PaperCitation, PaperIdentifiers, PaperLinks, PaperNames, PaperRecord, PaperSource
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from paperseek.web_app import app
from paperseek.web_app import SearchRequest, _config_from_payload
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

    def test_llm_models_post_remote_success(self):
        fake_response = unittest.mock.MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "data": [
                {"id": "deepseek-ai/DeepSeek-V3"},
                {"id": "deepseek-ai/DeepSeek-R1"},
            ]
        }
        with patch("paperseek_core.llm.requests.get", return_value=fake_response):
            response = self.client.post(
                "/api/llm/models",
                json={
                    "llm_provider": "siliconflow",
                    "llm_base_url": "https://api.siliconflow.cn/v1",
                    "llm_api_key": "sk-test",
                },
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["source"], "remote")
        self.assertEqual(data["models"], ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1"])
        self.assertEqual(data["count"], 2)

    def test_llm_models_post_ollama_tags(self):
        fake_response = unittest.mock.MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "models": [
                {"name": "qwen2.5:14b", "model": "qwen2.5:14b"},
                {"name": "llama3.1:8b", "model": "llama3.1:8b"},
            ]
        }
        with patch("paperseek_core.llm.requests.get", return_value=fake_response):
            response = self.client.post(
                "/api/llm/models",
                json={
                    "llm_provider": "ollama",
                    "llm_base_url": "http://127.0.0.1:11434/v1",
                },
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["source"], "remote")
        self.assertEqual(data["models"], ["qwen2.5:14b", "llama3.1:8b"])

    def test_llm_models_post_fallback_on_error(self):
        import requests
        with patch("paperseek_core.llm.requests.get", side_effect=requests.ConnectionError("Connection refused")):
            response = self.client.post(
                "/api/llm/models",
                json={
                    "llm_provider": "openai",
                    "llm_base_url": "http://unreachable-host-9999.local/v1",
                },
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["source"], "preset")
        self.assertIn("gpt-4o", data["models"])
        self.assertIn("warning", data)


    def test_config_save_endpoint_persists_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            with temporary_env(
                {"PAPERSEEK_CONFIG_FILE": str(Path(tmp) / "config.json")},
                clear=CONFIG_ENV_KEYS,
            ):
                response = self.client.post(
                    "/api/config/save",
                    json={
                        "settings": {
                            "LLM_PROVIDER": "deepseek",
                            "LLM_MODEL": "deepseek-chat",
                            "FEDERATED_PROFILE": "biomed",
                            "FEDERATED_MAX_PER_SOURCE": 35,
                        }
                    },
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["status"], "ok")
                self.assertEqual(data["saved_count"], 4)
                self.assertIn("LLM_PROVIDER", data["keys"])
                self.assertIn("FEDERATED_PROFILE", data["keys"])


if __name__ == "__main__":
    unittest.main()

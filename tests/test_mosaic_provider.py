import time
import sys
import types
import unittest
from unittest.mock import patch

from paperseek_core.integrations.mosaic_provider import (
    DEFAULT_PROFILE,
    MosaicFederatedProvider,
    SOURCE_PROFILES,
    _RoutedMosaicSource,
    is_federated_available,
)


class FakeMosaicPaper:
    def __init__(self, **kwargs):
        self.title = kwargs.get("title", "")
        self.authors = kwargs.get("authors", [])
        self.year = kwargs.get("year")
        self.doi = kwargs.get("doi")
        self.arxiv_id = kwargs.get("arxiv_id")
        self.pii = kwargs.get("pii")
        self.abstract = kwargs.get("abstract")
        self.journal = kwargs.get("journal")
        self.volume = kwargs.get("volume")
        self.issue = kwargs.get("issue")
        self.pages = kwargs.get("pages")
        self.pdf_url = kwargs.get("pdf_url")
        self.source = kwargs.get("source", "")
        self.is_open_access = kwargs.get("is_open_access", False)
        self.url = kwargs.get("url")
        self.citation_count = kwargs.get("citation_count")
        self.relevance_score = kwargs.get("relevance_score")
        self.openalex_id = kwargs.get("openalex_id")

    @property
    def uid(self):
        if self.doi:
            return f"doi:{self.doi.lower()}"
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id}"
        if self.pii:
            return f"pii:{self.pii}"
        return f"title:{self.title.lower()[:80]}"

    def to_dict(self):
        return {
            key: getattr(self, key)
            for key in (
                "title", "authors", "year", "doi", "arxiv_id", "pii", "abstract",
                "journal", "volume", "issue", "pages", "pdf_url", "source",
                "is_open_access", "url", "citation_count", "relevance_score",
                "openalex_id",
            )
        }


class FakeSource:
    def __init__(self, name, papers=None, error=None, available=True, delay=0.0):
        self.name = name
        self._papers = papers or []
        self._error = error
        self._available = available
        self.delay = delay
        self.calls = 0

    def available(self):
        return self._available

    def search(self, query, max_results=25, filters=None):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self._error:
            raise self._error
        return list(self._papers)[:max_results]


class FakeServices:
    @staticmethod
    def merge_papers(seen, paper):
        uid = paper.uid
        if uid not in seen:
            seen[uid] = paper
            return
        existing = seen[uid]
        if paper.abstract and not existing.abstract:
            existing.abstract = paper.abstract
        if paper.pdf_url and not existing.pdf_url:
            existing.pdf_url = paper.pdf_url
        if paper.doi and not existing.doi:
            existing.doi = paper.doi
        if paper.citation_count is not None and (
            existing.citation_count is None
            or paper.citation_count > existing.citation_count
        ):
            existing.citation_count = paper.citation_count


def fake_paper(**kwargs):
    return FakeMosaicPaper(**kwargs)


class MosaicFederatedProviderTest(unittest.TestCase):
    def _provider(self, **kwargs):
        return MosaicFederatedProvider(**kwargs)

    def _search(self, provider, fake_sources, query="q", limit=50, page=1):
        import paperseek_core.integrations.mosaic_provider as module

        class _Registry:
            @staticmethod
            def build_sources(cfg):
                return list(fake_sources)

        class _Mosaic:
            source_registry = _Registry
            services = FakeServices

        with patch.object(module, "require_mosaic", return_value=_Mosaic):
            return provider.search(query=query, limit=limit, page=page)

    def test_multi_source_success_collects_stats(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", [fake_paper(title="A", doi="10.1/a", source="OpenAlex")]),
            FakeSource("Crossref", [fake_paper(title="B", doi="10.1/b", source="Crossref")]),
        ])
        self.assertEqual(len(result.hits), 2)
        self.assertEqual(result.metadata.total, 2)
        self.assertEqual(provider.last_stats["raw_total"], 2)
        self.assertEqual(provider.last_stats["unique"], 2)
        self.assertEqual(provider.last_errors, [])

    def test_one_source_timeout_does_not_abort(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", [fake_paper(title="A", doi="10.1/a")]),
            FakeSource("Europe PMC", error=TimeoutError("read timed out")),
        ])
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(len(provider.last_errors), 1)
        self.assertIn("Europe PMC", provider.last_errors[0])

    def test_one_source_5xx_does_not_abort(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", [fake_paper(title="A", doi="10.1/a")]),
            FakeSource("Semantic Scholar", error=RuntimeError("HTTP 503")),
        ])
        self.assertEqual(len(result.hits), 1)
        self.assertIn("Semantic Scholar", provider.last_errors[0])

    def test_empty_source_contributes_nothing(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", []),
            FakeSource("Crossref", [fake_paper(title="B", doi="10.1/b")]),
        ])
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(provider.last_stats["per_source"].get("OpenAlex", 0), 0)

    def test_same_doi_from_two_sources_merges_and_tracks_provenance(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", [
                fake_paper(title="From OA", doi="10.1/dup", citation_count=5, source="OpenAlex")
            ]),
            FakeSource("Crossref", [
                fake_paper(title="From CR", doi="10.1/dup", source="Crossref")
            ]),
        ])
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(provider.last_stats["raw_total"], 2)
        self.assertEqual(provider.last_stats["unique"], 1)
        self.assertEqual(provider.last_stats["merged"], 1)
        self.assertEqual(result.hits[0].raw["mosaic_sources"], ["OpenAlex", "Crossref"])

    def test_complementary_metadata_from_two_sources(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", [
                fake_paper(
                    title="X",
                    doi="10.1/x",
                    abstract=None,
                    citation_count=7,
                    source="OpenAlex",
                )
            ]),
            FakeSource("Europe PMC", [
                fake_paper(
                    title="X alt",
                    doi="10.1/x",
                    abstract="The abstract",
                    source="Europe PMC",
                )
            ]),
        ])
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(result.hits[0].abstract, "The abstract")

    def test_mosaic_not_installed_raises_clear_error(self):
        import paperseek_core.integrations.mosaic_provider as module
        from paperseek_core.integrations.mosaic_adapter import MosaicNotInstalledError

        provider = self._provider()
        with patch.object(
            module,
            "require_mosaic",
            side_effect=MosaicNotInstalledError("no mosaic"),
        ):
            with self.assertRaises(MosaicNotInstalledError):
                provider.search(query="q")

    def test_unavailable_sources_are_filtered_before_search(self):
        provider = self._provider()
        unavailable = FakeSource(
            "IEEE Xplore",
            error=AssertionError("should not be searched"),
            available=False,
        )
        result = self._search(provider, [
            FakeSource("OpenAlex", [fake_paper(title="A", doi="10.1/a")]),
            unavailable,
        ])
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(unavailable.calls, 0)

    def test_unavailable_only_raises_provider_error(self):
        from paperseek_core.sources.providers import ProviderError

        provider = self._provider()
        with self.assertRaises(ProviderError) as ctx:
            self._search(provider, [FakeSource("IEEE", available=False)])
        self.assertIn("No federated sources", str(ctx.exception))

    def test_empty_query_raises_provider_error(self):
        from paperseek_core.sources.providers import ProviderError

        provider = self._provider()
        with self.assertRaises(ProviderError):
            provider.search(query="   ")

    def test_profiles_map_to_expected_sources(self):
        self.assertIn("pubmed", SOURCE_PROFILES["biomed"])
        self.assertIn("europepmc", SOURCE_PROFILES["biomed"])
        self.assertIn("pmc", SOURCE_PROFILES["biomed"])
        self.assertIn("biorxiv", SOURCE_PROFILES["biomed"])
        self.assertIn("arxiv", SOURCE_PROFILES["cs"])
        self.assertIn("dblp", SOURCE_PROFILES["cs"])
        self.assertIn("doaj", SOURCE_PROFILES["general"])
        self.assertEqual(DEFAULT_PROFILE, "general")

    def test_unknown_profile_falls_back_to_general(self):
        provider = self._provider(profile="nonexistent")
        self.assertEqual(provider.profile, SOURCE_PROFILES["general"])

    def test_limit_pages_without_losing_total_pool(self):
        provider = self._provider(max_per_source=4)
        source_a = FakeSource(
            "OpenAlex",
            [fake_paper(title=f"A{i}", doi=f"10.1/a{i}", source="OpenAlex") for i in range(4)],
        )
        source_b = FakeSource(
            "Crossref",
            [fake_paper(title=f"B{i}", doi=f"10.1/b{i}", source="Crossref") for i in range(4)],
        )
        first = self._search(provider, [source_a, source_b], limit=3)
        second = provider.search(query="q", limit=3, page=2)
        third = provider.search(query="q", limit=3, page=3)

        self.assertEqual(first.metadata.total, 8)
        self.assertEqual(second.metadata.total, 8)
        self.assertEqual([h.title for h in first.hits], ["A0", "B0", "A1"])
        self.assertEqual([h.title for h in second.hits], ["B1", "A2", "B2"])
        self.assertEqual([h.title for h in third.hits], ["A3", "B3"])
        self.assertEqual(source_a.calls, 1)
        self.assertEqual(source_b.calls, 1)

    def test_larger_request_refreshes_snapshot(self):
        provider = self._provider(max_per_source=2)
        source = FakeSource(
            "OpenAlex",
            [fake_paper(title=f"P{i}", doi=f"10.1/p{i}") for i in range(6)],
        )
        first = self._search(provider, [source], limit=2)
        self.assertEqual(first.metadata.total, 2)
        with patch(
            "paperseek_core.integrations.mosaic_provider.require_mosaic",
            side_effect=AssertionError("cache refresh must call require_mosaic"),
        ):
            with self.assertRaises(AssertionError):
                provider.search(query="q", limit=5)

    def test_parallel_completion_does_not_change_merge_precedence(self):
        provider = self._provider(parallel=True)
        slow_first = FakeSource(
            "OpenAlex",
            [fake_paper(title="OpenAlex title", doi="10.1/x", source="OpenAlex")],
            delay=0.03,
        )
        fast_second = FakeSource(
            "Crossref",
            [fake_paper(title="Crossref title", doi="10.1/x", source="Crossref")],
        )
        result = self._search(provider, [slow_first, fast_second])
        self.assertEqual(result.hits[0].title, "OpenAlex title")
        self.assertEqual(result.hits[0].raw["mosaic_sources"], ["OpenAlex", "Crossref"])

    def test_existing_source_credentials_are_passed_to_mosaic_config(self):
        captured = {}

        class _Registry:
            @staticmethod
            def build_sources(cfg):
                captured.update(cfg)
                return [FakeSource("OpenAlex"), FakeSource("Crossref")]

        class _Mosaic:
            source_registry = _Registry

        provider = self._provider(
            profile="biomed",
            openalex_email="oa@example.test",
            crossref_email="cr@example.test",
            semantic_scholar_api_key="ss-test-key",
            pubmed_api_key="ncbi-test-key",
        )
        sources = provider._build_sources(_Mosaic)
        self.assertEqual(
            captured["sources"]["semantic_scholar"]["api_key"],
            "ss-test-key",
        )
        self.assertEqual(captured["sources"]["pubmed"]["api_key"], "ncbi-test-key")
        self.assertEqual(captured["sources"]["pmc"]["api_key"], "ncbi-test-key")
        self.assertEqual(getattr(sources[0], "_email"), "oa@example.test")
        self.assertEqual(getattr(sources[1], "_email"), "cr@example.test")

    def test_routed_mosaic_source_uses_router_and_restores_module_httpx(self):
        module_name = "tests._fake_mosaic_httpx_source"
        module = types.ModuleType(module_name)

        class OriginalHTTPX:
            URL = staticmethod(lambda value: value)

        original_httpx = OriginalHTTPX()
        module.httpx = original_httpx
        sys.modules[module_name] = module

        class Response:
            status_code = 200
            def raise_for_status(self): return None
            def json(self): return {"ok": True}

        class Router:
            def __init__(self): self.calls = []
            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return Response()

        class Source:
            name = "Fake"
            def available(self): return True
            def search(self, query, max_results=25, filters=None):
                with module.httpx.Client(timeout=7, headers={"X-Base": "1"}) as client:
                    response = client.get("https://api.example.test/search", params={"q": query}, headers={"X-Extra": "2"})
                return [response.json()]

        Source.__module__ = module_name
        router = Router()
        try:
            routed = _RoutedMosaicSource(Source(), router)
            result = routed.search("motif")
            self.assertEqual(result, [{"ok": True}])
            self.assertEqual(router.calls[0][0], "GET")
            self.assertEqual(router.calls[0][1], "https://api.example.test/search")
            self.assertEqual(router.calls[0][2]["params"], {"q": "motif"})
            self.assertEqual(router.calls[0][2]["headers"], {"X-Base": "1", "X-Extra": "2"})
            self.assertIs(module.httpx, original_httpx)
        finally:
            sys.modules.pop(module_name, None)

    def test_retrieval_capabilities_single_relevance_lane(self):
        from paperseek_core.retrieval import RetrievalLane

        caps = self._provider().retrieval_capabilities()
        self.assertEqual(caps.source, "federated")
        self.assertEqual(caps.lanes, (RetrievalLane.RELEVANCE,))

    def test_is_federated_available_bool(self):
        self.assertIsInstance(is_federated_available(), bool)


if __name__ == "__main__":
    unittest.main()
